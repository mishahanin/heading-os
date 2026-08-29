#!/usr/bin/env python3
"""One lexical path normaliser, shared by every guard that reads a path.

A guard that asks "does this argument SPELL the forbidden directory" answers
about the spelling, not about the file. Writing `T/P/x.md` (where `T` is the
threads root and `P` its CEO-only subdirectory), `T/./P/x.md`, `T//P/x.md` and
`T/business/../P/x.md` all open the same bytes, and until 2026-08-29 the
personal-threads wall in `.claude/hooks/_dispatch.py` refused the first and
allowed the other three, for `Read`, for every Bash reader, and for the
write-content leak check. Measured: 4 of 9 spellings that name one CEO-only file
went through.

The collapse itself was already written and already correct. `data-path-redirect.py`
carried a private `_normalize_rel` fixed on 2026-08-23 for exactly this class,
after a `..`-climbing path was found being concatenated onto the data root. The
fix landed in one of the two hooks that needed it, which is the shape this
repository keeps producing, so the collapse now lives here once and both hooks
import it.

Lexical, never `Path.resolve()`. Two reasons, both load-bearing:

* these guards run BEFORE a Write, so the target usually does not exist yet and
  `resolve()` has nothing to resolve;
* `resolve()` follows symlinks, and a guard that follows a symlink is a guard
  whose answer depends on a filesystem an attacker may control. This workspace
  bans symlinks outright, so the guards must not silently depend on that ban
  holding.
"""
from __future__ import annotations


def normalize_segments(path: str) -> list[str]:
    """Path segments with `\\` folded to `/`, and `''`, `.` and `..` collapsed.

    An absolute path keeps a leading empty segment, so joining the result with
    `/` round-trips the leading slash. A `..` with nothing left to pop is
    DROPPED rather than kept: for a guard, a climbing path that lands on some
    ancestor's copy of the forbidden directory is still a path into it, and
    keeping the `..` would leave the segment list unmatchable.
    """
    text = path.replace("\\", "/")
    absolute = text.startswith("/")
    parts: list[str] = []
    for segment in text.split("/"):
        if segment in ("", "."):
            continue
        if segment == "..":
            if parts:
                parts.pop()
            continue
        parts.append(segment)
    return ([""] + parts) if absolute else parts


def normalize_path(path: str) -> str:
    """`normalize_segments` re-joined. `''` for a path that collapses to nothing."""
    segments = normalize_segments(path)
    if segments == [""]:
        return "/"
    return "/".join(segments)


def normalize_rel(path: str) -> str | None:
    """A RELATIVE path collapsed, or None if absolute, empty, or escaping.

    The stricter contract `data-path-redirect.py` needs. That hook rewrites a
    path onto the data root, so it must refuse rather than guess for anything it
    cannot place: an absolute path is not its business, and a path that climbs
    out of its relative root would be rewritten to somewhere outside the data
    tree. `normalize_segments` drops a leading `..` because a WALL should still
    recognise the directory; a REWRITER must not, so the escape is reported here
    instead of being collapsed away.
    """
    if not path:
        return None
    text = path.replace("\\", "/")
    if text.startswith("/") or (len(text) > 1 and text[1] == ":"):
        return None  # absolute (POSIX or Windows drive), never rewrite
    parts: list[str] = []
    for segment in text.split("/"):
        if segment in ("", "."):
            continue
        if segment == "..":
            if not parts:
                return None  # climbs out of the relative root
            parts.pop()
            continue
        parts.append(segment)
    return "/".join(parts) or None
