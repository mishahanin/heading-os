"""Two ways `scripts/compaction-probe.py` certified something it had not checked.

Found by the 2026-08-24 engine audit campaign, verified still present and fixed
2026-09-02.

  the empty fold   Every `--assert-*` is a per-event fold over the boundaries in
                   scope. With zero events every violation list is empty, so
                   `main` returned 0 and printed "All requested assertions hold."
                   MEASURED 2026-09-02: `--session does-not-exist-xyz
                   --assert-driven-compaction` printed exactly that and exited 0,
                   and so did `--since 2099-01-01`. A typo'd session id, a future
                   window, or a pruned transcript directory turned every gate
                   green with a success message. This is the "verdict over
                   nothing" `content-guard.py` refuses on principle, in a file
                   whose own footer already concedes it cannot see a pruned
                   transcript.

  the shared second `assert_driven` compared a boundary against the hook's
                   recorded request after truncating BOTH to whole seconds. The
                   transcript writes fractional seconds, so a request written up
                   to a second AFTER a boundary satisfied "precedes" and a
                   boundary the hook had not yet asked for was certified as
                   driven. The assertion's entire guarantee is that the request
                   CAUSED the boundary.

Also pinned here: the cascade gap's off-by-one, which the audit raised and which
turned out to be a PROSE defect rather than a code one. `turns < gap` is
deliberate and already held by
`test_compaction_probe.py::test_the_cascade_gap_is_a_minimum_not_an_exclusive_bound`;
the module docstring and the CLI help said "within N assistant turns", which
describes `turns <= gap`. The prose was the wrong side and moved. The test below
keeps the two from drifting apart again in either direction.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PROBE_PATH = ROOT / "scripts" / "compaction-probe.py"

SESSION = "9f2c1ab4-0000-4000-8000-0000000000aa"


def _probe():
    spec = importlib.util.spec_from_file_location("compaction_probe_vacuity", PROBE_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def probe():
    return _probe()


@pytest.fixture
def tree(tmp_path, probe, monkeypatch):
    """A workspace whose transcript and handoff directories are both under tmp."""
    project = tmp_path / "workspace"
    transcripts = tmp_path / "transcripts"
    handoffs = tmp_path / "handoffs"
    state = tmp_path / "state"
    for d in (project, transcripts, handoffs, state):
        d.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(probe.CP, "transcript_dir", lambda _p: transcripts)
    monkeypatch.setattr(probe.CP, "handoff_dir", lambda _p: handoffs)
    monkeypatch.setattr(probe.CP, "state_path", lambda _p, slug: state / f"{slug}.json")
    monkeypatch.setattr(probe, "get_workspace_root", lambda: project)
    return {"project": project, "transcripts": transcripts, "state": state}


def _boundary(timestamp: str, trigger: str = "manual") -> dict:
    return {
        "type": "system",
        "subtype": "compact_boundary",
        "timestamp": timestamp,
        "compactMetadata": {
            "trigger": trigger, "preTokens": 324190, "postTokens": 10929,
            "cumulativeDroppedTokens": 0, "durationMs": 900,
        },
    }


def _transcript(tree, records) -> None:
    (tree["transcripts"] / f"{SESSION}.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in records), encoding="utf-8"
    )


def _state(tree, probe, **payload) -> None:
    (tree["state"] / f"{probe.CP.safe_slug(SESSION)}.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )


def _run(probe, monkeypatch, *argv) -> int:
    monkeypatch.setattr(sys, "argv", ["compaction-probe.py", *argv])
    return probe.main()


# ============================================================
# An assertion over zero events is not a passing assertion
# ============================================================

def test_a_typod_session_no_longer_reports_that_every_assertion_holds(
    tree, probe, monkeypatch, capsys
):
    """The audit's own reproduction, exactly."""
    _transcript(tree, [_boundary("2026-08-19T09:50:47.749Z")])
    code = _run(probe, monkeypatch, "--session", "no-such-session",
                "--assert-driven-compaction")
    out = capsys.readouterr().out
    assert code == 1, "an assertion over zero events exited 0"
    assert "All requested assertions hold" not in out, out
    assert "asserted over nothing" in out, out
    assert "no-such-session" in out, (
        "the refusal must name the cause; a bare failure sends the operator "
        "hunting for a violation that does not exist"
    )


def test_a_window_that_excludes_every_boundary_no_longer_passes(
    tree, probe, monkeypatch, capsys
):
    """`--since` in the future is the second door into the same empty fold, and
    it reaches it without any typo at all."""
    _transcript(tree, [_boundary("2026-08-19T09:50:47.749Z")])
    code = _run(probe, monkeypatch, "--since", "2099-01-01", "--assert-no-cascade")
    assert code == 1
    assert "2099-01-01" in capsys.readouterr().out


def test_an_absent_transcript_directory_is_named_as_the_cause(
    tmp_path, probe, monkeypatch, capsys
):
    """The third door: nothing to read at all. `_iter_transcripts` already
    produced a "Not covered" note for it and the assertion path ignored it."""
    monkeypatch.setattr(probe.CP, "transcript_dir", lambda _p: tmp_path / "gone")
    monkeypatch.setattr(probe, "get_workspace_root", lambda: tmp_path)
    code = _run(probe, monkeypatch, "--assert-no-native-compaction")
    assert code == 1
    assert "no transcript directory" in capsys.readouterr().out


def test_a_bare_run_over_zero_events_still_asserts_nothing(
    tmp_path, probe, monkeypatch, capsys
):
    """The anchor, and the one this fix could most easily have broken.

    Reporting is not asserting. An invocation with no `--assert-*` flag must
    still exit 0 over an empty tree, or the probe stops being usable as a plain
    reader on a fresh clone.
    """
    monkeypatch.setattr(probe.CP, "transcript_dir", lambda _p: tmp_path / "gone")
    monkeypatch.setattr(probe, "get_workspace_root", lambda: tmp_path)
    assert _run(probe, monkeypatch) == 0
    assert "violation" not in capsys.readouterr().out


def test_an_assertion_with_events_in_scope_still_passes(tree, probe, monkeypatch, capsys):
    """The other anchor: a real, satisfied assertion must stay green. A guard
    that failed every run would read as a tightening and be reverted."""
    _transcript(tree, [_boundary("2026-08-19T09:50:47.749Z", "manual")])
    assert _run(probe, monkeypatch, "--assert-no-native-compaction") == 0
    assert "All requested assertions hold" in capsys.readouterr().out


# ============================================================
# assert_driven must compare the whole clock, not the second
# ============================================================

def test_a_request_written_after_the_boundary_no_longer_precedes_it(tree, probe):
    """700 ms after, inside the same second. The audit's fabricated pair.

    Truncating both sides to `2026-08-21-130805` made `<=` hold, so a boundary
    that fired BEFORE the hook wrote its request was certified as driven by it.
    """
    _transcript(tree, [_boundary("2026-08-21T13:08:05.200Z")])
    _state(tree, probe, compact_requests=[{"at": "2026-08-21T13:08:05.900Z", "bucket": 45}])
    violations = probe.assert_driven(
        probe._scan(tree["transcripts"] / f"{SESSION}.jsonl")[0], tree["project"]
    )
    assert violations, (
        "a request written 700 ms after the boundary was accepted as its cause"
    )


def test_a_request_written_just_before_the_boundary_still_correlates(tree, probe):
    """The anchor on the other side of the same millisecond.

    Sub-second precision has to cut one way only. A request at .100 against a
    boundary at .200 is the driven path working, and must stay green.
    """
    _transcript(tree, [_boundary("2026-08-21T13:08:05.200Z")])
    _state(tree, probe, compact_requests=[{"at": "2026-08-21T13:08:05.100Z", "bucket": 45}])
    assert probe.assert_driven(
        probe._scan(tree["transcripts"] / f"{SESSION}.jsonl")[0], tree["project"]
    ) == []


def test_the_two_clocks_are_still_not_mixed(tree, probe):
    """`assert_driven` compares UTC against UTC. Moving to real datetimes must
    not have quietly localised either side: `compact_requests[].at` is
    `CP.utc_now()`, written `+00:00`, and the transcript writes `Z`. The same
    instant in both spellings must correlate."""
    _transcript(tree, [_boundary("2026-08-21T13:08:05.200Z")])
    _state(tree, probe, compact_requests=[{"at": "2026-08-21T13:08:05.200000+00:00"}])
    assert probe.assert_driven(
        probe._scan(tree["transcripts"] / f"{SESSION}.jsonl")[0], tree["project"]
    ) == []


def test_an_unparseable_request_stamp_is_a_violation_not_a_pass(tree, probe):
    """Fail toward reporting. A request whose `at` will not parse must not be
    read as a request that qualifies."""
    _transcript(tree, [_boundary("2026-08-21T13:08:05.200Z")])
    _state(tree, probe, compact_requests=[{"at": "not-a-timestamp"}])
    assert probe.assert_driven(
        probe._scan(tree["transcripts"] / f"{SESSION}.jsonl")[0], tree["project"]
    )


# ============================================================
# The cascade gap: prose and code must agree on the boundary case
# ============================================================

def test_the_cascade_prose_no_longer_describes_an_inclusive_bound(probe):
    """`assert_no_cascade` flags `turns < gap`, so a gap of exactly `gap` passes.

    The module docstring and the `--assert-no-cascade` help both said "within N
    assistant turns", which describes `turns <= gap`. `test_compaction_probe.py`
    pins the strict reading deliberately, with a case exactly on the line, so the
    prose was the side that was wrong.
    """
    source = PROBE_PATH.read_text(encoding="utf-8")
    flat = " ".join(source.split())
    assert "no two boundaries within N assistant turns" not in flat, (
        "the module docstring still promises an inclusive bound the code does "
        "not implement"
    )
    assert "FEWER than N" in flat, "the docstring must state the strict bound"

    # And the behaviour the prose now describes, asked of the function.
    exact = [{"session": "s", "timestamp": "t1", "assistant_turn": 0},
             {"session": "s", "timestamp": "t2", "assistant_turn": 3}]
    assert probe.assert_no_cascade(exact, 3) == [], (
        "a gap of exactly the minimum was flagged; the prose fix now describes "
        "behaviour the code no longer has"
    )
    tight = [{"session": "s", "timestamp": "t1", "assistant_turn": 0},
             {"session": "s", "timestamp": "t2", "assistant_turn": 2}]
    assert probe.assert_no_cascade(tight, 3), "a real cascade stopped being flagged"
