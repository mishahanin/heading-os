#!/usr/bin/env python3
"""reminders-notify -- timer entrypoint that pushes due reminders to Telegram.

Thin orchestrator. On each fire it reads the reminders store, computes the due
set (once: when<=today and not fired; recurring: within RECURRING_CATCHUP_GRACE_DAYS
of the most-recent target and not already fired for that target), and sends each
due reminder to the CEO's Telegram
alert channel. A record is marked fired ONLY after a successful send, so a
transient Telegram failure simply leaves it due for the next tick -- nothing is
lost. Exit is 0 even on send failure (the oneshot unit is never left `failed`);
/prime backstops. A corrupt store is a genuine defect and exits non-zero.

Recipient (never hardcoded; read from the gitignored engine .env):
    REMINDERS_TELEGRAM_TARGET -> ODIN_CADENCE_TELEGRAM_TARGET -> unconfigured (no send)

Delivery is via the dedicated notifications bot (scripts/utils/telegram_notify.py).

Invoked by scripts/templates/systemd/reminders.service (daily timer). Also
runnable by hand:  python3 scripts/reminders-notify.py
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils import reminders_store as rs  # noqa: E402
from scripts.utils.workspace import get_default_tz, get_workspace_root  # noqa: E402
from scripts.utils.paths import load_env  # noqa: E402
from scripts.utils import telegram_notify  # noqa: E402

# Unconfigured default when no env target is set -- never a send attempt
# (Telegram's own-account "me"/Saved Messages sentinel is not a valid
# fallback anywhere in this system).
DEFAULT_RECIPIENT = ""


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


def _telegram_sender():
    recipient = (
        os.environ.get("REMINDERS_TELEGRAM_TARGET")
        or os.environ.get("ODIN_CADENCE_TELEGRAM_TARGET")
        or DEFAULT_RECIPIENT
    )

    def _send(message: str) -> bool:
        if not telegram_notify.notify(recipient, message):
            _log("telegram send failed (see telegram_notify log); /prime will backstop")
            return False
        return True

    return _send


def main() -> int:
    argparse.ArgumentParser(description="Push due reminders to Telegram.").parse_args()
    root = get_workspace_root()
    load_env(root)  # make .env recipient vars visible under systemd
    today = datetime.now(get_default_tz()).date()
    try:
        due = rs.due_records(today)
    except ValueError as exc:
        _log(f"store corrupt: {exc}")
        return 1
    if not due:
        _log("nothing due")
        return 0
    sent = send_due(today, _telegram_sender())
    _log(f"sent {len(sent)}/{len(due)} due reminder(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
