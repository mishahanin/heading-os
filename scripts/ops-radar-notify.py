#!/usr/bin/env python3
"""ops-radar notify -- timer entrypoint that pushes the ops-radar nudge to Telegram.

Thin orchestrator (no LLM, no state write of its own). On each fire it:
  1. runs `ops-radar.py heal` (Tier-A: restart ollama / rebuild a stale index),
  2. runs `ops-radar.py --quiet` (exception-only; one line of signal
     LABELS and COUNTS, never operational content),
  3. sends that line to the CEO's Telegram alert channel ONLY when non-empty.

When nothing is due it sends nothing. A transient send failure is logged and
SWALLOWED (exit 0) so the oneshot systemd unit is never left `failed` -- the next
`/prime` surfaces the same signals as a backstop.

The recipient is read from the gitignored engine `.env`, never hardcoded in this
engine-routed (eventually-public) file:
    OPS_RADAR_TELEGRAM_TARGET -> ODIN_CADENCE_TELEGRAM_TARGET -> unconfigured (no send)
The fallback chain gives zero-config continuity after the standalone Odin push
retires (the Odin signal is now folded into ops-radar). Counts-only on the wire
keeps the sovereignty contract identical to the Odin push it replaces. Delivery
is via the dedicated notifications bot (scripts/utils/telegram_notify.py).

Invoked by scripts/templates/systemd/ops-radar.service (daily timer). Also
runnable by hand:
    python3 scripts/ops-radar-notify.py
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
from scripts.utils import telegram_notify  # noqa: E402

OPS_RADAR = "scripts/ops-radar.py"

# Tier-A heal can trigger an incremental memory-index build; rebuild_index caps
# itself at 1800s, so allow a little headroom here, then proceed regardless.
HEAL_TIMEOUT = 1900
QUIET_TIMEOUT = 120

# Unconfigured default when no env target is set -- never a send attempt
# (Telegram's own-account "me"/Saved Messages sentinel is not a valid
# fallback anywhere in this system).
DEFAULT_RECIPIENT = ""


def _log(msg: str) -> None:
    print(f"[ops-radar-notify] {msg}", file=sys.stderr)


def main() -> int:
    argparse.ArgumentParser(description="Push the ops-radar nudge to Telegram on a due signal.").parse_args()

    root = get_workspace_root()
    load_env(root)  # make .env (OPS_RADAR_TELEGRAM_TARGET) visible under systemd too
    radar = root / OPS_RADAR
    if not radar.exists():
        _log(f"ops-radar absent ({radar}); nothing to do")
        return 0

    # 1. Tier-A auto-heal (best effort; never fatal to the nudge).
    try:
        subprocess.run([sys.executable, str(radar), "heal"],
                       cwd=str(root), capture_output=True, text=True, timeout=HEAL_TIMEOUT)
    except Exception as exc:  # noqa: BLE001 - heal failure is non-critical to the nudge
        _log(f"heal step failed to run ({type(exc).__name__}: {exc}); continuing to nudge")

    # 2. The exception-only line. `--quiet` prints each due signal's summary,
    #    and every summary is a fixed label plus numbers. That is what makes the
    #    wire safe, not the word "counts": see the NEVER clause in
    #    `.claude/skills/radar/SKILL.md`.
    try:
        proc = subprocess.run([sys.executable, str(radar), "--quiet"],
                              cwd=str(root), capture_output=True, text=True, timeout=QUIET_TIMEOUT)
    except Exception as exc:  # noqa: BLE001 - a missed nudge is non-critical
        _log(f"radar check failed to run ({type(exc).__name__}: {exc}); exiting 0")
        return 0

    # An exit code is not the same fact as an empty stdout. A radar that CRASHED
    # (module-level ImportError, a bad state file) exits nonzero with a
    # traceback on stderr and nothing on stdout, and this read that as "nothing
    # due" and exited 0 -- so the daily unattended timer reported health it
    # never measured, for as long as the crash lasted. `prime-health-parallel`
    # already checks `proc.returncode` on this exact subprocess, which is why an
    # interactive /prime surfaced it and the timer did not.
    if proc.returncode != 0:
        _log(f"radar exited {proc.returncode}; NOT a quiet radar: "
             f"{(proc.stderr or '').strip()[:300]}")
        return 1

    line = proc.stdout.strip()
    if not line:
        _log("nothing due -- no nudge to send")
        return 0

    # 3. Send THAT line, and only that line, to the alert channel. Never the
    #    detailed view and never the JSON: both carry paths and per-item detail
    #    that the sovereignty clause keeps off an external wire.
    recipient = (
        os.environ.get("OPS_RADAR_TELEGRAM_TARGET")
        or os.environ.get("ODIN_CADENCE_TELEGRAM_TARGET")
        or DEFAULT_RECIPIENT
    )
    if not recipient:
        # The docstring above promises "never a send attempt" when nothing is
        # configured. Passing "" through made that promise depend on how
        # telegram_notify happens to treat an empty target.
        _log("no recipient configured; skipping send")
        return 0
    if telegram_notify.notify(recipient, line):
        _log(f"nudge delivered to {recipient}: {line}")
    else:
        _log("nudge not delivered (see telegram_notify log); /prime will backstop")
    return 0


if __name__ == "__main__":
    sys.exit(main())
