#!/usr/bin/env python3
"""Create or update the Healthchecks.io deadman checks for the three non-fireside
Steward daemons: sentinel, eval-drift, and email-triage (inbox-pulse).

Fireside already has external monitoring (setup-fireside-healthchecks.py); this
closes the gap for the other three long-running daemons on the Steward host so a
silently-stuck daemon trips an external alert instead of going unnoticed.

Idempotent via Healthchecks.io's `unique: ["name"]` mechanism. Reads
HEALTHCHECKS_API_KEY from .env and writes the three ping URLs back to .env as
STEWARD_HC_<DAEMON>. The daemons read those URLs at runtime via
scripts/utils/healthchecks.ping(); deploy the new .env keys to the Steward host.

Usage:
    python scripts/setup-daemon-healthchecks.py           # create + write .env
    python scripts/setup-daemon-healthchecks.py --dry-run # show what would change
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.utils.healthchecks_setup import run_setup  # noqa: E402
from scripts.utils.workspace import get_default_tz_name  # noqa: E402

CHECKS = [
    {
        "env_key": "STEWARD_HC_SENTINEL",
        "name": "steward-sentinel",
        "timeout": 900,   # 15-min work cycle (sentinel check_interval default)
        "grace": 1200,    # 20 min: tolerate one slow cycle before alerting
        "tags": "steward steward-critical",
        "desc": "Sentinel comms-monitor work cycle. Pings each completed cycle (~15 min).",
    },
    {
        "env_key": "STEWARD_HC_EVAL_DRIFT",
        "name": "steward-eval-drift",
        "schedule": "0 2 * * *",   # daily 02:00 local (APScheduler cron)
        "grace": 1800,             # 30 min
        "tz": get_default_tz_name(),
        "tags": "steward",
        "desc": "Daily skill eval-drift run at 02:00 local. Pings on completed daily run.",
    },
    {
        "env_key": "STEWARD_HC_EMAIL_TRIAGE",
        "name": "steward-email-triage",
        "timeout": 300,   # 30-s poll loop; generous 5-min ceiling absorbs backoff
        "grace": 600,     # 10 min
        "tags": "steward steward-critical",
        "desc": "Inbox-pulse Exchange poll loop. Pings each clean poll cycle (~30 s).",
    },
]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    run_setup(CHECKS, args.dry_run)


if __name__ == "__main__":
    main()
