#!/usr/bin/env python3
"""Shared atomic file-write helper for non-bridge scripts.

Usage:
    from scripts.utils.atomic import atomic_write_text
    atomic_write_text(path, content)             # default mode 0o644
    atomic_write_text(path, content, mode=0o600) # owner-only

Writes to a tempfile in the same directory as `path`, then os.replace()s it
into place. The tmp file is cleaned up on any error so no orphans are left.
The bridge daemon uses its own scripts/bridge_daemon/_atomic.py (same pattern,
default mode 0o600) — do NOT import this module from bridge code.
"""
import os
import tempfile
from pathlib import Path


def atomic_write_text(
    path: Path,
    text: str,
    *,
    mode: int = 0o644,
    encoding: str = "utf-8",
) -> None:
    """Write *text* to *path* atomically via a same-directory tempfile.

    Creates parent directories if they do not exist.
    Sets file permissions to *mode* (default 0o644; pass 0o600 for sensitive files).
    Cleans up the tempfile on any error — no orphans.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding=encoding) as fh:
            fh.write(text)
        try:
            os.chmod(tmp, mode)
        except OSError:
            pass
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
