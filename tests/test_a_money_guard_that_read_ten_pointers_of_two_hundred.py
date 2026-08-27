#!/usr/bin/env python3
"""The volatile-hook guard matched the bullet, and the index stopped using it.

`scan_volatile_hooks` exists to catch a live money value written into a MEMORY.md
index hook, which `.claude/rules/memory-discipline.md` forbids: the index is
injected at every session start, so a price quoted there is read as current long
after the record behind it moved.

It found the hooks with `^\\s*-\\s*\\[title\\](file.md)`, which demands the link
IMMEDIATELY after the bullet. The index is grouped by subject, and a grouped line
reads `- Memory: [a](a.md) - [b](b.md) - [c](c.md)`. The label between the bullet
and the first bracket makes the pattern fail, so the whole line was skipped.

Measured against the live index on 2026-08-27: 10 lines matched, out of 216
pointers present. The guard reported "0 volatile hook(s)" and that reading was
believed, because a guard that scans 5% of its corpus says the same words as one
that scans all of it.

Two defects, one cause:

* Pointers past the first were never scanned at all.
* On a line that DID match, the signals were read from the whole line while
  `target` was the FIRST pointer, so a price in the fifth hook was reported
  against the first hook's file. The operator would open the wrong record.

`scripts/utils/memory_expiry.py` already solved the same shape with a per-pointer
`finditer`. This is that fix, applied to the guard beside it.

Found by the engine defect hunt, 2026-08-27.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.utils.memory_health import scan_volatile_hooks  # noqa: E402


@pytest.fixture()
def memory_dir(tmp_path):
    d = tmp_path / "auto-memory"
    d.mkdir()
    return d


def _scan(memory_dir, index_text: str):
    (memory_dir / "MEMORY.md").write_text(index_text, encoding="utf-8")
    result = scan_volatile_hooks(memory_dir)
    assert result["ok"], result["note"]
    return result["flagged"]


# ============================================================
# The grouped line, which is what the index is made of
# ============================================================

def test_a_money_value_on_a_grouped_line_is_found(memory_dir):
    flagged = _scan(memory_dir, "- Logistics: [warehouse](warehouse.md) - asking EUR 412,000\n")
    assert [f["target"] for f in flagged] == ["warehouse.md"]


def test_the_pointer_carrying_the_money_is_the_one_reported(memory_dir):
    """The first pointer on the line used to take the blame for the fifth."""
    flagged = _scan(memory_dir, (
        "- Logistics: [permit](permit.md) - done "
        "· [lease](lease.md) - EUR 412,000 loan "
        "· [haulier](haulier.md) - booked\n"
    ))
    assert [f["target"] for f in flagged] == ["lease.md"]


def test_two_priced_pointers_on_one_line_are_both_reported(memory_dir):
    flagged = _scan(memory_dir, (
        "- Deals: [alpha](alpha.md) - offer USD 90,000 "
        "· [beta](beta.md) - quiet "
        "· [gamma](gamma.md) - offer GBP 55,000\n"
    ))
    assert sorted(f["target"] for f in flagged) == ["alpha.md", "gamma.md"]


def test_the_group_label_belongs_to_the_first_pointer(memory_dir):
    """`- Lease EUR 412,000: [landlord](x.md)` is the same claim in another order."""
    flagged = _scan(memory_dir, "- Lease EUR 412,000: [landlord](landlord.md) - open\n")
    assert [f["target"] for f in flagged] == ["landlord.md"]


def test_a_magnitude_needs_its_money_word_on_the_same_pointer(memory_dir):
    """`2.4M` beside "budget" is a price. `2.4M` beside a row count is not.

    The context word must travel with the pointer, or the guard reads a neighbour's
    vocabulary and flags a hook that says nothing about money.
    """
    flagged = _scan(memory_dir, (
        "- Mixed: [budget](budget.md) - the deal budget "
        "· [rows](rows.md) - 2.4M rows indexed\n"
    ))
    assert flagged == []


# ============================================================
# What it must still leave alone
# ============================================================

def test_a_clean_grouped_line_is_not_flagged(memory_dir):
    flagged = _scan(memory_dir, (
        "- Memory: [never delete](never-delete.md) "
        "· [never pruned](never-pruned.md)\n"
    ))
    assert flagged == []


def test_a_thread_pointer_is_still_skipped(memory_dir):
    """Thread pointers are generated links to live records, not memory hooks."""
    flagged = _scan(memory_dir, (
        "- Threads: [deal](threads/business/deal.md) - offer EUR 412,000\n"
    ))
    assert flagged == []


def test_a_thread_pointer_does_not_silence_its_neighbours(memory_dir):
    flagged = _scan(memory_dir, (
        "- Threads: [deal](threads/business/deal.md) - quiet "
        "· [warehouse](warehouse.md) - asking EUR 412,000\n"
    ))
    assert [f["target"] for f in flagged] == ["warehouse.md"]


def test_a_line_that_is_not_a_bullet_is_ignored(memory_dir):
    """Prose in the index preamble is not a hook and has no target to report."""
    flagged = _scan(memory_dir, "See [the warehouse](warehouse.md), asking EUR 412,000.\n")
    assert flagged == []


def test_a_plain_single_pointer_hook_still_works(memory_dir):
    """The shape the old pattern did match must not regress."""
    flagged = _scan(memory_dir, "- [warehouse purchase](warehouse.md) - asking EUR 412,000\n")
    assert [f["target"] for f in flagged] == ["warehouse.md"]


def test_a_non_markdown_target_is_not_a_memory_hook(memory_dir):
    flagged = _scan(memory_dir, "- Docs: [the guide](https://example.test/x) - USD 90,000\n")
    assert flagged == []


# ============================================================
# The reported line, and the coverage claim itself
# ============================================================

def test_the_reported_line_is_the_pointer_not_the_whole_row(memory_dir):
    """A 300-character grouped row printed whole tells the operator nothing."""
    flagged = _scan(memory_dir, (
        "- Logistics: [permit](permit.md) - done "
        "· [lease](lease.md) - EUR 412,000 loan "
        "· [haulier](haulier.md) - booked\n"
    ))
    assert len(flagged) == 1
    line = flagged[0]["line"]
    assert "lease.md" in line
    assert "haulier.md" not in line, f"the whole row was reported: {line!r}"


def test_the_note_counts_what_was_flagged(memory_dir):
    (memory_dir / "MEMORY.md").write_text(
        "- Deals: [alpha](alpha.md) - offer USD 90,000 "
        "· [beta](beta.md) - offer GBP 55,000\n", encoding="utf-8")
    result = scan_volatile_hooks(memory_dir)
    assert result["note"].startswith("2 volatile hook(s)")


def test_a_missing_index_is_not_an_error(memory_dir):
    result = scan_volatile_hooks(memory_dir)
    assert result["ok"] and result["flagged"] == []


def test_the_guard_reaches_every_pointer_of_a_grouped_index(memory_dir):
    """The coverage claim, asserted rather than assumed.

    The old pattern saw the first pointer of a line and only when no label stood
    in front of it. This builds a grouped index where the money sits on the LAST
    pointer of each line, which is the position the guard was blindest to.
    """
    lines = []
    for i in range(20):
        lines.append(
            f"- Group {i}: [a{i}](a{i}.md) - fine "
            f"· [b{i}](b{i}.md) - fine "
            f"· [c{i}](c{i}.md) - asking EUR 412,000"
        )
    flagged = _scan(memory_dir, "\n".join(lines) + "\n")
    assert sorted(f["target"] for f in flagged) == sorted(f"c{i}.md" for i in range(20))
