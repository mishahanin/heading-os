"""The inbox-pulse classifier could not see a CRM contact on the current schema.

A contact card holds its owner's email address in one of two shapes:

    legacy   `email: someone@example.test` inline on the card
    entity   `entity_ref: some-slug`, the address at
             `crm/address-book/some-slug.md::canonical_email`

The entity shape is what `/crm` add-contact writes and what
`crm_migrate_to_entity_model.py --apply` rewrites cards into. It is the current
schema. `scripts/inbox_pulse/rules.py` carried two near-identical walks that read
the inline key ALONE, so a contact on the current schema scored as a total
stranger, and `scripts/inbox-pulse-report.py` scanned frontmatter text for a
line starting `email:` and was blind the same way.

Measured on the operator's real tree, 2026-08-29:

    contact cards: 169
      inline email    + entity_ref     : 89
      no inline email + entity_ref     : 66
      no inline email + no entity_ref  : 14
    addresses the shared CRM reader resolves : 148
    addresses inbox-pulse could see          :  89
    invisible to inbox-pulse                 :  59
      by type: prospect 29, inactive 7, investor-active 7, reseller 6,
               partner 5, government 2, customer 1, shareholder 1,
               investor-declined 1
      worth the maximum CRM weight of 3      :  37
      tribe-leadership among them            :   0

That last line is why the worst consequence is LATENT rather than live, and it
is stated because it would be easy to claim otherwise. `rules.py` short-circuits
an internal Tribe-Leadership sender who writes directly to the operator straight
to HIGH_LIKELY at weight 99, and demotes a non-leadership internal sender to LOW
with the marker `internal_nonlead_to_normal`. Read the block's own contract and
that marker means the CRM was consulted and returned a non-leadership type; a
blind reader made it mean "no card inlines this address". That is the
`.claude/rules/scope-claims.md` defect exactly. It is reproduced below because
it is one migrated card away from being live, but no tribe-leadership contact is
currently invisible, so no leadership email is being demoted today.

What IS live is the weight: 59 real contacts, 37 of them worth 3, scoring 0.

The failure hid itself twice over. The report's "LOW items from known good
domains (potential false negatives)" section exists to surface exactly case one,
and `load_known_crm_domains` was blind through the same key, so an invisible
contact's domain was not "known good" there either. It was filed under "Unknown
domains" with an `Add to always_normal` tuning suggestion beside it: the report
would have advised the operator to permanently suppress a customer the
classifier had already failed to recognise.

Why twelve green tests missed it: `tests/inbox_pulse/test_rules.py`'s
`_write_crm_contact` fixture wrote a card carrying BOTH keys. No producer makes
that card. The `entity_ref` line made every test that used it LOOK
migration-aware while the inline `email:` kept feeding the reader the one shape
it could parse. The one negative case,
`test_classify_internal_sender_no_crm_contact_treated_as_non_leadership`,
asserts the demotion fires when there is NO card at all, never when a card
exists and the reader cannot see into it.

Both readers now go through `scripts.utils.crm.contact_index_by_email`, the
shared reader. The tests here parametrize over all three real card shapes.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from scripts.inbox_pulse.overrides import RulesEngine
from scripts.inbox_pulse.rules import CheapClassifier

_RULES_YAML = """\
sender_overrides:
  always_critical: []
  always_important: []
  always_normal: []
keyword_overrides:
  promote_to_critical: []
  promote_to_important: []
quiet_hours:
  start: "23:00"
  end: "07:00"
  timezone: "Etc/GMT-4"
breakthrough_allowlist: []
internal_domains:
  - "31c.io"
cost_ceiling:
  monthly_anthropic_usd: 50
  warn_at_percent: 80
"""

# The three shapes that exist on the operator's tree, with the live counts.
SCHEMAS = ["legacy", "entity", "hybrid"]


def _now() -> datetime:
    return datetime(2026, 5, 28, 12, 0, 0, tzinfo=timezone.utc)


def _card(contacts_dir: Path, slug: str, email: str, rel_type: str, schema: str):
    """One contact card in `schema`, plus its address-book record when needed."""
    lines = ["---"]
    if schema in ("entity", "hybrid"):
        lines.append(f"entity_ref: {slug}")
    lines += [f"relationship_type: {rel_type}", ]
    if schema in ("legacy", "hybrid"):
        lines.append(f"email: {email}")
    lines += ["last_touch: 2026-05-28", "status: active", "---", "", f"# {slug}", ""]
    (contacts_dir / f"{slug}.md").write_text("\n".join(lines), encoding="utf-8")

    if schema in ("entity", "hybrid"):
        book = contacts_dir.parent / "address-book"
        book.mkdir(parents=True, exist_ok=True)
        # `hybrid` deliberately has NO canonical_email: that is the real
        # mid-migration state, where the address is still on the card. Four
        # live contacts sit in exactly that state.
        canonical = f"canonical_email: {email}\n" if schema == "entity" else ""
        (book / f"{slug}.md").write_text(
            f"---\nslug: {slug}\nname: {slug}\n{canonical}---\n", encoding="utf-8")


@pytest.fixture
def tree(tmp_path):
    """A data root with an empty CRM, plus the config the rules engine needs."""
    (tmp_path / "crm" / "contacts").mkdir(parents=True)
    (tmp_path / "crm" / "address-book").mkdir(parents=True)
    (tmp_path / "context").mkdir(parents=True)
    (tmp_path / "threads" / "business").mkdir(parents=True)
    (tmp_path / "rules.yaml").write_text(_RULES_YAML, encoding="utf-8")
    return tmp_path


def _classifier(workspace_root: Path, data_root: Path, my_email: str | None = None):
    rules = RulesEngine(yaml_path=workspace_root / "rules.yaml")
    return CheapClassifier(rules=rules, workspace_root=workspace_root,
                           data_root=data_root, my_email=my_email)


# ============================================================
# The CRM weight, on every card shape
# ============================================================

@pytest.mark.parametrize("schema", SCHEMAS)
def test_a_customer_scores_the_high_value_weight_on_every_card_shape(tree, schema):
    """3, not 0. On the entity shape this returned 0 before 2026-08-29."""
    _card(tree / "crm" / "contacts", "dana-okonkwo",
          "dana@nimbus-freight.test", "customer", schema)
    c = _classifier(tree, tree)

    assert c._score_crm_contact("dana@nimbus-freight.test") == 3, (
        f"schema={schema}: a customer scored "
        f"{c._score_crm_contact('dana@nimbus-freight.test')}; the CRM signal is "
        f"the largest non-override weight in the seven-signal aggregate")
    assert c._lookup_relationship_type("dana@nimbus-freight.test") == "customer"


@pytest.mark.parametrize("schema", SCHEMAS)
def test_a_low_value_contact_still_scores_one_on_every_card_shape(tree, schema):
    """Anti-vacuity: 3 must mean high-value, not "any card at all"."""
    _card(tree / "crm" / "contacts", "rowan-vance",
          "rowan@harbourline.test", "vendor", schema)
    c = _classifier(tree, tree)

    assert c._score_crm_contact("rowan@harbourline.test") == 1
    assert c._lookup_relationship_type("rowan@harbourline.test") == "vendor"


@pytest.mark.parametrize("schema", SCHEMAS)
def test_the_match_is_case_insensitive_on_every_card_shape(tree, schema):
    _card(tree / "crm" / "contacts", "dana-okonkwo",
          "Dana@Nimbus-Freight.Test", "customer", schema)
    c = _classifier(tree, tree)
    assert c._score_crm_contact("dana@nimbus-freight.TEST") == 3


def test_a_stranger_still_scores_zero(tree):
    """The other anti-vacuity end: a reader that returned 3 for everything would
    satisfy every test above."""
    _card(tree / "crm" / "contacts", "dana-okonkwo",
          "dana@nimbus-freight.test", "customer", "entity")
    c = _classifier(tree, tree)
    assert c._score_crm_contact("nobody@elsewhere.test") == 0
    assert c._lookup_relationship_type("nobody@elsewhere.test") is None


def test_a_dangling_entity_ref_scores_zero_and_does_not_raise(tree):
    """A card pointing at an address-book file that does not exist has no
    address anywhere, so it owns nothing. It must not crash the classifier."""
    (tree / "crm" / "contacts" / "ghost.md").write_text(
        "---\nentity_ref: ghost\nrelationship_type: customer\n"
        "last_touch: 2026-05-28\nstatus: active\n---\n", encoding="utf-8")
    c = _classifier(tree, tree)
    assert c._score_crm_contact("ghost@nowhere.test") == 0


def test_an_empty_crm_directory_scores_zero(tree):
    assert _classifier(tree, tree)._score_crm_contact("anyone@example.test") == 0


def test_a_missing_crm_directory_scores_zero(tmp_path):
    """No `crm/` at all, which is a fresh clone. Must be 0, never an exception."""
    assert _classifier(tmp_path, tmp_path)._score_crm_contact("a@b.test") == 0


# ============================================================
# The Tribe-Leadership short-circuit, and the marker that lied
# ============================================================

@pytest.mark.parametrize("schema", SCHEMAS)
def test_a_leadership_sender_writing_to_the_operator_reaches_high_likely(tree, schema):
    """Latent, not live: no tribe-leadership contact is currently invisible.

    Reproduced anyway, because it is one migrated card away. On the entity shape
    this came back LOW at weight 0 with `sender_override='internal_nonlead_to_normal'`,
    a marker whose contract means "the CRM was read and said not leadership".
    """
    _card(tree / "crm" / "contacts", "sam-tan",
          "sam@31c.io", "tribe-leadership", schema)
    c = _classifier(tree, tree, my_email="operator@31c.io")

    out = c.classify(sender_email="sam@31c.io", subject="quick question",
                     now=_now(), recipients_to=["operator@31c.io"])

    assert out["tier_guess"] == "HIGH_LIKELY", (
        f"schema={schema}: leadership writing directly to the operator came "
        f"back {out['tier_guess']} with "
        f"{out['reason_breakdown']['sender_override']!r}")
    assert out["weight"] == 99


def test_a_non_leadership_internal_sender_is_still_demoted(tree):
    """Anchor for the test above. The demotion must survive the fix, or the
    HIGH_LIKELY assertions prove only that everything is promoted."""
    _card(tree / "crm" / "contacts", "pat-lee", "pat@31c.io", "tribe", "entity")
    c = _classifier(tree, tree, my_email="operator@31c.io")

    out = c.classify(sender_email="pat@31c.io", subject="fyi",
                     now=_now(), recipients_to=["operator@31c.io"])
    assert out["tier_guess"] == "LOW"
    assert out["reason_breakdown"]["sender_override"] == "internal_nonlead_to_normal"


# ============================================================
# One reader, not three
# ============================================================

def test_the_classifier_reads_through_the_shared_crm_index(tree, monkeypatch):
    """The wiring, asserted directly.

    Two copies of the card walk lived in `rules.py` and a third text scan lived
    in `inbox-pulse-report.py`. A fix that landed in one of three is how this
    defect was born, so the delegation itself is pinned: patching the shared
    reader must change what the classifier sees.
    """
    from scripts.utils import crm

    _card(tree / "crm" / "contacts", "dana-okonkwo",
          "dana@nimbus-freight.test", "customer", "entity")
    calls: list = []

    real = crm.contact_index_by_email

    def spy(*args, **kwargs):
        calls.append(kwargs)
        return real(*args, **kwargs)

    monkeypatch.setattr(crm, "contact_index_by_email", spy)
    c = _classifier(tree, tree)
    assert c._score_crm_contact("dana@nimbus-freight.test") == 3
    assert calls, "the classifier did not go through the shared CRM reader"
    assert calls[0]["workspace_root"] == tree


def test_the_index_is_built_once_per_classifier(tree, monkeypatch):
    """The old shape reopened all 169 cards per question, and there are two
    questions per email. Caching is the reason the shared reader is affordable
    here, so it is asserted rather than assumed."""
    from scripts.utils import crm

    _card(tree / "crm" / "contacts", "dana-okonkwo",
          "dana@nimbus-freight.test", "customer", "entity")
    builds = []
    real = crm.contact_index_by_email
    monkeypatch.setattr(crm, "contact_index_by_email",
                        lambda *a, **k: (builds.append(1), real(*a, **k))[1])

    c = _classifier(tree, tree)
    for _ in range(5):
        c._score_crm_contact("dana@nimbus-freight.test")
        c._lookup_relationship_type("dana@nimbus-freight.test")

    assert len(builds) == 1, f"the index was rebuilt {len(builds)} times"


# ============================================================
# Which of the two addresses wins, and the four live cards that need it
# ============================================================

def test_an_entity_with_no_canonical_email_falls_back_to_the_card(tree):
    """Four live contacts are in exactly this state.

    The address-book record EXISTS and its `canonical_email` is empty, while the
    real address is still sitting on the relationship card. Taking the entity's
    value unconditionally reported those four as having no email at all, to CRM
    health, the dashboard, `aggregate-crm` and `/cold-sweep`, which drafts
    outreach and would have had nowhere to send it.
    """
    from scripts.utils.crm import contact_index_by_email

    contacts = tree / "crm" / "contacts"
    (contacts / "rowan-vance.md").write_text(
        "---\nentity_ref: rowan-vance\nrelationship_type: lead\n"
        "email: rowan@mid-migration.test\nlast_touch: 2026-05-28\nstatus: active\n---\n",
        encoding="utf-8")
    (tree / "crm" / "address-book" / "rowan-vance.md").write_text(
        "---\nslug: rowan-vance\nname: Rowan Vance\n---\n", encoding="utf-8")

    index = contact_index_by_email(contacts_dir=contacts, workspace_root=tree)
    assert "rowan@mid-migration.test" in index, (
        "the entity exists with an empty canonical_email, so the card's address "
        "is the only one there is")
    assert index["rowan@mid-migration.test"]["type"] == "lead"

    assert _classifier(tree, tree)._score_crm_contact("rowan@mid-migration.test") == 1


def test_the_entity_wins_when_both_carry_an_address(tree):
    """Anchor for the fallback: it must not become "the card always wins".

    The two-tier model says the address-book record is the biographical truth.
    A card left holding a stale address after the migration moved the real one
    must not override it.
    """
    from scripts.utils.crm import contact_index_by_email

    contacts = tree / "crm" / "contacts"
    (contacts / "dana-okonkwo.md").write_text(
        "---\nentity_ref: dana-okonkwo\nrelationship_type: customer\n"
        "email: stale@old-employer.test\nlast_touch: 2026-05-28\nstatus: active\n---\n",
        encoding="utf-8")
    (tree / "crm" / "address-book" / "dana-okonkwo.md").write_text(
        "---\nslug: dana-okonkwo\nname: Dana\n"
        "canonical_email: dana@nimbus-freight.test\n---\n", encoding="utf-8")

    index = contact_index_by_email(contacts_dir=contacts, workspace_root=tree)
    record = index.get("stale@old-employer.test")
    assert record is not None, (
        "the card's own address must still resolve; the classifier sees mail "
        "arriving from it")
    assert record["email"] == "dana@nimbus-freight.test", (
        f"the merged record reports {record['email']!r}; the entity is the "
        f"biographical truth and must win the `email` field")


def test_two_cards_claiming_one_address_resolve_deterministically(tree):
    """A duplicate is a data error, and a reader must still answer the same way
    every run. Cards are walked in sorted filename order and the first wins."""
    from scripts.utils.crm import contact_index_by_email

    contacts = tree / "crm" / "contacts"
    for slug, rel in (("aaa-first", "customer"), ("zzz-second", "vendor")):
        (contacts / f"{slug}.md").write_text(
            f"---\nrelationship_type: {rel}\nemail: shared@example.test\n"
            f"last_touch: 2026-05-28\nstatus: active\n---\n", encoding="utf-8")

    answers = {contact_index_by_email(contacts_dir=contacts,
                                      workspace_root=tree)["shared@example.test"]["type"]
               for _ in range(3)}
    assert answers == {"customer"}, (
        f"got {answers}; the first card in sorted order must win, every run")


def test_the_relationship_lookup_is_also_case_insensitive(tree):
    """`_score_crm_contact` and `_lookup_relationship_type` are two entry points
    and both take the address from the caller. Covering one left the other free
    to regress, which a mutation proved."""
    _card(tree / "crm" / "contacts", "dana-okonkwo",
          "Dana@Nimbus-Freight.Test", "customer", "entity")
    c = _classifier(tree, tree)
    assert c._lookup_relationship_type("dana@nimbus-freight.TEST") == "customer"


# ============================================================
# The fixture that hid the defect must keep writing the real shapes
# ============================================================

def test_the_shared_fixture_writes_three_genuinely_different_cards(tmp_path):
    """`tests/inbox_pulse/test_rules.py::_write_crm_contact` is the fixture that
    let twelve green tests miss this. It wrote a card carrying BOTH keys, which
    no producer makes, so every test using it looked migration-aware while the
    inline `email:` fed the reader the one shape it could parse.

    Pinned here because a fixture nothing asserts about can drift back: three
    mutations that collapsed the schemas all survived until this test existed.
    """
    from tests.inbox_pulse.test_rules import _write_crm_contact

    contacts = tmp_path / "crm" / "contacts"
    contacts.mkdir(parents=True)

    for schema in ("legacy", "entity", "hybrid"):
        _write_crm_contact(contacts, f"c-{schema}", f"{schema}@example.test",
                           "customer", schema=schema)

    def card(schema):
        return (contacts / f"c-{schema}.md").read_text(encoding="utf-8")

    def book(schema):
        p = contacts.parent / "address-book" / f"c-{schema}.md"
        return p.read_text(encoding="utf-8") if p.exists() else None

    # legacy: address on the card, no entity at all.
    assert "email: legacy@example.test" in card("legacy")
    assert "entity_ref:" not in card("legacy")
    assert book("legacy") is None

    # entity: no inline address; the address-book record carries it.
    assert "email: entity@example.test" not in card("entity")
    assert "entity_ref: c-entity" in card("entity")
    assert "canonical_email: entity@example.test" in book("entity")

    # hybrid: both keys, and an address-book record WITHOUT a canonical_email,
    # which is the real mid-migration state four live contacts are in.
    assert "email: hybrid@example.test" in card("hybrid")
    assert "entity_ref: c-hybrid" in card("hybrid")
    assert "canonical_email" not in book("hybrid")

    # And all three must resolve to the same address through the shared reader,
    # which is the property the whole shard is about.
    from scripts.utils.crm import contact_index_by_email
    index = contact_index_by_email(contacts_dir=contacts, workspace_root=tmp_path)
    for schema in ("legacy", "entity", "hybrid"):
        assert f"{schema}@example.test" in index, f"{schema} did not resolve"


def test_the_parametrized_schemas_cover_every_shape_on_the_tree():
    """Dropping a case from `SCHEMAS` silently removes coverage, and a mutation
    that dropped `entity` survived until this test existed."""
    assert set(SCHEMAS) == {"legacy", "entity", "hybrid"}
