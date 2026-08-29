"""A merge deleted the top of the log section, and a block scalar hoisted itself.

Four defects in `scripts/merge-contacts.py`, all of the same family: the parse
and the serialize halves disagreed about what a record contains, so a merge
asked to change ONE field rewrote parts of the file nobody asked it to touch.

THE ONE WITH A LIVE FOOTPRINT. `extract_interaction_log` returned
`(pre_log, entries, post_log)`, and whatever sat between the `## Interaction
Log` header and the first `### YYYY-MM-DD` entry was in none of them.
`merge_notes` rebuilds the body from those three parts, so that text was simply
gone from the merged record. MEASURED 2026-08-29 over the operator's live 334
records (165 contacts + 169 address-book entities): 39 of them keep log entries
there in BULLET form, `- 2026-04-29 | Email | Outbound: "..."`, which the `###`
entry pattern does not match. A merge deleted every one, silently, from both
sides. It is returned as a fourth element now and written back at the top of the
log region, where it was.

THE ONE WITH NO FOOTPRINT YET. A YAML block scalar (`notes: |`) carries a value
on its key line, so it took the plain-scalar branch and its indented body was
left to the outer loop. A body line holding a colon then parsed as a TOP-LEVEL
key, `serialize_frontmatter` wrote it at column 0, and `merge_frontmatter`'s
union carried the invented key into the OTHER exec's record. That is the exact
cross-record corruption `_Block` was written to end for nested mappings; only
the trigger differed. 0 of the 334 live records use a block scalar, which is why
nothing caught it.

The other two are round-trip fidelity: the OPENING frontmatter fence was still
greedy (`^---\\s*\\n`) and ate a blank line immediately inside the block, the
mirror of the closing-fence defect fixed earlier; and the two records' post-log
sections were concatenated raw, so two copies of one contact template produced
the same `## Follow-ups` heading twice with no way to tell whose was whose.

Verified after the fix: all 334 live records still round-trip byte-for-byte
through `parse_frontmatter` + `serialize_frontmatter`, and all 39 preambles come
back.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_spec = importlib.util.spec_from_file_location(
    "merge_contacts", ROOT / "scripts" / "merge-contacts.py")
mc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mc)


def _roundtrip(text: str) -> str:
    fm, body = mc.parse_frontmatter(text)
    return mc.serialize_frontmatter(fm) + body


# ============================================================
# The log-section preamble (39 live records)
# ============================================================

LIVE_SHAPE = """## Interaction Log

- 2026-04-29 | Email | Outbound: "31C x Kestrel Holdings" (CRM backfill)
- 2026-06-28 | Note | Radar freeze to 2026-10-01

### 2026-07-10
Teams invite accepted.
"""


def test_the_preamble_comes_back_as_its_own_element():
    pre, entries, post, lead = mc.extract_interaction_log(LIVE_SHAPE)
    assert lead.startswith("- 2026-04-29 | Email")
    assert "2026-06-28" in lead
    assert pre == ""
    assert len(entries) == 1
    assert post == ""


def test_the_bullet_entries_survive_a_merge():
    """The measured defect: 39 live records lost these lines entirely."""
    merged = mc.merge_notes(LIVE_SHAPE, "## Interaction Log\n\n### 2026-01-01\nold\n",
                            "exec-a", "exec-b")
    assert '- 2026-04-29 | Email | Outbound: "31C x Kestrel Holdings" (CRM backfill)' in merged
    assert "- 2026-06-28 | Note | Radar freeze to 2026-10-01" in merged


def test_the_preamble_stays_under_the_header_it_was_written_below():
    """Folding it into `pre_log` would move log content ABOVE the log header."""
    merged = mc.merge_notes(LIVE_SHAPE, "## Interaction Log\n\n### 2026-01-01\nold\n",
                            "exec-a", "exec-b")
    header = merged.index("## Interaction Log")
    bullet = merged.index("- 2026-04-29")
    assert header < bullet


def test_the_preamble_sits_above_the_dated_entries():
    merged = mc.merge_notes(LIVE_SHAPE, "## Interaction Log\n\n### 2026-01-01\nold\n",
                            "exec-a", "exec-b")
    assert merged.index("- 2026-04-29") < merged.index("### 2026-01-01")


def test_both_records_keep_their_preamble():
    other = "## Interaction Log\n\n- 2025-12-01 | Call | intro\n\n### 2026-01-01\nold\n"
    merged = mc.merge_notes(LIVE_SHAPE, other, "exec-a", "exec-b")
    assert "- 2026-04-29" in merged
    assert "- 2025-12-01 | Call | intro" in merged


def test_the_preamble_is_not_counted_as_a_log_entry():
    """`main` prints `len(extract_interaction_log(body)[1])`. A preamble folded
    into `entries` would be dated `0000-00-00` and inflate that number."""
    entries = mc.extract_interaction_log(LIVE_SHAPE)[1]
    assert len(entries) == 1
    assert all(e.startswith("### ") for e in entries)


def test_a_record_with_no_preamble_gains_nothing():
    plain = "## Interaction Log\n\n### 2026-01-01\nold\n"
    assert mc.extract_interaction_log(plain)[3] == ""
    merged = mc.merge_notes(plain, plain, "exec-a", "exec-b")
    assert "\n\n\n" not in merged


def test_a_log_with_no_dated_entry_is_unchanged():
    """No entries means the whole region is post_log, as before. The preamble
    must stay empty or the same text would be written twice."""
    body = "## Interaction Log\n\nfreeform note, no dated entries\n"
    pre, entries, post, lead = mc.extract_interaction_log(body)
    assert entries == []
    assert lead == ""
    assert "freeform note" in post


def test_no_log_header_at_all_returns_four_parts():
    assert mc.extract_interaction_log("just a body\n") == ("just a body\n", [], "", "")


def test_the_preamble_is_last_in_the_tuple():
    """`main` reads the entry count as `[1]`. Inserting the preamble there would
    make `len()` return a character count -- a wrong number in a summary."""
    parts = mc.extract_interaction_log(LIVE_SHAPE)
    assert isinstance(parts[1], list)
    assert isinstance(parts[3], str)


# ============================================================
# A block scalar hoisting its body into top-level keys
# ============================================================

BLOCK_SCALAR = """---
name: Priya Anand
notes: |
  met at conference: discussed deal
  followup soon
owner: exec-a
---
body
"""


def test_a_block_scalar_body_is_not_hoisted_into_a_key():
    fm, _body = mc.parse_frontmatter(BLOCK_SCALAR)
    assert "met at conference" not in fm
    assert {k for k in fm if not k.startswith(mc.RAW_KEY)} == {
        "name", "notes", "owner"}


def test_a_block_scalar_round_trips_byte_for_byte():
    assert _roundtrip(BLOCK_SCALAR) == BLOCK_SCALAR


def test_the_scalar_header_is_not_turned_into_a_nested_mapping():
    fm, _ = mc.parse_frontmatter(BLOCK_SCALAR)
    assert "notes: |" in mc.serialize_frontmatter(fm)


def test_the_hoisted_key_cannot_leak_into_the_other_record():
    """The cross-record half of the defect: the union carried the invented key
    into a record that never contained it."""
    fm_from, _ = mc.parse_frontmatter(BLOCK_SCALAR)
    fm_into, _ = mc.parse_frontmatter("---\nname: Priya Anand\nowner: exec-b\n---\nx\n")
    merged = mc.merge_frontmatter(fm_from, fm_into, "exec-a", "exec-b")
    assert "met at conference" not in merged


@pytest.mark.parametrize("header", ["|", ">", "|-", ">-", "|+", ">+", "|2", ">2-"])
def test_every_block_scalar_header_is_recognised(header):
    text = f"---\nnotes: {header}\n  a: b\n---\nx\n"
    fm, _ = mc.parse_frontmatter(text)
    assert "a" not in fm
    assert _roundtrip(text) == text


@pytest.mark.parametrize("value", ["plain", "a | b", "2026-01-01", '"q"', "[a, b]", ""])
def test_an_ordinary_value_is_not_read_as_a_block_scalar(value):
    """The other direction. `_BLOCK_SCALAR_RE` matching too widely would consume
    the lines below an ordinary field."""
    assert not mc._BLOCK_SCALAR_RE.match(value)


def test_a_blank_line_inside_a_block_scalar_is_kept():
    text = "---\nnotes: |\n  first para\n\n  second para\nowner: x\n---\nb\n"
    fm, _ = mc.parse_frontmatter(text)
    assert "owner" in fm
    assert _roundtrip(text) == text


def test_trailing_blanks_after_a_block_are_left_to_the_document():
    text = "---\nnotes: |\n  only line\n\nowner: x\n---\nb\n"
    fm, _ = mc.parse_frontmatter(text)
    assert fm["owner"] == "x"
    assert _roundtrip(text) == text


def test_a_nested_mapping_still_works():
    """`_consume_indented` serves both callers; the older form must not change
    behaviour."""
    text = "---\naddress:\n  street: 1 Main\n  city: Dubai\nowner: x\n---\nb\n"
    fm, _ = mc.parse_frontmatter(text)
    assert "street" not in fm
    assert fm["owner"] == "x"
    assert _roundtrip(text) == text


def test_consume_indented_takes_only_the_deeper_lines():
    lines = ["key:", "  a", "  b", "next: 1"]
    block, i = mc._consume_indented(lines, 1, 0)
    assert block == ["  a", "  b"]
    assert i == 3


def test_consume_indented_spans_a_blank_between_indented_lines():
    lines = ["  a", "", "  b", "next: 1"]
    block, i = mc._consume_indented(lines, 0, 0)
    assert block == ["  a", "", "  b"]
    assert i == 3


def test_consume_indented_does_not_take_a_trailing_blank():
    lines = ["  a", "", "next: 1"]
    block, i = mc._consume_indented(lines, 0, 0)
    assert block == ["  a"]
    assert i == 1


def test_consume_indented_does_not_take_a_blank_at_the_end_of_input():
    block, i = mc._consume_indented(["  a", "", ""], 0, 0)
    assert block == ["  a"]
    assert i == 1


def test_a_nested_mapping_spans_a_blank_line_too():
    """YAML lets a blank line sit inside a block mapping; the lines after it are
    still children of the same key."""
    text = "---\naddress:\n  street: 1 Main\n\n  city: Dubai\nowner: x\n---\nb\n"
    fm, _ = mc.parse_frontmatter(text)
    assert "city" not in fm
    assert "Dubai" in "\n".join(fm["address"].lines)
    assert _roundtrip(text) == text


def test_a_paragraph_below_a_blank_line_survives_the_union():
    """The observable cost of getting the blank wrong. A line the parser leaves
    outside the block becomes a `_Raw`, and `merge_frontmatter` does not union
    `_Raw` entries -- so the paragraph is dropped from the merged record while
    the source file still shows it."""
    src = "---\nnotes: |\n  first para\n\n  second para\nowner: exec-a\n---\nb\n"
    fm_from, _ = mc.parse_frontmatter(src)
    fm_into, _ = mc.parse_frontmatter("---\nowner: exec-b\n---\nx\n")
    merged = mc.serialize_frontmatter(
        mc.merge_frontmatter(fm_from, fm_into, "exec-a", "exec-b"))
    assert "first para" in merged
    assert "second para" in merged


def test_a_nested_child_below_a_blank_line_survives_the_union():
    src = "---\naddress:\n  street: 1 Main\n\n  city: Dubai\nowner: exec-a\n---\nb\n"
    fm_from, _ = mc.parse_frontmatter(src)
    fm_into, _ = mc.parse_frontmatter("---\nowner: exec-b\n---\nx\n")
    merged = mc.serialize_frontmatter(
        mc.merge_frontmatter(fm_from, fm_into, "exec-a", "exec-b"))
    assert "city: Dubai" in merged


# ============================================================
# The opening frontmatter fence
# ============================================================

def test_a_blank_line_inside_the_frontmatter_survives():
    text = "---\n\nname: Priya Anand\n---\nbody\n"
    assert _roundtrip(text) == text


def test_the_opening_fence_still_tolerates_trailing_whitespace():
    text = "--- \nname: Priya Anand\n---\nbody\n"
    fm, body = mc.parse_frontmatter(text)
    assert fm["name"] == "Priya Anand"
    assert body == "body\n"


def test_a_body_line_of_three_dashes_does_not_open_a_block():
    """`^` with no MULTILINE still anchors at the string start, and the fix must
    not have widened that."""
    fm, body = mc.parse_frontmatter("no frontmatter\n---\nname: x\n---\n")
    assert fm == {}
    assert body.startswith("no frontmatter")


# ============================================================
# The trailing sections
# ============================================================

def test_the_source_trailing_section_is_attributed():
    a = "## Interaction Log\n\n### 2026-01-01\none\n\n## Follow-ups\n- from target\n"
    b = "## Interaction Log\n\n### 2026-02-01\ntwo\n\n## Follow-ups\n- from source\n"
    merged = mc.merge_notes(b, a, "exec-a", "exec-b")
    assert "**Notes merged from exec-a:**" in merged.split("## Follow-ups", 1)[1]
    assert "- from target" in merged
    assert "- from source" in merged


def test_a_target_only_trailing_section_gets_no_header():
    a = "## Interaction Log\n\n### 2026-01-01\none\n\n## Follow-ups\n- from target\n"
    b = "## Interaction Log\n\n### 2026-02-01\ntwo\n"
    merged = mc.merge_notes(b, a, "exec-a", "exec-b")
    assert "- from target" in merged
    assert merged.count("**Notes merged from exec-a:**") == 0


def test_a_source_only_trailing_section_gets_no_header_either():
    a = "## Interaction Log\n\n### 2026-01-01\none\n"
    b = "## Interaction Log\n\n### 2026-02-01\ntwo\n\n## Follow-ups\n- from source\n"
    merged = mc.merge_notes(b, a, "exec-a", "exec-b")
    assert "- from source" in merged
    assert merged.count("**Notes merged from exec-a:**") == 0


# ============================================================
# The live corpus
# ============================================================

def test_the_live_records_still_round_trip():
    """A guard over an empty corpus proves nothing, so this asserts the real
    corpus was found before it asserts anything about it. Skips on a clone with
    no data overlay rather than passing over zero files."""
    from scripts.utils.workspace import get_crm_contacts_dir, get_data_root
    roots = [get_crm_contacts_dir(), get_data_root() / "crm" / "address-book"]
    files = [p for r in roots if r.exists() for p in sorted(r.glob("*.md"))]
    if len(files) < 50:
        pytest.skip(f"no CRM corpus here ({len(files)} records)")
    drift = []
    for path in files:
        text = path.read_text(encoding="utf-8")
        if not mc.FRONTMATTER_RE.match(text):
            continue
        if _roundtrip(text) != text:
            drift.append(path.stem)
    assert not drift, f"{len(drift)} records no longer round-trip: {drift[:5]}"
