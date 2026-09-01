"""An append-only log has to actually append.

Found by the 2026-08-23 engine audit (finding 15), measured wider here: the
shape was in SIX modules, eleven times, not one.

    with _LOCK:
        existing = log_path.read_text(...)
        new_content = existing + json.dumps(entry) + "\\n"
        atomic_write_text(log_path, new_content, mode=0o644)

``critical.py``'s module docstring called that "JSONL append + atomic write so
concurrent writers don't corrupt the file". Both halves mislead. It is a
read-modify-rewrite, and ``atomic_write_text`` buys atomicity of the REPLACE,
which prevents a torn file and does nothing about a lost update: two writers
that both read the pre-write state each rewrite the whole log, and the loser's
entry is gone with no error. The ``threading.Lock`` guarding each site orders
threads inside one interpreter and says nothing about a second process.

The pages this backs are approvals-sent, inbox-dismissed, tasks-done, pipeline
touches, investor sends and critical items -- five of the six are the record
that something already happened, so a lost entry re-offers an action the
operator has already taken.

Cost was also O(file) per write: marking the 5,000th critical item read and
rewrote 5,000 lines.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
SOURCES = ROOT / "scripts" / "bridge_daemon"

sys.path.insert(0, str(ROOT))
from scripts.bridge_daemon._jsonl import append_jsonl, read_jsonl_capped  # noqa: E402
from tests.repo_files import read_sources  # noqa: E402


def _lines(p: Path) -> list[dict]:
    return [json.loads(ln) for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]


# --- the shape is gone from the tree -----------------------------------------

def test_no_module_rebuilds_the_whole_log_to_add_one_line():
    """The scan is over the daemon package, not one file: the defect was
    copied, so a guard on the copy that was reported would let the next one
    through."""
    offenders = []
    inspected = 0
    # Read through `read_sources`: the rglob lists the paths and the loop reads
    # them, and a file can be created and removed between those two moments in a
    # checkout several agents share. A skip is the right answer for a scan - a
    # file that is gone cannot hold the forbidden shape - and it warns.
    vanished: list[Path] = []
    for path, src in read_sources(sorted(SOURCES.rglob("*.py")), vanished):
        if path.name == "_jsonl.py":
            continue                      # its docstring quotes the old shape
        inspected += 1
        for n, line in enumerate(src.splitlines(), 1):
            if re.search(r"new_content\s*\+?=", line):
                offenders.append(f"{path.relative_to(ROOT)}:{n}")
    # 44 daemon modules reached the regex on 2026-08-26; floor well under that so
    # retiring a module does not fail this test. If the `path.name == "_jsonl.py"`
    # skip ever widened to match every file, the offender list would be empty and
    # the scan below would pass while reading nothing.
    assert inspected >= 28, (
        f"only {inspected} modules reached the scan "
        f"({len(vanished)} vanished mid-walk)")
    assert not offenders, (
        "a read-modify-rewrite is back in an append-only log; use "
        "_jsonl.append_jsonl:\n  " + "\n  ".join(offenders)
    )


def test_every_log_writer_goes_through_the_shared_primitive():
    """Pins the detector above: if nothing imports it, the scan proves nothing."""
    # Same walk-then-read race. Skipping is safe here because this is a FLOOR: a
    # file that vanished can only make the count smaller, so the failure is loud
    # rather than a wrong answer. The count is reported with it.
    vanished: list[Path] = []
    users = [p.name for p, src in read_sources(sorted(SOURCES.rglob("*.py")), vanished)
             if "append_jsonl" in src and p.name != "_jsonl.py"]
    assert len(users) >= 6, f"{users} ({len(vanished)} vanished mid-walk)"


# --- the lost update, demonstrated -------------------------------------------

def _old_write(path: Path, entry: dict, stale: str) -> None:
    """The pre-fix write, replayed with a caller that read `stale` earlier."""
    content = stale
    if content and not content.endswith("\n"):
        content += "\n"
    path.write_text(content + json.dumps(entry) + "\n", encoding="utf-8")


def test_the_old_shape_loses_an_entry_under_an_interleave(tmp_path):
    """Anchor the premise. Two writers read the same state, then both write."""
    log = tmp_path / "old.jsonl"
    log.write_text("", encoding="utf-8")
    a_saw = log.read_text(encoding="utf-8")
    b_saw = log.read_text(encoding="utf-8")     # both read before either wrote
    _old_write(log, {"id": "a"}, a_saw)
    _old_write(log, {"id": "b"}, b_saw)
    assert [e["id"] for e in _lines(log)] == ["b"], "the premise no longer holds"


def test_the_new_shape_keeps_both(tmp_path):
    log = tmp_path / "new.jsonl"
    append_jsonl(log, {"id": "a"})
    append_jsonl(log, {"id": "b"})
    assert [e["id"] for e in _lines(log)] == ["a", "b"]


def test_two_real_processes_do_not_lose_entries(tmp_path):
    """The behavioural proof, across the process boundary a Lock cannot reach."""
    log = tmp_path / "concurrent.jsonl"
    prog = (
        "import sys;"
        f"sys.path.insert(0, {str(ROOT)!r});"
        "from pathlib import Path;"
        "from scripts.bridge_daemon._jsonl import append_jsonl;"
        "tag = sys.argv[1];"
        f"p = Path({str(log)!r});"
        "[append_jsonl(p, {'id': f'{tag}-{i}', 'pad': 'x' * 200}) for i in range(60)]"
    )
    procs = [subprocess.Popen([sys.executable, "-c", prog, tag]) for tag in "abcd"]
    for p in procs:
        assert p.wait(timeout=120) == 0
    got = _lines(log)
    assert len(got) == 240, f"lost {240 - len(got)} of 240 entries"
    assert len({e["id"] for e in got}) == 240, "an entry was written twice or clobbered"


# --- file hygiene -------------------------------------------------------------

def test_the_log_is_created_with_its_parent_directories(tmp_path):
    """The half of the old test that holds on every platform."""
    log = tmp_path / "sub" / "dir" / "mode.jsonl"
    append_jsonl(log, {"id": "a"})
    assert log.exists(), "parent directories were not created"


@pytest.mark.skipif(sys.platform == "win32",
                    reason="Windows does not carry POSIX owner/group/other bits; "
                           "os.chmod there sets the read-only attribute and "
                           "os.stat reports a writable file as 0o666")
def test_the_log_is_created_with_the_requested_mode(tmp_path):
    """0o644 exactly, and this is a POSIX claim.

    The bare assertion carried no platform guard until 2026-08-30, so on
    Windows it failed on a correctly-created file - a red that says nothing
    about the code. The mode still matters on the platforms that have one: a
    log the group can write is a log another account can rewrite.
    """
    log = tmp_path / "sub" / "dir" / "mode.jsonl"
    append_jsonl(log, {"id": "a"})
    assert oct(os.stat(log).st_mode & 0o777) == "0o644"


def test_a_file_missing_its_trailing_newline_is_repaired(tmp_path):
    """A crash under the old rewrite path could leave one. Gluing the next
    entry onto it would cost BOTH records, not one."""
    log = tmp_path / "torn.jsonl"
    log.write_text(json.dumps({"id": "first"}), encoding="utf-8")   # no "\n"
    append_jsonl(log, {"id": "second"})
    assert [e["id"] for e in _lines(log)] == ["first", "second"]


# --- the capped read keeps the newest, and says so ---------------------------

def test_an_uncapped_log_reads_whole_and_is_not_flagged(tmp_path):
    log = tmp_path / "small.jsonl"
    for i in range(5):
        append_jsonl(log, {"id": i})
    entries, truncated = read_jsonl_capped(log, 1_000_000)
    assert [e["id"] for e in entries] == [0, 1, 2, 3, 4]
    assert truncated is False


def test_over_the_cap_the_newest_entries_survive(tmp_path):
    """The old reader returned [] here, which a page renders as 'nothing is
    flagged' -- indistinguishable from an empty log, with writers still
    appending and no error anywhere."""
    log = tmp_path / "big.jsonl"
    for i in range(200):
        append_jsonl(log, {"id": i, "pad": "x" * 100})
    entries, truncated = read_jsonl_capped(log, 2_000)
    assert truncated is True
    assert entries, "the capped read still returns nothing"
    assert entries[-1]["id"] == 199, "the TAIL is what a newest-first page needs"
    assert len(entries) < 200


def test_the_partial_leading_line_is_dropped_not_guessed(tmp_path):
    log = tmp_path / "cut.jsonl"
    for i in range(50):
        append_jsonl(log, {"id": i, "pad": "y" * 80})
    entries, truncated = read_jsonl_capped(log, 500)
    assert truncated is True
    assert all(isinstance(e.get("id"), int) for e in entries), entries


def test_a_missing_file_is_empty_and_not_truncated(tmp_path):
    assert read_jsonl_capped(tmp_path / "nope.jsonl", 100) == ([], False)


def test_unparseable_and_non_dict_rows_are_skipped(tmp_path):
    log = tmp_path / "mixed.jsonl"
    log.write_text('{"id": "ok"}\nnot json\n["a list"]\n\n{"id": "ok2"}\n',
                   encoding="utf-8")
    entries, _ = read_jsonl_capped(log, 1_000_000)
    assert [e["id"] for e in entries] == ["ok", "ok2"]


@pytest.mark.parametrize("bad", [b"\xff\xfe\x00binary", "valid \udcff".encode("utf-8", "surrogateescape")])
def test_undecodable_bytes_do_not_raise(tmp_path, bad):
    """A log is opened in binary and decoded with `errors="replace"`, so a
    corrupt byte costs one row's readability, never the endpoint."""
    log = tmp_path / "bad.jsonl"
    log.write_bytes(bad + b'\n{"id": "after"}\n')
    entries, _ = read_jsonl_capped(log, 1_000_000)
    assert [e["id"] for e in entries] == ["after"]
