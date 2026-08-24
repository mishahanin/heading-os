"""Heartbeat writer for fleet observability.

Per the bridge architecture spec section 3.7, each daemon writes a
`<workspace>/.daemon-state/heartbeat.json` file every 60 seconds. The
existing per-exec workspace sync mirrors the file to CEO-side, where
`scripts/daemon-fleet-health.py` (Phase 3) aggregates them into the
11-cell status grid.

The heartbeat carries:
- pid: process id
- version: daemon build version
- config_loaded_version: version of the merged config currently in
  memory (lets the fleet-health script flag execs running stale
  config after a `/push-updates`)
- uptime_s: seconds since this MODULE was imported (`_BOOT_TS = time.time()`
  runs at import), which is a close proxy for daemon boot and not the same
  thing -- a reader diagnosing a restart loop should know which it is
- last_heartbeat: ISO-8601 UTC of this write (used by the reader to
  detect a stale daemon - file mtime works too, but the embedded
  timestamp is canonical)
- last_error: last logged exception or None (best-effort)
- recent_error_count: errors logged in the last hour, read live from
  `error_tracker` (the logging filter that Phase 3 was going to add has
  shipped; this line claimed "currently always 0" long after the payload
  started calling `tracker.recent_count()`)
- active_sessions: count of Claude Code sessions currently
  registered by bridge-hook.py session-start

Phase 1 ships the writer; Phase 3 ships the reader (CEO-side
fleet-health.py).
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

from ._atomic import atomic_write_text
from .error_tracker import get_tracker
from .version import __version__ as _DAEMON_VERSION

HEARTBEAT_FILE = "heartbeat.json"
_BOOT_TS = time.time()


def _active_session_count(workspace_root: Path) -> int:
    """Read .daemon-state/active-sessions.json (written by bridge-hook.py
    session-start) and return the entry count. Returns 0 on any error
    so a broken sessions file doesn't take down the heartbeat."""
    path = workspace_root / ".daemon-state" / "active-sessions.json"
    if not path.exists():
        return 0
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 0
    if isinstance(data, dict):
        return len(data)
    if isinstance(data, list):
        return len(data)
    return 0


def write_heartbeat(workspace_root: Path, config_version: str | None = None) -> None:
    """Atomic-write the heartbeat file. Called every 60s by APScheduler.

    Silent on success; logs a warning (not an exception) on write failure
    so the scheduler keeps running and only the one heartbeat is lost.
    """
    path = workspace_root / ".daemon-state" / HEARTBEAT_FILE
    try:
        # Inside the guard. `mkdir` sat above the try until 2026-08-24, so a
        # read-only mount or a `.daemon-state` that is a file raised OSError
        # straight out of a function whose docstring promises the opposite.
        # Every 60-second tick then logged a full job traceback instead of the
        # one designed warning line.
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        logging.warning("heartbeat directory unavailable: %s", e)
        return
    now = datetime.now(timezone.utc).isoformat()
    # Phase J: read error tracker for last_error + recent_error_count.
    # The tracker is fed by the logging.Handler installed at boot, so it
    # reflects every WARNING+ record since the daemon started (up to a
    # rolling 1-hour window).
    tracker = get_tracker()
    payload = {
        "pid": _proc_pid(),
        "version": _DAEMON_VERSION,
        "config_loaded_version": config_version or "unversioned",
        "uptime_s": int(time.time() - _BOOT_TS),
        "last_heartbeat": now,
        "last_error": tracker.last_error(),
        "recent_error_count": tracker.recent_count(),
        "active_sessions": _active_session_count(workspace_root),
    }
    try:
        # 0600, matching the token and queue files in the same directory. The
        # payload embeds `last_error`, which is raw log text and can carry file
        # paths, conversation ids or mail subjects; 0644 published that to every
        # account on the box. Aggregators run as the same user.
        atomic_write_text(path, json.dumps(payload, indent=2) + "\n", mode=0o600)
    except OSError as e:
        logging.warning("heartbeat write failed: %s", e)


def _proc_pid() -> int:
    """os.getpid() wrapped so tests can monkeypatch it."""
    import os
    return os.getpid()
