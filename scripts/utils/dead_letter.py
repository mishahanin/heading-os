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


_RESERVE_ATTEMPTS = 1000


def _reserve_unique_path(directory: Path, stem: str) -> Path:
    """Reserve `<stem>.json`, or `<stem>__N.json` when that name is taken.

    The name is claimed with ``O_CREAT | O_EXCL``, which is atomic on POSIX and
    on Windows, so two processes racing on the same stem get different names
    rather than one overwriting the other. The reserved file is left EMPTY;
    `_atomic_write` then replaces it, so the content is still written whole or
    not at all.

    A plain `path.exists()` probe would have been check-then-act, and the two
    callers here are short-lived separate processes - exactly the shape that
    loses the race. After `_RESERVE_ATTEMPTS` the last candidate is returned
    unreserved rather than looping: at that point something else is wrong, and a
    dead-letter write that overwrites is still better than one that hangs.
    """
    directory.mkdir(parents=True, exist_ok=True)
    for attempt in range(1, _RESERVE_ATTEMPTS + 1):
        suffix = "" if attempt == 1 else f"__{attempt}"
        candidate = directory / f"{stem}{suffix}.json"
        try:
            fd = os.open(str(candidate), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            continue
        os.close(fd)
        return candidate
    return directory / f"{stem}__{_RESERVE_ATTEMPTS}.json"


def _serialize(entry: dict) -> str:
    """JSON for one dead-letter entry, keeping the entry when the payload cannot.

    `default=str` handles the ordinary un-encodable values (Path, datetime,
    Exception). A payload that still refuses - a circular reference - costs the
    payload, never the record: the classification and the error are what the
    operator needs to decide on a retry, and losing them because one nested
    value was odd is the worse outcome.
    """
    try:
        return json.dumps(entry, indent=2, default=str) + "\n"
    except (TypeError, ValueError) as exc:
        salvaged = dict(entry)
        salvaged["payload"] = None
        salvaged["payload_error"] = f"{type(exc).__name__}: {exc}"
        return json.dumps(salvaged, indent=2, default=str) + "\n"


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

    The entry filename is ``<trace_id>__<kind>.json``, and a NUMBERED suffix is
    added when that name is taken: ``<trace_id>__<kind>__2.json`` and so on.

    Without the suffix the second record silently replaced the first. A trace_id
    identifies a PROCESS TREE, not a card - ``append_cards`` stamps every card
    it deposits with ``tracing.get()``, so one deposit gives every card the same
    id - and ``kind`` is the action_type, which repeats by design. Two
    permanently-failed ``email_send`` cards from one deposit therefore collided
    on one filename, ``os.replace`` clobbered the first, and ``record`` returned
    a path either way, so both callers printed "a durable record is at ...". One
    of the two records did not exist. Both callers reach here holding a failure
    they cannot otherwise report; losing half of them is the worst direction.
    Callers passing ``trace_id="-"`` (both do, when a card carries none) made the
    collision certain rather than likely.

    classification is coerced to a known value (unknown -> 'permanent', the safe
    default that forces re-approval rather than silent retry).
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
        path = _reserve_unique_path(dead_letter_dir(workspace_root), f"{tid}__{knd}")
        _atomic_write(path, _serialize(entry), mode=0o600)
        return path
    except (OSError, TypeError, ValueError) as e:
        # TypeError and ValueError joined OSError because the promise above is
        # "Never raises" and only OSError was caught. `json.dumps` raises
        # TypeError on a payload holding anything it cannot encode - a Path, a
        # datetime, an Exception, a set - and ValueError on a circular
        # reference. The callers are finalizers that are ALREADY handling a
        # failure, so the escape landed in the one place with nothing left to
        # catch it.
        _log.warning("dead-letter write failed for trace_id=%s kind=%s: %s", trace_id, kind, e)
        return None


def list_entries(*, workspace_root: Path | None = None) -> list[Path]:
    """Return the dead-letter entry paths, newest first by mtime.

    An entry that disappears between the glob and the stat is dropped, not
    raised over. The stat used to sit in a ``sorted`` key, so a file removed in
    that window raised ``FileNotFoundError`` out of a read path this module's
    docstring says must survive degraded conditions. The window is not
    theoretical: ``purge`` below calls this function BEFORE its own OSError
    guard, and ``scripts/dead-letter.py`` retries an entry and then deletes it,
    so two of those running together crashed the reader and ``purge`` with it.
    """
    directory = dead_letter_dir(workspace_root)
    dated: list[tuple[float, Path]] = []
    for path in directory.glob("*.json"):
        try:
            dated.append((path.stat().st_mtime, path))
        except OSError:
            continue
    dated.sort(key=lambda item: item[0], reverse=True)
    return [path for _, path in dated]


def load(path: Path) -> dict:
    """Load and parse a single dead-letter entry.

    Raises ValueError when the file is not an object. The annotation said dict
    and the body returned whatever `json.loads` gave it, so a truncated or
    hand-edited artifact holding `[]` or `null` reached `entry.get(...)` in
    `scripts/dead-letter.py` as an AttributeError, past handlers that catch only
    OSError and JSONDecodeError. `json.JSONDecodeError` is itself a ValueError,
    so a caller that widens its except clause to ValueError keeps catching both.
    """
    entry = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(entry, dict):
        raise ValueError(
            f"dead-letter entry {Path(path).name} is "
            f"{type(entry).__name__}, not an object"
        )
    return entry


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
