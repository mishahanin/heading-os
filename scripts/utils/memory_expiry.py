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

from scripts.utils.markdown import frontmatter_date, parse_frontmatter

INDEX_NAME = "MEMORY.md"


def _coerce_date(value) -> datetime.date | None:
    """Best-effort coerce a frontmatter value to a date. None on anything odd.

    The None contract is this module's, not the shared coercion's: a record whose
    `expires:` cannot be read must not be retired, and a raise here would abort a
    sweep over the whole index. The GRAMMAR of "what is a date" is shared, so the
    fourth private type branch is gone.

    MEASURED 2026-08-28 against `frontmatter_date` over nine value shapes, the
    two diverged twice and this copy was the more restrictive both times:
      * `"2026-08-25 09:30:00"` (a QUOTED datetime) -> None here, a date there,
        while the same instant UNQUOTED was read fine because YAML typed it. The
        record's fate depended on the author's quotes.
      * `"20260825"` (ISO basic form) -> None here, a date there.
    Both directions silently dropped a record from the sweep.
    """
    try:
        return frontmatter_date(value)
    except ValueError:
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


# One LINK in the index: `[title](name.md)`. The title is non-greedy and forbids
# `]`, so two links on one line can never be read as one. This is the grammar of
# a link and nothing more, which is what a READER of the index needs.
_LINK_RE = re.compile(r"\[[^\]]*?\]\((?P<target>[^)]+)\)")

# One pointer to REMOVE: the link plus any trailing note, up to the next `·`
# separator or the end of the line, so retiring a memory takes its note with it.
#
# That trailing run is why the two uses cannot share one pattern. It is greedy up
# to `·`, so on the index line
#     `- Address him as [Misha](a.md); he [calls me Mimir](b.md)`
# the FIRST match swallows the second link and `finditer` never reports it.
# Harmless when removing (the whole run goes), wrong when reading: measured
# 2026-08-29 against the live index, `b.md` was reported as an orphan although
# the index points straight at it. Reading uses `_LINK_RE`; removing uses this.
_POINTER_RE = re.compile(_LINK_RE.pattern + r"[^·\n]*")


def _pointers(text: str) -> list[re.Match]:
    """Every REMOVABLE pointer in ``text``, in order: a link and its trailing
    note. Used by the rewriter. A reader wants `_LINK_RE`, see above."""
    return list(_POINTER_RE.finditer(text))


def index_link_targets(index_text: str) -> set[str]:
    """The exact ``](<target>)`` link targets the index points at.

    The read-side of the same rule ``strip_index_pointers`` writes with, exposed
    because ``memory_health.compute_memory_defects`` was answering "is this file
    referenced?" with ``name in index_text`` - a substring of the whole index.
    Two ordinary inputs defeated that, and reproducing them took one file each:

      * A name nested in a longer one. With ``harbour-lantern-ledger.md`` linked
        and ``lantern-ledger.md`` not, the shorter file was reported as
        referenced forever, because its name sits inside its neighbour's.
      * A path-qualified pointer. ``](threads/business/drop.md)`` names a thread
        record, and a bare memory file ``drop.md`` was counted as referenced by
        it.

    Both are silent in the direction that matters: the orphan report said zero
    and the operator believed it. Targets are returned verbatim, so a
    subdirectory pointer stays distinct from the bare filename it ends with.

    Reads `_LINK_RE`, not `_POINTER_RE`: the removal pattern eats the trailing
    note up to the next `·`, which on a line joining two links with `;` hides
    the second one. See the comment on those two patterns.
    """
    return {m.group("target") for m in _LINK_RE.finditer(index_text)}


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
        pointers = _pointers(line)
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
