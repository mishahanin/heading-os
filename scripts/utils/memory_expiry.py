#!/usr/bin/env python3
"""memory_expiry.py - author-pre-authorized memory retirement (importable, pure).

A memory file MAY carry an explicit ``expires:`` date (top-level or under the
``metadata:`` block). It marks the LAST day the fact is considered live; the day
AFTER it, the memory is retired automatically. This is not judgement at
retire-time - it is the author honoring a deletion date they set when they wrote
the memory. Only date-boxed facts get the field; anything whose relevance is a
STATE ("delete once X happens") gets no field and stays a manual /dream call.

Pure and directory-parameterized. Reads text; never writes, retires, or touches
a store. The CLI wrapper scripts/memory-auto-retire.py does the mutation.

Consumed by:
  - scripts/memory-auto-retire.py
"""
from __future__ import annotations

import datetime
import re
import sys
from pathlib import Path
from typing import Iterable

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from scripts.utils.markdown import parse_frontmatter

INDEX_NAME = "MEMORY.md"


def _coerce_date(value) -> datetime.date | None:
    """Best-effort coerce a frontmatter value to a date. None on anything odd."""
    if isinstance(value, datetime.datetime):
        return value.date()
    if isinstance(value, datetime.date):
        return value
    if isinstance(value, str):
        try:
            return datetime.date.fromisoformat(value.strip())
        except ValueError:
            return None
    return None


def parse_expires(text: str) -> datetime.date | None:
    """Return the memory's expiry date, or None if it has no valid one.

    Accepts ``expires:`` at the top level or nested under ``metadata:``. A
    missing, empty, or unparseable value yields None (fail-safe: no expiry means
    never auto-retired)."""
    meta, _ = parse_frontmatter(text)
    if not isinstance(meta, dict):
        return None
    raw = meta.get("expires")
    if raw is None and isinstance(meta.get("metadata"), dict):
        raw = meta["metadata"].get("expires")
    if raw is None:
        return None
    return _coerce_date(raw)


def find_expired(memory_dir: Path, today: datetime.date) -> list[tuple[str, datetime.date]]:
    """Every fact file whose expiry is strictly before ``today``.

    Skips the MEMORY.md index unconditionally. A file that survives its last day
    (expires == today) is NOT selected; it is retired the day after."""
    out: list[tuple[str, datetime.date]] = []
    if not memory_dir.is_dir():
        return out
    for f in sorted(memory_dir.glob("*.md")):
        if f.name == INDEX_NAME or not f.is_file():
            continue
        try:
            exp = parse_expires(f.read_text(encoding="utf-8"))
        except OSError:
            continue
        if exp is not None and today > exp:
            out.append((f.name, exp))
    return out


# One pointer in the index: `[title](name.md)` plus any trailing note, up to the
# next `·` separator or the end of the line. The title is non-greedy and forbids
# `]`, so two pointers on one line can never be read as one.
_POINTER_RE = re.compile(r"\[[^\]]*?\]\((?P<target>[^)]+)\)[^·\n]*")


def strip_index_pointers(index_text: str, names: Iterable[str]) -> str:
    """Remove the MEMORY.md POINTERS for the given bare filenames.

    Pointers, not lines. This removed the whole line a match sat on, and the
    index groups related memories onto one line by design - 37 lines of the live
    index carry more than one pointer. So retiring one dated memory deleted the
    index entry of every memory grouped beside it, leaving them as orphans that
    `memory_health.compute_memory_defects` would then report and nothing would
    explain. Reproduced 2026-08-25: a three-pointer line, one name passed in, the
    whole line gone. The operator's standing rule is that nothing leaves this
    index without him saying so, and this could remove two facts for every one
    he had dated.

    A line is deleted only when every pointer on it matched, so a single-pointer
    line still disappears whole rather than leaving a bare group label.

    Matches the exact ``](<name>)`` link target, so a thread pointer like
    ``](threads/business/drop.md)`` is never hit by a bare ``drop.md``. Lines that
    match no name pass through unchanged. That carve-out was written for the
    ``## Active Threads`` block, which is retired as of 2026-08-27; the path-vs-
    name distinction is kept because any pointer to a subdirectory needs it.
    """
    wanted = set(names)
    out = []
    for line in index_text.splitlines(keepends=True):
        pointers = list(_POINTER_RE.finditer(line))
        matched = [m for m in pointers if m.group("target") in wanted]
        if not matched:
            out.append(line)
            continue
        if len(matched) == len(pointers):
            continue  # nothing of substance would survive; drop the line
        body, newline = (line[:-1], line[-1]) if line.endswith("\n") else (line, "")
        for match in reversed(matched):
            body = body[:match.start()] + body[match.end():]
        # Repair the separators the removal left behind: a doubled ` · `, and a
        # leading or trailing one where the first or last pointer was the one
        # taken out.
        body = re.sub(r"\s*·\s*(?=·)", "", body)
        body = re.sub(r"·\s*$", "", body).rstrip()
        body = re.sub(r"(?<=: )·\s*", "", body)
        # One space either side of every surviving separator. The pointer pattern
        # eats the space that preceded a `·`, so removing a middle pointer would
        # otherwise leave `[a](a.md)· [c](c.md)`.
        body = re.sub(r"\s*·\s*", " · ", body)
        out.append(body + newline)
    return "".join(out)
