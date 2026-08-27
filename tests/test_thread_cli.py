"""Tests for scripts.thread CLI.

Every test here used to set `MEMORY_MD` and assert against a `## Active Threads`
block in that file. The block was retired on 2026-08-20 and its writer removed
on 2026-08-27, so the assertions moved to the thread file, which was always the
record. `test_no_subcommand_writes_a_memory_index` is what stops the writer
coming back.
"""
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.utils.threads_lib import parse_thread_file

REPO_ROOT = Path(__file__).resolve().parents[1]


def _run(*argv: str, check: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "scripts/thread.py", *argv],
        capture_output=True, text=True, check=check, cwd=REPO_ROOT,
    )


@pytest.fixture()
def threads_root(tmp_path: Path, monkeypatch) -> Path:
    """An isolated threads root, and an isolated DATA ROOT beside it.

    The data root is pinned even though this CLI no longer reaches it. On
    2026-08-27 a mutation put the MEMORY.md writer back into `cmd_open` to prove
    the guard, and every test here that calls `open` inherited the operator's
    real `HEADING_OS_DATA` and truncated their live memory index. A test must not
    be able to reach the overlay whether or not today's code tries to.
    """
    root = tmp_path / "threads"
    monkeypatch.setenv("THREADS_ROOT", str(root))
    monkeypatch.setenv("HEADING_OS_DATA", str(tmp_path / "data"))
    (tmp_path / "data").mkdir(exist_ok=True)
    return root


def _open_thread(threads_root: Path, title: str = "Test thread") -> Path:
    """Open one thread and return its file path."""
    r = _run("open", "business", title)
    assert r.returncode == 0, r.stderr
    return list((threads_root / "business").glob("*.md"))[0]


def test_thread_open_creates_the_file(threads_root: Path) -> None:
    r = _run("open", "business", "Quillon registrar abuse report")
    assert r.returncode == 0, r.stderr

    files = list((threads_root / "business").glob("*.md"))
    assert len(files) == 1
    parsed = parse_thread_file(files[0])
    assert parsed.title == "Quillon registrar abuse report"
    assert parsed.status == "active"
    assert parsed.type == "business"


def test_thread_log_appends_the_entry_and_the_artifact(threads_root: Path) -> None:
    path = _open_thread(threads_root)
    r = _run("log", path.stem, "Sent reply to the abuse desk",
             "--artifact", "outputs/email-drafts/2026-04-29_reply.md")
    assert r.returncode == 0, r.stderr

    parsed = parse_thread_file(path)
    assert "Sent reply to the abuse desk" in parsed.body
    assert "outputs/email-drafts/2026-04-29_reply.md" in parsed.links["outputs"]


def test_thread_close_keeps_the_file_and_flips_the_status(threads_root: Path) -> None:
    path = _open_thread(threads_root, "Closeable")
    r = _run("close", path.stem, "--reason", "resolved")
    assert r.returncode == 0, r.stderr
    assert path.exists()
    assert parse_thread_file(path).status == "closed"


def test_thread_hold_and_reopen_round_trip(threads_root: Path) -> None:
    path = _open_thread(threads_root, "Holdable")

    _run("hold", path.stem, "--reason", "waiting on counterparty", check=True)
    assert parse_thread_file(path).status == "on-hold"

    _run("reopen", path.stem, check=True)
    assert parse_thread_file(path).status == "active"


def test_thread_list_shows_active_threads(threads_root: Path) -> None:
    _run("open", "business", "Alpha thread", check=True)
    _run("open", "business", "Bravo thread", check=True)

    r = _run("list")
    assert r.returncode == 0, r.stderr
    assert "Alpha thread" in r.stdout
    assert "Bravo thread" in r.stdout


def test_thread_list_hides_a_closed_thread(threads_root: Path) -> None:
    """`list` with no --status shows active only, which is what /prime reads."""
    path = _open_thread(threads_root, "Retired thread")
    _run("close", path.stem, "--reason", "done", check=True)
    assert "Retired thread" not in _run("list").stdout
    assert "Retired thread" in _run("list", "--status", "closed").stdout


def test_thread_find_matches_title_substring(threads_root: Path) -> None:
    _run("open", "business", "Quillon registrar", check=True)
    _run("open", "business", "ExampleTelco negotiation", check=True)

    r = _run("find", "Quillon")
    assert r.returncode == 0
    assert "Quillon registrar" in r.stdout
    assert "ExampleTelco" not in r.stdout


def test_thread_archive_scan_moves_old_closed_threads(threads_root: Path) -> None:
    from datetime import datetime, timedelta

    from scripts.utils.threads_lib import write_thread_file
    from scripts.utils.workspace import get_default_tz

    path = _open_thread(threads_root, "Old")
    _run("close", path.stem, "--reason", "resolved", check=True)

    # Backdate the file's last_touched to 100 days ago
    parsed = parse_thread_file(path)
    parsed.last_touched = (
        datetime.now(get_default_tz()).date() - timedelta(days=100)
    ).isoformat()
    write_thread_file(path, parsed)

    r = _run("archive-scan", "--apply")
    assert r.returncode == 0, r.stderr
    archived = list((threads_root / "archive").rglob("*.md"))
    assert len(archived) == 1
    assert not path.exists()


def test_thread_show_prints_file_content(threads_root: Path) -> None:
    path = _open_thread(threads_root, "Showable thread")
    r = _run("show", path.stem)
    assert r.returncode == 0, r.stderr
    assert "Showable thread" in r.stdout
    assert "## Open follow-ups" in r.stdout


def test_thread_show_returns_error_on_missing_thread(threads_root: Path) -> None:
    """C-1 regression: missing thread should print clean error, not Python traceback."""
    r = _run("show", "nonexistent-thread")
    assert r.returncode == 1
    assert "error:" in r.stderr.lower()
    assert "Traceback" not in r.stderr


# ======================================
# Scrutiny regressions (2026-04-30)
# ======================================


def test_log_two_follow_ups_does_not_duplicate_section(threads_root: Path) -> None:
    """H1 regression: appending two follow-ups must not corrupt or duplicate the section."""
    path = _open_thread(threads_root, "Two follow-ups")
    _run("log", path.stem, "e1", "--follow-up", "First", check=True)
    _run("log", path.stem, "e2", "--follow-up", "Second", check=True)

    body = path.read_text(encoding="utf-8")
    assert body.count("## Open follow-ups") == 1, "section header was duplicated"
    assert "## Open follow-ups\n\n- [ ] First\n- [ ] Second" in body


def test_log_three_decisions_does_not_duplicate_section(threads_root: Path) -> None:
    """H1 regression: same corruption pattern affects --decision, not just --follow-up."""
    path = _open_thread(threads_root, "Decisions stack")
    for txt in ("Alpha", "Bravo", "Charlie"):
        _run("log", path.stem, f"e-{txt}", "--decision", txt, check=True)
    assert path.read_text(encoding="utf-8").count("## Decisions") == 1


def test_log_done_indexes_remain_stable_after_multiple_adds(threads_root: Path) -> None:
    """H1 regression: --done <N> must target the right item after multiple --follow-up adds."""
    path = _open_thread(threads_root, "Done index")
    for txt in ("First", "Second", "Third"):
        _run("log", path.stem, f"e-{txt}", "--follow-up", txt, check=True)
    _run("log", path.stem, "tick", "--done", "1", check=True)

    body = path.read_text(encoding="utf-8")
    assert "- [x] Second" in body
    assert "- [ ] First" in body
    assert "- [ ] Third" in body


def test_log_collapses_whitespace_in_the_event(threads_root: Path) -> None:
    """L1 regression: a multi-paragraph event must not keep its line breaks.

    Retargeted 2026-08-18. The collapse originally protected the MEMORY.md hook,
    which stopped carrying event text; the sanitising step it guards still runs,
    and its output lands in the thread body, so the guard moved there rather than
    being deleted with the hook it used to watch. The hook itself is gone as of
    2026-08-27 and the body is now its only destination.
    """
    path = _open_thread(threads_root, "Whitespace test")
    _run("log", path.stem, "line one\nline two\n\nline three", check=True)

    body = parse_thread_file(path).body
    assert "line one line two line three" in body
    assert "line one  line two" not in body  # no double spaces


def test_open_rejects_empty_slug_with_clean_error(threads_root: Path) -> None:
    """H5 + M3 regression: empty-slug title must produce clean rc=1, not a traceback."""
    r = _run("open", "business", "!!!")
    assert r.returncode == 1
    assert "error:" in r.stderr.lower()
    assert "Traceback" not in r.stderr


def test_log_on_an_unknown_thread_writes_nothing(threads_root: Path) -> None:
    """Atomicity: a log that cannot resolve its target must leave the tree alone.

    This replaces `test_log_aborts_when_memory_md_missing`, which pinned the
    same property against the second file `log` used to write. With one file
    left, the remaining way to half-apply a log is to resolve the wrong target
    or none, so that is what is asserted.
    """
    existing = _open_thread(threads_root, "Atomic test")
    before = existing.read_text(encoding="utf-8")

    r = _run("log", "2026-01-01-no-such-thread", "should-not-land")
    assert r.returncode == 1
    assert "not found" in r.stderr.lower()
    assert "Traceback" not in r.stderr
    assert existing.read_text(encoding="utf-8") == before
    assert list((threads_root / "business").glob("*.md")) == [existing]


def test_list_warns_about_corrupted_threads(threads_root: Path) -> None:
    """Corrupted threads must surface as a stderr warning, not silently disappear."""
    _open_thread(threads_root, "Healthy thread")

    # Plant a corrupted thread file (id-stem mismatch triggers L3 ValueError).
    bad = threads_root / "business" / "2026-04-30-corrupted.md"
    bad.write_text(
        "---\nid: wrong-id\ntitle: t\nstatus: active\ntype: business\n"
        "classification: ceo-only\nopened: 2026-04-30\nlast_touched: 2026-04-30\n"
        "counterparties: []\nlinks: {}\ntags: []\n---\nbody\n",
        encoding="utf-8",
    )

    r = _run("list")
    assert r.returncode == 0
    assert "Healthy thread" in r.stdout
    assert "warning" in r.stderr.lower()
    assert "2026-04-30-corrupted.md" in r.stderr


def test_log_accepts_multiple_followups_artifacts_decisions_in_one_call(
    threads_root: Path,
) -> None:
    """Repeatable-flag regression: passing --follow-up / --artifact / --decision
    twice or more in one log call must record EVERY value, not just the last.
    """
    path = _open_thread(threads_root, "Repeatable flags")
    r = _run("log", path.stem, "multi-flag event",
             "--artifact", "outputs/a/one.md",
             "--artifact", "outputs/a/two.pdf",
             "--follow-up", "Follow-up alpha",
             "--follow-up", "Follow-up bravo",
             "--follow-up", "Follow-up charlie",
             "--decision", "Decision one",
             "--decision", "Decision two")
    assert r.returncode == 0, r.stderr

    parsed = parse_thread_file(path)
    assert "outputs/a/one.md" in parsed.links["outputs"]
    assert "outputs/a/two.pdf" in parsed.links["outputs"]

    body = path.read_text(encoding="utf-8")
    assert "- [ ] Follow-up alpha" in body
    assert "- [ ] Follow-up bravo" in body
    assert "- [ ] Follow-up charlie" in body
    assert "Decision one" in body
    assert "Decision two" in body
    assert body.count("## Open follow-ups") == 1
    assert body.count("## Decisions") == 1


# ======================================
# The retired MEMORY.md index (2026-08-27)
# ======================================


@pytest.mark.parametrize("argv", [
    ("open", "business", "A second probe"),
    ("open", "personal", "A personal probe"),
    ("log", "{id}", "an event"),
    ("log", "{id}", "an event", "--follow-up", "something"),
    ("quiet", "{id}", "--until", "2999-01-01"),
    ("quiet", "{id}", "--indefinite"),
    ("quiet", "{id}", "--clear"),
    ("hold", "{id}", "--reason", "waiting"),
    ("close", "{id}", "--reason", "done"),
    ("reopen", "{id}"),
    ("list",),
    ("find", "probe"),
    ("archive-scan", "--apply"),
])
def test_no_subcommand_writes_a_memory_index(
    threads_root: Path, tmp_path: Path, monkeypatch, argv: tuple[str, ...],
) -> None:
    """No path through the CLI may create or touch an auto-memory index.

    The check is the whole data root, not one filename: the resolver this CLI
    used to call was `get_data_root() / "auto-memory" / "MEMORY.md"`, so a
    re-added writer lands there whatever it calls the file. `HEADING_OS_DATA`
    points at an empty directory, so ANY file appearing under it is the writer
    coming back.
    """
    data_root = tmp_path / "data"  # created and pinned by the threads_root fixture
    (data_root / "auto-memory").mkdir(parents=True, exist_ok=True)
    monkeypatch.delenv("MEMORY_MD", raising=False)

    path = _open_thread(threads_root, "Index probe")
    before = sorted(p.relative_to(data_root) for p in data_root.rglob("*") if p.is_file())

    r = _run(*[a.format(id=path.stem) for a in argv])
    assert r.returncode == 0, r.stderr

    after = sorted(p.relative_to(data_root) for p in data_root.rglob("*") if p.is_file())
    assert after == before == [], (
        f"`thread.py {argv[0]}` wrote {after} under the data root; the "
        f"`## Active Threads` index was retired on 2026-08-20 and its writer "
        f"removed on 2026-08-27"
    )


def test_the_reindex_subcommand_is_gone(threads_root: Path) -> None:
    """`reindex` existed only to repair MEMORY.md drift.

    It must refuse loudly rather than survive as a no-op that reports success
    over an index it no longer maintains.
    """
    r = _run("reindex")
    assert r.returncode != 0
    assert "invalid choice" in r.stderr.lower()


def test_the_event_text_lands_in_the_body_and_nowhere_else(
    threads_root: Path, tmp_path: Path, monkeypatch,
) -> None:
    """Until 2026-08-18 `log` wrote `event[:120]` into the always-loaded index.

    That was a live value in a pointer, which `.claude/rules/memory-discipline.md`
    forbids. It was cut back to a status-and-date hook, and then the hook went
    with the index. The event has one home.
    """
    data_root = tmp_path / "data"  # created and pinned by the threads_root fixture

    path = _open_thread(threads_root, "Hook shape")
    event_text = "Marlow Carter answered at 08:00 UTC with a commercial objection"
    _run("log", path.stem, event_text, check=True)

    assert event_text in parse_thread_file(path).body
    assert list(data_root.rglob("*")) == []


def _open_thread_legacy(tmp_path: Path, monkeypatch, title: str = "Test thread"):
    """Set up an isolated registry with one open thread; return (root, path)."""
    root = tmp_path / "threads"
    monkeypatch.setenv("THREADS_ROOT", str(root))
    monkeypatch.setenv("HEADING_OS_DATA", str(tmp_path / "data"))
    (tmp_path / "data").mkdir(exist_ok=True)
    r = _run("open", "business", title)
    assert r.returncode == 0, r.stderr
    return root, list((root / "business").glob("*.md"))[0]


def test_close_refuses_without_a_reason(tmp_path: Path, monkeypatch) -> None:
    """A close with no recorded reason is the defect this guard exists for.

    One operator run flipped nineteen threads from active to closed at once.
    `close` wrote exactly `status` and `last_touched`, so a thread that was
    resolved and a thread that merely went quiet became indistinguishable on
    disk. Six of them closed over a loop the deal pipeline still showed as live:
    one awaiting a data dump, another a meeting slot. Reading the registry
    afterwards could not tell you which.
    """
    _, path = _open_thread_legacy(tmp_path, monkeypatch)
    result = _run("close", path.stem)
    assert result.returncode != 0, "close succeeded with no reason"
    assert "reason" in (result.stderr + result.stdout).lower()
    assert parse_thread_file(path).status == "active", "status changed on a refused close"


def test_close_with_a_reason_records_it_in_the_log(tmp_path: Path, monkeypatch) -> None:
    """The reason must land in the body, not only in the operator's memory."""
    _, path = _open_thread_legacy(tmp_path, monkeypatch)
    result = _run("close", path.stem, "--reason", "Superseded by the Q3 rollout thread")
    assert result.returncode == 0, result.stderr
    parsed = parse_thread_file(path)
    assert parsed.status == "closed"
    assert "Superseded by the Q3 rollout thread" in parsed.body
    assert "Closed" in parsed.body


def test_hold_also_requires_a_reason(tmp_path: Path, monkeypatch) -> None:
    """`hold` retires a thread from the active set exactly like `close` does.

    A silent hold is the same loss of information, so the guard covers both.
    `reopen` is deliberately exempt: it brings a thread BACK, and demanding a
    justification to resume work is friction with nothing behind it.
    """
    _, path = _open_thread_legacy(tmp_path, monkeypatch)
    bare = _run("hold", path.stem)
    assert bare.returncode != 0
    assert parse_thread_file(path).status == "active"

    ok = _run("hold", path.stem, "--reason", "Waiting on the counterparty's legal review")
    assert ok.returncode == 0, ok.stderr
    parsed = parse_thread_file(path)
    assert parsed.status == "on-hold"
    assert "Waiting on the counterparty's legal review" in parsed.body


def test_reopen_needs_no_reason(tmp_path: Path, monkeypatch) -> None:
    """Resuming work is not a decision that needs defending."""
    _, path = _open_thread_legacy(tmp_path, monkeypatch)
    _run("close", path.stem, "--reason", "done", check=True)
    result = _run("reopen", path.stem)
    assert result.returncode == 0, result.stderr
    assert parse_thread_file(path).status == "active"


def test_the_reason_gate_reads_the_destination_status(tmp_path: Path, monkeypatch) -> None:
    """The gate used to test `index_action == "remove"`, an index that is gone.

    `new_status != "active"` must cover the same two commands and no others, so
    the rename cannot have quietly widened or narrowed it. Asserted through the
    CLI: close and hold refuse, reopen does not.
    """
    _, path = _open_thread_legacy(tmp_path, monkeypatch)
    assert _run("close", path.stem).returncode != 0
    assert _run("hold", path.stem).returncode != 0
    _run("hold", path.stem, "--reason", "parked", check=True)
    assert _run("reopen", path.stem).returncode == 0


# ======================================
# The reason gate, asked of the function
# ======================================
#
# The four tests above drive the CLI, and the CLI cannot reach the gate: `main`
# declares `--reason` as `required=True` on `close` and `hold`, so argparse
# refuses first with exit 2. Mutation testing found this on 2026-08-27 - the gate
# was deleted outright and every CLI test stayed green.
#
# The gate is not dead code. `cmd_close`, `cmd_hold` and `cmd_quiet` are imported
# and called in-process (two shard test files do exactly that), and a caller
# there supplies its own Namespace with no argparse in between. So the CLI keeps
# its argparse guard, the function keeps its own, and these ask the function.


@pytest.fixture()
def th(tmp_path: Path, monkeypatch):
    """`scripts/thread.py` loaded in-process, pointed at an isolated tree."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "thread_reason_gate", REPO_ROOT / "scripts" / "thread.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["thread_reason_gate"] = module
    spec.loader.exec_module(module)

    threads = tmp_path / "threads"
    (threads / "business").mkdir(parents=True)
    monkeypatch.setattr(module, "_threads_root", lambda: threads)
    monkeypatch.setenv("HEADING_OS_DATA", str(tmp_path / "data"))
    (tmp_path / "data").mkdir(exist_ok=True)
    return module, threads


def _ns(**kw):
    import argparse

    return argparse.Namespace(**kw)


def _one_thread(module, threads: Path, title: str = "Gate probe") -> Path:
    module.cmd_open(_ns(type="business", title=title))
    (made,) = sorted((threads / "business").glob("*.md"))
    return made


@pytest.mark.parametrize("command", ["cmd_close", "cmd_hold"])
@pytest.mark.parametrize("reason", [None, "", "   ", "\n\t "])
def test_a_direct_retire_with_no_reason_raises(th, command, reason) -> None:
    """Both retiring commands, and every shape of an empty reason."""
    module, threads = th
    path = _one_thread(module, threads)
    with pytest.raises(ValueError, match="needs --reason"):
        getattr(module, command)(_ns(thread_id=path.stem, reason=reason))
    assert parse_thread_file(path).status == "active", "status moved on a refused call"


@pytest.mark.parametrize("command", ["cmd_close", "cmd_hold"])
def test_a_direct_retire_with_a_reason_is_allowed(th, command) -> None:
    """The gate must refuse the empty case only, not the whole call."""
    module, threads = th
    path = _one_thread(module, threads)
    assert getattr(module, command)(_ns(thread_id=path.stem, reason="a real reason")) == 0
    assert parse_thread_file(path).status in ("closed", "on-hold")


def test_a_direct_reopen_needs_no_reason(th) -> None:
    """`reopen` is the exemption, and it must survive the gate being tightened."""
    module, threads = th
    path = _one_thread(module, threads)
    module.cmd_hold(_ns(thread_id=path.stem, reason="parked"))
    assert module.cmd_reopen(_ns(thread_id=path.stem)) == 0
    assert parse_thread_file(path).status == "active"


def test_a_stray_until_does_not_survive_a_direct_clear(th) -> None:
    """`--clear` wins over a `--until` that arrives with it.

    Through the CLI the two cannot arrive together: `main` puts `--until`,
    `--indefinite` and `--clear` in a mutually exclusive group, so `args.until`
    is already None whenever the other two are set. A direct caller has no such
    group, and dropping the `None if ...` here changed nothing the CLI could
    show. It changes this.
    """
    module, threads = th
    path = _one_thread(module, threads)
    module.cmd_quiet(_ns(thread_id=path.stem, until="2999-01-01",
                         clear=False, indefinite=False))
    assert parse_thread_file(path).quiet_until == "2999-01-01"

    module.cmd_quiet(_ns(thread_id=path.stem, until="2999-01-01",
                         clear=True, indefinite=False))
    assert parse_thread_file(path).quiet_until is None


def test_a_stray_until_does_not_survive_a_direct_indefinite(th) -> None:
    """Same shape: an indefinite freeze must carry no date, ever."""
    module, threads = th
    path = _one_thread(module, threads)
    module.cmd_quiet(_ns(thread_id=path.stem, until="2999-01-01",
                         clear=False, indefinite=True))
    parsed = parse_thread_file(path)
    assert parsed.do_not_remind is True
    assert parsed.quiet_until is None
