#!/usr/bin/env python3
"""reminders-notify -- timer entrypoint that pushes due reminders to Telegram.

Thin orchestrator. On each fire it reads the reminders store, computes the due
set (once: when<=today and not fired; recurring: today matches the rule and not
already fired this period), and sends each due reminder to the CEO's Telegram
alert channel. A record is marked fired ONLY after a successful send, so a
transient Telegram failure simply leaves it due for the next tick -- nothing is
lost. Exit is 0 even on send failure (the oneshot unit is never left `failed`);
/prime backstops. A corrupt store is a genuine defect and exits non-zero.

Recipient (never hardcoded; read from the gitignored engine .env):
    REMINDERS_TELEGRAM_TARGET -> ODIN_CADENCE_TELEGRAM_TARGET -> "me"

Invoked by scripts/templates/systemd/reminders.service (daily timer). Also
runnable by hand:  python3 scripts/reminders-notify.py
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils import reminders_store as rs  # noqa: E402
from scripts.utils.workspace import get_workspace_root  # noqa: E402
from scripts.utils.paths import load_env  # noqa: E402

TELEGRAM_CLIENT = ".claude/skills/telegram/scripts/telegram_client.py"
DEFAULT_RECIPIENT = "me"


def _log(msg: str) -> None:
    print(f"[reminders-notify] {msg}", file=sys.stderr)


def _format(rec: dict) -> str:
    lines = [f"Reminder: {rec['message']}"]
    if rec.get("command"):
        lines.append(f"Run: {rec['command']}")
    if rec.get("thread"):
        lines.append(f"Thread: {rec['thread']}")
    return "\n".join(lines)


def send_due(today: date, send_fn) -> list[str]:
    """Send every due reminder via send_fn(message)->bool. Mark fired on success.

    Returns the ids successfully sent. Pure of Telegram: send_fn is injected.
    """
    sent: list[str] = []
    for rec in rs.due_records(today):
        try:
            ok = send_fn(_format(rec))
        except Exception as exc:  # noqa: BLE001 - one bad send must not drop the rest
            _log(f"send raised for {rec['id']} ({type(exc).__name__}: {exc})")
            ok = False
        if ok:
            rs.mark_fired(rec["id"], today)
            sent.append(rec["id"])
    return sent


def _telegram_sender(root: Path):
    recipient = (
        os.environ.get("REMINDERS_TELEGRAM_TARGET")
        or os.environ.get("ODIN_CADENCE_TELEGRAM_TARGET")
        or DEFAULT_RECIPIENT
    )
    tg = root / TELEGRAM_CLIENT

    def _send(message: str) -> bool:
        if not tg.exists():
            _log(f"telegram client absent ({tg}); /prime will backstop")
            return False
        try:
            proc = subprocess.run(
                [sys.executable, str(tg), "send", recipient, message],
                cwd=str(root), capture_output=True, text=True, timeout=120,
            )
        except Exception as exc:  # noqa: BLE001 - transient send error non-fatal
            _log(f"telegram send raised ({type(exc).__name__}: {exc})")
            return False
        if proc.returncode != 0:
            _log(f"telegram send exit {proc.returncode}: {proc.stderr.strip()[:200]}")
            return False
        return True

    return _send


def main() -> int:
    argparse.ArgumentParser(description="Push due reminders to Telegram.").parse_args()
    root = get_workspace_root()
    load_env(root)  # make .env recipient vars visible under systemd
    try:
        due = rs.due_records(date.today())
    except ValueError as exc:
        _log(f"store corrupt: {exc}")
        return 1
    if not due:
        _log("nothing due")
        return 0
    sent = send_due(date.today(), _telegram_sender(root))
    _log(f"sent {len(sent)}/{len(due)} due reminder(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
