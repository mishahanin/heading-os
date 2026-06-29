"""Tests for the adoption-gate aggregator (Phase 1.150)."""
import json
from datetime import date, datetime, timedelta, timezone

from scripts.bridge_daemon.adoption import summarize


def _ts(d: date, hour: int = 10, minute: int = 0) -> str:
    """Build an ISO-8601 UTC timestamp for local (UTC+4)-local datetime."""
    dt = datetime(d.year, d.month, d.day, hour, minute, tzinfo=timezone(timedelta(hours=4)))
    return dt.astimezone(timezone.utc).isoformat()


def _write_events(workspace_root, events: list[dict]) -> None:
    log = workspace_root / ".daemon-state" / "usage.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text("\n".join(json.dumps(e) for e in events) + "\n")


def test_empty_log_returns_zeros(workspace_root):
    today = date(2026, 5, 19)
    s = summarize(workspace_root, days=14, today=today)
    assert s["totals"]["page_views"] == 0
    assert s["totals"]["actions"] == 0
    assert s["metrics"]["avg_actions_per_day"] == 0
    assert s["gate"]["all_pass"] is False


def test_window_size_and_bounds(workspace_root):
    today = date(2026, 5, 19)
    s = summarize(workspace_root, days=14, today=today)
    assert s["window_days"] == 14
    assert s["window_start"] == "2026-05-06"
    assert s["window_end"] == "2026-05-19"
    assert len(s["per_day"]) == 14


def test_events_outside_window_ignored(workspace_root):
    today = date(2026, 5, 19)
    before = date(2026, 4, 1)
    _write_events(workspace_root, [
        {"ts": _ts(before), "event": "page_view", "page": "pulse", "duration_s": 300},
        {"ts": _ts(today), "event": "page_view", "page": "pulse", "duration_s": 600},
    ])
    s = summarize(workspace_root, days=14, today=today)
    assert s["totals"]["page_views"] == 1


def test_action_count_sums_launch_and_finalize(workspace_root):
    today = date(2026, 5, 19)
    _write_events(workspace_root, [
        {"ts": _ts(today, 9), "event": "launch", "action": "osint"},
        {"ts": _ts(today, 10), "event": "launch", "action": "email-respond"},
        {"ts": _ts(today, 11), "event": "finalize", "action": "send-email", "artifact_id": "a"},
        {"ts": _ts(today, 12), "event": "page_view", "page": "pulse"},
    ])
    s = summarize(workspace_root, days=14, today=today)
    assert s["totals"]["actions"] == 3
    assert s["totals"]["page_views"] == 1


def test_browser_first_morning_flag(workspace_root):
    today = date(2026, 5, 19)  # Tuesday (weekday)
    _write_events(workspace_root, [
        # First event of the day is a page_view -> browser-first
        {"ts": _ts(today, 7, 30), "event": "page_view", "page": "pulse", "duration_s": 1800},
        {"ts": _ts(today, 9, 0), "event": "launch", "action": "osint"},
    ])
    s = summarize(workspace_root, days=14, today=today)
    today_row = [d for d in s["per_day"] if d["date"] == today.isoformat()][0]
    assert today_row["browser_first"] is True
    assert s["totals"]["browser_first_mornings"] == 1


def test_terminal_first_morning(workspace_root):
    today = date(2026, 5, 19)
    _write_events(workspace_root, [
        # First event is a launch (terminal-first)
        {"ts": _ts(today, 7, 30), "event": "launch", "action": "osint"},
        {"ts": _ts(today, 9, 0), "event": "page_view", "page": "pulse"},
    ])
    s = summarize(workspace_root, days=14, today=today)
    today_row = [d for d in s["per_day"] if d["date"] == today.isoformat()][0]
    assert today_row["browser_first"] is False
    assert s["totals"]["browser_first_mornings"] == 0


def test_weekday_counting(workspace_root):
    today = date(2026, 5, 19)  # Tuesday
    s = summarize(workspace_root, days=7, today=today)
    # Window: 2026-05-13 (Wed) .. 2026-05-19 (Tue) = 7 days, 5 weekdays
    weekday_rows = [d for d in s["per_day"] if d["is_weekday"]]
    assert len(weekday_rows) == 5


def test_tab_time_uses_duration_when_provided(workspace_root):
    today = date(2026, 5, 19)
    _write_events(workspace_root, [
        {"ts": _ts(today, 9), "event": "page_view", "page": "pulse", "duration_s": 1800},  # 30 min
        {"ts": _ts(today, 10), "event": "page_view", "page": "inbox", "duration_s": 600},   # 10 min
    ])
    s = summarize(workspace_root, days=1, today=today)
    today_row = s["per_day"][0]
    assert today_row["tab_time_minutes"] == 40.0


def test_tab_time_falls_back_to_30s_min(workspace_root):
    today = date(2026, 5, 19)
    _write_events(workspace_root, [
        # No duration_s -> falls back to 30 sec
        {"ts": _ts(today, 9), "event": "page_view", "page": "pulse"},
    ])
    s = summarize(workspace_root, days=1, today=today)
    today_row = s["per_day"][0]
    assert today_row["tab_time_minutes"] == 0.5


def test_gate_passes_when_all_thresholds_met(workspace_root):
    today = date(2026, 5, 19)  # Tuesday
    # 7-day window with 5 weekdays; avg divides by 7 (full window).
    # For tab-time avg > 30 min/day across 7 days we need >210 min total,
    # so 5 weekdays * 50 min = 250 min => 35.7 min/day average.
    # For actions avg > 5/day across 7 days we need >35 actions total,
    # so 5 weekdays * 8 launches = 40 launches => 5.7 actions/day.
    events = []
    for day_offset in range(7):
        d = today - timedelta(days=day_offset)
        if d.weekday() >= 5:
            continue
        events.append({"ts": _ts(d, 7, 30), "event": "page_view", "page": "pulse", "duration_s": 50 * 60})
        for h in range(8, 16):
            events.append({"ts": _ts(d, h, 0), "event": "launch", "action": "x"})
    _write_events(workspace_root, events)
    s = summarize(workspace_root, days=7, today=today)
    assert s["metrics"]["avg_tab_time_min_per_day"] > 30
    assert s["metrics"]["avg_actions_per_day"] > 5
    assert s["metrics"]["browser_first_pct_weekdays"] > 0.50
    assert s["gate"]["all_pass"] is True


def test_gate_fails_when_below_any_threshold(workspace_root):
    today = date(2026, 5, 19)
    # Tab time + actions adequate, but browser-first mornings = 0 (terminal first)
    events = []
    for day_offset in range(5):
        d = today - timedelta(days=day_offset)
        if d.weekday() >= 5:
            continue
        events.append({"ts": _ts(d, 7, 30), "event": "launch", "action": "first"})
        events.append({"ts": _ts(d, 8, 0), "event": "page_view", "page": "pulse", "duration_s": 60 * 60})
        for h in range(9, 15):
            events.append({"ts": _ts(d, h, 0), "event": "launch", "action": "x"})
    _write_events(workspace_root, events)
    s = summarize(workspace_root, days=7, today=today)
    assert s["gate"]["all_pass"] is False
    assert s["gate"]["browser_first_pass"] is False


def test_malformed_lines_are_skipped(workspace_root):
    today = date(2026, 5, 19)
    log = workspace_root / ".daemon-state" / "usage.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text("\n".join([
        json.dumps({"ts": _ts(today, 9), "event": "page_view", "page": "pulse"}),
        "{this is not valid json",
        json.dumps({"ts": _ts(today, 10), "event": "launch", "action": "osint"}),
        "",  # blank line
        json.dumps({"ts": "not-a-real-timestamp", "event": "page_view"}),
        json.dumps({"ts": _ts(today, 11), "event": "launch", "action": "follow-up"}),
    ]) + "\n")
    s = summarize(workspace_root, days=1, today=today)
    assert s["totals"]["page_views"] == 1
    assert s["totals"]["actions"] == 2


def test_summary_includes_data_time(workspace_root):
    today = date(2026, 5, 19)
    s = summarize(workspace_root, days=14, today=today)
    assert "data_time" in s
    # Just verify it parses as ISO.
    datetime.fromisoformat(s["data_time"])
