"""Unit tests for the Phase 1 -> Phase 2 adoption-gate aggregator.

The aggregator is load-bearing for the Phase 1 ship decision - if it
mis-computes the metrics, the gate is meaningless. These tests pin the
metric semantics with explicit synthetic event streams."""
import importlib.util
import sys
from datetime import datetime, timezone
from pathlib import Path

# Load scripts/bridge-adoption-report.py via importlib (hyphen-in-name).
_REPORT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "bridge-adoption-report.py"
_spec = importlib.util.spec_from_file_location("bridge_adoption_report", _REPORT_PATH)
_mod = importlib.util.module_from_spec(_spec)
sys.modules["bridge_adoption_report"] = _mod
_spec.loader.exec_module(_mod)


def _ev(ts_iso: str, event: str, **extra) -> dict:
    """Build a synthetic event record."""
    return {"ts": ts_iso, "event": event, **extra}


def test_empty_events_returns_zero_metrics():
    """An empty event list yields zero metrics across the board."""
    m = _mod.compute_metrics([], now=datetime(2026, 5, 18, 12, 0, tzinfo=timezone.utc))
    assert m["n_weekdays"] == 0
    assert m["avg_tab_min"] == 0.0
    assert m["avg_clicks"] == 0.0
    assert m["browser_first_pct"] == 0.0


def test_page_view_duration_is_summed_into_tab_time():
    """page_view duration_s contributes to tab-time; other events do not."""
    now = datetime(2026, 5, 18, 23, 0, tzinfo=timezone.utc)
    # Monday 2026-05-18 is a weekday.
    events = [
        _ev("2026-05-18T09:00:00+00:00", "page_view", duration_s=600),  # 10 min
        _ev("2026-05-18T10:00:00+00:00", "page_view", duration_s=1200),  # 20 min
        _ev("2026-05-18T11:00:00+00:00", "launch"),  # not a page_view, no contribution
    ]
    m = _mod.compute_metrics(events, now=now)
    assert m["n_weekdays"] == 1
    assert m["avg_tab_min"] == 30.0


def test_launch_and_finalize_count_as_clicks_page_view_does_not():
    """launch + finalize each count as one click; page_view does not."""
    now = datetime(2026, 5, 18, 23, 0, tzinfo=timezone.utc)
    events = [
        _ev("2026-05-18T09:00:00+00:00", "launch"),
        _ev("2026-05-18T10:00:00+00:00", "launch"),
        _ev("2026-05-18T11:00:00+00:00", "finalize"),
        _ev("2026-05-18T12:00:00+00:00", "page_view"),  # NOT a click
    ]
    m = _mod.compute_metrics(events, now=now)
    assert m["avg_clicks"] == 3.0


def test_browser_first_is_one_when_first_event_is_page_view():
    """browser_first_pct counts weekdays where the FIRST relevant event was page_view."""
    now = datetime(2026, 5, 18, 23, 0, tzinfo=timezone.utc)
    events = [
        _ev("2026-05-18T08:00:00+00:00", "page_view"),  # browser-first
        _ev("2026-05-18T09:00:00+00:00", "launch"),
    ]
    m = _mod.compute_metrics(events, now=now)
    assert m["browser_first_pct"] == 100.0


def test_browser_first_is_zero_when_first_event_is_launch():
    """When the day starts with a terminal launch, browser-first does NOT count."""
    now = datetime(2026, 5, 18, 23, 0, tzinfo=timezone.utc)
    events = [
        _ev("2026-05-18T08:00:00+00:00", "launch"),  # terminal-first
        _ev("2026-05-18T09:00:00+00:00", "page_view"),
    ]
    m = _mod.compute_metrics(events, now=now)
    assert m["browser_first_pct"] == 0.0


def test_weekends_excluded_from_metrics():
    """Saturday + Sunday events are filtered out (weekday() >= 5)."""
    now = datetime(2026, 5, 18, 23, 0, tzinfo=timezone.utc)
    events = [
        # 2026-05-16 = Saturday; 2026-05-17 = Sunday.
        _ev("2026-05-16T09:00:00+00:00", "page_view", duration_s=10000),
        _ev("2026-05-17T09:00:00+00:00", "launch"),
    ]
    m = _mod.compute_metrics(events, now=now)
    assert m["n_weekdays"] == 0


def test_events_older_than_lookback_are_filtered():
    """Events older than lookback_days are excluded."""
    now = datetime(2026, 5, 18, 12, 0, tzinfo=timezone.utc)
    # 30 days ago = 2026-04-18 - well before the 14-day cutoff.
    events = [
        _ev("2026-04-18T09:00:00+00:00", "page_view", duration_s=600),
        _ev("2026-05-17T09:00:00+00:00", "page_view", duration_s=600),
    ]
    m = _mod.compute_metrics(events, lookback_days=14, now=now)
    # 2026-05-17 = Sunday, so weekdays = 0 - the older event is rightly filtered.
    # Use an actual weekday for clarity.
    events = [
        _ev("2026-04-18T09:00:00+00:00", "page_view", duration_s=600),  # filtered (>14d)
        _ev("2026-05-18T09:00:00+00:00", "page_view", duration_s=600),  # Monday, kept
    ]
    m = _mod.compute_metrics(events, lookback_days=14, now=now)
    assert m["n_weekdays"] == 1
    assert m["avg_tab_min"] == 10.0


def test_evaluate_gate_returns_pass_fail_tuple():
    """_evaluate_gate returns (tab_ok, click_ok, browser_first_ok) booleans."""
    metrics_all_pass = {
        "avg_tab_min": 35.0, "avg_clicks": 7.0, "browser_first_pct": 60.0,
        "n_weekdays": 10,
    }
    assert _mod._evaluate_gate(metrics_all_pass) == (True, True, True)
    metrics_all_fail = {
        "avg_tab_min": 10.0, "avg_clicks": 2.0, "browser_first_pct": 20.0,
        "n_weekdays": 10,
    }
    assert _mod._evaluate_gate(metrics_all_fail) == (False, False, False)


def test_malformed_ts_skipped():
    """Events with malformed or missing ts are silently dropped."""
    now = datetime(2026, 5, 18, 23, 0, tzinfo=timezone.utc)
    events = [
        _ev("not-a-date", "page_view", duration_s=600),
        {"event": "page_view", "duration_s": 600},  # no ts at all
        _ev("2026-05-18T09:00:00+00:00", "page_view", duration_s=600),
    ]
    m = _mod.compute_metrics(events, now=now)
    assert m["n_weekdays"] == 1
    assert m["avg_tab_min"] == 10.0
