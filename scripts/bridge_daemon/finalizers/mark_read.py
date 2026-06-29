"""Mark-read finalizer for the bridge Inbox.

Subprocesses `email-intelligence.py --mark-read` / `--mark-unread` to
flip a conversation's is_read state in Exchange, so the dashboard's
'Done' action stays in sync with Outlook. Invoked from /inbox/dismiss
(mark read) and /inbox/undo-dismiss (mark unread).

Subprocessed rather than called in-process for the same reason the
email refresher subprocesses the producer: the Exchange connection is
heavy and must not run inside the daemon event loop.
"""
import json
import subprocess
import sys
from pathlib import Path

# Marking a conversation read is a single EWS round-trip per message;
# 60s is generous headroom over a connect + a handful of saves.
MARK_READ_TIMEOUT_S = 60


def mark_conversation_read(workspace_root: Path, conv_id: str, mark_read: bool) -> dict:
    """Flip is_read on a conversation in Exchange via email-intelligence.py.

    Returns the producer's JSON result: {ok: True, messages_changed: N}
    on success, or {ok: False, error: "..."} on any failure.
    """
    if not isinstance(conv_id, str) or not conv_id.strip():
        return {"ok": False, "error": "conv_id is required"}
    if len(conv_id) > 500:
        return {"ok": False, "error": "conv_id too long"}
    script = workspace_root / "scripts" / "email-intelligence.py"
    if not script.exists():
        return {"ok": False, "error": "email-intelligence.py not found"}

    flag = "--mark-read" if mark_read else "--mark-unread"
    try:
        result = subprocess.run(
            [sys.executable, str(script), flag, conv_id.strip()],
            cwd=str(workspace_root),
            capture_output=True,
            text=True,
            timeout=MARK_READ_TIMEOUT_S,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "Exchange mark-read timed out"}
    except OSError as e:
        return {"ok": False, "error": f"subprocess failed: {e}"}

    # The producer prints a single JSON result line to stdout. Scan from
    # the last line back so any incidental output before it is ignored.
    for line in reversed((result.stdout or "").strip().splitlines()):
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return {"ok": False, "error": f"no result from mark-read (exit {result.returncode})"}
