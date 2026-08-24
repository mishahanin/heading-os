"""One JSONL log primitive for the bridge daemon's append-only logs.

Six source modules kept an append-only JSONL log -- critical items, sent
approvals, dismissed inbox rows, done tasks, pipeline touches, investor sends --
and all six spelled the write the same way, eleven times over::

    with _LOCK:
        existing = log_path.read_text(...)      # read the WHOLE file
        new_content = existing + json.dumps(entry) + "\\n"
        atomic_write_text(log_path, new_content)

That is a read-modify-rewrite, not an append, and the distinction is the bug.
``atomic_write_text`` prevents a torn file; it does nothing about a lost update.
Two processes that read the same pre-write state each rewrite the whole log and
the second one wins, so the first one's entry is gone with no error anywhere.
``threading.Lock`` does not help: it serialises threads inside ONE interpreter.
``critical.py``'s module docstring claimed "JSONL append + atomic write so
concurrent writers don't corrupt the file", which is true of corruption and
false of the thing an operator actually loses.

Cost also grew with the file: marking the 5,000th critical item read and
rewrote 5,000 lines.

``append_jsonl`` below is a real ``O_APPEND`` write of one line. POSIX makes the
seek-to-end and the write a single operation for a file opened that way, so a
second process cannot land inside the first one's line and cannot overwrite it.

``read_jsonl_capped`` is the read half. Every one of those logs guarded itself
with ``if size > MAX_BYTES: return []`` -- which shows the operator an EMPTY
page while the writers keep appending, with no error, no rotation and no way
back. For the /critical page, whose whole content is what the operator flagged,
rendering empty is the worst available outcome. The capped reader keeps the
TAIL instead: the most recent entries, which is what every one of these pages
displays, and reports that it truncated so the caller can say so.

Found by the 2026-08-23 engine audit (finding 15, and finding 14 for the cap).
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)


def append_jsonl(path: Path, entry: dict, mode: int = 0o644) -> None:
    """Append one JSON object as a line. Raises OSError on failure.

    Opened ``O_APPEND``, so the offset lookup and the write are one atomic
    operation and a concurrent writer in another PROCESS cannot lose the entry
    (a ``threading.Lock`` only covers the current one).

    A file whose last byte is not a newline -- a hand-edit, or a crash mid-line
    under the old rewrite path -- gets one inserted first, so the repaired line
    stays parseable rather than being glued to this entry.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(entry) + "\n"
    existed = path.exists()
    needs_newline = False
    if existed:
        try:
            size = path.stat().st_size
            if size:
                with path.open("rb") as f:
                    f.seek(-1, os.SEEK_END)
                    needs_newline = f.read(1) != b"\n"
        except OSError:
            # Unreadable tail: append anyway rather than refuse the write. A
            # stray glued line is recoverable; a dropped critical mark is not.
            logger.warning("could not inspect the tail of %s", path, exc_info=True)
    if not existed:
        # Create it EMPTY, at the requested mode, before a byte goes in.
        # `open("a")` creates at the process umask -- commonly 0o644 -- and the
        # chmod below landed only AFTER the first record was written, so a
        # caller asking for 0o600 got a window in which the file was
        # world-readable and already held content. `_atomic.py`, in this same
        # package, carries a comment about closing exactly this race by
        # chmodding before `os.replace`; this function had reopened it.
        #
        # The chmod after stays. `os.open` masks the mode with the umask, so
        # under a strict umask a requested 0o644 would come out 0o600; the
        # chmod restores the caller's intent. That direction is safe -- briefly
        # too NARROW, never too wide.
        try:
            os.close(os.open(path, os.O_CREAT | os.O_WRONLY | os.O_APPEND, mode))
        except OSError:  # pragma: no cover - the append below reports the real failure
            logger.warning("could not pre-create %s at mode %o", path, mode,
                           exc_info=True)
    with path.open("a", encoding="utf-8") as f:
        if needs_newline:
            f.write("\n")
        f.write(line)
    if not existed:
        try:
            os.chmod(path, mode)
        except OSError:  # pragma: no cover - Windows chmod is a partial no-op
            logger.warning("could not set mode %o on %s", mode, path, exc_info=True)


def read_jsonl_capped(path: Path, max_bytes: int) -> tuple[list[dict], bool]:
    """Return ``(entries, truncated)`` for an append-only JSONL log.

    Reads at most ``max_bytes`` from the END of the file. Over the cap the
    leading partial line is dropped and ``truncated`` is True -- the caller
    shows the recent entries instead of the old ``return []``, which showed
    nothing and said nothing.

    **Truncation is logged here, by this function.** All nine call sites bind
    the flag to ``_truncated`` and drop it, so the docstring's older promise
    that "the caller can label the page degraded" described an intention, not
    the code. On the sent-log and dismiss-log readers a dropped head means an
    old entry falls out of the active set and its item RE-SURFACES as if it had
    never been actioned, which is the one outcome those logs exist to prevent.
    A warning in the daemon log is not a UI badge, but it is the difference
    between a degradation someone can find and one nobody can.

    A missing file is ``([], False)``: genuinely empty, not truncated.
    Unparseable lines are skipped; non-dict JSON values are skipped.
    """
    if not path.exists():
        return [], False
    try:
        size = path.stat().st_size
        truncated = size > max_bytes
        with path.open("rb") as f:
            if truncated:
                f.seek(size - max_bytes)
            blob = f.read()
    except OSError:
        logger.warning("could not read %s", path, exc_info=True)
        return [], False
    text = blob.decode("utf-8", errors="replace")
    lines = text.splitlines()
    if truncated and lines:
        # The first line almost certainly starts mid-record.
        lines = lines[1:]
        logger.warning(
            "%s is %d bytes, over the %d cap: read the newest %d only. Entries "
            "older than that are NOT in the returned set, so anything recorded "
            "there reads as never-actioned. Compact or rotate this log.",
            path, size, max_bytes, max_bytes,
        )
    out: list[dict] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(entry, dict):
            out.append(entry)
    return out, truncated
