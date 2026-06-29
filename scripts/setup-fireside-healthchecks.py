#!/usr/bin/env python3
"""Create or update the 5 Healthchecks.io checks for the Fireside Daemon.

Idempotent via Healthchecks.io's `unique: ["name"]` mechanism: re-running
matches existing checks by name and updates them instead of creating duplicates.

Reads HEALTHCHECKS_API_KEY from .env.
Writes the 5 ping URLs back to .env as FIRESIDE_HC_<JOB>.

Usage:
    python scripts/setup-fireside-healthchecks.py           # create + write .env
    python scripts/setup-fireside-healthchecks.py --dry-run # show what would change
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.utils.healthchecks_setup import run_setup  # noqa: E402
from scripts.utils.workspace import get_default_tz_name  # noqa: E402

CHECKS = [
    {
        "env_key": "FIRESIDE_HC_POLL",
        "name": "fireside-poll",
        "timeout": 300,          # 5 min interval
        "grace": 900,            # 15 min
        "tags": "fireside fireside-critical",
        "desc": "Telegram poll cycle. Pings every 5 min on successful poll.",
    },
    {
        "env_key": "FIRESIDE_HC_SUNDAY_PREVIEW",
        "name": "fireside-sunday-preview",
        "schedule": "0 18 * * 0",   # Sun 18:00
        "grace": 1800,                # 30 min
        "tz": get_default_tz_name(),
        "tags": "fireside fireside-critical",
        "desc": "Weekly preview post to 31C Tribe group, Sundays 18:00 local time.",
    },
    {
        "env_key": "FIRESIDE_HC_DAYOF_REMINDERS",
        "name": "fireside-dayof-reminders",
        "schedule": "30 15 * * 1,3", # Mon+Wed 15:30
        "grace": 1800,
        "tz": get_default_tz_name(),
        "tags": "fireside fireside-critical",
        "desc": "Day-of Zoom-link DMs to today's 3 speakers, 3h before session.",
    },
    {
        "env_key": "FIRESIDE_HC_SPEAKER_DMS",
        "name": "fireside-speaker-dms",
        "schedule": "0 9 * * *",     # daily 09:00
        "grace": 1800,
        "tz": get_default_tz_name(),
        "tags": "fireside",
        "desc": "Daily 2wk + 3day speaker reminder DMs at 09:00 local time.",
    },
    {
        "env_key": "FIRESIDE_HC_HELMSMAN_BRIEF",
        "name": "fireside-helmsman-brief",
        "schedule": "0 10 * * *",    # daily 10:00
        "grace": 1800,
        "tz": get_default_tz_name(),
        "tags": "fireside",
        "desc": "Daily Helmsman briefing at 10:00 local (idempotent via briefed flag).",
    },
]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    run_setup(CHECKS, args.dry_run)


if __name__ == "__main__":
    main()
