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


def strip_index_pointers(index_text: str, names: Iterable[str]) -> str:
    """Remove MEMORY.md pointer lines for the given bare filenames.

    Matches the exact ``](<name>)`` link target, so a managed thread pointer like
    ``](threads/business/drop.md)`` is never hit by a bare ``drop.md``. Lines that
    match no name pass through unchanged, preserving the managed ## Active Threads
    section."""
    targets = {f"]({name})" for name in names}
    kept = [
        line
        for line in index_text.splitlines(keepends=True)
        if not any(t in line for t in targets)
    ]
    return "".join(kept)
