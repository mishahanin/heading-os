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

logger = logging.getLogger(__name__)

# The engine root this module was imported from. It is the FALLBACK only:
# `refresh()` resolves the producer from the workspace_root it is handed, the
# way `finalizers/mark_read.py` already does for the same script. The module
# constant used to be the only lookup, so `refresh(other_root, ...)` ran a
# script out of THIS tree with its cwd set to a different one -- two roots in
# one subprocess, and the parameter it accepted was decoration.
_MODULE_ENGINE_ROOT = Path(__file__).resolve().parents[3]


def producer_script(workspace_root: "Path | None" = None) -> Path:
    """`scripts/email-intelligence.py` under the given ENGINE root."""
    return (workspace_root or _MODULE_ENGINE_ROOT) / "scripts" / "email-intelligence.py"


PRODUCER_SCRIPT = producer_script()  # back-compat for existing importers
# --unread mode analyzes new/changed unread mail with Haiku; the cache
# keeps steady-state runs fast, but a fresh backlog can take a couple
# of minutes.
PRODUCER_TIMEOUT_S = 300  # cold WSL run benchmarked at ~122s (Exchange + Anthropic analysis); 300s = 2x headroom, fits within 5-min refresh interval (APScheduler max_instances=1 prevents overlap)


def read_email_state(data_root: "Path | None" = None) -> dict:
    """Read the email-intelligence state.json (DATA).

    HEADING OS engine/data split: state.json resolves under ``data_root``,
    which falls back to the ``get_data_root()`` seam when not supplied. The
    dead leading ``workspace_root`` went on 2026-08-24.

    Unreadable degrades to ``{"messages": []}`` and says WHICH file it dropped,
    at warning level in the daemon log. An empty return that names nothing is a
    zero-unread badge indistinguishable from a genuinely clear inbox.
    """
    if data_root is None:
        data_root = get_data_root()
    f = data_root / "outputs" / "operations" / "email-intelligence" / "state.json"
    if not f.exists():
        return {"messages": []}
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
    # OSError too: a permissions fault or a race with the writer used to
    # propagate out of a reader whose whole contract is "degrade to empty".
    # `UnicodeDecodeError` walked past BOTH names for the same reason it walks
    # past them everywhere: it is a `ValueError`, so a SIBLING of
    # `json.JSONDecodeError` and not an `OSError`. The decode happens inside
    # `read_text`, before `json.loads` is entered at all, so no JSON handler
    # can ever see it. MEASURED 2026-09-01 with one 0xe9 byte in state.json:
    # `UnicodeDecodeError: invalid continuation byte` raised out of the mail
    # refresher instead of the documented empty state.
    except (json.JSONDecodeError, OSError, UnicodeDecodeError) as exc:
        logger.warning("email state at %s is unreadable (%s); "
                       "reporting zero messages for this tick", f, exc)
        return {"messages": []}
    return data if isinstance(data, dict) else {"messages": []}

def count_unread(state: dict) -> int:
    """Unread messages in a state dict, however that dict is shaped.

    `read_email_state` above validates only the TOP level, so `messages` can be
    a list of strings, a dict (iterating one yields its keys), or a number. Each
    made `m.get` an AttributeError out of a reader family whose stated contract,
    three lines up, is "degrade to empty". The shape check stopped one level
    short of the value it actually reads.
    """
    messages = state.get("messages")
    if not isinstance(messages, list):
        return 0
    return sum(1 for m in messages if isinstance(m, dict) and m.get("unread"))

def _failure_detail(result: "subprocess.CompletedProcess") -> str:
    """Best available reason for a non-zero producer exit.

    The producer reports its one expected failure -- `exchange_unreachable`,
    the route to the configured Exchange host being down -- as a JSON object on
    STDOUT, then exits 2. Logging stderr alone therefore printed a bare exit
    code with the explanation sitting one stream away, which is what two
    2026-08-12 log lines looked like. Read stdout too, and prefer its
    structured error when there is one.
    """
    stdout = (result.stdout or "").strip()
    stderr = (result.stderr or "").strip()
    try:
        payload = json.loads(stdout)
    except (json.JSONDecodeError, TypeError):
        payload = None
    if isinstance(payload, dict) and payload.get("error"):
        parts = [f"error={payload['error']}"]
        for key in ("detail", "hint"):
            if payload.get(key):
                parts.append(f"{key}={str(payload[key])[:200]}")
        return "; ".join(parts)
    if stderr:
        return f"stderr={stderr[:500]}"
    if stdout:
        return f"stdout={stdout[:500]}"
    return "no output on either stream"


def refresh(workspace_root: Path, state_obj: "State") -> None:
    """Refresher callback: invoke `email-intelligence.py --unread`.

    --unread mode fetches the live Inbox unread set from Exchange,
    analyzes new/changed conversations, and writes _latest-fetch.json -
    the dashboard's feed. The dashboard therefore mirrors the CEO's
    actual inbox: anything read or deleted in Outlook drops off here.

    Failure modes are caught and logged, never raised - a daemon
    scheduler must not crash on transient Exchange errors. The version
    counter advances either way so the browser re-reads; the freshness
    clock advances only on a run that actually fetched.
    """
    script = producer_script(workspace_root)
    if not script.exists():
        logging.warning(
            "bridge.email: producer script missing at %s; skipping fetch",
            script,
        )
        state_obj.bump("inbox", fresh=False)
        return

    fetched = False
    try:
        result = subprocess.run(
            [sys.executable, str(script), "--unread"],
            cwd=str(workspace_root),
            capture_output=True,
            text=True,
            timeout=PRODUCER_TIMEOUT_S,
            check=False,
        )
        if result.returncode != 0:
            logging.warning(
                "bridge.email: producer exited %d; %s",
                result.returncode,
                _failure_detail(result),
            )
        else:
            fetched = True
            logging.info("bridge.email: producer ok")
    except subprocess.TimeoutExpired:
        logging.warning(
            "bridge.email: producer timed out after %ds", PRODUCER_TIMEOUT_S,
        )
    except OSError as e:
        logging.warning("bridge.email: subprocess failed: %s", e)

    # Version always, data_time only when the fetch happened: a failed run has
    # established nothing about how old the inbox on screen is.
    state_obj.bump("inbox", fresh=fetched)
