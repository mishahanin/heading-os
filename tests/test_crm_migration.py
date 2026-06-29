"""Tests for crm_migrate_to_entity_model.py grouping logic."""

import pytest


def test_groups_exact_email_match():
    from scripts.crm_migrate_to_entity_model import group_records
    records = [
        {"owner": "exec-a", "name": "Sam Tester", "email": "sam@example.com", "company": "AllianceCo"},
        {"owner": "exec-b", "name": "Samuel Tester", "email": "sam@example.com", "company": "AllianceCo"},
    ]
    groups = group_records(records)
    assert len(groups) == 1
    g = groups[0]
    assert len(g["records"]) == 2
    assert g["confidence"] == "high"


def test_does_not_group_same_name_different_email():
    from scripts.crm_migrate_to_entity_model import group_records
    records = [
        {"owner": "exec-a", "name": "John Smith", "email": "john@acme.com", "company": "Acme"},
        {"owner": "exec-b", "name": "John Smith", "email": "john@globex.com", "company": "Globex"},
    ]
    groups = group_records(records)
    assert len(groups) == 2  # different people; not merged


def test_low_confidence_grouping_name_match_no_email():
    from scripts.crm_migrate_to_entity_model import group_records
    records = [
        {"owner": "exec-a", "name": "Maria Lopez", "email": "", "company": "Acme Holdings"},
        {"owner": "exec-b", "name": "Maria Lopez", "email": "", "company": "Acme-Holdings"},
    ]
    groups = group_records(records)
    assert len(groups) == 1
    assert groups[0]["confidence"] == "low"  # surface for manual review


def test_canonical_slug_generation():
    from scripts.crm_migrate_to_entity_model import generate_slug
    assert generate_slug("Samuel Tester") == "samuel-tester"
    assert generate_slug("Maria Lopez") == "maria-lopez"
    assert generate_slug("Carol Nguyen") == "carol-nguyen"
    # Collision case
    assert generate_slug("John Smith", existing={"john-smith"}) == "john-smith-2"


def test_pick_canonical_owner_picks_highest_priority():
    from scripts.crm_migrate_to_entity_model import pick_canonical_owner
    records = [
        {"type": "prospect"},
        {"type": "partner"},
    ]
    # Both map to owner-exec-b; either type is fine
    assert pick_canonical_owner(records) == "owner-exec-b"

    records = [
        {"type": "investor-active"},
        {"type": "prospect"},
    ]
    # investor-active wins (higher in priority list)
    assert pick_canonical_owner(records) == "owner-exec-a"


def test_render_address_book_entry_minimal():
    from scripts.crm_migrate_to_entity_model import render_address_book_entry
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        f = Path(tmp) / "src.md"
        f.write_text(
            "---\nname: Sam Tester\nemail: sam@example.com\n---\n\n## Profile\n- Background\n",
            encoding="utf-8",
        )
        group = {
            "proposed_slug": "sam-tester",
            "canonical_name": "Sam Tester",
            "records": [
                {"owner": "owner-exec-a", "file_path": str(f), "name": "Sam Tester",
                 "email": "sam@example.com", "company": "Globex & Co", "type": "partner",
                 "phone": "", "linkedin": "", "region": "Germany", "timezone": "Europe/Berlin"},
            ],
            "confidence": "singleton",
        }
        out = render_address_book_entry(group)
        assert "slug: sam-tester" in out
        assert "name: Sam Tester" in out
        # canonical_email is quoted by _yaml_quote because '@' is a YAML-special char
        assert 'canonical_email: "sam@example.com"' in out
        # employer is quoted by _yaml_quote because '&' is a YAML-special char
        assert 'employer: "Globex & Co"' in out
        assert "canonical_owner: owner-exec-b" in out  # partner -> commercial
        assert "## Profile" in out  # body lifted


def test_render_relationship_record_minimal():
    from scripts.crm_migrate_to_entity_model import render_relationship_record
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        f = Path(tmp) / "src.md"
        f.write_text(
            "---\nname: Sam Tester\nemail: sam@example.com\ntype: partner\nlast_touch: 2026-05-11\ncadence: 14\n---\n\n"
            "## Profile\n- Background note\n\n"
            "## Active Commitments\n- Follow up next week\n\n"
            "## Interaction Log\n- 2026-05-11 | Demo call\n",
            encoding="utf-8",
        )
        record = {
            "owner": "owner-exec-a",
            "file_path": str(f),
            "name": "Sam Tester",
            "email": "sam@example.com",
            "company": "AllianceCo",
            "type": "partner",
            "last_touch": "2026-05-11",
            "cadence": 14,
            "source": "",
        }
        out = render_relationship_record(record, entity_slug="sam-tester")
        # Frontmatter checks:
        assert "entity_ref: sam-tester" in out
        assert "relationship_type: partner" in out
        assert "last_touch: 2026-05-11" in out
        assert "cadence: 14" in out
        assert "pipeline_company: AllianceCo" in out
        assert "owner: owner-exec-a" in out
        assert "status: active" in out
        # Body checks: only Active Commitments + Interaction Log should be kept
        assert "## Active Commitments" in out
        assert "## Interaction Log" in out
        assert "Follow up next week" in out
        assert "2026-05-11 | Demo call" in out
        # Profile section should be filtered out
        assert "## Profile" not in out
        assert "Background note" not in out
