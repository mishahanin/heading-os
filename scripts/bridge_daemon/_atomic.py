"""Atomic state file writes.

Workspace global rule: state/PID files must use write-to-tmp + os.replace
to prevent partial reads on concurrent access or crash-during-write.
"""
import os
import tempfile
from pathlib import Path


def atomic_write_text(path: Path, content: str, mode: int = 0o600) -> None:
    """Atomically write text to path. Creates parent dirs as needed.

    Writes to a tempfile on the same filesystem (parent dir), sets the
    requested mode on the tempfile, then os.replace() onto the final path.
    On any failure, the tempfile is unlinked and the original exception
    is re-raised.

    Default mode is 0o600 (owner read/write only) - the right default for
    state files that may carry credentials. Pass mode=0o644 explicitly
    for non-sensitive state.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        # Set mode BEFORE the replace, so the final file lands with the
        # restrictive bits already applied (closes the chmod-after-write
        # race that briefly leaves the file world-readable).
        try:
            os.chmod(tmp, mode)
        except OSError:
            # On Windows os.chmod has limited effect; ignore the failure
            # rather than abort the write. POSIX honors it.
            pass
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
