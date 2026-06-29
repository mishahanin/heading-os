#!/usr/bin/env python3
"""Aggregate bridge telemetry into the Phase 1 adoption gate metrics.

Reads .daemon-state/usage.jsonl and computes for the last 14 days:
  - average daily tab-time (sum of page_view.duration_s per day, in minutes)
  - average daily action-clicks (count of launch + finalize events per day)
  - browser-first-action mornings (percent of weekdays where first event
    of the day was a page_view, not a launch from terminal)

Run after 2 weeks of Phase 1 use to evaluate the gate.
"""
import json
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parent.parent
USAGE = WORKSPACE / ".daemon-state" / "usage.jsonl"

# Phase 1 -> Phase 2 gate thresholds (per spec section 4).
TAB_TIME_THRESHOLD_MIN = 30
CLICK_THRESHOLD = 5
BROWSER_FIRST_THRESHOLD_PCT = 50
LOOKBACK_DAYS = 14


def _load_events(usage_path: Path) -> list[dict]:
    """Read JSONL events from disk. Silently skip malformed lines."""
    if not usage_path.exists():
        return []
    events = []
    for line in usage_path.read_text(encoding="utf-8").splitlines():
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return events


def compute_metrics(events: list[dict], now: datetime | None = None,
                    lookback_days: int = LOOKBACK_DAYS) -> dict:
    """Compute the 3 gate metrics from a list of telemetry events.

    Args:
      events: parsed JSONL records, each carrying at minimum ts (ISO 8601 UTC)
              and event (str). page_view events may carry duration_s.
      now: reference "now" datetime (UTC). Defaults to current wall-clock.
      lookback_days: how far back to look. Defaults to 14.

    Returns: dict with avg_tab_min, avg_clicks, browser_first_pct, n_weekdays.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=lookback_days)
    filtered = []
    for e in events:
        try:
            ts = datetime.fromisoformat(e["ts"])
        except (KeyError, ValueError):
            continue
        if ts >= cutoff:
            filtered.append(e)

    by_day: dict[str, list[dict]] = defaultdict(list)
    for e in filtered:
        d = e["ts"][:10]
        by_day[d].append(e)

    days = sorted(by_day.keys())
    weekdays = [d for d in days if datetime.fromisoformat(d).weekday() < 5]

    tab_seconds: list[float] = []
    click_counts: list[int] = []
    browser_first = 0

    for d in weekdays:
        evs = by_day[d]
        tab_seconds.append(sum(
            e.get("duration_s", 0) or 0
            for e in evs if e["event"] == "page_view"
        ))
        click_counts.append(sum(
            1 for e in evs if e["event"] in ("launch", "finalize")
        ))
        first = next(
            (e for e in evs if e["event"] in ("page_view", "launch", "finalize")),
            None,
        )
        if first and first["event"] == "page_view":
            browser_first += 1

    n = len(weekdays) or 1
    return {
        "n_weekdays": len(weekdays),
        "avg_tab_min": round(sum(tab_seconds) / n / 60, 1),
        "avg_clicks": round(sum(click_counts) / n, 1),
        "browser_first_pct": round(browser_first * 100 / n, 1),
    }


def _evaluate_gate(metrics: dict) -> tuple[bool, bool, bool]:
    """Return (tab_ok, click_ok, browser_first_ok) tuple of pass/fail booleans."""
    return (
        metrics["avg_tab_min"] > TAB_TIME_THRESHOLD_MIN,
        metrics["avg_clicks"] > CLICK_THRESHOLD,
        metrics["browser_first_pct"] > BROWSER_FIRST_THRESHOLD_PCT,
    )


def _print_report(metrics: dict) -> None:
    n = metrics["n_weekdays"] or 1
    tab_ok, click_ok, bf_ok = _evaluate_gate(metrics)

    def _verdict(passed: bool) -> str:
        return "PASS" if passed else "FAIL"

    print(f"Phase 1 adoption metrics (last {metrics['n_weekdays']} weekdays):")
    print(f"  Avg daily tab-time:      {metrics['avg_tab_min']:>6} min   "
          f"(gate: > {TAB_TIME_THRESHOLD_MIN} min)   [{_verdict(tab_ok)}]")
    print(f"  Avg daily action-clicks: {metrics['avg_clicks']:>6}       "
          f"(gate: > {CLICK_THRESHOLD})        [{_verdict(click_ok)}]")
    print(f"  Browser-first mornings:  {metrics['browser_first_pct']:>5}%      "
          f"(gate: > {BROWSER_FIRST_THRESHOLD_PCT}%)      [{_verdict(bf_ok)}]")
    print()
    print("Subjective gate: CEO verdict 'I want this for the execs.' (yes / no)")
    print()
    if tab_ok and click_ok and bf_ok:
        print("All three quantitative gates PASS. CEO verdict decides.")
    else:
        print("At least one quantitative gate FAIL. See spec for shelve protocol.")


def main() -> int:
    if not USAGE.exists():
        print("No usage data yet. Run the daemon for at least one session.",
              file=sys.stderr)
        return 1
    events = _load_events(USAGE)
    if not events:
        print("usage.jsonl exists but has no parseable events.", file=sys.stderr)
        return 1
    metrics = compute_metrics(events)
    _print_report(metrics)
    return 0


if __name__ == "__main__":
    sys.exit(main())
