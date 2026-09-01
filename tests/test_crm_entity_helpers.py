"""Tests for the entity helper functions in scripts/utils/crm.py."""

import pytest


# Test fixtures: create a fake address-book/ + contacts/ tree in a tmpdir,
# then point the helpers at it via the workspace_root override.

@pytest.fixture
def fake_workspace(tmp_path, monkeypatch):
    """Set up a fake CRM workspace under tmp_path."""
    crm = tmp_path / "crm"
    (crm / "address-book").mkdir(parents=True)
    (crm / "contacts").mkdir(parents=True)

    # Address book entry
    entity = crm / "address-book" / "karl-mertens.md"
    entity.write_text(
        "---\n"
        "slug: karl-mertens\n"
        "name: Karl Mertens\n"
        "canonical_email: karl.mertens@rivex.com\n"
        "other_emails:\n"
        "  - karl.mertens@rivex.com\n"
        "employer: AllianceCo\n"
        "canonical_owner: alex-rivera\n"
        "created: 2026-03-15\n"
        "linkedin: https://www.linkedin.com/in/karlmertens/\n"
        "---\n\n"
        "# Karl Mertens\n\nBiographical body here.\n",
        encoding="utf-8",
    )

    # Relationship record
    rel = crm / "contacts" / "karl-mertens.md"
    rel.write_text(
        "---\n"
        "entity_ref: karl-mertens\n"
        "relationship_type: partner\n"
        "last_touch: 2026-05-11\n"
        "created: 2026-03-15\n"
        "cadence: 30\n"
        "owner: misha-hanin\n"
        "---\n\n"
        "## Active Commitments\n- Item\n\n"
        "## Interaction Log\n",
        encoding="utf-8",
    )

    monkeypatch.setenv("CRM_WORKSPACE_ROOT", str(tmp_path))
    return tmp_path


# NOTE on `other_emails`: scripts/utils/crm.py uses the string-coerced variant
# of parse_frontmatter, so YAML lists like `other_emails: [a@x.com, b@x.com]`
# come back as the stringified form `"['a@x.com', 'b@x.com']"`. The substring
# `in` check below passes because the full email is contained in that string -
# but it would also pass for partial fragments like "@x" or "x.com". Phase 1's
# auto-log entity resolver needs real list iteration; either parse the string
# back to a list at the call site, or migrate load_entity to use the native
# scripts.utils.markdown.parse_frontmatter that preserves list types.
def test_load_entity_returns_parsed_frontmatter(fake_workspace):
    from scripts.utils.crm import load_entity
    entity = load_entity("karl-mertens", workspace_root=fake_workspace)
    assert entity["name"] == "Karl Mertens"
    assert entity["canonical_email"] == "karl.mertens@rivex.com"
    assert "karl.mertens@rivex.com" in entity["other_emails"]
    assert entity["employer"] == "AllianceCo"


def test_load_entity_missing_returns_none(fake_workspace):
    from scripts.utils.crm import load_entity
    assert load_entity("does-not-exist", workspace_root=fake_workspace) is None


def test_resolve_entity_ref_loads_linked_entity(fake_workspace):
    from scripts.utils.crm import resolve_entity_ref
    rel = {"entity_ref": "karl-mertens"}
    entity = resolve_entity_ref(rel, workspace_root=fake_workspace)
    assert entity is not None
    assert entity["name"] == "Karl Mertens"


def test_resolve_entity_ref_returns_none_for_dangling(fake_workspace):
    from scripts.utils.crm import resolve_entity_ref
    rel = {"entity_ref": "ghost-person"}
    assert resolve_entity_ref(rel, workspace_root=fake_workspace) is None


def test_merge_entity_and_relationship_combines_facts(fake_workspace):
    from scripts.utils.crm import load_entity, merge_entity_and_relationship
    entity = load_entity("karl-mertens", workspace_root=fake_workspace)
    rel = {
        "entity_ref": "karl-mertens",
        "relationship_type": "partner",
        "last_touch": "2026-05-11",
        "cadence": 30,
    }
    merged = merge_entity_and_relationship(entity, rel)
    # From entity:
    assert merged["name"] == "Karl Mertens"
    assert merged["company"] == "AllianceCo"  # employer renamed to company in merge
    assert merged["email"] == "karl.mertens@rivex.com"
    # From relationship:
    assert merged["type"] == "partner"
    assert merged["last_touch"] == "2026-05-11"
    assert merged["cadence"] == 30


def test_merge_with_none_entity_returns_defaulted_keys():
    """Verifies the dangling-entity_ref path: entity-side keys default to '' instead of being absent."""
    from scripts.utils.crm import merge_entity_and_relationship
    rel = {
        "entity_ref": "ghost-person",
        "relationship_type": "prospect",
        "last_touch": "2026-05-15",
    }
    merged = merge_entity_and_relationship(None, rel)
    # Entity-side keys present but empty:
    assert merged["name"] == ""
    assert merged["company"] == ""
    assert merged["email"] == ""
    assert merged["linkedin"] == ""
    assert merged["telegram"] == ""
    assert merged["phone"] == ""
    assert merged["region"] == ""
    assert merged["timezone"] == ""
    # Relationship-side still populated:
    assert merged["type"] == "prospect"
    assert merged["last_touch"] == "2026-05-15"
    assert merged["entity_ref"] == "ghost-person"
    # Default status:
    assert merged["status"] == "active"


def test_scan_contacts_merges_entity_facts(fake_workspace):
    """Verify scan_contacts produces flat records that include entity facts."""
    from scripts.utils.crm import scan_contacts, parse_config

    # Minimal config: partner cadence 30d, yellow 20d, red 30d
    config_file = fake_workspace / "crm" / "config.md"
    config_file.write_text(
        "| Type | Expected Cadence | Yellow Threshold | Red Threshold |\n"
        "|------|-----------------|-----------------|---------------|\n"
        "| partner | 30 | 20 | 30 |\n",
        encoding="utf-8",
    )

    config = parse_config(config_file)
    result = scan_contacts(
        config,
        contacts_dir=fake_workspace / "crm" / "contacts",
        workspace_root=fake_workspace,
    )
    # scan_contacts returns (contacts, tribe_warnings, dangling_refs, stages, aliases)
    contacts, tribe_warnings, dangling_refs, _stages, _aliases = result
    assert len(contacts) == 1
    assert dangling_refs == []
    c = contacts[0]
    assert c["name"] == "Karl Mertens"    # from entity
    assert c["company"] == "AllianceCo"   # from entity (employer)
    assert c["type"] == "partner"              # from relationship (relationship_type)
    assert c["last_touch"] == "2026-05-11"     # from relationship


def test_scan_contacts_records_dangling_entity_refs(fake_workspace, monkeypatch, tmp_path):
    """A relationship file pointing at a missing entity is recorded in dangling_refs
    and its contact record is dropped (name defaults to '' so the post-merge guard skips it)."""
    from scripts.utils.crm import scan_contacts, parse_config

    # Write a config like the happy-path test
    config_file = fake_workspace / "crm" / "config.md"
    config_file.write_text(
        "| Type | Expected Cadence | Yellow Threshold | Red Threshold |\n"
        "|------|-----------------|-----------------|---------------|\n"
        "| partner | 14 | 10 | 14 |\n",
        encoding="utf-8",
    )

    # Add a second relationship file whose entity_ref does NOT exist in the address book
    dangling = fake_workspace / "crm" / "contacts" / "ghost.md"
    dangling.write_text(
        "---\n"
        "entity_ref: ghost-person\n"
        "relationship_type: partner\n"
        "last_touch: 2026-05-15\n"
        "created: 2026-05-15\n"
        "---\n\n"
        "## Interaction Log\n",
        encoding="utf-8",
    )

    config = parse_config(config_file)
    result = scan_contacts(
        config,
        contacts_dir=fake_workspace / "crm" / "contacts",
        workspace_root=fake_workspace,
    )
    contacts, tribe_warnings, dangling_refs, _stages, _aliases = result

    # The dangling ref is captured:
    assert len(dangling_refs) == 1
    assert dangling_refs[0]["file"] == "ghost.md"
    assert dangling_refs[0]["entity_ref"] == "ghost-person"

    # The contacts list contains only the happy-path karl-mertens (the ghost record is dropped):
    assert len(contacts) == 1
    assert contacts[0]["file"] == "karl-mertens.md"


def test_merge_falls_back_to_the_cards_address_when_the_entity_has_none():
    """Four live contacts are in exactly this state, measured 2026-08-29.

    The address-book record EXISTS and its `canonical_email` is empty, while the
    real address is still on the relationship card: four cards, all of them
    `entity_found=True canonical_email=''`. Taking the entity's value
    unconditionally reported those four as having NO email, to `scan_contacts`
    and through it to CRM health, the dashboard, `aggregate-crm`, and
    `/cold-sweep`, which drafts outreach and would have had nowhere to send it.

    Driven through `merge_entity_and_relationship` directly, not through
    `contact_index_by_email`: the index consults the card's inline key itself,
    so it papers over this and cannot detect the regression.
    """
    from scripts.utils.crm import merge_entity_and_relationship

    entity = {"slug": "rowan-vance", "name": "Rowan Vance", "canonical_email": ""}
    rel = {"entity_ref": "rowan-vance", "relationship_type": "lead",
           "email": "rowan@mid-migration.test", "last_touch": "2026-05-28"}

    merged = merge_entity_and_relationship(entity, rel)
    assert merged["email"] == "rowan@mid-migration.test", (
        f"merged email is {merged['email']!r}; the entity carries none, so the "
        f"card's address is the only one that exists")
    assert merged["name"] == "Rowan Vance"


def test_merge_keeps_the_entity_address_when_both_are_present():
    """Anchor: the fallback must not become "the card always wins".

    The address-book record is the biographical truth in the two-tier model. A
    card left holding a stale address after the migration moved the real one
    must not override it.
    """
    from scripts.utils.crm import merge_entity_and_relationship

    entity = {"slug": "dana", "name": "Dana",
              "canonical_email": "dana@nimbus-freight.test"}
    rel = {"entity_ref": "dana", "relationship_type": "customer",
           "email": "stale@old-employer.test"}

    assert merge_entity_and_relationship(entity, rel)["email"] == \
        "dana@nimbus-freight.test"


@pytest.mark.parametrize("raw,expected", [
    (None, []),          # a card written with a bare `tags:` line
    ("", []),
    ("sovereign", ["sovereign"]),   # one bare word, not five characters
    (["a", "b"], ["a", "b"]),
])
def test_merge_always_hands_downstream_a_list_of_tags(raw, expected):
    """`merged["tags"]` is iterated by every consumer of the merged shape, and a
    `[]` default only applies when the key is ABSENT, and a card carrying a bare
    `tags:` parses to None through yaml.safe_load and the key IS present. The
    coercion belongs to `frontmatter_list`; nothing pinned that it is still
    called, so replacing it with `relationship.get("tags", [])` left the whole
    repository green (measured 2026-09-01) while handing every reader a None to
    iterate."""
    from scripts.utils.crm import merge_entity_and_relationship
    rel = {"entity_ref": "x", "relationship_type": "lead", "tags": raw}
    assert merge_entity_and_relationship({"name": "X"}, rel)["tags"] == expected


def test_merge_reports_no_address_when_neither_side_has_one():
    """The empty case must stay empty, or `email` becomes a field that is never
    falsy and every "has an address" check downstream silently passes."""
    from scripts.utils.crm import merge_entity_and_relationship

    merged = merge_entity_and_relationship(
        {"slug": "ghost", "name": "Ghost"}, {"entity_ref": "ghost"})
    assert merged["email"] == ""
