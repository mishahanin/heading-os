#!/usr/bin/env python3
"""One correct way to build a read-only SQLite `file:` URI.

Six call sites built theirs the same wrong way:

    sqlite3.connect(f"file:{path}?mode=ro", uri=True)

An f-string pastes the path into a URI without quoting it. A path containing
`?` starts the query string early, so everything after it is read as connection
parameters and the filename is truncated. A `#` starts a fragment, with the same
result. A space is not legal in a URI at all, and a Windows path (`C:\\Users\\...`)
has both a colon and backslashes where the URI grammar expects neither.

The failure is silent where it matters most. `.claude/hooks/memory-inject.py`
catches the connect error and calls `_emit("")`, so a data root under a
directory with a `?` in its name turns memory injection off permanently with no
diagnostic anywhere. Found by the 2026-08-23 audit.

`Path.as_uri()` does the quoting the standard library way, including the Windows
drive form, and it requires an absolute path, which is what these callers have.

Usage:

    from scripts.utils.sqlite_uri import read_only_uri
    conn = sqlite3.connect(read_only_uri(db_path), uri=True)
"""
from __future__ import annotations

from pathlib import Path

__all__ = ["read_only_uri"]


def read_only_uri(path: str | Path) -> str:
    """A `file:...?mode=ro` URI with the path properly quoted.

    The path is made absolute first: `as_uri()` refuses a relative path, and
    every caller here means a real file on this machine.
    """
    return f"{Path(path).absolute().as_uri()}?mode=ro"
