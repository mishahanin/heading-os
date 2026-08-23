"""The 🔒 badge on a docs card must mean what every page says it means.

Every `docs/skills-*.html` page carries the same legend: "Commands marked 🔒 are
explicit-invocation only." The mechanism behind that sentence is one frontmatter
key, `disable-model-invocation: true`, documented in
`.claude/rules/development-standards.md`: "the model cannot trigger the skill
from natural language or as a tool."

Nothing compared the badge to the key. The 2026-08-23 engine audit found four
drifts, in both directions:

  * `/odin` wore the badge while its own card said "Auto-routes on 'Odin' as a
    name or address", and its frontmatter has no lock. The badge was wrong.
  * `/checkpoint` wore the badge and had no lock either, but here the badge was
    right and the enforcement was missing: its description says "NEVER
    auto-trigger", the router says "Explicit /checkpoint only", and
    `development-standards.md` lists it BY NAME among the adopters of the key.
    Three documents asserted a control that did not exist. The key was added.
  * `/queue-draft` is locked and had no card at all, so the one skill built to
    demonstrate the draft/send boundary was missing from the public catalog.
  * `/canopus` looked unbadged to the first version of this scan because
    `skills-operations-quality.html` wrote the glyph as `&#128274;` while the
    other eight pages used the literal. A detector that does not unescape sees
    a page that renders correctly as broken; that page is now normalized and
    this scan unescapes anyway.

The badge is the only signal a reader gets about whether saying a word can fire
a skill. When it disagrees with the key, one of them is lying and the reader
cannot tell which.
"""
from __future__ import annotations

import html
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
SKILLS = ROOT / ".claude" / "skills"
CATALOG = DOCS / "skills-mcp-plugins.html"
LOCK = "\U0001f512"

_CARD = re.compile(r'<h3 id="s-([a-z0-9-]+)".*?<span class="badge">([^<]*)</span>')
_LOCK_KEY = re.compile(r"^disable-model-invocation:\s*true\s*$", re.M)

# Skills deliberately absent from the published site, each with the decision
# behind it. Anything else missing a card is a gap, not a policy.
NO_CARD_BY_DECISION = {
    "modem-tune": "operator decision 2026-08-23: personal-hardware tool, not "
                  "published on the docs site. See "
                  "tests/test_modem_tune_is_not_on_the_docs_site.py",
}


def _category_pages() -> list[Path]:
    return [p for p in sorted(DOCS.glob("skills-*.html")) if p != CATALOG]


def _locked_skills() -> set[str]:
    locked = set()
    for skill in sorted(SKILLS.glob("*/SKILL.md")):
        text = skill.read_text(encoding="utf-8")
        if not text.startswith("---"):
            continue
        frontmatter = text.split("---", 2)[1]
        if _LOCK_KEY.search(frontmatter):
            locked.add(skill.parent.name)
    return locked


def _badged_cards() -> set[str]:
    """Cards whose badge shows the lock, entity or literal."""
    badged = set()
    for page in _category_pages():
        for anchor, badge in _CARD.findall(page.read_text(encoding="utf-8")):
            if LOCK in html.unescape(badge):
                badged.add(anchor)
    return badged


def _all_cards() -> set[str]:
    cards = set()
    for page in _category_pages():
        cards |= set(re.findall(r'<h3 id="s-([a-z0-9-]+)"', page.read_text(encoding="utf-8")))
    return cards


# --- the two directions of badge drift ----------------------------------------

def test_no_card_claims_a_lock_the_skill_does_not_have():
    wrong = sorted(_badged_cards() - _locked_skills())
    assert wrong == [], (
        f"these cards show 🔒 but their SKILL.md has no "
        f"`disable-model-invocation: true`: {wrong}. Either the skill really "
        "auto-routes and the badge is wrong, or the control is missing and the "
        "badge is the only thing enforcing it."
    )


def test_every_locked_skill_that_has_a_card_shows_the_lock():
    carded_and_locked = _locked_skills() & _all_cards()
    missing = sorted(carded_and_locked - _badged_cards())
    assert missing == [], (
        f"these skills cannot be triggered from natural language but their "
        f"card does not say so: {missing}"
    )


# --- coverage: a skill with no card at all -------------------------------------

def test_every_skill_has_a_card_or_a_recorded_reason():
    on_disk = {p.parent.name for p in SKILLS.glob("*/SKILL.md")}
    missing = sorted(on_disk - _all_cards() - set(NO_CARD_BY_DECISION))
    assert missing == [], (
        f"skills with no card on any category page: {missing}. Add a card, or "
        "add the name to NO_CARD_BY_DECISION with the decision behind it."
    )


def test_the_deliberate_omissions_are_still_real_skills():
    """An allowlist that outlives its subject silently stops meaning anything."""
    on_disk = {p.parent.name for p in SKILLS.glob("*/SKILL.md")}
    stale = sorted(set(NO_CARD_BY_DECISION) - on_disk)
    assert stale == [], f"NO_CARD_BY_DECISION names skills that no longer exist: {stale}"


def test_the_deliberate_omissions_really_have_no_card():
    """And the reverse: a skill listed as omitted that has since been carded."""
    carded = sorted(set(NO_CARD_BY_DECISION) & _all_cards())
    assert carded == [], (
        f"these are listed as deliberately undocumented but now have a card: "
        f"{carded}"
    )


# --- the glyph must be readable by the same scan on every page -----------------

@pytest.mark.parametrize("page", _category_pages(), ids=lambda p: p.name)
def test_the_lock_glyph_is_written_literally(page):
    """One page used `&#128274;`. Both render, but a mixed corpus means every
    future check has to remember to unescape, and one will forget."""
    text = page.read_text(encoding="utf-8")
    assert "&#128274;" not in text, (
        f"{page.name} writes the lock as an HTML entity; the other pages use "
        "the literal 🔒"
    )


def test_the_legend_still_defines_the_badge():
    """If the sentence goes, the badge means nothing and these tests guard a
    symbol with no stated meaning."""
    for page in _category_pages():
        text = page.read_text(encoding="utf-8")
        assert "explicit-invocation only" in text, (
            f"{page.name} lost the legend defining 🔒"
        )


# --- the detectors must be able to fail ----------------------------------------

def test_the_detectors_are_not_vacuous():
    assert len(_locked_skills()) > 15, "parsed almost no locked skills"
    assert len(_badged_cards()) > 15, "parsed almost no badged cards"
    assert len(_all_cards()) > 80, "parsed almost no cards"
