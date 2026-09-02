#!/usr/bin/env python3
"""One index pointer was invisible to the index parser, and its label held `[`.

MEASURED 2026-09-02 against the live index. `MEMORY.md` line 25 read:

    - Code scanners: [the mode is args[0] on Path.open, args[1] on open](a-scanner-....md)

`_LINK_RE` was `\\[[^\\]]*?\\]\\((?P<target>[^)]+)\\)`. The title class forbids
`]`, so it cannot cross the `]` of `args[0]`. The engine tried to close the
title there, found ` on Path.open` where it needed `(`, and no backtracking
could recover, because `[^\\]]` can never consume a `]`. The whole pointer
matched nothing.

This is not an exotic input. The index hooks are one-line summaries of code
defects, so brackets are ordinary: subscripts, `argv[1]`, `args[0]`. One of 266
pointers carried one, and it was invisible.

## What it cost, in both directions

The parser is used by a READER and by a REMOVER, and they fail differently.

* **Reader.** `memory_health.compute_memory_defects` asks "which memories has
  the index lost a pointer to?" and answered with that file's name, one line
  below the pointer that names it. A verification agent then relayed the false
  orphan to the operator as a real finding. A false orphan in the one report
  whose job is to find lost memories is a report the operator stops believing.
* **Remover.** `_POINTER_RE` is built from `_LINK_RE.pattern`, and
  `strip_index_pointers` removes with it. `retire-memory.py` deletes the fact
  file from every store and then strips its pointer. On a bracketed label the
  strip would have matched nothing, leaving `MEMORY.md` pointing at a file that
  no longer exists anywhere. That is the worse half, and nothing had ever
  exercised it.

The fix admits ONE level of balanced brackets in the title. This file pins both
halves, pins the properties the old pattern was credited with so the widening
did not buy them back at a cost, and pins the LIVE index, because a corpus-level
count is what would have caught this on the day the pointer was written.
"""
from __future__ import annotations

import re
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils.memory_expiry import (_LINK_RE, index_link_targets,  # noqa: E402
                                         strip_index_pointers)

#: The live line, reduced to its shape. Invented name, real bracket pattern.
BRACKETED = ("- Code scanners: [the mode is args[0] on open, args[1] on read]"
             "(a-scanner-that-read-the-wrong-argument.md)")
BRACKETED_TARGET = "a-scanner-that-read-the-wrong-argument.md"


# ============================================================
# The reader
# ============================================================

def test_a_pointer_whose_label_holds_a_bracket_is_visible():
    """The founding case."""
    assert index_link_targets(BRACKETED) == {BRACKETED_TARGET}


@pytest.mark.parametrize("label", [
    "args[0]",
    "[0] leading",
    "trailing [9]",
    "two [1] and [2] subscripts",
    "empty []",
])
def test_every_shape_of_bracketed_label_parses(label):
    line = f"- group: [{label}](x.md)"
    assert index_link_targets(line) == {"x.md"}, f"{label!r} defeated the parser"


def test_an_unbalanced_bracket_does_not_swallow_the_rest_of_the_line():
    """One level of nesting, not a general balanced matcher.

    An unmatched `[` must not let the title run on and eat a later link's
    target. Failing to parse is acceptable here; parsing the WRONG target is
    not, because the reader would then call a linked file an orphan and the
    remover would strip a pointer nobody asked it to strip.
    """
    line = "- broken: [a [ b](first.md) and [c](second.md)"
    targets = index_link_targets(line)
    assert "second.md" in targets, "a later, well-formed link was lost"
    assert targets <= {"first.md", "second.md"}, (
        f"the parser invented a target: {targets}")


# ============================================================
# The properties the old pattern was credited with
# ============================================================

def test_two_links_on_one_line_are_still_two():
    """The old comment credited this to banning `]` in the title.

    It survives the widening for a different reason: both alternatives of the
    new title class stop dead at `]`, so a title can never reach across the
    `](` that closes the first link. If this ever fails, the widening bought
    visibility at the cost of correctness and must be reverted, not patched.
    """
    line = "- Address him as [Misha](a.md); he [calls me Mimir](b.md)"
    assert index_link_targets(line) == {"a.md", "b.md"}


def test_a_name_nested_in_a_longer_one_is_still_not_referenced():
    """`lantern-ledger.md` is not referenced by `harbour-lantern-ledger.md`."""
    line = "- [ledger](harbour-lantern-ledger.md)"
    assert index_link_targets(line) == {"harbour-lantern-ledger.md"}
    assert "lantern-ledger.md" not in index_link_targets(line)


def test_a_path_qualified_pointer_stays_distinct_from_its_bare_filename():
    line = "- [a dropped thread](threads/business/drop.md)"
    assert index_link_targets(line) == {"threads/business/drop.md"}
    assert "drop.md" not in index_link_targets(line)


def test_the_title_class_cannot_backtrack_catastrophically():
    """The two alternatives are disjoint on their first character.

    A nested-quantifier title written as `(?:[^\\]]|\\[.*\\])*` would be
    ambiguous and blow up on a long run of brackets. Bound it in time rather
    than by reading the pattern, so a future rewrite that reintroduces the
    ambiguity fails here instead of hanging a Stop hook.
    """
    hostile = "[" + "a[b" * 400 + "](never-closed"
    start = time.monotonic()
    _LINK_RE.search(hostile)
    assert time.monotonic() - start < 1.0, (
        "the title class backtracked; it must stay unambiguous")


# ============================================================
# The remover, which nothing had ever exercised on this input
# ============================================================

def test_the_remover_can_strip_a_bracketed_pointer():
    """The serious half. Before the fix this returned the line unchanged.

    `retire-memory.py` deletes the fact file from every store FIRST, then
    strips. A no-op strip leaves the index pointing at nothing.
    """
    out = strip_index_pointers(BRACKETED, {BRACKETED_TARGET})
    assert BRACKETED_TARGET not in out, (
        "the pointer survived removal; the index would now point at a file "
        "that no longer exists in any store")


def test_removing_a_bracketed_pointer_leaves_its_neighbours_alone():
    line = (f"- group: [plain](keep-one.md) · [the mode is args[0]]"
            f"({BRACKETED_TARGET}) · [other](keep-two.md)")
    out = strip_index_pointers(line, {BRACKETED_TARGET})
    assert BRACKETED_TARGET not in out
    assert "keep-one.md" in out, "a neighbour was taken with it"
    assert "keep-two.md" in out, "a neighbour was taken with it"


# ============================================================
# The live index, which is what would have caught this
# ============================================================

def _live_index() -> str | None:
    try:
        from scripts.utils.workspace import get_data_root
        path = get_data_root() / "auto-memory" / "MEMORY.md"
    except Exception:  # noqa: BLE001 - reported as a skip below, never swallowed
        return None
    if not path.is_file():
        return None
    return path.read_text(encoding="utf-8", errors="ignore")


def test_every_pointer_in_the_live_index_is_visible_to_the_parser():
    """A corpus-level count, because the unit tests above are all invented.

    The defect reached the live index and sat there. What would have caught it
    on day one is not another fixture, it is asking whether the number of links
    a permissive reading finds equals the number the real parser finds.
    """
    text = _live_index()
    if text is None:
        pytest.skip("no private data overlay on this machine, so no live index")

    # A deliberately permissive second opinion: anything that looks like a
    # markdown link to a `.md`, however its title is punctuated. Written
    # independently of `_LINK_RE` on purpose; deriving it from the code under
    # test would let one bug agree with itself.
    permissive = re.compile(r"\]\((?P<target>[^)\s]+\.md)\)")
    loose = {m.group("target") for m in permissive.finditer(text)}
    strict = index_link_targets(text)

    assert loose, "the permissive reading found no pointers at all; the index " \
                  "is empty or its format changed, and this guard is now blind"
    invisible = loose - strict
    assert not invisible, (
        f"{len(invisible)} pointer(s) in the live MEMORY.md are invisible to "
        f"index_link_targets, so they read as orphans and cannot be removed: "
        f"{sorted(invisible)}")
