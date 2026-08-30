#!/usr/bin/env python3
"""One `rmtree` that clears the read-only bit, on every supported Python.

Two scripts each carried a private copy of the same three-line handler and each
passed it as `onexc=`. That keyword landed in Python 3.12; `pyproject.toml` pins
`requires-python = ">=3.11"` and the workspace itself runs 3.11.15, so every one
of those calls raised

    TypeError: rmtree() got an unexpected keyword argument 'onexc'

`publish-service` hit it on any re-publish where a directory already existed,
and `pull-service-state` hit it on the first mirror that had ever been pulled.
The two handlers were identical, so the duplication also meant a fix to one
would not have reached the other.

`onerror` still works on 3.12+ (it warns), and `onexc` does not exist below it,
so the version check picks the keyword rather than guessing.
"""

from __future__ import annotations

import os
import shutil
import stat
import sys
from pathlib import Path

# `onexc` was added in 3.12; `onerror` is deprecated there but still honoured.
_HAS_ONEXC = sys.version_info >= (3, 12)


def _clear_readonly(func, path, _exc_or_info):
    """Clear what is blocking the removal and retry once.

    The third parameter differs between the two hooks (`onerror` passes an
    exc_info triple, `onexc` passes the exception), and neither copy of this
    handler ever read it, so one signature serves both.

    Two things were wrong with the three-line original, and both showed on the
    same run (Linux, Python 3.11, a directory at mode 000 holding one file;
    measured 2026-08-26):

    1. `os.chmod(path, stat.S_IWRITE)` REPLACES the mode with 0o200. On POSIX a
       directory also needs read and execute to be listed or entered, so the
       chmod that was meant to unblock the removal left the directory less
       usable than it found it: the measured mode afterwards was 0o200. The
       mode bits are now ADDED to what is already there.
    2. `func(path)` assumes `func` takes exactly one argument. `shutil.rmtree`
       also passes `os.open`, `os.scandir` and `os.listdir` here, and retrying
       `os.open(path)` raises `TypeError: open() missing required argument
       'flags'`. TypeError is not caught by either caller
       (`scripts/pull-service-state.py`, `scripts/publish-service.py`), so the
       tree was left in place and the exception escaped. After the chmod the
       directory IS traversable, so the retry restarts the walk instead.
    """
    target = Path(path)
    try:
        mode = os.lstat(target).st_mode
    except OSError:
        return  # already gone; nothing to unblock
    extra = stat.S_IWRITE
    if stat.S_ISDIR(mode):
        extra |= stat.S_IRUSR | stat.S_IXUSR
    try:
        os.chmod(target, stat.S_IMODE(mode) | extra)
    except OSError:
        raise  # cannot unblock it; the caller must see the real failure
    try:
        func(path)
    except TypeError:
        # `func` was a directory reader, not a remover. Redo the walk now that
        # the directory can be entered; each pass strictly relaxes permissions,
        # so this cannot loop forever.
        if target.is_dir() and not target.is_symlink():
            _rmtree(target)
        else:
            os.unlink(target)


def _rmtree(target: Path) -> None:
    """One `shutil.rmtree` with whichever error hook this Python supports."""
    if _HAS_ONEXC:
        shutil.rmtree(target, onexc=_clear_readonly)
    else:
        shutil.rmtree(target, onerror=_clear_readonly)


def rmtree_force(path: Path | str, *, missing_ok: bool = True) -> None:
    """Remove a tree, retrying once past a read-only file or directory.

    `missing_ok` mirrors `Path.unlink`: an absent path is not an error.

    A BROKEN symlink is not absent. `Path.exists()` follows the link, so it
    answers False for one, and the function returned having removed nothing and
    raised nothing - the caller was left holding the exact entry it asked to
    delete. `Path.unlink(missing_ok=True)`, the semantics this docstring claims
    to mirror, removes the link itself, because unlink operates on the path and
    not on its target. MEASURED 2026-08-30: after `os.symlink("/nonexistent",
    link); rmtree_force(link)`, the link was still on disk.
    """
    target = Path(path)
    if target.is_symlink() and not target.exists():
        target.unlink()
        return
    if missing_ok and not target.exists():
        return
    _rmtree(target)
