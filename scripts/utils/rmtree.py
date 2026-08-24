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
    """Windows leaves the read-only bit set; clear it and retry once.

    The third parameter differs between the two hooks (`onerror` passes an
    exc_info triple, `onexc` passes the exception), and neither copy of this
    handler ever read it, so one signature serves both.
    """
    os.chmod(path, stat.S_IWRITE)
    func(path)


def rmtree_force(path: Path | str, *, missing_ok: bool = True) -> None:
    """Remove a tree, retrying once past a read-only file.

    `missing_ok` mirrors `Path.unlink`: an absent path is not an error.
    """
    target = Path(path)
    if missing_ok and not target.exists():
        return
    if _HAS_ONEXC:
        shutil.rmtree(target, onexc=_clear_readonly)
    else:
        shutil.rmtree(target, onerror=_clear_readonly)
