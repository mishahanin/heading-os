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


def test_last_interaction_excerpt_no_log_section(tmp_path):
    """Files without an Interaction Log section return the sentinel."""
    from scripts.crm_next import last_interaction_excerpt
    f = tmp_path / "x.md"
    f.write_text("---\nname: X\n---\n\nNo interaction log here.\n", encoding="utf-8")
    out = last_interaction_excerpt(f)
    assert out == "(no prior interaction)"
