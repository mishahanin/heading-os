"""Tests for the adoption-gate aggregator (Phase 1.150).

Two of the three gate booleans had no negative case, and the fourth gate metric
had no case at all, until 2026-08-31.

`summarize()` returns three booleans and `all_pass` is their conjunction. Only
`browser_first_pass` was ever asserted False on its own, by
`test_gate_fails_when_below_any_threshold`. The other two were read through
`all_pass`, and a conjunction is already False as soon as one term is, so a term
hardwired True changed no assertion anywhere. Measured against this tree, each
mutation applied alone to `scripts/bridge_daemon/adoption.py` and the whole
directory run:

    baseline (unmutated)                 -> 1312 passed, 1 skipped
    actions_pass = True                  -> 1312 passed, 1 skipped
    tab_time_pass = True                 -> 1312 passed, 1 skipped
    avg_actions >= GATE (was >)          -> 1312 passed, 1 skipped
    elif RETURN_TO_BROWSER: pass         -> 1312 passed, 1 skipped

The third line is the boundary: `>` and `>=` differ only for a window sitting
exactly ON the threshold, and no window did. Each gate now has a case on the
line as well as either side of it, and each boolean is asserted by name in a
window where it is the only failing one.

This is not an abstract shortfall. `scripts/bridge-adoption-report.py` prints
`[PASS]` per gate straight from these booleans, and that report is what decides
whether the bridge ships to the executive fleet, so an always-true gate would
have printed PASS with nothing red.

The fourth line is the same hole one level deeper: nothing here ever wrote a
`return_to_browser` event, so the branch counting them could be replaced with
`pass` while `returns_to_browser` and `return_to_browser_rate` kept being served
at `/telemetry/summary` as zeros. That metric is number 4 in this module's own
docstring and in the spec.
"""
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
    # Every boolean by name. `all_pass` alone cannot tell a gate that passed
    # from one hardwired True, and it cannot tell WHICH gate went False.
    assert s["gate"]["tab_time_pass"] is True
    assert s["gate"]["actions_pass"] is True
    assert s["gate"]["browser_first_pass"] is True
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


# ============================================================
# One failing gate at a time, so each boolean is measured alone
# ============================================================

def _weekday_window(today: date, days: int, tab_minutes: int, launches: int,
                    browser_first: bool = True) -> list[dict]:
    """One page_view plus `launches` launches on every weekday in the window.

    `tab_minutes` is per weekday. When `browser_first` is False a launch is
    written before the page_view, which is what hands the day's first-event slot
    to the terminal.
    """
    events: list[dict] = []
    for offset in range(days):
        d = today - timedelta(days=offset)
        if d.weekday() >= 5:
            continue
        if not browser_first:
            events.append({"ts": _ts(d, 7, 0), "event": "launch", "action": "first"})
        events.append({"ts": _ts(d, 7, 30), "event": "page_view", "page": "pulse",
                       "duration_s": tab_minutes * 60})
        for h in range(9, 9 + launches):
            events.append({"ts": _ts(d, h, 0), "event": "launch", "action": "x"})
    return events


def test_only_the_actions_gate_fails(workspace_root):
    """Tab-time and browser-first hold; the click rate does not.

    Window: 7 days ending Tuesday 2026-05-19, 5 weekdays.
    Tab-time  5 * 50 min = 250 min over 7 days = 35.7/day  -> pass
    Actions   5 *  2     =  10 over 7 days     =  1.43/day -> FAIL
    Browser   5 of 5 weekdays                  =  1.0      -> pass
    """
    today = date(2026, 5, 19)
    _write_events(workspace_root, _weekday_window(today, 7, tab_minutes=50, launches=2))
    gate = summarize(workspace_root, days=7, today=today)["gate"]
    assert gate["actions_pass"] is False, (
        "the click-rate gate passed on 1.43 clicks/day against a threshold of 5")
    assert gate["tab_time_pass"] is True
    assert gate["browser_first_pass"] is True
    assert gate["all_pass"] is False


def test_only_the_tab_time_gate_fails(workspace_root):
    """Clicks and browser-first hold; the time on the page does not.

    Tab-time  5 * 10 min =  50 min over 7 days =  7.1/day  -> FAIL
    Actions   5 *  8     =  40 over 7 days     =  5.71/day -> pass
    Browser   5 of 5 weekdays                  =  1.0      -> pass
    """
    today = date(2026, 5, 19)
    _write_events(workspace_root, _weekday_window(today, 7, tab_minutes=10, launches=8))
    gate = summarize(workspace_root, days=7, today=today)["gate"]
    assert gate["tab_time_pass"] is False, (
        "the tab-time gate passed on 7.1 min/day against a threshold of 30")
    assert gate["actions_pass"] is True
    assert gate["browser_first_pass"] is True
    assert gate["all_pass"] is False


def test_only_the_browser_first_gate_fails(workspace_root):
    """The third boolean, with the other two now explicitly passing.

    `test_gate_fails_when_below_any_threshold` covers this direction too, but
    asserts nothing about the other two, so it could not distinguish this window
    from one where all three fail.
    """
    today = date(2026, 5, 19)
    _write_events(workspace_root, _weekday_window(
        today, 7, tab_minutes=50, launches=8, browser_first=False))
    gate = summarize(workspace_root, days=7, today=today)["gate"]
    assert gate["browser_first_pass"] is False
    assert gate["tab_time_pass"] is True
    assert gate["actions_pass"] is True
    assert gate["all_pass"] is False


# ============================================================
# The thresholds are strict: a window ON the line does not pass
# ============================================================

def test_a_click_rate_exactly_on_the_threshold_does_not_pass(workspace_root):
    """5 weekdays * 7 launches = 35 over 7 days = exactly 5.00/day.

    `>` and `>=` are indistinguishable everywhere except here, and until this
    test existed the mutation `avg_actions >= GATE_ACTION_CLICKS_PER_DAY` left
    the whole directory green.
    """
    today = date(2026, 5, 19)
    _write_events(workspace_root, _weekday_window(today, 7, tab_minutes=50, launches=7))
    s = summarize(workspace_root, days=7, today=today)
    assert s["metrics"]["avg_actions_per_day"] == 5.0
    assert s["gate"]["actions_pass"] is False, "the gate is > 5, not >= 5"


def test_a_tab_time_exactly_on_the_threshold_does_not_pass(workspace_root):
    """5 weekdays * 42 min = 210 min over 7 days = exactly 30.0/day."""
    today = date(2026, 5, 19)
    _write_events(workspace_root, _weekday_window(today, 7, tab_minutes=42, launches=8))
    s = summarize(workspace_root, days=7, today=today)
    assert s["metrics"]["avg_tab_time_min_per_day"] == 30.0
    assert s["gate"]["tab_time_pass"] is False, "the gate is > 30, not >= 30"


def test_a_browser_first_share_exactly_on_the_threshold_does_not_pass(workspace_root):
    """Two browser-first mornings out of four weekdays is exactly 0.50.

    Window: 4 days ending Thursday 2026-05-14, so Mon 11 .. Thu 14 and no
    weekend day to make the denominator odd.
    """
    today = date(2026, 5, 14)
    events = []
    for offset, browser_first in enumerate([True, True, False, False]):
        d = today - timedelta(days=offset)
        if not browser_first:
            events.append({"ts": _ts(d, 7, 0), "event": "launch", "action": "first"})
        events.append({"ts": _ts(d, 7, 30), "event": "page_view", "page": "pulse",
                       "duration_s": 50 * 60})
    _write_events(workspace_root, events)
    s = summarize(workspace_root, days=4, today=today)
    assert s["totals"]["weekdays_in_window"] == 4
    assert s["metrics"]["browser_first_pct_weekdays"] == 0.5
    assert s["gate"]["browser_first_pass"] is False, "the gate is > 50%, not >= 50%"


# ============================================================
# Metric 4: return-to-browser
# ============================================================

def test_return_to_browser_events_are_counted(workspace_root):
    """The branch that counts them had no case, so `pass` in its place was
    invisible to the whole directory."""
    today = date(2026, 5, 19)
    _write_events(workspace_root, [
        {"ts": _ts(today, 9), "event": "page_view", "page": "pulse", "duration_s": 600},
        {"ts": _ts(today, 10), "event": "page_view", "page": "inbox", "duration_s": 600},
        {"ts": _ts(today, 11), "event": "return_to_browser"},
        {"ts": _ts(today, 12), "event": "return_to_browser"},
        {"ts": _ts(today, 13), "event": "return_to_browser"},
    ])
    s = summarize(workspace_root, days=1, today=today)
    assert s["totals"]["returns_to_browser"] == 3
    assert s["per_day"][0]["returns_to_browser"] == 3
    # returns / (returns + page_views) = 3 / 5
    assert s["metrics"]["return_to_browser_rate"] == 0.6


def test_a_return_to_browser_is_not_counted_as_an_action(workspace_root):
    """Anchor: the counter must not be wired into the click-rate gate."""
    today = date(2026, 5, 19)
    _write_events(workspace_root, [
        {"ts": _ts(today, 9), "event": "return_to_browser"},
    ])
    s = summarize(workspace_root, days=1, today=today)
    assert s["totals"]["actions"] == 0
    assert s["totals"]["page_views"] == 0
    assert s["totals"]["returns_to_browser"] == 1


def test_no_returns_is_a_zero_rate_not_a_division_error(workspace_root):
    today = date(2026, 5, 19)
    _write_events(workspace_root, [
        {"ts": _ts(today, 9), "event": "page_view", "page": "pulse", "duration_s": 60},
    ])
    s = summarize(workspace_root, days=1, today=today)
    assert s["totals"]["returns_to_browser"] == 0
    assert s["metrics"]["return_to_browser_rate"] == 0.0
