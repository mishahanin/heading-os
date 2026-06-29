"""Regression tests for the eval-drift daemon per-trace aggregation defect (R13).

Two paths, both of which fail against the pre-fix code:
  N=0 - the daemon's normal state. Pre-fix, run_iteration raised an
        unconditional NameError (check_results unbound) on the zero-traces path.
  N=3 - pre-fix, only the LAST trace's check_results were counted and only the
        last failing trace_id was recorded; the fix sums every trace.

The daemon filename is kebab-case, so it is importlib-loaded by path (mirrors
tests/test_fireside_daemon.py).
"""
from __future__ import annotations

import importlib.util
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture(scope="module")
def daemon_mod():
    """Load eval-drift-daemon.py as a module (hyphen in filename)."""
    path = Path(__file__).resolve().parent.parent / "scripts" / "eval-drift-daemon.py"
    spec = importlib.util.spec_from_file_location("eval_drift_daemon", str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _common_stubs(daemon_mod, monkeypatch, captured):
    """Stub out the daemon's I/O and model edges so run_iteration is exercisable
    in-process. Capture the SkillDriftResult list via write_report."""
    monkeypatch.setattr(daemon_mod, "_sensitive_session", lambda: False)
    monkeypatch.setattr(daemon_mod, "load_env", lambda: None)
    monkeypatch.setattr(
        daemon_mod, "load_state",
        lambda: {"version": 1, "last_run": None, "skills": {}, "errors": []},
    )
    monkeypatch.setattr(daemon_mod, "_load_run_skill_eval", lambda: SimpleNamespace())
    monkeypatch.setattr(daemon_mod, "list_skills_with_evals", lambda: ["fake-skill"])
    monkeypatch.setattr(daemon_mod, "save_state", lambda state: None)
    monkeypatch.setattr(daemon_mod, "notify_regressions", lambda results, logger: None)

    def _capture_report(results, run_started_iso):
        captured["results"] = results
        return Path("/dev/null")

    monkeypatch.setattr(daemon_mod, "write_report", _capture_report)


def test_zero_traces_does_not_raise(daemon_mod, monkeypatch):
    """N=0: the common path. Pre-fix this raised NameError; post-fix it returns
    cleanly with zero aggregates and an empty failed_cases."""
    captured: dict = {}
    _common_stubs(daemon_mod, monkeypatch, captured)
    monkeypatch.setattr(daemon_mod, "fetch_recent_traces", lambda skill, **kw: [])
    # replay_trace must never be called when there are no traces.
    monkeypatch.setattr(
        daemon_mod, "replay_trace",
        lambda *a, **k: pytest.fail("replay_trace called on the zero-traces path"),
    )

    logger = logging.getLogger("test-eval-drift-n0")
    rc = daemon_mod.run_iteration("fake-skill", dry_run=False, logger=logger)

    assert rc == 0
    res = captured["results"]
    assert len(res) == 1
    assert res[0].checks_passed == 0
    assert res[0].checks_total == 0
    assert res[0].failed_cases == []
    assert res[0].pass_rate == 1.0  # 0/0 -> 1.0 by the pass_rate property


def test_three_traces_aggregate_all(daemon_mod, monkeypatch):
    """N=3: every trace's checks must be summed, and every failing trace's id
    recorded. Pre-fix only the last trace counted (checks_total would be 1, one
    failed_case); post-fix totals are 5/3 with two failed_cases."""
    captured: dict = {}
    _common_stubs(daemon_mod, monkeypatch, captured)

    traces = [
        {"id": "t1", "timestamp": "2026-06-06T00:00:01"},
        {"id": "t2", "timestamp": "2026-06-06T00:00:02"},
        {"id": "t3", "timestamp": "2026-06-06T00:00:03"},
    ]
    results_by_trace = {
        # t1: two checks, both pass
        "t1": [{"check": "a", "passed": True, "detail": ""},
               {"check": "b", "passed": True, "detail": ""}],
        # t2: two checks, one fails -> failing trace
        "t2": [{"check": "a", "passed": True, "detail": ""},
               {"check": "b", "passed": False, "detail": "boom"}],
        # t3: one check, fails -> failing trace
        "t3": [{"check": "a", "passed": False, "detail": "boom"}],
    }
    monkeypatch.setattr(daemon_mod, "fetch_recent_traces", lambda skill, **kw: list(traces))
    monkeypatch.setattr(
        daemon_mod, "replay_trace",
        lambda rse_mod, skill_dir, trace, dry_run: ("", results_by_trace[trace["id"]]),
    )

    logger = logging.getLogger("test-eval-drift-n3")
    daemon_mod.run_iteration("fake-skill", dry_run=False, logger=logger)

    res = captured["results"][0]
    assert res.traces_seen == 3
    assert res.checks_total == 5, "must sum all three traces, not just the last"
    assert res.checks_passed == 3
    # Both failing traces recorded, by their own ids (not only the last).
    failing_ids = sorted(fc["trace_id"] for fc in res.failed_cases)
    assert failing_ids == ["t2", "t3"]
    assert res.pass_rate == pytest.approx(3 / 5)
