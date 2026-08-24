"""Tests for the engine CONTENT-leak gate (scripts/utils/content_denylist.py).

The gate is the content sibling of the routing guards: it flags real entities
(person slugs/names, handles, e-mails, Telegram IDs, curated company/event tokens)
embedded in engine-routed files. These tests build a denylist from a synthetic
DATA overlay (never the real one) and assert it flags real tokens, exempts the
public-identity + fictional allowlists, honors inline suppression, and degrades to
a no-op when the overlay is absent.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.utils.content_denylist import build_denylist


def _make_overlay(tmp_path: Path) -> Path:
    data = tmp_path / ".heading-os-data"
    (data / "crm" / "contacts").mkdir(parents=True)
    (data / "admin").mkdir(parents=True)
    (data / "config").mkdir(parents=True)
    # a real-ish CRM contact (slug = filename) with organisation + e-mail frontmatter
    (data / "crm" / "contacts" / "zenon-makarios.md").write_text(
        "---\n"
        "name: Zenon Makarios\n"
        "email: zenon@vorlite.test\n"
        "pipeline_company: Vorlite (Somewhere)\n"
        "---\n\n# x", encoding="utf-8")
    # one-word org with a generic tail -> bare head word is a token
    (data / "crm" / "contacts" / "hale-quorix.md").write_text(
        "---\npipeline_company: Quorix Technologies\n---\n", encoding="utf-8")
    # org opening with an ordinary English word -> phrase only, NEVER the word
    (data / "crm" / "contacts" / "ida-route.md").write_text(
        "---\npipeline_company: Route Zenthar\n---\n", encoding="utf-8")
    (data / "crm" / "contacts" / "ola-policy.md").write_text(
        "---\npipeline_company: Policy Vorlite Experts\n---\n", encoding="utf-8")
    # placeholder employer values name nobody
    (data / "crm" / "contacts" / "una-none.md").write_text(
        "---\npipeline_company: Independent (Freelance)\n---\n", encoding="utf-8")
    (data / "crm" / "contacts" / "urs-none.md").write_text(
        "---\npipeline_company: Unknown\n---\n", encoding="utf-8")
    # executives
    (data / "admin" / "executives.json").write_text(
        json.dumps({"executives": [
            {"slug": "vex-thorne", "name": "Vex Thorne", "github_user": "vthorne",
             "data_repo": "heading-os-data-vex-thorne", "status": "active"}
        ]}), encoding="utf-8")
    # The cycle config, in the shape the real file actually has: speakers are
    # PLAIN STRINGS under weeks[].mon / weeks[].wed. This fixture used to write a
    # member dict here instead, which is the shape `_iter_member_dicts` wants but
    # one `fireside-schedule.json` has never carried -- so the test passed against
    # an invented file while the harvester found zero members in the real one, and
    # every Tribe-only member went unguarded. Two real handles and a real full
    # name reached the public repo behind that green test.
    (data / "config" / "fireside-schedule.json").write_text(
        json.dumps({"cycle": 3, "cycle_1_start_monday": "2026-09-21",
                    "weeks": [{"week": 1, "theme": "Origins",
                               "mon": ["Qorvath Lune"], "wed": ["Sethra Vaig"]}]}),
        encoding="utf-8")
    # The membership source of truth, where the member dicts really live.
    fireside_state = data / "datastore" / "operations" / "tribe" / "fireside-state"
    fireside_state.mkdir(parents=True)
    (fireside_state / "tribe-roster.json").write_text(
        json.dumps({"qorvath": {"name": "Qorvath Lune", "telegram_user_id": 581234567,
                                "active": True}}), encoding="utf-8")
    # a config carrying a real-ish e-mail
    (data / "config" / "exec-registry.json").write_text(
        json.dumps({"people": [{"email": "zenon.makarios@realco.test"}]}), encoding="utf-8")
    # curated non-person tokens
    (data / "config" / "content-denylist.yaml").write_text(
        "companies: [\"Krellide Systems\"]\nevents: [\"Vortex Summit\"]\n"
        "competitors: [\"Nullsoft Telco\"]\n", encoding="utf-8")
    return data


def test_flags_harvested_real_entities(tmp_path):
    dl = build_denylist(_make_overlay(tmp_path))
    assert not dl.degraded and dl.tokens
    # slug, exec slug+name, handle, telegram id, email, curated all present
    sample = "deploy to zenon-makarios via vex-thorne; ping qorvath at 581234567"
    hits = {m.lower() for _, m, _ in dl.scan_text(sample)}
    assert "zenon-makarios" in hits
    assert "vex-thorne" in hits
    assert "qorvath" in hits
    assert "581234567" in hits
    assert dl.scan_text("contact zenon.makarios@realco.test")  # email
    assert dl.scan_text("the Krellide Systems deal")           # curated company
    assert dl.scan_text("met them at Vortex Summit")           # curated event


def test_allowlist_and_fictional_not_flagged(tmp_path):
    dl = build_denylist(_make_overlay(tmp_path))
    for safe in (
        "Misha Hanin leads 31 Concept on ODUN.ONE and TrustONE",
        "draft an email to alice and bob about ExampleCorp",
        "the jane-doe exec slug and Acme Globex",
    ):
        assert dl.scan_text(safe) == [], f"false positive on: {safe!r}"


def test_inline_suppression(tmp_path):
    dl = build_denylist(_make_overlay(tmp_path))
    assert dl.scan_text("uses vex-thorne here")  # flagged without marker
    assert dl.scan_text("uses vex-thorne here  # content-guard: ok (fixture)") == []


def test_word_boundary_no_substring_false_positive(tmp_path):
    dl = build_denylist(_make_overlay(tmp_path))
    # 'qorvath' must not match when glued inside a larger identifier
    assert dl.scan_text("qorvathic_helper = 1") == []
    assert dl.scan_text("xqorvath = 1") == []


def test_flags_bare_organisation_name(tmp_path):
    """The 2026-08-08 hole: a contact slug pairs person+employer, so the whole
    slug was a token while the BARE organisation name -- what prose actually
    contains -- matched nothing. Both forms must flag now."""
    dl = build_denylist(_make_overlay(tmp_path))
    for leaked in (
        "Vorlite",                                   # one-word org, bare
        "subject='31C / Quorix - technical call'",   # compound org, bare head word
        'attendees=["someone@quorix.tech"]',         # bare name inside a domain
        "the Route Zenthar integration",             # ordinary-word org, phrase form
    ):
        assert dl.scan_text(leaked), f"MISSED a real organisation in: {leaked!r}"


def test_flags_contact_email_from_crm_frontmatter(tmp_path):
    """A contact's own address lives in CRM frontmatter, which the config-only
    e-mail regex never read."""
    dl = build_denylist(_make_overlay(tmp_path))
    assert dl.scan_text('attendees=["zenon@vorlite.test"]')


def test_organisation_harvest_produces_no_ordinary_word_tokens(tmp_path):
    """The harvest must never turn an organisation's ordinary-English opening
    word into a bare token -- that is the failure mode that makes a gate cry
    wolf until somebody disables it. Phrase-form matching is the fix, so these
    sentences (which use the words, never the names) must stay silent."""
    dl = build_denylist(_make_overlay(tmp_path))
    for benign in (
        "route the request through the policy engine",
        "traffic experts disagree about the route",
        "an independent reviewer, employer unknown",
        "mobile clients follow the same policy",
    ):
        assert dl.scan_text(benign) == [], f"false positive on: {benign!r}"
    for word in ("route", "policy", "traffic", "experts", "mobile",
                 "independent", "unknown"):
        assert word not in dl.tokens, f"ordinary word harvested as a token: {word}"


def test_flags_person_display_name(tmp_path):
    """The 2026-08-23 hole: the harvest emitted a contact's SLUG (`zenon-makarios`)
    and, in strict mode only, its bare words -- never the space-separated form a
    sentence actually contains. So a live counterparty from an open deal thread
    sat by name in a tracked engine test file and every layer of the wall read
    the tree as clean. The two-word phrase is as safe as the organisation phrase
    form already emitted beside it: it cannot collide with ordinary English.

    Writing this test proved the point twice -- the first draft named the real
    person in this docstring, and the fixed gate flagged it.
    """
    dl = build_denylist(_make_overlay(tmp_path))
    for leaked in (
        "Zenon Makarios answered at 08:00 UTC",   # prose, title case
        "assert 'zenon makarios' not in body",    # lower case
        "ping Hale Quorix about the renewal",     # a second contact
    ):
        assert dl.scan_text(leaked), f"MISSED a real person's name in: {leaked!r}"
    # and the bare surname stays out of the default denylist -- the phrase form is
    # the addition, not a back door into strict mode.
    assert dl.scan_text("the makarios report") == []


def test_public_contributor_name_is_not_flagged(tmp_path):
    """A named public contributor is credited on purpose in CHANGELOG.md and
    docs/PLUGINS.md. He is also a CRM contact, so the display-name harvest above
    would flag that deliberate credit as a leak. The allowlist is what keeps the
    gate from crying wolf on published attribution."""
    dl = build_denylist(_make_overlay(tmp_path))
    assert dl.scan_text("Contributed by Mahmoud Maatuq.") == []  # content-guard: ok the published contributor this very assertion allowlists


def test_an_unparseable_curated_list_marks_the_gate_degraded(tmp_path):
    """The 2026-08-23 hole: `_harvest_curated` swallowed its own exception and
    returned, so `build_denylist`'s outer handler never saw it and `degraded`
    stayed False. The gate then ran WITHOUT the operator's hand-curated
    companies, events and codenames -- and reported the tree clean.

    `engine_content_scan` skips entirely on `degraded`, so a silent partial list
    is strictly worse than a loud empty one: it looks like coverage.
    """
    overlay = _make_overlay(tmp_path)
    (overlay / "config" / "content-denylist.yaml").write_text(
        "companies: [unclosed\n  - broken: : :\n", encoding="utf-8")
    dl = build_denylist(overlay)
    assert dl.degraded, "a curated list that failed to parse must degrade the gate"


def test_a_missing_curated_list_is_not_a_degradation(tmp_path):
    """A public clone has no curated file. Absence is normal; corruption is not."""
    overlay = _make_overlay(tmp_path)
    (overlay / "config" / "content-denylist.yaml").unlink()
    dl = build_denylist(overlay)
    assert not dl.degraded
    assert dl.tokens, "the harvested tokens still stand without a curated list"


def test_degrades_without_overlay():
    dl = build_denylist(None)
    assert dl.degraded
    assert dl.tokens == {}
    assert dl.scan_text("zenon-makarios vex-thorne 581234567") == []


def test_strict_adds_name_words_default_does_not(tmp_path):
    overlay = _make_overlay(tmp_path)
    default = build_denylist(overlay, strict=False)
    strict = build_denylist(overlay, strict=True)
    # bare surname word from the slug is only present in strict mode
    assert default.scan_text("the makarios report") == []
    assert strict.scan_text("the makarios report")
    assert len(strict.tokens) > len(default.tokens)
