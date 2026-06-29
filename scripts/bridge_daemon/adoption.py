"""Adoption-telemetry aggregator.

Reads .daemon-state/usage.jsonl (written by Telemetry in telemetry.py)
and computes the four Phase 1 -> Phase 2 gate metrics defined in
`docs/superpowers/specs/2026-05-17-living-interface-bridge-architecture.md`
section 4:

  1. avg daily tab-time (target: > 30 min/day)
  2. action-click rate (target: > 5/day)
  3. browser-first-action mornings (target: > 50% of weekdays)
  4. return-to-browser rate from terminal sessions

`summarize()` returns a JSON-serialisable dict the daemon's
GET /telemetry/summary endpoint can return directly.

Phase 1 scope:
- Per-day rollup over the last N days (default 14, matching the spec
  evaluation window).
- Pure read; never mutates usage.jsonl.

Phase 2 (when usage.jsonl rotates daily): glob the parent dir for
usage-YYYY-MM-DD.jsonl files and merge.
"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Iterator

from scripts.utils.workspace import get_default_tz

PAGE_VIEW = "page_view"
LAUNCH = "launch"
RETURN_TO_BROWSER = "return_to_browser"
FINALIZE = "finalize"

# Spec section 4 thresholds.
GATE_TAB_TIME_MIN_PER_DAY = 30
GATE_ACTION_CLICKS_PER_DAY = 5
GATE_BROWSER_FIRST_PCT = 0.50
WEEKDAY_INDEXES = {0, 1, 2, 3, 4}  # Mon..Fri


def _iter_records(path: Path) -> Iterator[dict]:
    """Yield each well-formed JSON line as a dict; skip malformed lines silently.

    Resilient because usage.jsonl is append-only and concurrent writes can
    leave partial lines mid-write if the daemon crashes; we don't want a
    single torn line to break a 14-day report.
    """
    if not path.exists():
        return
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(rec, dict) and "ts" in rec:
            yield rec


def _local_date(iso_ts: str) -> date | None:
    """Convert an ISO-8601 UTC timestamp to a local calendar date."""
    try:
        # Telemetry.event writes datetime.now(timezone.utc).isoformat()
        # which produces '...+00:00'. fromisoformat handles that in py3.11+.
        dt = datetime.fromisoformat(iso_ts)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(get_default_tz()).date()


def _local_dt(iso_ts: str) -> datetime | None:
    """Convert an ISO-8601 UTC timestamp to a local naive datetime."""
    try:
        dt = datetime.fromisoformat(iso_ts)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(get_default_tz()).replace(tzinfo=None)


def summarize(workspace_root: Path, days: int = 14, today: date | None = None) -> dict:
    """Compute the four Phase 1 -> Phase 2 gate metrics.

    Args:
        workspace_root: bridge workspace root (the dir containing
            .daemon-state/usage.jsonl).
        days: window size in days, ending at `today`. Default 14 matches
            the spec evaluation window.
        today: local date the window ends on. Defaults to the
            current local date.

    Returns:
        {
            "window_days": int,
            "window_start": "YYYY-MM-DD",
            "window_end": "YYYY-MM-DD",
            "per_day": [
                {
                    "date": "YYYY-MM-DD",
                    "is_weekday": bool,
                    "tab_time_minutes": float,
                    "page_views": int,
                    "actions": int,            # launch + finalize
                    "returns_to_browser": int,
                    "browser_first": bool,     # first event of the day was a page_view
                }
            ],
            "totals": {
                "page_views": int,
                "actions": int,
                "returns_to_browser": int,
                "weekdays_in_window": int,
                "browser_first_mornings": int,
                "tab_time_total_minutes": float,
            },
            "metrics": {
                "avg_tab_time_min_per_day": float,
                "avg_actions_per_day": float,
                "browser_first_pct_weekdays": float,
                "return_to_browser_rate": float,
            },
            "gate": {
                "tab_time_pass": bool,
                "actions_pass": bool,
                "browser_first_pass": bool,
                "all_pass": bool,
                "criteria": {
                    "tab_time_threshold_min": int,
                    "actions_threshold": int,
                    "browser_first_pct": float,
                },
            },
            "data_time": ISO-8601 timestamp this report was computed.
        }
    """
    if today is None:
        today = datetime.now(get_default_tz()).date()
    window_start = today - timedelta(days=days - 1)

    usage_path = workspace_root / ".daemon-state" / "usage.jsonl"
    records = list(_iter_records(usage_path))

    per_day_views: dict[date, list[dict]] = defaultdict(list)
    per_day_actions: dict[date, int] = defaultdict(int)
    per_day_returns: dict[date, int] = defaultdict(int)
    per_day_first_event: dict[date, datetime] = {}
    per_day_first_event_kind: dict[date, str] = {}

    for rec in records:
        local_date = _local_date(rec["ts"])
        if local_date is None:
            continue
        if local_date < window_start or local_date > today:
            continue
        evt = rec.get("event")
        local_dt = _local_dt(rec["ts"])
        if local_dt and (local_date not in per_day_first_event or local_dt < per_day_first_event[local_date]):
            per_day_first_event[local_date] = local_dt
            per_day_first_event_kind[local_date] = evt or ""
        if evt == PAGE_VIEW:
            per_day_views[local_date].append(rec)
        elif evt == LAUNCH:
            per_day_actions[local_date] += 1
        elif evt == FINALIZE:
            per_day_actions[local_date] += 1
        elif evt == RETURN_TO_BROWSER:
            per_day_returns[local_date] += 1

    per_day_out: list[dict] = []
    total_tab_time = 0.0
    total_views = 0
    total_actions = 0
    total_returns = 0
    weekdays_in_window = 0
    browser_first_mornings = 0

    cursor = window_start
    while cursor <= today:
        is_weekday = cursor.weekday() in WEEKDAY_INDEXES
        views = per_day_views.get(cursor, [])
        # Tab time estimate: when a page_view record carries duration_s,
        # use that; otherwise fall back to a 30s minimum (the freshness
        # poll cadence) so single-event days don't drop to zero.
        day_tab_seconds = 0.0
        for v in views:
            dur = v.get("duration_s")
            if isinstance(dur, (int, float)) and dur > 0:
                day_tab_seconds += float(dur)
            else:
                day_tab_seconds += 30.0
        day_tab_minutes = day_tab_seconds / 60.0
        actions_count = per_day_actions.get(cursor, 0)
        returns_count = per_day_returns.get(cursor, 0)
        browser_first = (per_day_first_event_kind.get(cursor) == PAGE_VIEW)

        per_day_out.append({
            "date": cursor.isoformat(),
            "is_weekday": is_weekday,
            "tab_time_minutes": round(day_tab_minutes, 1),
            "page_views": len(views),
            "actions": actions_count,
            "returns_to_browser": returns_count,
            "browser_first": browser_first,
        })

        total_tab_time += day_tab_minutes
        total_views += len(views)
        total_actions += actions_count
        total_returns += returns_count
        if is_weekday:
            weekdays_in_window += 1
            if browser_first:
                browser_first_mornings += 1
        cursor += timedelta(days=1)

    avg_tab_time = round(total_tab_time / max(1, days), 1)
    avg_actions = round(total_actions / max(1, days), 2)
    browser_first_pct = round(browser_first_mornings / weekdays_in_window, 3) if weekdays_in_window else 0.0
    # Return-to-browser rate: returns / (returns + page_views) - rough
    # proxy for 'how often the CEO came back to the browser vs stayed
    # in terminal'. With session-launch telemetry the denominator would
    # be sessions; we don't track sessions here so approximate.
    rtb_denominator = total_returns + total_views
    rtb_rate = round(total_returns / rtb_denominator, 3) if rtb_denominator else 0.0

    tab_time_pass = avg_tab_time > GATE_TAB_TIME_MIN_PER_DAY
    actions_pass = avg_actions > GATE_ACTION_CLICKS_PER_DAY
    browser_first_pass = browser_first_pct > GATE_BROWSER_FIRST_PCT

    return {
        "window_days": days,
        "window_start": window_start.isoformat(),
        "window_end": today.isoformat(),
        "per_day": per_day_out,
        "totals": {
            "page_views": total_views,
            "actions": total_actions,
            "returns_to_browser": total_returns,
            "weekdays_in_window": weekdays_in_window,
            "browser_first_mornings": browser_first_mornings,
            "tab_time_total_minutes": round(total_tab_time, 1),
        },
        "metrics": {
            "avg_tab_time_min_per_day": avg_tab_time,
            "avg_actions_per_day": avg_actions,
            "browser_first_pct_weekdays": browser_first_pct,
            "return_to_browser_rate": rtb_rate,
        },
        "gate": {
            "tab_time_pass": tab_time_pass,
            "actions_pass": actions_pass,
            "browser_first_pass": browser_first_pass,
            "all_pass": tab_time_pass and actions_pass and browser_first_pass,
            "criteria": {
                "tab_time_threshold_min": GATE_TAB_TIME_MIN_PER_DAY,
                "actions_threshold": GATE_ACTION_CLICKS_PER_DAY,
                "browser_first_pct": GATE_BROWSER_FIRST_PCT,
            },
        },
        "data_time": datetime.now(timezone.utc).isoformat(),
    }
