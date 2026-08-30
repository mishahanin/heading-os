"""`chronicle build --since` took any string at all, and two of them were costly.

`--since` was declared `type=str` with no validation anywhere in `main`, and
`select_sessions` hands the value straight to `mday < cutoff` as a raw
lexicographic string compare against a `YYYY-MM-DD` produced by
`.date().isoformat()`. Measured on this file's fixtures, both silent, both
exit 0:

  --since 1         `"2026-08-28" < "1"` is False for every date, so EVERY
                    session was selected: an unrequested full backfill at
                    roughly 150 s of model prefill each, writing entries that
                    `already_chronicled` then makes permanent.
  --since 2026-8-1  an unpadded month sorts after every `2026-MM-DD`, so
                    "0 session(s) to process", exit 0, no reason given.

`iso_date_arg` refuses both at parse time, so `cmd_build` never runs and the
session loop is never entered. It refuses rather than repairs: `2026-8-1` is
not read as `2026-08-01`, because that spelling will not be repaired wherever
the operator carries it next. The round-trip through `isoformat()` is the
substance of the check, not decoration: bare `date.fromisoformat` also accepts
`20260801` and `2026-W31-1`, and both sort WRONG against the other operand.

Also locked here: the build summary counts a free length-based skip apart from
a model-judged one that cost a full prefill (they were summed as "N trivial"),
and the budget comment no longer points readers at an `OLLAMA_HOST` that has
never existed in this module.

No test here reaches the network or a model: `socket.connect` is blocked for
every test in the file and the blocker is proved to be armed.

Tests: scripts/chronicle.py
"""

from __future__ import annotations

import argparse
import json
import os
import re
import socket
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import scripts.chronicle as ch  # noqa: E402

CHRONICLE_SRC = ROOT / "scripts" / "chronicle.py"


# ==========================================================================
# Isolation: no network, no model, no operator data
# ==========================================================================

@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Every test in this file runs with the network amputated.

    `summarize` POSTs to a real ollama endpoint. A test that reaches it depends
    on a daemon being up and costs real prefill time; blocking at `connect` is
    the narrowest place no client library can route around.
    """
    reached = []

    def _blocked(self_or_addr, *args, **kwargs):
        reached.append(str(self_or_addr))
        raise RuntimeError("a test in this file tried to open a real socket")

    monkeypatch.setattr(socket.socket, "connect", _blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", _blocked)
    monkeypatch.setattr(socket, "create_connection", _blocked)
    yield reached
    assert reached == [], f"a test reached the network: {reached}"


def test_the_network_blocker_is_actually_armed(no_network):
    """A guard nobody made refuse is not known to refuse anything."""
    with pytest.raises(RuntimeError, match="real socket"):
        socket.create_connection(("host.invalid", 11434))
    assert no_network == ["('host.invalid', 11434)"]
    no_network.clear()  # this one attempt was made on purpose


@pytest.fixture(autouse=True)
def isolated_data_root(tmp_path, monkeypatch):
    """chronicle/ resolves under tmp_path, never the operator's live overlay.

    `chronicle_root()` is `get_data_root() / "chronicle"`, and `record_skipped`
    and `write_marker` both write there. Without this a build test would append
    to the real skip ledger and move the real high-water mark.
    """
    data = tmp_path / "data"
    (data / "chronicle").mkdir(parents=True)
    monkeypatch.setenv("HEADING_OS_DATA", str(data))
    # `_personal_keywords` caches the merged keyword list across tests, and its
    # private file is resolved from the data root we just moved.
    monkeypatch.setattr(ch, "_PERSONAL_KEYWORDS_CACHE", None)
    return data


# ==========================================================================
# Fixture corpus
# ==========================================================================

def _session(dirpath: Path, day: str, sid: str, text: str) -> Path:
    """One top-level transcript, mtime pinned to its own day (UTC)."""
    f = dirpath / f"sess-{day}-{sid}.jsonl"
    f.write_text(
        json.dumps({
            "type": "user",
            "timestamp": f"{day}T10:00:00Z",
            "message": {"role": "user", "content": text},
        }) + "\n",
        encoding="utf-8",
    )
    os.utime(f, (_epoch(day), _epoch(day)))
    return f


def _epoch(day: str) -> float:
    from datetime import datetime, timezone
    return datetime.fromisoformat(f"{day}T10:00:00+00:00").replace(
        tzinfo=timezone.utc).timestamp()


SUBSTANTIVE = "We weighed the retry budget against the ingest backlog. " * 8


@pytest.fixture()
def sessions(tmp_path) -> Path:
    """Three sessions across three days; all trivially short."""
    d = tmp_path / "sessions"
    d.mkdir()
    for day in ("2026-08-10", "2026-08-20", "2026-08-28"):
        _session(d, day, "aaaa" + day[-2:], "hi")
    return d


@pytest.fixture()
def tripwires(monkeypatch):
    """Records every entry into the expensive path, and lets nothing through.

    Asserting only on the exit code would pass while the backfill still ran:
    argparse exits 2 on a usage error, but so would a build that selected every
    session and then failed for another reason. These record whether
    `select_sessions` was reached at all and whether the model was called.
    """
    calls = {"select": [], "summarize": [], "parse": []}

    def _select(*a, **kw):
        calls["select"].append(a)
        raise AssertionError("select_sessions was reached; the expensive path ran")

    def _summarize(*a, **kw):
        calls["summarize"].append(a)
        raise AssertionError("summarize was reached; a model prefill was spent")

    def _parse(*a, **kw):
        calls["parse"].append(a)
        raise AssertionError("parse_jsonl was reached; the session loop ran")

    monkeypatch.setattr(ch, "select_sessions", _select)
    monkeypatch.setattr(ch, "summarize", _summarize)
    monkeypatch.setattr(ch, "parse_jsonl", _parse)
    return calls


def _build(argv: list[str], sessions_dir: Path) -> int:
    return ch.main(["build", "--sessions-dir", str(sessions_dir), *argv])


# ==========================================================================
# 1 - the expensive defect: --since 1 selected everything
# ==========================================================================

@pytest.mark.parametrize("bad", ["1", "0", "yesterday", "", "2026", "last-week"])
def test_a_since_that_is_not_a_date_is_refused(bad, sessions, tripwires, capsys):
    """Exit 2, a message naming the value, and the session loop never entered.

    `--since 1` is the load-bearing case: it is the one that selected EVERY
    session rather than none, so its failure mode is hours of model time and a
    set of permanent entries, not an empty run.
    """
    with pytest.raises(SystemExit) as exc:
        _build([f"--since={bad}"], sessions)

    assert exc.value.code == 2, f"--since {bad!r} should exit 2, got {exc.value.code}"
    err = capsys.readouterr().err
    assert "invalid ISO date" in err
    assert repr(bad) in err
    # The whole point: nothing downstream of the parser ran.
    assert tripwires["select"] == []
    assert tripwires["summarize"] == []
    assert tripwires["parse"] == []


def test_since_one_no_longer_selects_every_session(sessions):
    """The measured consequence, stated as the comparison that caused it.

    `"2026-08-28" < "1"` is False, which is why every session passed the filter.
    The relation is still true; the value can no longer reach it.
    """
    assert not ("2026-08-28" < "1")  # the raw compare that caused the backfill

    with pytest.raises(SystemExit) as exc:
        _build(["--since=1", "--dry-run"], sessions)
    assert exc.value.code == 2


# ==========================================================================
# 2 - the silent-zero defect, and the forms that sort wrong
# ==========================================================================

def test_an_unpadded_month_is_refused_not_repaired(sessions, tripwires, capsys):
    """`2026-8-1` exits 2 and is never read as `2026-08-01`.

    Repairing it would teach a spelling that the next tool will not repair. The
    message names the canonical form so the operator is corrected, not coerced.
    """
    # The measured consequence it used to have: an unpadded month sorts ABOVE
    # every canonical date, so the filter excluded every session and the run
    # reported "0 session(s) to process" at exit 0.
    assert "2026-08-28" < "2026-8-1"

    with pytest.raises(SystemExit) as exc:
        _build(["--since=2026-8-1"], sessions)
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "invalid ISO date" in err
    assert "zero-padded" in err          # told why, plainly
    assert tripwires["select"] == []     # and NOT quietly repaired into a run


@pytest.mark.parametrize("sneaky,canonical,why", [
    ("20260801", "2026-08-01", "basic form sorts above every YYYY-MM-DD"),
    ("2026-W31-1", "2026-07-27", "week date sorts above every YYYY-MM-DD"),
])
def test_forms_fromisoformat_accepts_but_the_compare_cannot(sneaky, canonical, why,
                                                            sessions, tripwires,
                                                            capsys):
    """The round-trip check is load-bearing, not decoration.

    `date.fromisoformat` parses both of these happily. A validator that stopped
    there would bless a value that sorts WRONG against `mday`, recreating the
    silent-zero defect with a validator's blessing on it.
    """
    from datetime import date
    assert date.fromisoformat(sneaky).isoformat() == canonical  # parsed happily

    # ...and yet it sorts ABOVE every canonical date, so `mday < cutoff` is
    # true for every session and the run selects nothing. This is the compare
    # `select_sessions` performs, spelled out.
    # Operand order mirrors `mday < cutoff` in select_sessions on purpose; the
    # point is the comparison the code performs, not the tidier spelling.
    assert "2026-08-28" < sneaky, why  # noqa: SIM300

    with pytest.raises(SystemExit) as exc:
        _build([f"--since={sneaky}"], sessions)
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "invalid ISO date" in err
    assert f"did you mean {canonical!r}" in err   # the correction is named...
    assert tripwires["select"] == []              # ...never applied


# ==========================================================================
# 3 - the good path still works (the fix is not a blanket refusal)
# ==========================================================================

def test_a_canonical_since_is_accepted_and_filters_correctly(sessions, capsys):
    rc = _build(["--since=2026-08-25", "--dry-run"], sessions)
    assert rc == 0
    out = _plain(capsys.readouterr().out)
    assert "1 session(s) to process" in out, out


def test_iso_date_arg_returns_the_value_unchanged():
    """It is a validator, not a normaliser: what goes in comes back out."""
    assert ch.iso_date_arg("2026-07-01") == "2026-07-01"
    assert ch.iso_date_arg("2026-12-31") == "2026-12-31"


def test_iso_date_arg_raises_the_type_argparse_turns_into_exit_2():
    """The exit code is inherited from argparse, never invented here.

    `--limit abc` already exits 2 through the same mechanism, so `--since`
    refusing this way adds no new code to the script's contract.
    """
    with pytest.raises(argparse.ArgumentTypeError):
        ch.iso_date_arg("1")


def test_the_sibling_limit_argument_sets_the_exit_code_convention(sessions):
    with pytest.raises(SystemExit) as exc:
        _build(["--limit=abc"], sessions)
    assert exc.value.code == 2


# ==========================================================================
# 4 - the summary called a paid skip "trivial"
# ==========================================================================

def _plain(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


def test_a_free_skip_and_a_paid_skip_are_counted_apart(tmp_path, monkeypatch, capsys):
    """One length-based skip and one model-judged skip: never "2 trivial".

    The length skip is decided from `len(body)` before any model call and costs
    nothing. The `{"skip": true}` verdict comes back only after a full prefill,
    which is why `record_skipped` exists to stop it being paid twice. Summing
    them reported a night that spent minutes of model time as one that spent
    none.
    """
    d = tmp_path / "sessions"
    d.mkdir()
    _session(d, "2026-08-27", "shrt", "hi")               # free: too short
    _session(d, "2026-08-28", "long", SUBSTANTIVE)        # paid: reaches model

    prefills = []

    def fake_summarize(body, timeout=300):
        prefills.append(len(body))
        return {"skip": True}

    monkeypatch.setattr(ch, "summarize", fake_summarize)

    rc = _build(["--backfill", "--dry-run"], d)
    assert rc == 0
    out = _plain(capsys.readouterr().out)

    assert len(prefills) == 1, "exactly one session should have reached the model"
    assert "1 trivial" in out, out
    assert "1 no substantive content" in out, out
    assert "2 trivial" not in out, "the paid skip is being reported as trivial"


def test_the_two_skip_counts_are_separate_variables():
    """A single counter incremented in both branches cannot report them apart."""
    src = CHRONICLE_SRC.read_text(encoding="utf-8")
    assert "trivial += 1" in src
    assert "no_content += 1" in src
    assert "skipped += 1" not in src, "the conflated counter is still incremented"


def test_the_summary_labels_match_the_per_session_lines(tmp_path, monkeypatch, capsys):
    """A reader must be able to reconcile the summary against the log above it."""
    d = tmp_path / "sessions"
    d.mkdir()
    _session(d, "2026-08-28", "long", SUBSTANTIVE)
    monkeypatch.setattr(ch, "summarize", lambda body, timeout=300: {"skip": True})

    _build(["--backfill", "--dry-run"], d)
    out = _plain(capsys.readouterr().out)
    assert "skip (no substantive content)" in out   # the per-session line
    assert "no substantive content" in out.split("done:")[1]  # and the summary
    assert "0 trivial" in out


def test_only_the_paid_skip_is_recorded_in_the_ledger(tmp_path, monkeypatch,
                                                      isolated_data_root, capsys):
    """The counter split mirrors the persistence policy it exists to expose.

    A trivial skip is re-decided free every run and deliberately leaves no
    trace; a model-judged one is written to `.skipped-sessions` so it is not
    paid for twice.
    """
    d = tmp_path / "sessions"
    d.mkdir()
    _session(d, "2026-08-27", "shrt", "hi")
    _session(d, "2026-08-28", "long", SUBSTANTIVE)
    monkeypatch.setattr(ch, "summarize", lambda body, timeout=300: {"skip": True})

    _build(["--backfill"], d)   # not a dry run: the ledger is written
    ledger = isolated_data_root / "chronicle" / ".skipped-sessions"
    recorded = ledger.read_text(encoding="utf-8").split()
    assert recorded == ["sess-2026-08-28-long"], recorded
    capsys.readouterr()


# ==========================================================================
# 5 - the comment pointed at a symbol that never existed
# ==========================================================================

def test_the_budget_comment_names_no_phantom_ollama_host():
    """`see OLLAMA_HOST below` pointed at nothing, and pointed the wrong way.

    There has never been an `OLLAMA_HOST` in this module. The only related name
    is `HEADING_OS_OLLAMA_HOST`, which appears twice and both times ABOVE the
    line that said "below".
    """
    src = CHRONICLE_SRC.read_text(encoding="utf-8")
    phantom = re.findall(r"(?<!HEADING_OS_)\bOLLAMA_HOST\b", src)
    assert phantom == [], f"chronicle.py still names a nonexistent OLLAMA_HOST: {phantom}"


def test_the_budget_comment_names_what_actually_resolves_the_host():
    """It must send the reader to the real resolver, not merely drop the bad name."""
    src = CHRONICLE_SRC.read_text(encoding="utf-8")
    budget = src.split("BODY_CHAR_BUDGET =")[0]
    # Anchored on the sentence that used to carry the bad pointer. "198.7 tok/s"
    # would not do: it appears three times in this region.
    anchor = "the claim held and the host now points there."
    assert anchor in budget, "the corrected sentence is not where expected"
    tail = budget.split(anchor)[1]
    assert "ollama_url()" in tail, "the comment does not name the resolver"
    assert "generation_host()" in tail, "the comment does not name the seam it calls"
    assert "ollama-hosts.yaml" in tail, "the comment does not say where the pin lives"
    # The old text said "below" while the referent was above it. The corrected
    # text says ABOVE, and names the line.
    assert "ABOVE" in tail, "the comment does not say which direction to look"


def test_the_resolver_the_comment_names_is_the_one_the_code_calls():
    """The pointer is checked against the code, not just against itself."""
    import inspect
    assert "generation_host()" in inspect.getsource(ch.ollama_url)
