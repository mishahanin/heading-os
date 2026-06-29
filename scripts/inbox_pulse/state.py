#!/usr/bin/env python3
"""State file helpers for the Inbox Pulse daemon.

All helpers operate relative to get_state_dir() from paths.py.  They are
deliberately minimal: append-only JSONL logging, generic JSON state load/save
with atomic writes, and a daemon heartbeat writer.

Atomic write pattern
--------------------
save_state() uses tempfile.mkstemp() in the same directory as the target,
writes and fsyncs to the temp file, then os.replace()s it onto the target.
This satisfies the workspace security policy: "Non-atomic writes to state/PID
files (must use write-to-tmp + os.replace())" is listed as a forbidden pattern.

Usage::

    from scripts.inbox_pulse.state import (
        append_jsonl,
        load_state,
        save_state,
        write_heartbeat,
    )

    append_jsonl("events.jsonl", {"action": "email_received", "uid": 42})
    cfg = load_state("config.json", default={})
    save_state("config.json", {"key": "value"})
    write_heartbeat(extra={"queue_depth": 3})
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime, timezone, timedelta
from typing import Any

from scripts.inbox_pulse.paths import get_state_dir
from scripts.utils.workspace import get_default_tz, get_default_tz_name

__all__ = [
    "append_jsonl",
    "load_state",
    "save_state",
    "write_heartbeat",
]

logger = logging.getLogger(__name__)

# local timezone (UTC+4, no DST)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def append_jsonl(filename: str, event: dict) -> None:
    """Append an event dict as a single JSON line to <state_dir>/<filename>.

    The line is written, flushed, and fsynced so a crash mid-write cannot
    corrupt prior lines.  Each call produces exactly one line terminated
    with a newline character.

    Args:
        filename: Basename of the JSONL file (e.g. "events.jsonl").
        event:    Dict to serialise as a single JSON line.
    """
    path = get_state_dir() / filename
    line = json.dumps(event, ensure_ascii=False)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")
        fh.flush()
        os.fsync(fh.fileno())


def load_state(filename: str, default: Any = None) -> Any:
    """Load a JSON state file from <state_dir>/<filename>.

    Returns `default` when the file is missing or empty.
    Raises json.JSONDecodeError when the file exists but contains invalid
    JSON (loud failure -- the caller decides how to handle corruption).

    Args:
        filename: Basename of the JSON file (e.g. "state.json").
        default:  Value returned when the file is absent or empty.
    """
    path = get_state_dir() / filename
    if not path.exists():
        return default
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return default
    # Raises json.JSONDecodeError on corrupted content -- intentional.
    return json.loads(text)


def save_state(filename: str, data: Any) -> None:
    """Atomic write of `data` (JSON-serialised) to <state_dir>/<filename>.

    Pattern: tempfile.mkstemp in the same directory, write + flush + fsync,
    then os.replace onto the target path.  A crash at any point leaves the
    previous file intact (os.replace is atomic on POSIX; near-atomic on
    Windows via MoveFileEx).

    Args:
        filename: Basename of the JSON file (e.g. "state.json").
        data:     JSON-serialisable object to persist.
    """
    path = get_state_dir() / filename
    tmp_fd, tmp_name = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def write_heartbeat(extra: dict | None = None) -> None:
    """Write the daemon heartbeat to <state_dir>/state.json.

    Default fields written:
      - last_heartbeat: ISO-8601 datetime in the configured timezone timezone (UTC+4)
      - daemon_pid:     os.getpid()
      - queue_depth:    int, taken from extra["queue_depth"] if present, else 0

    The `extra` dict is merged on top of the defaults, so callers can add
    fields such as "last_email_processed_at" without losing the base fields.
    Written atomically via save_state().

    Args:
        extra: Optional dict merged on top of the base heartbeat fields.
    """
    now_local = datetime.now(tz=get_default_tz())
    payload: dict = {
        "last_heartbeat": now_local.isoformat(),
        "daemon_pid": os.getpid(),
        "queue_depth": 0,
    }
    if extra:
        payload.update(extra)
    save_state("state.json", payload)
