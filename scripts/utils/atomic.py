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
import sys
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
        except OSError as exc:
            # Swallowed, and SAID. The swallow is deliberate: `mkstemp` creates
            # at 0o600, so a chmod that fails leaves the file NARROWER than
            # requested, never wider. Refusing the whole write over a
            # permission tightening that already holds would turn a cosmetic
            # failure into a lost record.
            #
            # The silence was not deliberate. `except OSError: pass` is the
            # shape the workspace security rule forbids outright ("log or
            # re-raise, never silently swallow"), and it is worse here than
            # usual: the one caller class that passes `mode` explicitly is the
            # one writing CREDENTIALS at 0o600, so a filesystem that refuses
            # chmod (a mounted share, a container volume) would leave the
            # requested mode unapplied with nothing anywhere saying so.
            #
            # Deliberately NOT raising, and deliberately not `logging`: this
            # module is imported by hooks that must not configure logging as a
            # side effect of an import. stderr is the one channel every caller
            # already has.
            print(f"[warn] atomic_write_text: could not set mode {mode:#o} on "
                  f"{path} (the file keeps mkstemp's 0o600, which is "
                  f"narrower): {exc}", file=sys.stderr)
        os.replace(tmp, path)
    except BaseException:
        # BaseException, not Exception. `KeyboardInterrupt` and `SystemExit` do
        # not derive from `Exception`, so a Ctrl-C or an interpreter shutdown
        # landing between the mkstemp and the replace left the scratch file
        # behind beside the target, named `tmpXXXXXXXX` and owned by nothing.
        # The three sibling copies of this helper (`scripts/bridge_daemon/
        # _atomic.py`, `scripts/utils/crm_autolog.atomic_write`, and the
        # eval-viewer's private copy) already caught `BaseException` for this
        # exact reason; this copy, the one with the most callers of the four,
        # kept the narrower clause until 2026-09-01. The target file is
        # untouched either way (the `os.replace` is what makes the write
        # visible), so this is about the orphan, not about corruption.
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
