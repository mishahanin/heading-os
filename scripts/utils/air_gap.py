#!/usr/bin/env python3
"""Air-gap predicate -- the single source of truth for what must never be read.

Two classes of path are denied regardless of caller config (belt-and-braces):
  - prefix  _secure/   (the former CEO Eyes Only vault; vault removed in Plan 5,
                        the deny is retained as defence-in-depth against any
                        stray `_secure/` path reappearing)
  - segment personal   (personal thread branches, any future personal CRM)

Case-folded: a capitalised segment (`Personal`, `PERSONAL`) or a capitalised
prefix is denied too -- a path's letter-case must never open the air-gap.

Imported by scripts/memory-index.py (associative index) and the /odin collect
episode detection pass, so both enforce one identical boundary. Keep this the
only definition; never re-inline a copy.

Usage:
    from scripts.utils.air_gap import is_denied
    if is_denied(rel_path):          # honours hard-coded _secure/ + personal denies
        continue                     # never read this path
    is_denied(rel, deny_prefixes=cfg["deny_prefixes"],
              deny_segments=cfg["deny_segments"])   # plus caller's config denies
"""

import os

# Belt-and-braces air-gap. Enforced even if the caller's config is empty or broken.
HARDCODED_DENY_PREFIXES = ("_secure/",)
HARDCODED_DENY_SEGMENTS = ("personal",)


def is_denied(rel_path: str, deny_prefixes=(), deny_segments=()) -> bool:
    """True if rel_path is air-gapped. Hard-coded denies always apply.

    A path is denied if (case-insensitively) it starts with any deny prefix, OR
    if any of its `/`-separated segments equals a denied segment. The hard-coded
    vault prefix and `personal` segment are denied regardless of what the caller
    passes, so a broken or emptied config can never open the air-gap.

    Comparison is case-folded: a path whose segment is `Personal` is denied
    exactly as one whose segment is `personal`. Letter-case is never a boundary.

    Case-folded means `str.casefold()`, not `str.lower()`. The two are not the
    same function: a character whose fold mapping differs from its lowercase
    mapping slips a `.lower()` comparison entirely. MEASURED 2026-08-30 -
    `"perſonal".lower()` is `"perſonal"` (unchanged, U+017F LATIN
    SMALL LETTER LONG S is already lowercase) while `.casefold()` is
    `"personal"`, so `is_denied("perſonal/todo.md")` returned False while
    `is_denied("personal/todo.md")` returned True. A one-character bypass of a
    hard-coded segment, in the module this tree calls the single source of truth
    for what must never be read. Both sides fold, or the boundary is only as
    strong as the caller's spelling.

    Traversal-safe: `..` is collapsed lexically BEFORE any check, so a path like
    `threads/business/../../_secure/x` resolves to `_secure/x` and still trips
    the deny. A path that still escapes its root after collapse (`../x`) is not a
    legitimate workspace-relative ingest path and is denied fail-closed. This is
    a pure string transform - no filesystem access.
    """
    prefixes = tuple(deny_prefixes) + HARDCODED_DENY_PREFIXES
    segments = set(deny_segments) | set(HARDCODED_DENY_SEGMENTS)
    raw = rel_path.replace("\\", "/").lstrip("/")
    norm = os.path.normpath(raw).replace("\\", "/").lstrip("/").casefold()
    if norm == ".":
        return False
    if norm == ".." or norm.startswith("../"):
        return True
    if any(norm.startswith(p.lstrip("/").casefold()) for p in prefixes):
        return True
    seg_folded = {s.casefold() for s in segments}
    return any(seg in seg_folded for seg in norm.split("/"))
