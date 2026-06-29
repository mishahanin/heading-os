#!/usr/bin/env python3
"""Odin cadence notify -- timer entrypoint that pushes the cadence nudge to Telegram.

Thin orchestrator (no LLM, no brain write). Runs the read-only cadence checker;
on a genuine nudge it sends the one-line, COUNTS-ONLY suggestion to the CEO's
Telegram alert channel ("Urgent Stuff for M" by default; override with
ODIN_CADENCE_TELEGRAM_TARGET, "me" for Saved Messages) via the existing headless
client. When up to date it
sends nothing. A transient send failure is logged and SWALLOWED (exit 0) so the
oneshot systemd unit is never left in `failed` state -- the next `/prime` surfaces
the same signal as a backstop (plan Decision 9).

It runs `odin-cadence.py --quiet`, whose stdout IS the canonical suggestion line
(empty when up to date). Reusing that line verbatim -- rather than rebuilding it
here from `--json` -- guarantees the Telegram text can never drift from the line
the CEO sees at `/prime`, and keeps the counts-only contract in one place.

Invoked by scripts/templates/systemd/odin-cadence.service (weekly timer). Also
runnable by hand for a dry-run:
    python3 scripts/odin-cadence-notify.py            # send only if a nudge is due
    python3 scripts/odin-cadence-notify.py --min-entries 1   # force-test delivery
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

# Workspace import bootstrap (per development-standards.md)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils.workspace import get_workspace_root  # noqa: E402
from scripts.utils.paths import load_env  # noqa: E402

TELEGRAM_CLIENT = ".claude/skills/telegram/scripts/telegram_client.py"
CADENCE_SCRIPT = "scripts/odin-cadence.py"

# Where the weekly nudge lands. The recipient is read from the gitignored engine
# .env (ODIN_CADENCE_TELEGRAM_TARGET) so no personal channel id lives in this
# engine-routed (eventually-public) file. The CEO routes it to his "Urgent Stuff
# for M" alert channel via that .env value. The in-code fallback is "me" (Saved
# Messages) -- a fleet-safe default when the env var is unset.
DEFAULT_RECIPIENT = "me"


def _log(msg: str) -> None:
    print(f"[odin-cadence-notify] {msg}", file=sys.stderr)


def main() -> int:
    ap = argparse.ArgumentParser(description="Push the Odin cadence nudge to Telegram on a nudge.")
    ap.add_argument("--min-entries", type=int, default=None,
                    help="override the un-harvested threshold (for dry-run testing)")
    args = ap.parse_args()

    root = get_workspace_root()
    load_env(root)  # make .env (ODIN_CADENCE_TELEGRAM_TARGET) visible under systemd too
    cadence = root / CADENCE_SCRIPT
    if not cadence.exists():
        _log(f"cadence script absent ({cadence}); nothing to do")
        return 0

    cmd = [sys.executable, str(cadence), "--quiet"]
    if args.min_entries is not None:
        cmd += ["--min-entries", str(args.min_entries)]
    try:
        proc = subprocess.run(cmd, cwd=str(root), capture_output=True, text=True, timeout=120)
    except Exception as exc:  # noqa: BLE001 - boundary; a missed nudge is non-critical
        _log(f"cadence check failed to run ({type(exc).__name__}: {exc}); exiting 0")
        return 0

    line = proc.stdout.strip()
    if not line:
        _log("up to date -- no nudge to send")
        return 0

    # Send the counts-only line to the CEO's alert channel (override-able target).
    recipient = os.environ.get("ODIN_CADENCE_TELEGRAM_TARGET", DEFAULT_RECIPIENT)
    tg = root / TELEGRAM_CLIENT
    if not tg.exists():
        _log(f"telegram client absent ({tg}); nudge not delivered, /prime will backstop")
        return 0
    try:
        send = subprocess.run(
            [sys.executable, str(tg), "send", recipient, line],
            cwd=str(root), capture_output=True, text=True, timeout=120,
        )
    except Exception as exc:  # noqa: BLE001 - transient send error is non-critical
        _log(f"telegram send raised ({type(exc).__name__}: {exc}); /prime will backstop")
        return 0

    if send.returncode != 0:
        _log(f"telegram send exit {send.returncode}: {send.stderr.strip()[:200]}; /prime will backstop")
        return 0

    _log(f"nudge delivered to {recipient}: {line}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
