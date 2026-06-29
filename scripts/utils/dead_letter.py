"""Dead-letter queue writer for the Action Queue spine (R14).

A failed finalizer (a permanent send failure, an exhausted transient retry)
becomes a durable, trace-keyed JSON artifact under
``outputs/operations/dead-letter/`` instead of vanishing. The artifacts are
inert files - the dead-letter CLI (``scripts/dead-letter.py``) reads, retries,
and purges them directly, so recovery works with the bridge daemon down. This
is the one place a direct-file path is correct: these are not the live
single-writer queue, just recoverable records.

Each entry is named ``<trace_id>__<kind>.json`` and is written with mode 0o600
(it may carry a recipient address or message body). Writes are atomic
(tmp + os.replace) and never raise - a DLQ write must not take down the caller.

Classification is one of ``transient`` (timeout / connection blip, retryable)
or ``permanent`` (bad recipient / empty body, needs re-approval).

The module is dependency-free on the bridge package by design: non-bridge
daemons and CLIs import it without pulling in FastAPI. It uses a small local
atomic write rather than ``scripts.bridge_daemon._atomic``.

Usage::

    from scripts.utils import dead_letter

    dead_letter.record(
        trace_id="abc123",
        kind="email_send",
        payload={"to": "x@y.com", "subject": "..."},
        classification="permanent",
        error="empty recipient",
    )
    for path in dead_letter.list_entries():
        entry = dead_letter.load(path)
    dead_letter.purge(older_than_days=90)

    delay = dead_letter.backoff_schedule(attempt=2)  # jittered seconds
"""
from __future__ import annotations

import json
import logging
import os
import random
import re
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from scripts.utils.workspace import get_outputs_dir

CLASSIFICATIONS = ("transient", "permanent")

_SAFE_SEGMENT = re.compile(r"[^A-Za-z0-9._-]")

_log = logging.getLogger(__name__)


def dead_letter_dir(workspace_root: Path | None = None) -> Path:
    """Return the dead-letter directory PATH (does not create it).

    The read paths (``list_entries`` / ``purge``) must work even when the
    directory has never been created and - on a restrictive mount - cannot be
    created; ``Path.glob`` over a missing directory simply yields nothing. The
    write path (``record`` -> ``_atomic_write``) creates the parent on demand,
    inside a try/except, so a write to a missing dir degrades to ``None`` rather
    than crashing the caller.

    Resolves under the DATA root via ``get_outputs_dir()`` (data-root seam), so
    dead-letter artifacts never land in the engine clone. ``workspace_root`` is a
    test-injection seam: when given, the outputs tree is rooted there instead so
    a test never touches the real ``outputs/`` tree.
    """
    if workspace_root is not None:
        outputs = workspace_root.joinpath("outputs")  # test-injection seam (not the engine root)
    else:
        outputs = get_outputs_dir()
    return outputs / "operations" / "dead-letter"


def _sanitize(segment: str) -> str:
    """Reduce a filename segment to a safe slug. Empty input -> 'unknown'."""
    cleaned = _SAFE_SEGMENT.sub("-", str(segment)).strip("-")
    return cleaned or "unknown"


def _atomic_write(path: Path, content: str, mode: int = 0o600) -> None:
    """Atomically write text to path with the given mode. Creates parents."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        try:
            os.chmod(tmp, mode)
        except OSError:
            # Windows os.chmod has limited effect; POSIX honours it.
            pass
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def record(
    trace_id: str,
    kind: str,
    payload: dict,
    classification: str,
    error: str,
    *,
    workspace_root: Path | None = None,
) -> Path | None:
    """Write a classified dead-letter entry keyed by trace_id.

    Returns the written path, or ``None`` if the write failed. Never raises -
    a finalizer that already failed must not be made worse by a DLQ write that
    throws.

    The entry filename is ``<trace_id>__<kind>.json``. classification is
    coerced to a known value (unknown -> 'permanent', the safe default that
    forces re-approval rather than silent retry).
    """
    if classification not in CLASSIFICATIONS:
        classification = "permanent"
    tid = _sanitize(trace_id)
    knd = _sanitize(kind)
    entry = {
        "trace_id": trace_id,
        "kind": kind,
        "classification": classification,
        "error": error,
        "payload": payload,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        path = dead_letter_dir(workspace_root) / f"{tid}__{knd}.json"
        _atomic_write(path, json.dumps(entry, indent=2) + "\n", mode=0o600)
        return path
    except OSError as e:
        _log.warning("dead-letter write failed for trace_id=%s kind=%s: %s", trace_id, kind, e)
        return None


def list_entries(*, workspace_root: Path | None = None) -> list[Path]:
    """Return the dead-letter entry paths, newest first by mtime."""
    directory = dead_letter_dir(workspace_root)
    paths = sorted(
        directory.glob("*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return paths


def load(path: Path) -> dict:
    """Load and parse a single dead-letter entry."""
    return json.loads(Path(path).read_text(encoding="utf-8"))


def purge(older_than_days: int = 90, *, workspace_root: Path | None = None) -> int:
    """Delete dead-letter entries older than the cutoff. Returns count removed.

    Age is measured against the entry file mtime. An entry exactly at the
    cutoff is kept; only strictly older entries are removed.
    """
    cutoff = time.time() - older_than_days * 86400
    removed = 0
    for path in list_entries(workspace_root=workspace_root):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
                removed += 1
        except OSError as e:
            _log.warning("dead-letter purge failed for %s: %s", path, e)
    return removed


def backoff_schedule(
    attempt: int,
    *,
    base: float = 60.0,
    factor: float = 2.0,
    cap: float = 1800.0,
    rng: random.Random | None = None,
) -> float:
    """Return a full-jitter backoff delay in seconds for a retry attempt.

    Full jitter (AWS "Exponential Backoff and Jitter"): the delay is a random
    value in ``[0, min(cap, base * factor ** attempt)]``. The computed ceiling
    is monotonic non-decreasing in ``attempt`` (more attempts never lower the
    ceiling) and capped at ``cap``.

    ``attempt`` is 0-based (attempt 0 is the first retry). Deterministic when a
    seeded ``random.Random`` is injected, so tests can assert the bounds.
    """
    if attempt < 0:
        attempt = 0
    ceiling = min(cap, base * (factor ** attempt))
    if ceiling < 0:
        ceiling = 0.0
    source = rng if rng is not None else random
    return source.uniform(0.0, ceiling)
