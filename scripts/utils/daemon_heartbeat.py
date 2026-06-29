"""Per-daemon liveness heartbeat (R14).

Each daemon emits a *liveness* beat on a fast tick (~60s, decoupled from its
work cadence) into ``<workspace>/.daemon-state/heartbeats/<daemon_name>.json``.
One file per daemon means five processes never race on a shared file, and the
watchdog (``scripts/daemon-watchdog.py``) sees each daemon independently. The
bridge daemon keeps its richer ``heartbeat.json`` for fleet-health back-compat
and additionally beats here.

Dependency-free by design: this util does its OWN small atomic write (write a
tempfile, then ``os.replace``) rather than importing the ``bridge_daemon``
package, so non-bridge daemons (fireside, sync-exchange, eval-drift, sentinel)
can call it without pulling in the bridge stack.

Fields written:
- ``daemon``: the daemon name (also the filename stem)
- ``pid``: process id
- ``version``: caller-supplied build version, or "unversioned"
- ``config_loaded_version``: version of the merged config in memory, or "unversioned"
- ``uptime_s``: seconds since this process imported the module (per-process boot ts)
- ``last_heartbeat``: ISO-8601 UTC of this write
- ``trace_id``: the process-tree trace ID (``scripts.utils.trace.get()``), or None

Never raises: a write failure logs a warning so the scheduler keeps ticking and
only the one beat is lost.

Usage::

    from scripts.utils import daemon_heartbeat
    daemon_heartbeat.beat("sync-exchange", config_version="3")
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from scripts.utils import trace
from scripts.utils.workspace import get_workspace_root

# Per-process boot timestamp, captured once at import. uptime_s is measured
# against this so a fresh import means a fresh uptime baseline.
_BOOT_TS = time.time()

HEARTBEATS_DIR = "heartbeats"


def _atomic_write_json(path: Path, payload: dict) -> None:
    """Atomically write JSON to path (own implementation, no bridge import).

    Writes to a tempfile on the same filesystem, then os.replace() onto the
    final path. The tempfile is unlinked on any failure before re-raising.
    Liveness beats carry no credentials, so mode 0o644 is the right default.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(payload, indent=2) + "\n"
    fd, tmp = tempfile.mkstemp(dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        try:
            os.chmod(tmp, 0o644)
        except OSError:
            # Windows os.chmod has limited effect; POSIX honors it.
            pass
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def beat(daemon_name: str, *, config_version: str | None = None) -> None:
    """Write a per-daemon liveness heartbeat. Never raises.

    Args:
        daemon_name: the daemon's name; also the filename stem
            (``.daemon-state/heartbeats/<daemon_name>.json``).
        config_version: version of the merged config currently in memory, if
            the caller tracks one. Defaults to "unversioned".
    """
    try:
        path = get_workspace_root() / ".daemon-state" / HEARTBEATS_DIR / f"{daemon_name}.json"
        payload = {
            "daemon": daemon_name,
            "pid": os.getpid(),
            "version": config_version if config_version is not None else "unversioned",
            "config_loaded_version": config_version if config_version is not None else "unversioned",
            "uptime_s": int(time.time() - _BOOT_TS),
            "last_heartbeat": datetime.now(timezone.utc).isoformat(),
            "trace_id": trace.get(),
        }
        _atomic_write_json(path, payload)
    except OSError as e:
        logging.warning("daemon heartbeat write failed for %s: %s", daemon_name, e)
