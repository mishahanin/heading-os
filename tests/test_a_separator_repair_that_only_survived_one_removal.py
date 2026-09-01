#!/usr/bin/env python3
"""The separator repair held for ONE removal per line and broke at two.

`memory_expiry.strip_index_pointers` repairs the separators a pointer removal
leaves behind. Three repairs run in order: collapse a doubled separator, drop a
trailing one, drop a leading one. The doubled collapse read `\\s*<sep>\\s*(?=<sep>)`,
so it ate the whitespace in FRONT of the surviving separator as well. Both
leading repairs are anchored on that whitespace: the labelled one is a
`(?<=: )` lookbehind wanting exactly one space after the colon, and the
label-less one wants the separator to be the first thing after the line's
indent-and-bullet prefix. After the collapse neither condition held any more,
so the leading repair ran over a line the earlier repair had already deformed
and matched nothing.

MEASURED 2026-09-01 against the shipped module, removing the first TWO pointers
of a three-pointer line:

    labelled     "- Ledgers: [a](a.md) . [b](b.md) . [c](c.md)"  {a,b}
                 -> "- Ledgers: . [c](c.md)"      (want "- Ledgers: [c](c.md)")
    label-less   "- [a](a.md) . [b](b.md) . [c](c.md)"           {a,b}
                 -> "-  [c](c.md)"                (want "- [c](c.md)")

(the separator is U+00B7; written as a full stop here so this docstring stays
ASCII.) A stray separator and a doubled space, written into an operator-curated
index the module's own docstring says it may not mangle. The eight cases in
`tests/test_a_pointer_removal_that_left_a_separator_hanging_off_a_bullet.py`
each remove exactly ONE pointer from a line, so none of them reached the
collapse and the leading repair together, and the whole file stayed green.

A third shape went with it. `_PREFIX_RE` knew `-`, `*` and `+` and no ordered
marker, so on `1. [a](a.md) . [b](b.md)` the prefix came back empty, the
label-less repair measured from column 0, and removing the first pointer
returned `1. . [b](b.md)`. Latent: the live index is bulleted throughout.

Measured after the fix (collapse the RIGHT duplicate, and teach `_PREFIX_RE` the
ordered markers): 0 of 15 cases failing, against 4 of 15 before. Mutation check
on the fixed module, restoring `\\s*` in front of the collapse: 3 failed of the
new cases here, 0 failed of the eight older ones.

ONE SHAPE IS DELIBERATELY LEFT AS IT IS, and it is not an oversight. On a line
whose first pointer sits after ORDINARY text with no label colon and no list
marker, removing that pointer still leaves the separator: `2026 [a](a.md) .
[b](b.md)` with `{a}` returns `2026 . [b](b.md)`. The general rule that would
fix it, "a separator with no surviving link to its left separates nothing", was
written and measured, and it is WRONG: it also fires when the first pointer was
never removed, so `- Odd . [a](a.md) . [b](b.md)` with `{b}` came back as
`- Odd [a](a.md)`, deleting a separator the operator typed. Repairing that shape
correctly needs the removal POSITIONS carried into the repair, which is a
redesign of the function rather than a fix to it. The live index is bulleted and
either labelled or pointer-first throughout, so the shape does not occur; it is
recorded here so the next reader does not mistake it for an untried case.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.utils.memory_expiry import strip_index_pointers  # noqa: E402

SEP = "·"


def _line(prefix: str, *names: str, tail: str = "") -> str:
    body = f" {SEP} ".join(f"[{n}]({n}.md)" for n in names)
    return f"{prefix}{body}{tail}\n"


# ============================================================
# Two leading pointers, which is where the repair stopped working
# ============================================================

def test_two_leading_removals_on_a_labelled_line_leave_no_separator():
    """The headline case. One removal repaired; two did not."""
    out = strip_index_pointers(_line("- Ledgers: ", "a", "b", "c"),
                               {"a.md", "b.md"})
    assert out == "- Ledgers: [c](c.md)\n", out


def test_two_leading_removals_on_a_label_less_line_leave_one_space():
    """The same defect without a label: a doubled space after the bullet."""
    out = strip_index_pointers(_line("- ", "a", "b", "c"), {"a.md", "b.md"})
    assert out == "- [c](c.md)\n", out


def test_three_leading_removals_still_leave_a_clean_line():
    """Two collapses in one pass, so the repair has to survive repetition."""
    out = strip_index_pointers(_line("- Ledgers: ", "a", "b", "c", "d"),
                               {"a.md", "b.md", "c.md"})
    assert out == "- Ledgers: [d](d.md)\n", out


def test_two_trailing_removals_still_drop_every_separator():
    """The other end of the line, for the same doubled-collapse reason."""
    out = strip_index_pointers(_line("- Ledgers: ", "a", "b", "c"),
                               {"b.md", "c.md"})
    assert out == "- Ledgers: [a](a.md)\n", out


def test_two_removals_around_a_survivor_leave_it_alone():
    """First and last taken, the middle one kept and unpadded."""
    out = strip_index_pointers(_line("- Ledgers: ", "a", "b", "c"),
                               {"a.md", "c.md"})
    assert out == "- Ledgers: [b](b.md)\n", out


# ============================================================
# The prefix the repair measures its content from
# ============================================================

@pytest.mark.parametrize("prefix", ["- ", "* ", "+ ", "  - ", "\t- ", "",
                                    "1. ", "10. ", "1) "])
def test_a_leading_removal_is_repaired_under_every_list_marker(prefix):
    """`_PREFIX_RE` decides where the line's CONTENT starts, and the label-less
    repair strips a separator that leads that content. A marker the pattern does
    not know puts the separator past the start it computed."""
    out = strip_index_pointers(_line(prefix, "a", "b"), {"a.md"})
    assert out == f"{prefix}[b](b.md)\n", out


def test_an_ordered_marker_is_not_invented_where_there_is_none():
    """The other direction: a bare number is not a list marker.

    Asked of the prefix pattern rather than of the repaired line, because that
    is the claim: `\\d+[.)]` must need the dot or the paren. A widening that read
    `2026 ` as a marker would move the content start four characters to the
    right and let the leading repair strip a separator the operator wrote.
    """
    from scripts.utils.memory_expiry import _PREFIX_RE

    assert _PREFIX_RE.match("2026 and then some").group(0) == ""
    assert _PREFIX_RE.match("1. and then some").group(0) == "1. "
    assert _PREFIX_RE.match("- and then some").group(0) == "- "


# ============================================================
# Corpus guards: the fix must not have emptied the behaviour
# ============================================================

def test_a_line_nothing_matched_is_still_byte_for_byte_unchanged():
    line = f"- Ledgers: [a](a.md) {SEP} [b](b.md)  \n"
    assert strip_index_pointers(line, {"zulu.md"}) == line


def test_a_line_whose_every_pointer_matched_is_still_dropped_whole():
    assert strip_index_pointers(_line("- Ledgers: ", "a", "b"),
                                {"a.md", "b.md"}) == ""


def test_a_single_middle_removal_still_leaves_one_padded_separator():
    """The case the eight older tests already covered, re-asserted here because
    the collapse rule is what this file changed."""
    out = strip_index_pointers(_line("- Ledgers: ", "a", "b", "c"), {"b.md"})
    assert out == f"- Ledgers: [a](a.md) {SEP} [c](c.md)\n", out


def test_a_note_carrying_a_colon_is_not_read_as_a_label():
    """The labelled repair keys on a colon, and a pointer's trailing note may
    carry one. Removing the pointer AFTER such a note must not pull the
    survivor onto the colon."""
    line = (f"- [a](a.md) ratio: 3 {SEP} [b](b.md) {SEP} [c](c.md)\n")
    out = strip_index_pointers(line, {"b.md"})
    assert out == f"- [a](a.md) ratio: 3 {SEP} [c](c.md)\n", out
