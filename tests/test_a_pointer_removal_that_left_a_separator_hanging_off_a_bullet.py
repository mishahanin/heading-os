"""A pointer removal that left a ` · ` separator hanging off a label-less bullet.

`memory_expiry.strip_index_pointers` repairs the separators a removal leaves
behind. The trailing and doubled cases were handled; the LEADING case was keyed
to a `(?<=: )` lookbehind, so it only fired when the removed pointer had sat
after a `Label: ` prefix.

The live MEMORY.md carries label-less lines too - a bullet whose first element
is the link itself. Removing the first pointer on one of those left the line's
`·` in place, and the final normaliser then rendered it as `- · [b](b.md)`: a
stray separator written into an operator-curated index this module's own
docstring says it may not mangle ("The operator's standing rule is that nothing
leaves this index without him saying so").

The fix takes the line's indent-and-bullet prefix from the ORIGINAL text - every
removal happens to the right of it - and strips a separator that leads the
content, whatever shape the line has.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.utils.memory_expiry import strip_index_pointers  # noqa: E402


def test_a_bullet_with_no_label_loses_its_leading_separator():
    out = strip_index_pointers("- [alpha](alpha.md) · [bravo](bravo.md)\n",
                               {"alpha.md"})
    assert out == "- [bravo](bravo.md)\n"


def test_a_line_with_no_bullet_and_no_label_loses_it_too():
    out = strip_index_pointers("[alpha](alpha.md) · [bravo](bravo.md)\n",
                               {"alpha.md"})
    assert out == "[bravo](bravo.md)\n"


def test_the_labelled_case_that_already_worked_still_works():
    out = strip_index_pointers(
        "- Ledgers: [alpha](alpha.md) · [bravo](bravo.md)\n", {"alpha.md"})
    assert out == "- Ledgers: [bravo](bravo.md)\n"


def test_removing_the_last_pointer_still_drops_the_trailing_separator():
    out = strip_index_pointers(
        "- Ledgers: [alpha](alpha.md) · [bravo](bravo.md)\n", {"bravo.md"})
    assert out == "- Ledgers: [alpha](alpha.md)\n"


def test_removing_a_middle_pointer_leaves_one_separator_between_the_survivors():
    out = strip_index_pointers(
        "- Ledgers: [a](a.md) · [b](b.md) · [c](c.md)\n", {"b.md"})
    assert out == "- Ledgers: [a](a.md) · [c](c.md)\n"


def test_a_line_whose_every_pointer_matched_is_dropped_whole():
    assert strip_index_pointers("- Ledgers: [a](a.md)\n", {"a.md"}) == ""


def test_a_line_that_matches_nothing_passes_through_byte_for_byte():
    line = "- Ledgers: [a](a.md) · [b](b.md)  \n"
    assert strip_index_pointers(line, {"zulu.md"}) == line


def test_a_trailing_note_leaves_with_its_pointer_and_no_separator_remains():
    out = strip_index_pointers(
        "- [alpha](alpha.md) — a note · [bravo](bravo.md)\n", {"alpha.md"})
    assert out == "- [bravo](bravo.md)\n"
