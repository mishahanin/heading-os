"""Unit coverage for scripts/implement-trajectory-log.py (v1.7).

Covers the typed-flag payload builder and its type-aware defaults, the
typed-vs-data-* mutual exclusion, the tri-state --parallel / --passed flags,
the --verify self-check (clean, and each structural defect class), the v1.7
emit-time sequencing guard (exit 5, wave-aware), and the v1.7 run-level files
reconciliation in verify_trajectory (via the monkeypatchable _git_changed_files
seam).

Note: --parallel uses argparse.BooleanOptionalAction (Python 3.9+); the
engine CI matrix is 3.11/3.12, so it is available.
"""
import importlib.util
import json
from pathlib import Path

import pytest


def _load():
    """Import the kebab-case scripts/implement-trajectory-log.py as a module."""
    path = Path(__file__).resolve().parent.parent / "scripts" / "implement-trajectory-log.py"
    spec = importlib.util.spec_from_file_location("implement_trajectory_log", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


itl = _load()


@pytest.fixture
def traj_dir(tmp_path, monkeypatch):
    """Redirect TRAJECTORY_DIR to a tmp dir so nothing touches real outputs."""
    d = tmp_path / "impl"
    d.mkdir()
    monkeypatch.setattr(itl, "TRAJECTORY_DIR", d)
    return d


def _seed(run_id):
    """Create the trajectory file with a run_start so --event will append."""
    itl.write_run_start(run_id, "plans/x.md")


def _payloads(run_id):
    return [json.loads(x) for x in itl.trajectory_path(run_id).read_text().splitlines() if x.strip()]


def _write_traj(run_id, events):
    p = itl.trajectory_path(run_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(json.dumps(e) for e in events) + "\n")


# ============================================================
# Typed-flag payload assembly
# ============================================================
def test_step_end_type_aware_defaults(traj_dir):
    _seed("rid1")
    itl.main(["--event", "--run-id", "rid1", "--type", "step_start", "--step", "2"])
    rc = itl.main(["--event", "--run-id", "rid1", "--type", "step_end", "--step", "2"])
    assert rc == 0
    pl = _payloads("rid1")[-1]["payload"]
    assert pl["step"] == 2
    assert pl["files_affected"] == []
    assert pl["status"] == "ok"


def test_step_end_two_files_in_order(traj_dir):
    _seed("rid2")
    itl.main(["--event", "--run-id", "rid2", "--type", "step_start", "--step", "1"])
    itl.main(["--event", "--run-id", "rid2", "--type", "step_end", "--step", "1",
              "--file", "a.py", "--file", "b.py", "--status", "ok", "--notes", "done"])
    pl = _payloads("rid2")[-1]["payload"]
    assert pl["files_affected"] == ["a.py", "b.py"]
    assert pl["notes"] == "done"


def test_run_end_autofills(traj_dir):
    _seed("rid3")
    itl.main(["--event", "--run-id", "rid3", "--type", "run_end", "--summary", "s"])
    pl = _payloads("rid3")[-1]["payload"]
    assert pl["run_id"] == "rid3"
    assert pl["trajectory_path"].endswith("_trajectory_rid3.jsonl")
    assert pl["plan_status"] == "Implemented"
    assert pl["summary"] == "s"


def test_wave_start_no_parallel_is_false(traj_dir):
    _seed("rid4")
    itl.main(["--event", "--run-id", "rid4", "--type", "wave_start", "--wave", "1",
              "--step-count", "2", "--no-parallel"])
    pl = _payloads("rid4")[-1]["payload"]
    assert pl["parallel"] is False
    assert pl["step_count"] == 2


def test_wave_start_omits_parallel_when_unset(traj_dir):
    _seed("rid5")
    itl.main(["--event", "--run-id", "rid5", "--type", "wave_start", "--wave", "1"])
    pl = _payloads("rid5")[-1]["payload"]
    assert "parallel" not in pl


def test_validation_failed_and_passed(traj_dir):
    _seed("rid6")
    itl.main(["--event", "--run-id", "rid6", "--type", "validation_check",
              "--check", "c", "--failed", "--detail", "d"])
    assert _payloads("rid6")[-1]["payload"]["passed"] is False
    itl.main(["--event", "--run-id", "rid6", "--type", "validation_check",
              "--check", "c", "--passed"])
    assert _payloads("rid6")[-1]["payload"]["passed"] is True


def test_step_end_dict_equal_to_legacy(traj_dir):
    """CAP-1: typed payload equals the legacy hand-authored dict."""
    _seed("rid7")
    itl.main(["--event", "--run-id", "rid7", "--type", "step_start", "--step", "3"])
    itl.main(["--event", "--run-id", "rid7", "--type", "step_end", "--step", "3",
              "--file", "a.py", "--status", "ok"])
    pl = _payloads("rid7")[-1]["payload"]
    assert pl == {"step": 3, "files_affected": ["a.py"], "status": "ok"}


# ============================================================
# Mutual exclusion
# ============================================================
def test_typed_and_data_json_mutually_exclusive(traj_dir):
    _seed("rid8")
    rc = itl.main(["--event", "--run-id", "rid8", "--type", "step_start",
                   "--step", "9", "--data-json", '{"step":9}'])
    assert rc == 2


def test_passed_and_failed_mutually_exclusive(traj_dir):
    _seed("rid9")
    with pytest.raises(SystemExit) as exc:
        itl.main(["--event", "--run-id", "rid9", "--type", "validation_check",
                  "--passed", "--failed"])
    assert exc.value.code == 2


# ============================================================
# verify_trajectory
# ============================================================
def _clean_events():
    """A wave-mode run with nothing wrong with it.

    Step 0 (the plan-load marker) sits outside the bracket on purpose - it is
    the one exemption from the "no step outside a wave bracket" check. Both
    real steps are bracketed, which is what the plan format requires of a run
    that uses waves at all.
    """
    return [
        {"event_type": "run_start", "step_number": 0, "payload": {}},
        {"event_type": "step_start", "step_number": 0, "payload": {"step": 0}},
        {"event_type": "step_end", "step_number": 0,
         "payload": {"step": 0, "files_affected": [], "status": "ok"}},
        {"event_type": "wave_start", "step_number": None,
         "payload": {"wave": 1, "step_count": 2, "parallel": True}},
        {"event_type": "step_start", "step_number": 1, "payload": {"step": 1}},
        {"event_type": "step_end", "step_number": 1,
         "payload": {"step": 1, "files_affected": ["x.py"], "status": "ok"}},
        {"event_type": "step_start", "step_number": 2, "payload": {"step": 2}},
        {"event_type": "step_end", "step_number": 2,
         "payload": {"step": 2, "files_affected": ["y.py"], "status": "ok"}},
        {"event_type": "wave_end", "step_number": None,
         "payload": {"wave": 1, "successes": 2, "failures": 0}},
        {"event_type": "validation_check", "step_number": None,
         "payload": {"check": "pytest", "passed": True, "detail": "ok"}},
        {"event_type": "run_end", "step_number": None, "payload": {"summary": "ok"}},
    ]


def test_verify_clean(traj_dir):
    _write_traj("vc", _clean_events())
    assert itl.verify_trajectory("vc") == []


def test_verify_flags_zero_validation_check(traj_dir):
    """A completed run with no validation_check events trips the advisory flag."""
    events = [e for e in _clean_events() if e["event_type"] != "validation_check"]
    _write_traj("vzvc", events)
    defects = itl.verify_trajectory("vzvc")
    assert any("zero validation_check events" in d for d in defects)


def test_verify_missing_step_end(traj_dir):
    events = [e for e in _clean_events()
              if not (e["event_type"] == "step_end" and e["step_number"] == 2)]
    _write_traj("vse", events)
    defects = itl.verify_trajectory("vse")
    assert any("never closed by a step_end" in d for d in defects)


def test_verify_missing_wave_end(traj_dir):
    events = [e for e in _clean_events() if e["event_type"] != "wave_end"]
    _write_traj("vwe", events)
    defects = itl.verify_trajectory("vwe")
    assert any("never closed by a wave_end" in d for d in defects)


def test_verify_orphan_wave_end_still_reconciles_successes(traj_dir):
    """An orphan wave_end must not buy its successes claim a free pass.

    The 2026-08-08 impeccable run emitted exactly this shape: one orphan
    wave_end claiming successes=3 over four bracketed steps. verify skipped the
    reconciliation and reported only the pairing defect.
    """
    events = [e for e in _clean_events() if e["event_type"] != "wave_start"]
    _write_traj("vowe", events)
    defects = itl.verify_trajectory("vowe")
    assert any("has no matching wave_start" in d for d in defects)
    assert any("implicit bracket" in d and "successes=2" in d for d in defects)


def test_verify_flags_step_outside_every_wave_bracket(traj_dir):
    """A wave-mode run that leaves a step unbracketed is a bracketing defect."""
    events = _clean_events()
    tail = [
        {"event_type": "step_start", "step_number": 3, "payload": {"step": 3}},
        {"event_type": "step_end", "step_number": 3,
         "payload": {"step": 3, "files_affected": ["z.py"], "status": "ok"}},
    ]
    events = events[:-1] + tail + events[-1:]
    _write_traj("voutside", events)
    defects = itl.verify_trajectory("voutside")
    assert any("outside every wave bracket: 3" in d for d in defects)


def test_verify_no_wave_run_is_not_flagged_for_bracketing(traj_dir):
    """A bare sequential run legitimately has no brackets - never flag it."""
    events = [e for e in _clean_events()
              if e["event_type"] not in ("wave_start", "wave_end")]
    _write_traj("vnowave", events)
    assert itl.verify_trajectory("vnowave") == []


def test_verify_flags_wave_start_missing_shape(traj_dir):
    events = _clean_events()
    for e in events:
        if e["event_type"] == "wave_start":
            e["payload"] = {"wave": 1}
    _write_traj("vshape", events)
    defects = itl.verify_trajectory("vshape")
    assert any("omits step_count/parallel" in d for d in defects)


def test_verify_flags_backwards_timestamp(traj_dir):
    events = _clean_events()
    for e in events:
        e["timestamp"] = "2026-08-09T10:00:00+00:00"
        if e["event_type"] == "step_end" and e["step_number"] == 1:
            e["timestamp"] = "2026-08-09T09:59:59+00:00"
    _write_traj("vts", events)
    defects = itl.verify_trajectory("vts")
    assert any("earlier than position" in d for d in defects)


def test_verify_clean_with_timestamps_stays_clean(traj_dir):
    events = _clean_events()
    for i, e in enumerate(events):
        e["timestamp"] = f"2026-08-09T10:00:{i:02d}+00:00"
    _write_traj("vtsok", events)
    assert itl.verify_trajectory("vtsok") == []


def test_verify_successes_mismatch(traj_dir):
    events = _clean_events()
    for e in events:
        if e["event_type"] == "wave_end":
            e["payload"]["successes"] = 5
    _write_traj("vsm", events)
    defects = itl.verify_trajectory("vsm")
    assert any("successes=5" in d for d in defects)


def test_verify_glob_in_files_affected(traj_dir):
    events = _clean_events()
    for e in events:
        if e["event_type"] == "step_end" and e["step_number"] == 1:
            e["payload"]["files_affected"] = ["src/*.py"]
    _write_traj("vg", events)
    defects = itl.verify_trajectory("vg")
    assert any("not a literal path" in d for d in defects)


def test_verify_run_start_not_first(traj_dir):
    events = _clean_events()
    events = events[1:] + [events[0]]  # move run_start to the end
    _write_traj("vrs", events)
    defects = itl.verify_trajectory("vrs")
    assert any("run_start is not the first" in d for d in defects)


def test_verify_missing_run_end(traj_dir):
    events = [e for e in _clean_events() if e["event_type"] != "run_end"]
    _write_traj("vre", events)
    defects = itl.verify_trajectory("vre")
    assert any("run_end event is missing" in d for d in defects)


# ============================================================
# cmd_verify exit codes
# ============================================================
def test_cmd_verify_clean_exit_0(traj_dir):
    _write_traj("cvc", _clean_events())
    assert itl.main(["--verify", "--run-id", "cvc"]) == 0


def test_cmd_verify_defect_exit_1(traj_dir):
    events = [e for e in _clean_events() if e["event_type"] != "run_end"]
    _write_traj("cvd", events)
    assert itl.main(["--verify", "--run-id", "cvd"]) == 1


def test_cmd_verify_missing_exit_3(traj_dir):
    assert itl.main(["--verify", "--run-id", "nope"]) == 3


# ============================================================
# v1.7: emit-time sequencing guard (exit 5)
# ============================================================
def _emit(run_id, *args):
    return itl.main(["--event", "--run-id", run_id, *args])


def test_guard_clean_sequential_run_accepted(traj_dir):
    _seed("gseq")
    assert _emit("gseq", "--type", "step_start", "--step", "1", "--title", "s1") == 0
    assert _emit("gseq", "--type", "step_end", "--step", "1", "--status", "ok") == 0
    assert _emit("gseq", "--type", "step_start", "--step", "2", "--title", "s2") == 0
    assert _emit("gseq", "--type", "step_end", "--step", "2", "--status", "ok") == 0


def test_guard_rejects_step_start_while_step_open(traj_dir, capsys):
    _seed("gopen")
    assert _emit("gopen", "--type", "step_start", "--step", "1", "--title", "s1") == 0
    rc = _emit("gopen", "--type", "step_start", "--step", "2", "--title", "s2")
    assert rc == 5
    err = capsys.readouterr().err
    assert "[1]" in err and "still" in err


def test_guard_rejects_orphan_step_end(traj_dir, capsys):
    _seed("gorph")
    rc = _emit("gorph", "--type", "step_end", "--step", "7", "--status", "ok")
    assert rc == 5
    assert "no open step_start" in capsys.readouterr().err


def test_guard_suspended_inside_open_parallel_wave(traj_dir):
    _seed("gpar")
    assert _emit("gpar", "--type", "wave_start", "--wave", "1",
                 "--step-count", "2", "--parallel") == 0
    assert _emit("gpar", "--type", "step_start", "--step", "1", "--title", "s1") == 0
    # step 1 is still open, but the open parallel wave suspends the guard.
    assert _emit("gpar", "--type", "step_start", "--step", "2", "--title", "s2") == 0


def test_guard_non_parallel_wave_still_enforced(traj_dir):
    _seed("gnp")
    assert _emit("gnp", "--type", "wave_start", "--wave", "1",
                 "--step-count", "2", "--no-parallel") == 0
    assert _emit("gnp", "--type", "step_start", "--step", "1", "--title", "s1") == 0
    # A non-parallel wave does NOT suspend the guard: opening step 2 while step 1
    # is open is rejected.
    assert _emit("gnp", "--type", "step_start", "--step", "2", "--title", "s2") == 5


# ============================================================
# v1.7: run-level files reconciliation in verify_trajectory
# ============================================================
def _recon_events(git_head, recorded):
    """Structurally clean trajectory whose single step records `recorded`."""
    return [
        {"event_type": "run_start", "step_number": 0, "payload": {"git_head": git_head}},
        {"event_type": "step_start", "step_number": 1, "payload": {"step": 1}},
        {"event_type": "step_end", "step_number": 1,
         "payload": {"step": 1, "files_affected": list(recorded), "status": "ok"}},
        {"event_type": "validation_check", "step_number": None,
         "payload": {"check": "pytest", "passed": True, "detail": "ok"}},
        {"event_type": "run_end", "step_number": None, "payload": {"summary": "ok"}},
    ]


def test_reconcile_flags_unrecorded_file(traj_dir, monkeypatch):
    monkeypatch.setattr(itl, "_git_changed_files", lambda gh: {"a.py", "b.py"})
    _write_traj("rf", _recon_events("deadbeef", ["a.py"]))
    defects = itl.verify_trajectory("rf")
    assert any("(advisory)" in d and "b.py" in d for d in defects)
    assert not any("a.py" in d for d in defects)


def test_reconcile_clean_when_all_recorded(traj_dir, monkeypatch):
    monkeypatch.setattr(itl, "_git_changed_files", lambda gh: {"a.py"})
    _write_traj("rc", _recon_events("deadbeef", ["a.py"]))
    assert itl.verify_trajectory("rc") == []


def test_reconcile_skipped_on_unknown_git_head(traj_dir, monkeypatch):
    monkeypatch.setattr(itl, "_git_changed_files", lambda gh: {"z.py"})
    _write_traj("ru", _recon_events("unknown", ["a.py"]))
    defects = itl.verify_trajectory("ru")
    assert not any("z.py" in d for d in defects)


def test_reconcile_skipped_on_git_error(traj_dir, monkeypatch):
    # Helper returns empty set on git failure -> no reconciliation defect.
    monkeypatch.setattr(itl, "_git_changed_files", lambda gh: set())
    _write_traj("rge", _recon_events("deadbeef", ["a.py"]))
    assert itl.verify_trajectory("rge") == []


# ============================================================
# Plan reconciliation (2026-08-09 scrutiny: M1, M2, N1, L2)
# ============================================================
# Three findings from that audit were one shape: the run diverged from its own
# plan and only the write-up noticed. A write-up authored by whoever diverged is
# the weakest available check, so these read the plan file.
_PLAN = """# Plan

### Step 1: Do the thing

**Files affected:**

- `scripts/alpha.py`
- `reference/workspace-overview.md`

---

## Implementation Notes

### Deviations from Plan

1. **First.** Because.
2. **Second.** Because.

### Issues Encountered

None.
"""


def _plan_file(tmp_path, text=_PLAN):
    p = tmp_path / "2026-08-09-a-plan.md"
    p.write_text(text, encoding="utf-8")
    return p


def _plan_events(plan_path, recorded, deviations=0, deviation_first=False):
    """Structurally clean run naming `plan_path`, recording `recorded`."""
    events = [
        {"event_type": "run_start", "step_number": 0,
         "payload": {"plan_path": str(plan_path)}},
    ]
    if deviation_first:
        events.append({"event_type": "deviation", "step_number": 1,
                       "payload": {"step": 1, "reason": "r", "what_changed": "w"}})
    events.append({"event_type": "step_start", "step_number": 1, "payload": {"step": 1}})
    for _ in range(deviations):
        events.append({"event_type": "deviation", "step_number": 1,
                       "payload": {"step": 1, "reason": "r", "what_changed": "w"}})
    events += [
        {"event_type": "step_end", "step_number": 1,
         "payload": {"step": 1, "files_affected": list(recorded), "status": "ok"}},
        {"event_type": "validation_check", "step_number": None,
         "payload": {"check": "pytest", "passed": True, "detail": "ok"}},
        {"event_type": "run_end", "step_number": None, "payload": {"summary": "ok"}},
    ]
    return events


def test_planned_files_parses_the_block(tmp_path):
    assert itl.planned_files(_PLAN) == {
        "scripts/alpha.py", "reference/workspace-overview.md"}


def test_declared_deviation_count(tmp_path):
    assert itl.declared_deviation_count(_PLAN) == 2
    assert itl.declared_deviation_count("# Plan\n\nno notes here\n") == 0


def test_covers_is_anchored_on_a_separator():
    assert itl._covers("scripts/alpha.py", "scripts/alpha.py")
    assert itl._covers("engine/scripts/alpha.py", "scripts/alpha.py")
    # The defect this guards: a suffix match that is not a path boundary.
    assert not itl._covers("scripts/scrutinize_record.py", "record.py")


def test_verify_flags_planned_file_recorded_nowhere(traj_dir, tmp_path):
    """M1: the run edited the one file its step named, and recorded four others."""
    plan = _plan_file(tmp_path)
    _write_traj("pf1", _plan_events(plan, ["scripts/alpha.py"], deviations=2))
    defects = itl.verify_trajectory("pf1")
    assert any("reference/workspace-overview.md" in d and "no step's files_affected" in d
               for d in defects)
    assert not any("scripts/alpha.py" in d for d in defects)


def test_verify_clean_when_every_planned_file_is_recorded(traj_dir, tmp_path):
    plan = _plan_file(tmp_path)
    _write_traj("pf2", _plan_events(
        plan, ["scripts/alpha.py", "reference/workspace-overview.md"], deviations=2))
    assert itl.verify_trajectory("pf2") == []


def test_verify_accepts_a_planned_file_recorded_under_another_prefix(traj_dir, tmp_path):
    """The plan writes repo-relative; a run may record the overlay prefix."""
    plan = _plan_file(tmp_path)
    _write_traj("pf3", _plan_events(
        plan,
        ["scripts/alpha.py", ".heading-os-data/reference/workspace-overview.md"],
        deviations=2))
    assert itl.verify_trajectory("pf3") == []


def test_verify_flags_undeclared_deviation_shortfall(traj_dir, tmp_path):
    """M2: the plan declared six deviations, the trajectory carried five."""
    plan = _plan_file(tmp_path)
    _write_traj("pf4", _plan_events(
        plan, ["scripts/alpha.py", "reference/workspace-overview.md"], deviations=1))
    defects = itl.verify_trajectory("pf4")
    assert any("declares 2 deviation(s)" in d and "carries 1 deviation event(s)" in d
               for d in defects)


def test_verify_does_not_flag_more_events_than_declared(traj_dir, tmp_path):
    """Only the shortfall matters: an extra event is a run that over-recorded."""
    plan = _plan_file(tmp_path)
    _write_traj("pf5", _plan_events(
        plan, ["scripts/alpha.py", "reference/workspace-overview.md"], deviations=3))
    assert not any("deviation event(s)" in d for d in itl.verify_trajectory("pf5"))


def test_verify_is_silent_when_the_plan_cannot_be_found(traj_dir, tmp_path):
    """A missing plan is not a trajectory defect."""
    missing = tmp_path / "nope" / "2026-08-09-gone.md"
    _write_traj("pf6", _plan_events(missing, ["scripts/alpha.py"]))
    assert itl.verify_trajectory("pf6") == []


def test_verify_flags_deviation_before_its_step_start(traj_dir, tmp_path):
    """L2: a consumer reading in order sees a deviation for a step not begun."""
    plan = _plan_file(tmp_path)
    _write_traj("pf7", _plan_events(
        plan, ["scripts/alpha.py", "reference/workspace-overview.md"],
        deviations=1, deviation_first=True))
    defects = itl.verify_trajectory("pf7")
    assert any("deviation for step 1" in d and "precedes" in d for d in defects)


def test_verify_exempts_a_wave_scoped_deviation_from_the_ordering_check(traj_dir):
    """A deferred wave emits no step_start at all, by design."""
    events = [
        {"event_type": "run_start", "step_number": 0, "payload": {}},
        {"event_type": "deviation", "step_number": 4,
         "payload": {"step": 4, "scope": "wave", "wave": 2, "reason": "deferred"}},
        {"event_type": "validation_check", "step_number": None,
         "payload": {"check": "pytest", "passed": True, "detail": "ok"}},
        {"event_type": "run_end", "step_number": None, "payload": {"summary": "ok"}},
    ]
    _write_traj("pf8", events)
    assert not any("precedes" in d for d in itl.verify_trajectory("pf8"))


# ============================================================
# --list-files
# ============================================================
def test_list_files_prints_the_deduped_union(traj_dir, capsys):
    events = [
        {"event_type": "run_start", "step_number": 0, "payload": {}},
        {"event_type": "step_start", "step_number": 1, "payload": {"step": 1}},
        {"event_type": "step_end", "step_number": 1,
         "payload": {"step": 1, "files_affected": ["b.py", "a.py"], "status": "ok"}},
        {"event_type": "step_start", "step_number": 2, "payload": {"step": 2}},
        {"event_type": "step_end", "step_number": 2,
         "payload": {"step": 2, "files_affected": ["a.py", "c.md"], "status": "ok"}},
        {"event_type": "run_end", "step_number": None, "payload": {"summary": "ok"}},
    ]
    _write_traj("lf1", events)
    assert itl.main(["--list-files", "--run-id", "lf1"]) == 0
    assert capsys.readouterr().out.split() == ["a.py", "b.py", "c.md"]


def test_list_files_missing_trajectory_exits_3(traj_dir):
    assert itl.main(["--list-files", "--run-id", "nope"]) == 3
