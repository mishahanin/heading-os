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
    return [
        {"event_type": "run_start", "step_number": 0, "payload": {}},
        {"event_type": "step_start", "step_number": 1, "payload": {"step": 1}},
        {"event_type": "step_end", "step_number": 1,
         "payload": {"step": 1, "files_affected": ["x.py"], "status": "ok"}},
        {"event_type": "wave_start", "step_number": None,
         "payload": {"wave": 1, "step_count": 1, "parallel": True}},
        {"event_type": "step_start", "step_number": 2, "payload": {"step": 2}},
        {"event_type": "step_end", "step_number": 2,
         "payload": {"step": 2, "files_affected": ["y.py"], "status": "ok"}},
        {"event_type": "wave_end", "step_number": None,
         "payload": {"wave": 1, "successes": 1, "failures": 0}},
        {"event_type": "run_end", "step_number": None, "payload": {"summary": "ok"}},
    ]


def test_verify_clean(traj_dir):
    _write_traj("vc", _clean_events())
    assert itl.verify_trajectory("vc") == []


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
