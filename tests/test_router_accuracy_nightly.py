"""Tests for the F-6.2 nightly router-accuracy trend.

Two surfaces:
  1. `scripts/router-accuracy-nightly.py` (kebab, loaded via importlib): with the
     harness subprocess mocked, a run writes a dated artifact + one trend.jsonl line;
     two runs -> two artifacts + two lines (the two-consecutive-artifacts acceptance);
     `is_sensitive()` true writes nothing and exits 0; a harness non-zero exit -> 1.
  2. `scripts/utils/ops_signals.py::classify_router_accuracy` / `router_accuracy_state`:
     a > 10-point single-skill drop vs the rolling baseline -> due `tier:"B"` warn/high
     naming the skill (the sabotage-trips-flag acceptance; the tier is pinned so a
     refactor cannot drop it off the wire); a flat trend and < 2 records -> not-due; a
     gradual multi-night bleed to a > 10-point cumulative drop -> due (M1 rolling guard).
"""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from scripts.utils.egress_proof import EGRESS_CLEAR, EGRESS_UNVERIFIABLE
from scripts.utils.ops_signals import classify_router_accuracy, router_accuracy_state

ROOT = Path(__file__).resolve().parent.parent
RUNNER_PATH = ROOT / "scripts" / "router-accuracy-nightly.py"


def _load_runner():
    spec = importlib.util.spec_from_file_location("router_accuracy_nightly", RUNNER_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


runner = _load_runner()


HARNESS_JSON = {
    "model": "sonnet",
    "elapsed_seconds": 1.0,
    "overall_rate": 0.9,
    "total_passed": 9,
    "total_cases": 10,
    "threshold": 0.9,
    "strict": False,
    "below_threshold": [],
    "skills": [
        {"skill": "osint", "cases": 5, "passed": 5, "results": [], "skipped": False},
        {"skill": "recall", "cases": 5, "passed": 4, "results": [], "skipped": False},
        {"skill": "manualonly", "cases": 0, "passed": 0, "results": [], "skipped": True},
    ],
}


class _Proc:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class _FakeNow:
    def __init__(self, s):
        self._s = s

    def strftime(self, _fmt):
        return self._s


class _FakeDatetime:
    def __init__(self, s):
        self._s = s

    def now(self, _tz=None):
        return _FakeNow(self._s)


@pytest.fixture
def wired_runner(tmp_path, monkeypatch):
    """Point the runner at a tmp datastore, no-op the write guard, and default to
    non-sensitive + a passing harness. Individual tests override as needed."""
    datastore = tmp_path / "datastore"
    monkeypatch.setattr(runner, "get_datastore_dir", lambda: datastore)
    monkeypatch.setattr(runner, "require_writable_data_root", lambda: datastore)
    # The egress decision is the runner's new gate; `is_sensitive` no longer
    # governs it. Default the fixture to "this payload earned egress" so each
    # test below exercises what it names.
    monkeypatch.setattr(runner, "sensitivity_is_declared", lambda: False)
    monkeypatch.setattr(runner, "egress_state",
                        lambda *a, **k: (EGRESS_CLEAR, ""))
    monkeypatch.setattr(runner, "datetime", _FakeDatetime("2026-07-08"))
    monkeypatch.setattr(runner.subprocess, "run",
                        lambda *a, **k: _Proc(0, json.dumps(HARNESS_JSON)))
    out = datastore / "operations" / "router-accuracy"
    return out


def test_run_writes_dated_artifact_and_trend_line(wired_runner, monkeypatch):
    out = wired_runner
    rc = runner.run("sonnet")
    assert rc == 0

    dated = out / "2026-07-08.json"
    trend = out / "trend.jsonl"
    assert dated.exists()
    assert json.loads(dated.read_text())["overall_rate"] == 0.9

    lines = trend.read_text().splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["date"] == "2026-07-08"
    assert rec["overall_rate"] == 0.9
    assert rec["total_passed"] == 9 and rec["total_cases"] == 10
    # skipped skill excluded; per-skill rate = passed/cases
    assert rec["per_skill"] == {"osint": 1.0, "recall": 0.8}


def test_two_runs_two_artifacts_two_trend_lines(wired_runner, monkeypatch):
    out = wired_runner
    assert runner.run("sonnet") == 0
    monkeypatch.setattr(runner, "datetime", _FakeDatetime("2026-07-09"))
    assert runner.run("sonnet") == 0

    assert (out / "2026-07-08.json").exists()
    assert (out / "2026-07-09.json").exists()
    assert len((out / "trend.jsonl").read_text().splitlines()) == 2


def test_a_refused_run_writes_no_measurement(wired_runner, monkeypatch):
    """Replaces test_sensitive_mode_writes_nothing, whose premise the egress-proof
    slice retired: the blanket SENSITIVE_MODE skip is gone and a per-payload proof
    decides. What must still hold is that a refusal produces NO measurement, and
    what is new is that it produces a refusal RECORD instead of pure silence."""
    out = wired_runner
    monkeypatch.setattr(runner, "egress_state",
                        lambda *a, **k: (EGRESS_UNVERIFIABLE, "no denylist"))

    assert runner.run("sonnet") == 0

    assert not (out / "2026-07-08.json").exists()
    record = json.loads((out / "trend.jsonl").read_text().splitlines()[-1])
    assert record["status"] == "refused"


def test_a_declared_sensitivity_outranks_a_clear_payload(wired_runner, monkeypatch):
    """A machine proof must not overrule a person who typed the variable."""
    out = wired_runner
    monkeypatch.setattr(runner, "sensitivity_is_declared", lambda: True)

    assert runner.run("sonnet") == 0

    assert not (out / "2026-07-08.json").exists()
    assert json.loads((out / "trend.jsonl").read_text().splitlines()[-1])["status"] == "refused"


def test_the_harness_loads_the_operators_timezone_before_dating_the_record(
    wired_runner, monkeypatch
):
    """The record must be dated in the timezone the schedule runs on.

    Found by the first live nightly run: the timer fired at 2026-08-03 03:00:02
    Dubai and the record read `2026-08-02`. `HEADING_OS_TZ` lives in the
    gitignored .env and `get_default_tz()` reads os.environ only, so every dated
    artifact was stamped UTC while the unit's OnCalendar ran on local time,
    putting each night's record under the previous day.

    Asserted on the CALL rather than on a date string: the date itself depends on
    a real .env this test must not require, while "the environment was loaded
    before the timezone was read" is the behaviour that was missing.
    """
    calls = []
    monkeypatch.setattr(runner, "load_env", lambda *a, **k: calls.append(True))

    assert runner.run("sonnet") == 0
    assert calls, "_run_harness must load .env before get_default_tz() reads it"


def test_harness_failure_returns_nonzero(wired_runner, monkeypatch):
    monkeypatch.setattr(runner.subprocess, "run",
                        lambda *a, **k: _Proc(1, "", "boom"))
    assert runner.run("sonnet") == 1


# --- producer -------------------------------------------------------------------


def _rec(overall, per_skill):
    return {"overall_rate": overall, "per_skill": per_skill}


def test_classify_flags_single_skill_drop_tier_b():
    latest = _rec(0.86, {"osint": 0.80, "recall": 0.90})
    baseline = _rec(0.90, {"osint": 0.94, "recall": 0.90})  # osint -14pt
    sig = classify_router_accuracy(latest, baseline)
    assert sig["due"] is True
    assert sig["severity"] in ("warn", "high")
    assert sig["value"]["worst_skill"] == "osint"
    assert sig["tier"] == "B"  # pins H1: Tier-A would be dropped before Telegram
    assert "osint" in sig["summary"]


def test_classify_flat_trend_not_due():
    latest = _rec(0.90, {"osint": 0.94, "recall": 0.90})
    baseline = _rec(0.90, {"osint": 0.94, "recall": 0.90})
    sig = classify_router_accuracy(latest, baseline)
    assert sig["due"] is False
    assert sig["severity"] == "ok"


def test_classify_no_baseline_not_due():
    latest = _rec(0.90, {"osint": 0.94})
    sig = classify_router_accuracy(latest, None)
    assert sig["due"] is False
    assert sig["tier"] == "B"


def test_classify_aggregate_drop_escalates_high():
    latest = _rec(0.70, {"osint": 0.68, "recall": 0.72})
    baseline = _rec(0.85, {"osint": 0.72, "recall": 0.76})  # overall -15pt
    sig = classify_router_accuracy(latest, baseline)
    assert sig["due"] is True
    assert sig["severity"] == "high"


def _write_trend(data_root: Path, records: list[dict]) -> None:
    d = data_root / "datastore" / "operations" / "router-accuracy"
    d.mkdir(parents=True, exist_ok=True)
    with (d / "trend.jsonl").open("w", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r) + "\n")


def test_state_gradual_bleed_trips_flag(tmp_path):
    # osint declines ~3pt/night; a consecutive-pair delta would never fire, but the
    # rolling baseline (mean of the prior 7) vs the latest is a > 10-point drop.
    rates = [1.0, 0.97, 0.94, 0.91, 0.88, 0.85, 0.82, 0.79]
    records = [{"date": f"2026-07-{i:02d}", "overall_rate": r,
                "per_skill": {"osint": r}} for i, r in enumerate(rates, start=1)]
    _write_trend(tmp_path, records)
    sig = router_accuracy_state(tmp_path)
    assert sig["due"] is True
    assert sig["value"]["worst_skill"] == "osint"
    assert sig["tier"] == "B"


def test_state_single_record_not_due(tmp_path):
    _write_trend(tmp_path, [{"date": "2026-07-08", "overall_rate": 0.9,
                             "per_skill": {"osint": 0.9}}])
    sig = router_accuracy_state(tmp_path)
    assert sig["due"] is False


def test_state_absent_trend_is_due(tmp_path):
    """Inverted by the egress-proof slice, deliberately and with evidence.

    This test previously asserted `due is False, severity == "ok"` for a trend
    that does not exist, and that assertion is exactly what hid the defect:
    measured 2026-08-03, the producer had never run once on any host, and this
    Tier-B signal reported healthy for the whole of that time. A signal whose
    absence of data reads as good news cannot detect its producer being dead,
    which is the failure that matters most.

    The neighbouring `test_state_single_record_not_due` still holds and is the
    boundary: one real measurement with no baseline yet is a trend legitimately
    forming, not a silent producer.
    """
    sig = router_accuracy_state(tmp_path)  # nothing on disk

    assert sig["due"] is True
    assert sig["severity"] != "ok"
    assert sig["tier"] == "B"
