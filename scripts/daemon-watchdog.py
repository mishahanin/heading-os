#!/usr/bin/env python3
"""Daemon liveness watchdog CLI (R14, console-first).

Thin wrapper over ``scripts/watchdog_core.py``. Runs one classification pass
over every configured daemon's liveness beat and routes a tiered alert on a
missed beat (deduped). Console-first: it only reads on-disk heartbeats + a tiny
dedup state file, so it works with the bridge daemon down.

The bridge daemon also runs ``watchdog_core.check_once`` in-process every tick
(``_watchdog_job``); this CLI is the manual / cron path.

Usage:
  python scripts/daemon-watchdog.py --once
  python scripts/daemon-watchdog.py --once --json
  python scripts/daemon-watchdog.py --once --stale-default 90

Exit codes:
  0 - all configured daemons ok
  1 - one or more daemons silent or missing

CEO-only: alerts route to the CEO's Telegram via scripts/utils/alert.py.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts import watchdog_core
from scripts.utils import trace
from scripts.utils.colors import BOLD, GRAY, GREEN, RED, RESET, YELLOW
from scripts.utils.trace_filter import attach
from scripts.utils.workspace import get_workspace_root

_STATUS_COLOR = {"ok": GREEN, "silent": YELLOW, "missing": RED}


def _print_grid(report: dict) -> None:
    verdict = report.get("verdict", "ok")
    vcol = GREEN if verdict == "ok" else RED
    fired = report.get("alerts_fired", 0)
    print(f"{BOLD}{vcol}watchdog: {verdict}{RESET}  {GRAY}({fired} alert(s) fired){RESET}")
    print()
    print(f"  {'DAEMON':<16} {'STATUS':<10} {'AGE':<8} {'THRESHOLD':<10}")
    print(f"  {'-' * 16} {'-' * 10} {'-' * 8} {'-' * 10}")
    for d in report.get("daemons", []):
        col = _STATUS_COLOR.get(d["status"], RESET)
        age = watchdog_core.format_age(d["age_s"]) if isinstance(d.get("age_s"), int) else "-"
        thr = f"{d.get('threshold_s', '-')}s"
        print(f"  {d['daemon']:<16} {col}{d['status']:<10}{RESET} {age:<8} {thr:<10}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true",
                        help="run a single pass (the default and only mode)")
    parser.add_argument("--json", action="store_true", help="emit a JSON report instead of the grid")
    parser.add_argument("--stale-default", type=int, default=None, metavar="N",
                        help="treat any daemon silent longer than N seconds as down, "
                             "overriding the per-daemon config cadence")
    args = parser.parse_args(argv)

    # Adopt an inherited trace ID if present (in-process daemon path sets one),
    # else mint a fresh one for this CLI run.
    trace.ensure()
    import logging
    attach(logging.getLogger("x31c.watchdog"))

    workspace_root = get_workspace_root()
    report = watchdog_core.check_once(
        workspace_root,
        threshold_override=args.stale_default,
    )

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        _print_grid(report)

    return 0 if report.get("verdict") == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
