"""Email refresher.

Phase 1.34: subprocesses `email-intelligence.py --unread` so the Inbox
dashboard reflects exactly the conversations unread in Exchange right
now - analyzed, with summaries and recommended actions. Read or delete
a message in Outlook and it leaves the unread set on the next tick.
"""
import json
import logging
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from scripts.utils.paths import get_data_root

if TYPE_CHECKING:
    from scripts.bridge_daemon.state import State

WORKSPACE = Path(__file__).resolve().parents[3]  # ceo-main workspace root
PRODUCER_SCRIPT = WORKSPACE / "scripts" / "email-intelligence.py"
# --unread mode analyzes new/changed unread mail with Haiku; the cache
# keeps steady-state runs fast, but a fresh backlog can take a couple
# of minutes.
PRODUCER_TIMEOUT_S = 300  # cold WSL run benchmarked at ~122s (Exchange + Anthropic analysis); 300s = 2x headroom, fits within 5-min refresh interval (APScheduler max_instances=1 prevents overlap)


def read_email_state(workspace_root: Path, data_root: "Path | None" = None) -> dict:
    """Read the email-intelligence state.json (DATA).

    HEADING OS engine/data split: state.json resolves under ``data_root``
    (falls back to ``workspace_root`` when not supplied).
    """
    if data_root is None:
        data_root = get_data_root()
    f = data_root / "outputs" / "operations" / "email-intelligence" / "state.json"
    if not f.exists():
        return {"messages": []}
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"messages": []}

def count_unread(state: dict) -> int:
    return sum(1 for m in state.get("messages", []) if m.get("unread"))

def refresh(workspace_root: Path, state_obj: "State") -> None:
    """Refresher callback: invoke `email-intelligence.py --unread`.

    --unread mode fetches the live Inbox unread set from Exchange,
    analyzes new/changed conversations, and writes _latest-fetch.json -
    the dashboard's feed. The dashboard therefore mirrors the CEO's
    actual inbox: anything read or deleted in Outlook drops off here.

    Failure modes are caught and logged, never raised - a daemon
    scheduler must not crash on transient Exchange errors. We bump inbox
    so the version counter (and freshness UI) advances regardless.
    """
    if not PRODUCER_SCRIPT.exists():
        logging.warning(
            "bridge.email: producer script missing at %s; skipping fetch",
            PRODUCER_SCRIPT,
        )
        state_obj.bump("inbox")
        return

    try:
        result = subprocess.run(
            [sys.executable, str(PRODUCER_SCRIPT), "--unread"],
            cwd=str(workspace_root),
            capture_output=True,
            text=True,
            timeout=PRODUCER_TIMEOUT_S,
            check=False,
        )
        if result.returncode != 0:
            logging.warning(
                "bridge.email: producer exited %d; stderr=%s",
                result.returncode,
                result.stderr.strip()[:500],
            )
        else:
            logging.info("bridge.email: producer ok")
    except subprocess.TimeoutExpired:
        logging.warning(
            "bridge.email: producer timed out after %ds", PRODUCER_TIMEOUT_S,
        )
    except OSError as e:
        logging.warning("bridge.email: subprocess failed: %s", e)

    # Always bump inbox so the ETag advances and the browser sees a fresh
    # data_time even if the producer didn't update state.json.
    state_obj.bump("inbox")
