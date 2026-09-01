#!/usr/bin/env python3
"""Three ways scripts/implement-trajectory-log.py failed its own audit record.

The tool's product is a verbatim record of what a run did. Each of these turned
that record into something quieter than the truth, and each exited 0 doing it.

An empty `--data-json ""` is a supplied payload. `bool("")` is False, so the
mode gate read it as "no data mode", fell through to the typed-flag builder,
appended an event with an empty payload and returned 0. Two documented
behaviours went with it: the exit-4 parse error (`json.loads("")` is a parse
error and was never called), and the typed-vs-data mutual exclusion, so
`--data-json "" --check x` passed the guard and the data-json argument vanished.

`--list-files` promises "one literal path per line" and the Phase 3
hidden-character scan reads its output as a file list. It printed every
`files_affected` entry verbatim - globs, `+3 more` count tokens, blank strings -
even though `_is_literal_path` sits in the same file and `--verify` already
rejects all three.

`trajectory_path` interpolated the operator's run_id straight into a path. The
`_trajectory_` prefix stops a LEADING `..`; a separator in the middle walked out
of the directory and appended an audit record somewhere else entirely.
"""
from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _plain(text: str) -> str:
    return _ANSI.sub("", text)


def _load():
    path = ROOT / "scripts" / "implement-trajectory-log.py"
    spec = importlib.util.spec_from_file_location("implement_trajectory_log", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


itl = _load()


@pytest.fixture
def traj_dir(tmp_path, monkeypatch):
    d = tmp_path / "implement"
    d.mkdir()
    monkeypatch.setattr(itl, "trajectory_dir", lambda d=d: d)
    return d


@pytest.fixture
def run_id(traj_dir):
    rid = "2026-08-30_090000_bond-dossier"
    itl.write_run_start(rid, "plans/2026-08-30-bond-dossier.md")
    return rid


def _events(rid):
    # split("\n"), for the same reason the module under test uses it: this
    # helper read the file with `splitlines()` and would have shredded the
    # very records the cases below plant.
    return [json.loads(line) for line
            in itl.trajectory_path(rid).read_text(encoding="utf-8").split("\n")
            if line.strip()]


# ============================================================
# An empty --data-* value is a supplied payload, not an absent one
# ============================================================

def test_an_empty_data_json_is_a_parse_error_not_an_empty_event(run_id):
    # `load_data` reports its own exit codes through `sys.exit`, so the parse
    # error arrives as SystemExit(4) rather than a return value.
    with pytest.raises(SystemExit) as exc:
        itl.main(["--event", "--run-id", run_id, "--type", "validation_check",
                  "--data-json", ""])
    assert exc.value.code == 4


def test_an_empty_data_json_appends_nothing(run_id):
    before = len(_events(run_id))
    with pytest.raises(SystemExit):
        itl.main(["--event", "--run-id", run_id, "--type", "validation_check",
                  "--data-json", ""])
    assert len(_events(run_id)) == before, "an unparseable payload was recorded"


def test_an_empty_data_json_beside_a_typed_flag_is_still_mutually_exclusive(run_id):
    rc = itl.main(["--event", "--run-id", run_id, "--type", "validation_check",
                   "--data-json", "", "--check", "hidden-chars"])
    assert rc == 2


def test_the_typed_flag_does_not_smuggle_an_event_past_the_dropped_payload(run_id):
    before = len(_events(run_id))
    itl.main(["--event", "--run-id", run_id, "--type", "validation_check",
              "--data-json", "", "--check", "hidden-chars"])
    assert len(_events(run_id)) == before


def test_an_empty_data_json_beside_data_stdin_counts_as_two_modes(run_id,
                                                                  monkeypatch):
    """`load_data` counted by truthiness too, so `"" + --data-stdin` read as one."""
    monkeypatch.setattr(sys, "stdin", __import__("io").StringIO('{"a": 1}'))
    with pytest.raises(SystemExit) as exc:
        itl.load_data(__import__("argparse").Namespace(
            data_file=None, data_stdin=True, data_json=""))
    assert exc.value.code == 2


def test_a_real_data_json_payload_still_lands(run_id):
    """The presence check must not refuse the mode it exists to protect."""
    rc = itl.main(["--event", "--run-id", run_id, "--type", "validation_check",
                   "--data-json", '{"check": "ruff", "passed": true}'])
    assert rc == 0
    assert _events(run_id)[-1]["payload"] == {"check": "ruff", "passed": True}


def test_the_typed_flag_path_still_works_with_no_data_mode_at_all(run_id):
    rc = itl.main(["--event", "--run-id", run_id, "--type", "validation_check",
                   "--check", "ruff", "--passed"])
    assert rc == 0
    assert _events(run_id)[-1]["payload"] == {"check": "ruff", "passed": True}


# ============================================================
# --list-files prints paths, and says what it dropped
# ============================================================

_MIXED = {
    "step": 1,
    "files_affected": ["scripts/*.py", "scripts/q-branch.py", "+3 more", "   ",
                       "scripts/{a,b}.py"],
}


@pytest.fixture
def mixed_run(run_id):
    assert itl.main(["--event", "--run-id", run_id, "--type", "step_start",
                     "--step", "1"]) == 0
    assert itl.main(["--event", "--run-id", run_id, "--type", "step_end",
                     "--data-json", json.dumps(_MIXED)]) == 0
    return run_id


def test_list_files_prints_only_the_literal_paths(mixed_run, capsys):
    import argparse
    assert itl.cmd_files(argparse.Namespace(run_id=mixed_run)) == 0
    printed = capsys.readouterr().out.splitlines()
    assert printed == ["scripts/q-branch.py"]


def test_list_files_names_what_it_dropped_on_stderr(mixed_run, capsys):
    import argparse
    itl.cmd_files(argparse.Namespace(run_id=mixed_run))
    err = _plain(capsys.readouterr().err)
    assert "dropped 4 non-literal" in err
    for entry in ("scripts/*.py", "+3 more", "scripts/{a,b}.py"):
        assert repr(entry) in err


def test_the_dropped_entries_are_exactly_what_verify_calls_defects(mixed_run):
    """One predicate, two readers - so the two can never disagree again."""
    defects = [d for d in itl.verify_trajectory(mixed_run)
               if "is not a literal path" in d]
    assert len(defects) == 4
    assert not any("q-branch" in d for d in defects)


@pytest.mark.parametrize("entry,scannable", [
    ("scripts/q-branch.py", True),
    ("scripts/*.py", False),
    ("scripts/{a,b}.py", False),
    ("+3 more", False),
    ("   ", False),
    ("", False),
    ("report+notes.md", True),
])
def test_the_shared_predicate_decides_each_shape(entry, scannable):
    assert itl.is_scannable_path(entry) is scannable


# ============================================================
# A record the reader shredded on a line separator it does not use
# ============================================================

# DERIVED, not listed. The writer emits `json.dumps(record,
# ensure_ascii=False)`, which escapes the C0 controls and passes everything else
# through verbatim, so only the code points that (a) survive json.dumps and
# (b) `str.splitlines()` treats as a break can shred a record. Guessing the set
# by hand is how five of eight parametrize cases end up unreachable.
_SPLITLINES_ONLY = [
    ch for ch in (
        "\x0b", "\x0c", "\x1c", "\x1d", "\x1e",       # C0: escaped by json.dumps
        "\x85", "\u2028", "\u2029",              # C1 + Unicode: NOT escaped
    )
    if len(f"x{ch}y".splitlines()) > 1
]
_REACHABLE = [ch for ch in _SPLITLINES_ONLY
              if ch in json.dumps(ch, ensure_ascii=False)]


def test_the_reachable_separator_set_is_what_this_file_thinks_it_is():
    """The anti-decay half: the derivation above must still hold.

    If `json.dumps` ever stopped escaping the C0 controls, or `splitlines()`
    changed its set, the cases below would silently start testing something
    else. Asserted by SET EQUALITY so a widened set fails as loudly as a
    narrowed one.
    """
    assert set(_REACHABLE) == {"\x85", "\u2028", "\u2029"}, (
        f"reachable set moved: {[hex(ord(c)) for c in _REACHABLE]}")
    # And the derivation is doing work: the C0 controls really are excluded
    # because json.dumps escapes them, not because nobody listed them.
    assert set(_SPLITLINES_ONLY) - set(_REACHABLE) == {
        "\x0b", "\x0c", "\x1c", "\x1d", "\x1e"}


@pytest.mark.parametrize("sep", _REACHABLE, ids=lambda c: f"U+{ord(c):04X}")
def test_a_separator_in_a_summary_does_not_drop_the_event(run_id, sep):
    """One record in, one record back. `splitlines()` returned neither.

    MEASURED 2026-09-01 before the fix: `--summary "before<U+2028>after"` wrote
    two well-formed JSONL records, `splitlines()` saw three fragments, and
    `_read_events` returned ONE - the run_end event was gone from the audit
    record, with nothing printed and exit 0.
    """
    before = len(_events(run_id))
    rc = itl.main(["--event", "--run-id", run_id, "--type", "run_end",
                   "--summary", f"before{sep}after"])
    assert rc == 0

    path = itl.trajectory_path(run_id)
    raw = path.read_text(encoding="utf-8")
    # The premise: the file really does hold the shape that breaks splitlines.
    assert len(raw.splitlines()) > len([x for x in raw.split("\n") if x]), (
        "the separator did not survive into the file; this case proves nothing")

    # Through the PRODUCTION reader, not this file's `_events` helper. Asking
    # the helper measured the helper: MEASURED 2026-09-01, reverting
    # `_read_events` to `splitlines()` left this test green because `_events`
    # does its own `split("\n")` and never calls the function under test.
    events = itl._read_events(path)
    assert len(events) == before + 1, "the event was dropped from the record"
    assert sep in events[-1]["payload"]["summary"], (
        "the event survived but its text did not")
    # And the helper agrees, so the two readers cannot silently diverge.
    assert len(_events(run_id)) == len(events)


@pytest.mark.parametrize("sep", _REACHABLE, ids=lambda c: f"U+{ord(c):04X}")
def test_the_verifier_calls_that_record_well_formed(run_id, sep):
    """The loud half of the same defect, at the other reader.

    `verify_trajectory` reported two `malformed JSON` defects against a file
    that is valid JSONL, on line numbers the file does not have.
    """
    itl.main(["--event", "--run-id", run_id, "--type", "run_end",
              "--summary", f"before{sep}after"])

    malformed = [d for d in itl.verify_trajectory(run_id) if "malformed JSON" in d]
    assert malformed == [], malformed


def test_a_genuinely_malformed_line_is_still_reported(run_id):
    """The sole witness for the other direction, so "report nothing" does not
    satisfy the two cases above."""
    itl.trajectory_path(run_id).open("a", encoding="utf-8").write("{not json\n")

    malformed = [d for d in itl.verify_trajectory(run_id) if "malformed JSON" in d]
    assert len(malformed) == 1, malformed
    assert "line 2" in malformed[0], malformed


# ============================================================
# A run_id is one file-name part, never a path
# ============================================================

@pytest.mark.parametrize("bad", [
    "x/../../victim",
    "../victim",
    "sub/victim",
    "back\\slash",
    "",
    "   ",
    "..",
])
def test_a_run_id_that_is_a_path_is_refused(bad):
    with pytest.raises(ValueError):
        itl.trajectory_path(bad)


def test_a_traversing_run_id_exits_two_from_the_cli(traj_dir, tmp_path):
    victim = tmp_path / "victimdir"
    victim.mkdir()
    (victim / "victim.jsonl").write_text("ORIGINAL\n", encoding="utf-8")
    # The intermediate directory `mint_unique_run_id`'s mkdir(parents=True)
    # would have created for a run_id carrying a slash.
    (traj_dir / "_trajectory_x").mkdir()
    rc = itl.main(["--event", "--run-id", "x/../../victimdir/victim",
                   "--type", "run_end", "--summary", "pwned"])
    assert rc == 2
    assert (victim / "victim.jsonl").read_text(encoding="utf-8") == "ORIGINAL\n"


def test_the_refusal_says_which_character_earned_it(traj_dir, capsys):
    itl.main(["--event", "--run-id", "x/../y", "--type", "run_end"])
    err = _plain(capsys.readouterr().err)
    assert "never a path" in err
    assert "'/'" in err and "'..'" in err


@pytest.mark.parametrize("ok", [
    "2026-08-30_090000_bond-dossier",
    "2026-08-30_090000_wave two",
    "rid1",
    "run.2",
])
def test_an_ordinary_run_id_is_untouched(ok, traj_dir):
    """A DENYLIST: `derive_slug` takes the slug off a filename stem, so a plan
    called "wave two.md" mints a run_id with a space and must still run."""
    assert itl.trajectory_path(ok).parent == traj_dir


def test_a_minted_run_id_survives_its_own_validator(traj_dir):
    rid = itl.mint_unique_run_id("plans/2026-08-30-wave two.md")
    assert itl.validate_run_id(rid) == rid
    assert itl.write_run_start(rid, "plans/2026-08-30-wave two.md").exists()
