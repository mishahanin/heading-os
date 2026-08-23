#!/usr/bin/env python3
"""Print the Phase 1 -> Phase 2 adoption gate, from the one place that computes it.

Reads `.daemon-state/usage.jsonl` through `scripts/bridge_daemon/adoption.py` and
renders the three gate metrics for the last 14 days:
  - average daily tab-time (minutes)
  - average daily action-clicks (launch + finalize)
  - browser-first mornings (share of weekdays whose first event was a page_view)

Run after two weeks of Phase 1 use to evaluate the gate.

WHY THIS FILE COMPUTES NOTHING
------------------------------
Until 2026-08-23 it carried its own copy of the three metrics, and the copy did
not agree with `adoption.summarize()` — the same numbers the daemon serves at
`/telemetry/summary`. Four differences, all silent, measured on one synthetic
fortnight:

* No 30-second minimum per view, so a `page_view` with no `duration_s` counted
  as zero and tab-time read systematically low.
* "Browser-first" took the first event in FILE order rather than the earliest by
  timestamp, so an out-of-order append flipped the metric.
* The denominator was weekdays that happened to HAVE events, not weekdays in the
  window, which inflates every average.
* Days were bucketed by the UTC date rather than the operator's local date.

On that fortnight the two tools reported 5.0 min vs 0.8 min, 1.0 clicks vs 0.14,
and 50% vs 20% browser-first. The gate decides whether Phase 2 ships, and a gate
that answers differently depending on which of the repo's own tools you ask is
not a gate. So the math lives in `adoption.py` and this file renders it.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.bridge_daemon import adoption  # noqa: E402

WORKSPACE = Path(__file__).resolve().parent.parent
USAGE = WORKSPACE / ".daemon-state" / "usage.jsonl"
LOOKBACK_DAYS = 14


def _print_report(summary: dict) -> None:
    metrics = summary["metrics"]
    gate = summary["gate"]
    criteria = gate["criteria"]

    def _verdict(passed: bool) -> str:
        return "PASS" if passed else "FAIL"

    weekdays = summary["totals"]["weekdays_in_window"]
    browser_first_pct = round(metrics["browser_first_pct_weekdays"] * 100, 1)
    print(f"Phase 1 adoption metrics ({summary['window_start']} .. "
          f"{summary['window_end']}, {weekdays} weekdays):")
    print(f"  Avg daily tab-time:      "
          f"{metrics['avg_tab_time_min_per_day']:>6} min   "
          f"(gate: > {criteria['tab_time_threshold_min']} min)   "
          f"[{_verdict(gate['tab_time_pass'])}]")
    print(f"  Avg daily action-clicks: {metrics['avg_actions_per_day']:>6}       "
          f"(gate: > {criteria['actions_threshold']})        "
          f"[{_verdict(gate['actions_pass'])}]")
    print(f"  Browser-first mornings:  {browser_first_pct:>5}%      "
          f"(gate: > {round(criteria['browser_first_pct'] * 100)}%)      "
          f"[{_verdict(gate['browser_first_pass'])}]")
    print()
    print("Subjective gate: CEO verdict 'I want this for the execs.' (yes / no)")
    print()
    if gate["all_pass"]:
        print("All three quantitative gates PASS. CEO verdict decides.")
    else:
        print("At least one quantitative gate FAIL. See spec for shelve protocol.")


def main() -> int:
    if not USAGE.exists():
        print("No usage data yet. Run the daemon for at least one session.",
              file=sys.stderr)
        return 1
    summary = adoption.summarize(WORKSPACE, days=LOOKBACK_DAYS)
    if not summary["totals"]["page_views"] and not summary["totals"]["actions"]:
        print("usage.jsonl exists but has no parseable events in the window.",
              file=sys.stderr)
        return 1
    _print_report(summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
