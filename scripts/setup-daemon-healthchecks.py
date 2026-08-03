#!/usr/bin/env python3
"""Create or update the Healthchecks.io deadman checks for the two non-fireside
Steward daemons: sentinel and email-triage (inbox-pulse).

Fireside already has external monitoring (setup-fireside-healthchecks.py); this
closes the gap for the other long-running daemons on the Steward host so a
silently-stuck daemon trips an external alert instead of going unnoticed.

Idempotent via Healthchecks.io's `unique: ["name"]` mechanism. Reads
HEALTHCHECKS_API_KEY from .env and writes the ping URLs back to .env as
STEWARD_HC_<DAEMON>. The daemons read those URLs at runtime via
scripts/utils/healthchecks.ping(); deploy the new .env keys to the Steward host.

A check whose daemon has been retired is a deadman that alerts forever, so an
entry here is removed in the same change as the code it watches. The third check
that used to sit here was the only one carrying a `schedule`/`tz` pair, and its
`tz` came from a module-scope zone read with `.env` still unloaded -- so it was
registered in UTC while the daemon fired at local 02:00. Any future entry needing
a zone must call `load_env()` before reading one. See CHANGELOG, 2026-08-03.

Usage:
    python scripts/setup-daemon-healthchecks.py           # create + write .env
    python scripts/setup-daemon-healthchecks.py --dry-run # show what would change
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.utils.healthchecks_setup import run_setup  # noqa: E402

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
