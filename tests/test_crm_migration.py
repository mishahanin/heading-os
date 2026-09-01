"""Tests for crm_migrate_to_entity_model.py grouping logic."""

import pytest


def test_groups_exact_email_match():
    from scripts.crm_migrate_to_entity_model import group_records
    records = [
        {"owner": "exec-a", "name": "Marlow Tester", "email": "marlow@example.com", "company": "AllianceCo"},
        {"owner": "exec-b", "name": "Felix Leiter", "email": "marlow@example.com", "company": "AllianceCo"},
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
    assert generate_slug("Felix Leiter") == "felix-leiter"
    assert generate_slug("Maria Lopez") == "maria-lopez"
    assert generate_slug("Carol Moneypenny") == "carol-moneypenny"
    # Collision case
    assert generate_slug("John Smith", existing={"john-smith"}) == "john-smith-2"


def test_the_collision_suffix_keeps_counting_past_two():
    """One collision cannot tell a counter from a constant.

    The single case above is satisfied by `return f"{base}-2"`, and measured
    2026-09-01 that replacement left this file and all 16 other files naming
    the migration green. The suffix loop only matters on the SECOND collision,
    and that is not a hypothetical shape: this migration slugs the whole address
    book in one pass, so three people called John Smith is an ordinary Tuesday
    in a CRM of any size.

    What a frozen `-2` costs is the thing the migration exists to prevent.
    `generate_slug` is how a group gets its identity; two groups handed the same
    slug write the same address-book file, and the second overwrites the first.
    The migration would report both as migrated.
    """
    from scripts.crm_migrate_to_entity_model import generate_slug
    assert generate_slug("John Smith", existing={"john-smith", "john-smith-2"}) == \
        "john-smith-3"
    assert generate_slug(
        "John Smith",
        existing={"john-smith", "john-smith-2", "john-smith-3"},
    ) == "john-smith-4"
    # Every slug this yields is distinct from every name already taken, which is
    # the property the loop is actually for.
    taken = {"john-smith"}
    for _ in range(4):
        fresh = generate_slug("John Smith", existing=taken)
        assert fresh not in taken, f"{fresh!r} collides with {sorted(taken)}"
        taken.add(fresh)


def test_a_name_with_no_sluggable_characters_still_gets_a_slug():
    """The `if not base: base = "unnamed"` guard, which nothing exercised.

    Every name in this file is plain ASCII, so the guard could be deleted and
    stay green (measured 2026-09-01 across 17 files). The names it exists for
    are the ones a real address book holds: a record whose `name` is a
    Cyrillic or Arabic string, or punctuation, or empty because the source file
    had no `name:` at all. `re.sub(r"[^a-z0-9\\s-]", ...)` strips every one of
    those to nothing.

    An empty slug is not a cosmetic defect. The slug becomes the address-book
    FILENAME, so an empty one writes `.md` -- a dotfile -- and every such record
    lands on the same one, each overwriting the last.
    """
    from scripts.crm_migrate_to_entity_model import generate_slug
    assert generate_slug("") == "unnamed"
    assert generate_slug("!!!") == "unnamed"
    assert generate_slug("Мария Волкова") == "unnamed"
    # And the collision path still applies to it, so two unnameable records do
    # not both become `unnamed`.
    assert generate_slug("", existing={"unnamed"}) == "unnamed-2"


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


def test_an_unrecognised_type_falls_back_to_the_documented_default():
    """"Defaults to owner-exec-a for unknown/missing types" -- untested until
    2026-09-01, when changing that literal left all 17 files green.

    Every case above hands the function a type the policy table already holds,
    so the default was free to be anything, including a slug no owner answers
    to. This is a MIGRATION: the owner it picks is written into the address-book
    entry, and a record filed under an owner that does not exist belongs to
    nobody and appears in no owner's view.

    Three shapes reach the same fallback and all three are real -- a legacy type
    string the policy never learned, a record with no `type` key, and a `type`
    explicitly set to None by a source file with a bare `type:` line.
    """
    from scripts.crm_migrate_to_entity_model import pick_canonical_owner
    assert pick_canonical_owner([{"type": "some-retired-type"}]) == "owner-exec-a"
    assert pick_canonical_owner([{}]) == "owner-exec-a"
    assert pick_canonical_owner([{"type": None}]) == "owner-exec-a"
    assert pick_canonical_owner([]) == "owner-exec-a"
    # A recognised type anywhere in the group still wins over the fallback, so
    # this cannot pass by the function having stopped consulting the table.
    assert pick_canonical_owner(
        [{"type": "some-retired-type"}, {"type": "partner"}]) == "owner-exec-b"


def test_render_address_book_entry_minimal():
    from scripts.crm_migrate_to_entity_model import render_address_book_entry
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        f = Path(tmp) / "src.md"
        f.write_text(
            "---\nname: Marlow Tester\nemail: marlow@example.com\n---\n\n## Profile\n- Background\n",
            encoding="utf-8",
        )
        group = {
            "proposed_slug": "marlow-tester",
            "canonical_name": "Marlow Tester",
            "records": [
                {"owner": "owner-exec-a", "file_path": str(f), "name": "Marlow Tester",
                 "email": "marlow@example.com", "company": "Globex & Co", "type": "partner",
                 "phone": "", "linkedin": "", "region": "Germany", "timezone": "Europe/Berlin"},
            ],
            "confidence": "singleton",
        }
        out = render_address_book_entry(group)
        assert "slug: marlow-tester" in out
        assert "name: Marlow Tester" in out
        # canonical_email is quoted by _yaml_quote because '@' is a YAML-special char
        assert 'canonical_email: "marlow@example.com"' in out
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
            "---\nname: Marlow Tester\nemail: marlow@example.com\ntype: partner\nlast_touch: 2026-05-11\ncadence: 14\n---\n\n"
            "## Profile\n- Background note\n\n"
            "## Active Commitments\n- Follow up next week\n\n"
            "## Interaction Log\n- 2026-05-11 | Demo call\n",
            encoding="utf-8",
        )
        record = {
            "owner": "owner-exec-a",
            "file_path": str(f),
            "name": "Marlow Tester",
            "email": "marlow@example.com",
            "company": "AllianceCo",
            "type": "partner",
            "last_touch": "2026-05-11",
            "cadence": 14,
            "source": "",
        }
        out = render_relationship_record(record, entity_slug="marlow-tester")
        # Frontmatter checks:
        assert "entity_ref: marlow-tester" in out
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
