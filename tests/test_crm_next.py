"""Tests for scripts/crm_next.py ranker."""

import pytest


def test_rank_by_stage_tier_first():
    from scripts.crm_next import rank_candidates
    contacts = [
        {"slug": "a", "stage": "Lead", "days_overdue": 30, "health": "red"},
        {"slug": "b", "stage": "Negotiation", "days_overdue": 5, "health": "red"},
        {"slug": "c", "stage": "Demo", "days_overdue": 15, "health": "red"},
    ]
    ranked = rank_candidates(contacts, top_n=3)
    assert ranked[0]["slug"] == "b"  # Negotiation wins
    assert ranked[1]["slug"] == "c"  # Demo
    assert ranked[2]["slug"] == "a"  # Lead


def test_rank_days_overdue_tiebreak_within_stage():
    from scripts.crm_next import rank_candidates
    contacts = [
        {"slug": "a", "stage": "Demo", "days_overdue": 5, "health": "red"},
        {"slug": "b", "stage": "Demo", "days_overdue": 25, "health": "red"},
        {"slug": "c", "stage": "Demo", "days_overdue": 15, "health": "red"},
    ]
    ranked = rank_candidates(contacts, top_n=3)
    assert ranked[0]["slug"] == "b"  # most overdue wins
    assert ranked[1]["slug"] == "c"
    assert ranked[2]["slug"] == "a"


def test_rank_filters_red_freeze_and_already_green():
    from scripts.crm_next import rank_candidates
    contacts = [
        {"slug": "a", "stage": "Demo", "days_overdue": 25, "health": "red", "radar_freeze_until": "2026-12-01"},
        {"slug": "b", "stage": "Demo", "days_overdue": 5, "health": "red"},
        {"slug": "c", "stage": "Demo", "days_overdue": 0, "health": "green"},
    ]
    ranked = rank_candidates(contacts, top_n=3, today="2026-05-16")
    assert len(ranked) == 1
    assert ranked[0]["slug"] == "b"


def test_rank_returns_fewer_than_n_when_few_reds():
    from scripts.crm_next import rank_candidates
    contacts = [
        {"slug": "a", "stage": "Demo", "days_overdue": 5, "health": "red"},
    ]
    ranked = rank_candidates(contacts, top_n=3)
    assert len(ranked) == 1


def test_freeze_expired_includes_contact():
    from scripts.crm_next import rank_candidates
    contacts = [
        {"slug": "a", "stage": "Demo", "days_overdue": 25, "health": "red", "radar_freeze_until": "2026-05-01"},
    ]
    ranked = rank_candidates(contacts, top_n=3, today="2026-05-16")
    assert len(ranked) == 1  # freeze expired


def test_demo_poc_ranks_same_as_demo():
    """Demo/POC (canonical pipeline.md spelling) should rank same as Demo (short form)."""
    from scripts.crm_next import rank_candidates
    contacts = [
        {"slug": "demo-short", "stage": "Demo", "days_overdue": 10, "health": "red"},
        {"slug": "demo-poc", "stage": "Demo/POC", "days_overdue": 10, "health": "red"},
        {"slug": "negotiation", "stage": "Negotiation", "days_overdue": 5, "health": "red"},
    ]
    ranked = rank_candidates(contacts, top_n=3)
    # Negotiation (tier 1) first, then the two Demo variants both at tier 3
    assert ranked[0]["slug"] == "negotiation"
    # Both Demo and Demo/POC are tier 3 with the same days_overdue,
    # so order between them is stable-sort-determined by input order.
    demo_slugs = {ranked[1]["slug"], ranked[2]["slug"]}
    assert demo_slugs == {"demo-short", "demo-poc"}


def test_a_stage_the_table_does_not_know_sorts_last(tmp_path):
    """`STAGE_TIER.get(stage, 6)` -- the fallback, which nothing exercised.

    Every case above hands `rank_candidates` a stage the table already holds, so
    the default was free to be anything. Measured 2026-09-01: changing it from 6
    to 0 left this file and all nine other test files naming `crm_next` green,
    while an unrecognised stage jumped from LAST to ahead of Negotiation. That is
    the outreach queue reordered by a spelling nobody had entered in the table --
    the exact effect a new pipeline stage produces on the day it is introduced.

    Six, not five: the table's own `"": 6` entry means "no stage recorded", and a
    stage the engine does not recognise must not outrank a contact whose stage
    IS known. Sorting it beside the blank is the honest answer.
    """
    from scripts.crm_next import rank_candidates
    contacts = [
        {"slug": "unrecognised", "stage": "Discovery", "days_overdue": 90,
         "health": "red"},
        {"slug": "lead", "stage": "Lead", "days_overdue": 1, "health": "red"},
    ]
    ranked = rank_candidates(contacts, top_n=2)
    assert [c["slug"] for c in ranked] == ["lead", "unrecognised"], (
        "a stage absent from STAGE_TIER outranked a known one; the days_overdue "
        "gap here is 90 to 1, so only the tier can decide this order"
    )


def test_last_interaction_excerpt_heading_style(tmp_path):
    """### YYYY-MM-DD entries (one of the two formats in use) parse correctly."""
    from scripts.crm_next import last_interaction_excerpt
    f = tmp_path / "x.md"
    f.write_text(
        "---\nname: X\n---\n\n"
        "## Interaction Log\n\n"
        "### 2026-05-01 | Email | Subject\n"
        "Body line\n",
        encoding="utf-8",
    )
    out = last_interaction_excerpt(f)
    assert "2026-05-01" in out
    assert "Subject" in out


def test_last_interaction_excerpt_bullet_style(tmp_path):
    """- YYYY-MM-DD entries (the format used by 52 of 116 live contacts) parse correctly."""
    from scripts.crm_next import last_interaction_excerpt
    f = tmp_path / "x.md"
    f.write_text(
        "---\nname: X\n---\n\n"
        "## Interaction Log\n"
        "- 2026-05-01 | Email | Subject body content\n"
        "- 2026-04-01 | Older entry\n",
        encoding="utf-8",
    )
    out = last_interaction_excerpt(f)
    assert "2026-05-01" in out
    assert "Subject body content" in out


def test_the_excerpt_carries_the_entry_plus_its_continuation_lines(tmp_path):
    """"the matched entry plus up to 3 following lines, capped at 4 lines total".

    The docstring's promise, and no case measured it: both format tests above
    happen to produce a one- or two-line entry, so `[:4]` could have been `[:1]`
    and stayed green (measured 2026-09-01). A one-line excerpt is not a
    cosmetic loss -- `/cold-sweep` writes this text into the draft it asks the
    operator to approve, so the body of the last interaction is the context the
    approval decision rests on.
    """
    from scripts.crm_next import last_interaction_excerpt
    f = tmp_path / "x.md"
    f.write_text(
        "---\nname: X\n---\n\n"
        "## Interaction Log\n\n"
        "### 2026-05-01 | Call | Renewal scope\n"
        "second line\n"
        "third line\n"
        "fourth line\n"
        "fifth line should be cut\n\n"
        "### 2026-04-01 | Older entry\n",
        encoding="utf-8",
    )
    out = last_interaction_excerpt(f)
    lines = out.splitlines()
    assert "2026-05-01" in lines[0], out
    assert "second line" in out and "third line" in out and "fourth line" in out
    assert len(lines) == 4, f"expected the 4-line cap, got {len(lines)}: {out!r}"
    assert "fifth line should be cut" not in out
    assert "2026-04-01" not in out, "the excerpt ran into the previous entry"


def test_an_undecodable_record_costs_one_excerpt_not_the_whole_sweep(tmp_path):
    """The `UnicodeDecodeError` arm of the read handler, with no case on it.

    The handler is right and its comment explains exactly why (`/cold-sweep`
    calls this once per contact, so one byte-corrupt CRM file took the whole
    sweep down). Nothing held it: narrowing `except (OSError, UnicodeDecodeError)`
    back to `except OSError` left this file green on 2026-09-01, and only a
    neighbour four files away noticed. A guard whose own test file cannot tell
    whether it is present is one refactor from being dropped.

    `\\xff` is not valid UTF-8 in any position, so the failure is in the READ,
    before any parsing -- which is why an OSError handler walks straight past it.
    """
    from scripts.crm_next import last_interaction_excerpt
    f = tmp_path / "torn.md"
    f.write_bytes(b"---\nname: X\n---\n\n## Interaction Log\n- 2026-05-01 | \xff\xfe\n")

    assert last_interaction_excerpt(f) == "(no prior interaction)"


def test_a_record_that_is_not_there_is_the_documented_sentinel(tmp_path):
    """The first of the docstring's three fallbacks, and the only one that had
    no case. Behaviourally it is reachable two ways -- the `exists()` guard and,
    failing that, the FileNotFoundError arm of the read handler -- so this pins
    the ANSWER rather than which of the two produced it."""
    from scripts.crm_next import last_interaction_excerpt
    assert last_interaction_excerpt(tmp_path / "no-such-contact.md") == (
        "(no prior interaction)")


def test_last_interaction_excerpt_no_log_section(tmp_path):
    """Files without an Interaction Log section return the sentinel."""
    from scripts.crm_next import last_interaction_excerpt
    f = tmp_path / "x.md"
    f.write_text("---\nname: X\n---\n\nNo interaction log here.\n", encoding="utf-8")
    out = last_interaction_excerpt(f)
    assert out == "(no prior interaction)"
