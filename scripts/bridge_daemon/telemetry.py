"""Adoption telemetry: append-only JSONL of usage events.

Phase 1 collects four event types feeding the CEO-side aggregator (Task 26)
that computes tab-time, action-click rate, browser-first mornings, and the
CEO subjective verdict.

## Allowed events + per-event fields

| name                | fields                                   |
|---------------------|------------------------------------------|
| page_view           | page (str), duration_s (int or None)     |
| launch              | action (str)                             |
| return_to_browser   | session_id (str), target (str)           |
| finalize            | action (str), artifact_id (str)          |

Every record additionally carries:
- ts (ISO-8601 UTC, set by Telemetry.event)
- event (the name argument)

Callers MUST NOT pass arbitrary kwargs. Phase 2 may tighten the schema
into Literal-typed methods; for now this docstring is the contract.

## Resolved hardening

- Disk-full hardening (2026-05-20): event() swallows OSError and logs a
  warning. A failed telemetry write must not propagate as HTTP 500 to
  the browser; missing one usage event is preferable to breaking the
  user-visible action that triggered it.

## Phase 2 TODOs

- Multi-process safety: the per-instance threading.Lock does not survive
  fork or multiple uvicorn workers. If the daemon ever runs --workers > 1,
  JSONL will corrupt. Use a file-lock (portalocker) or central writer.
- Rotation: usage.jsonl grows unboundedly. Add daily rotation to
  .daemon-state/usage-YYYY-MM-DD.jsonl.
"""
import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path


class Telemetry:
    def __init__(self, workspace_root: Path):
        self.path = workspace_root / ".daemon-state" / "usage.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def event(self, name: str, **kwargs) -> None:
        """Append one event line. Swallows OSError so a full disk or read-only
        FS does not propagate as HTTP 500 to the caller that triggered the
        event. The Phase J error tracker (scripts/bridge_daemon/error_tracker)
        picks up the warning and surfaces it in the next heartbeat."""
        rec = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": name,
            **kwargs,
        }
        line = json.dumps(rec) + "\n"
        with self._lock:
            try:
                with self.path.open("a", encoding="utf-8") as f:
                    f.write(line)
            except OSError as e:
                logging.warning("telemetry write failed (event=%s): %s", name, e)
