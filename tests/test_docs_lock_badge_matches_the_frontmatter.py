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

The scan above could not see the one page most readers open first.
`_category_pages()` returned `sorted(DOCS.glob("skills-*.html"))` filtered by
`p != CATALOG`, so `docs/skills-mcp-plugins.html` was excluded outright, and the
exclusion was not arbitrary: the catalog carries no cards at all. MEASURED
2026-08-29, it holds 0 occurrences of `<h3 id="s-`, and its 93 skill references
are index-table anchors of a shape no other page uses:

    <a class="cmd-link" href="skills-operations-quality.html#s-canopus"
       ><code>/canopus</code></a> 🔒

The card regex cannot match that, so the page was skipped rather than parsed,
and the badge on the index table went unchecked from the day it was written.

Drift followed. MEASURED 2026-08-29: 22 skills carry the key, 21 of them are
linked from the index, and 20 of those 21 rows carry the glyph. The one that did
not was `/scrutinize`, which has `disable-model-invocation: true` on line 3 of
its own `SKILL.md`, wears the badge on its card in
`skills-operations-quality.html`, and is listed "NEVER auto-trigger" by
`.claude/rules/skill-router.md`. Three surfaces agreed and the index quietly
disagreed, on the page whose own legend defines the glyph.

The fix is a second parser, not a deleted exclusion. The card scan still runs
over the category pages, because that is the only place cards exist; the index
table gets a reader of its own shape, and both feed the same predicate.
"""
from __future__ import annotations

import html
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tests.repo_files import read_sources, tracked_paths  # noqa: E402

# Every child this file spawns is `git` in a scratch tree, and `git` has never
# read HEADING_OS_DATA. Pinning it away from the operator's live overlay costs
# these tests nothing and removes them from the reachability ratchet in
# tests/conftest.py. See the `scratch_data_root` fixture for the measurement.
pytestmark = pytest.mark.usefixtures("scratch_data_root")

DOCS = ROOT / "docs"
CATALOG = DOCS / "skills-mcp-plugins.html"
LOCK = "\U0001f512"

_CARD = re.compile(r'<h3 id="s-([a-z0-9-]+)".*?<span class="badge">([^<]*)</span>')
_LOCK_KEY = re.compile(r"^disable-model-invocation:\s*true\s*$", re.M)

# One row of the catalog index table. The glyph, when present, sits in the bare
# text just past the closing anchor: `</a> 🔒`, before the comma that separates
# it from the next command. The window therefore runs to the next tag, which is
# where the neighbouring link begins, so a marked command cannot lend its glyph
# to the unmarked one in front of it. Running to the next tag rather than a fixed
# character count also leaves room for the `&#128274;` entity form, which is
# banned below but must not be silently misread while it is.
_INDEX_LINK = re.compile(r'<a class="cmd-link" href="[^"#]*#s-([a-z0-9-]+)"><code>/[^<]+</code></a>')

# Skills deliberately absent from the published site, each with the decision
# behind it. Anything else missing a card is a gap, not a policy.
NO_CARD_BY_DECISION = {
    "modem-tune": "operator decision 2026-08-23: personal-hardware tool, not "
                  "published on the docs site. See "
                  "tests/test_modem_tune_is_not_on_the_docs_site.py",
}


def _skill_files() -> list[Path]:
    return tracked_paths((".claude/skills/*/SKILL.md",))


def _all_pages() -> list[Path]:
    """Every published skills page, index included."""
    return tracked_paths(("docs/skills-*.html",))


def _category_pages() -> list[Path]:
    """The pages that carry cards. The catalog carries the index table instead,
    which `_index_entries()` reads with a parser of its own shape."""
    return [p for p in _all_pages() if p != CATALOG]


def _sources(paths: list[Path], what: str) -> list[tuple[Path, str]]:
    """`(path, text)` for a walked list, or a failure naming what disappeared.

    The walk and the read are two moments, and on a checkout several agents
    share a file can be gone by the second one. Every caller of this helper is
    building one side of a SET COMPARISON -- which skills carry the key, which
    cards carry the glyph, which pages carry the legend -- so a file dropped
    quietly does not narrow the answer, it CHANGES it: a lost SKILL.md turns its
    correctly-badged card into a card claiming a lock that does not exist, and a
    lost page turns every skill carded only there into a skill with no card.
    Both read as drift that is not there, and the opposite direction reads as
    clean when it is not.

    So the race is read through `read_sources`, retried once, and then FAILS
    naming the file. This is the count/completeness half of the rule, not the
    scan half: there is no correct answer to give over a corpus that shrank.
    """
    lost: list[Path] = []
    out = list(read_sources(paths, lost))
    if lost:
        still_gone: list[Path] = []
        out += list(read_sources(lost, still_gone))
        if still_gone:
            raise AssertionError(
                f"{what} disappeared between the walk and the read and is still "
                "gone on retry; the badge-versus-key comparison cannot be made "
                "over a file nobody read: "
                + ", ".join(str(p) for p in still_gone))
    return out


def _locked_skills() -> set[str]:
    locked = set()
    for skill, text in _sources(_skill_files(), "a SKILL.md"):
        if not text.startswith("---"):
            continue
        frontmatter = text.split("---", 2)[1]
        if _LOCK_KEY.search(frontmatter):
            locked.add(skill.parent.name)
    return locked


def lock_badge_drift(marked: dict[str, bool], locked: set[str]) -> tuple[list[str], list[str]]:
    """The predicate both surfaces are held to, over one surface at a time.

    ``marked`` maps every skill the surface mentions to whether that mention
    carries the glyph. Returns the two directions of drift: names claiming a lock
    the frontmatter does not set, and names the frontmatter locks whose mention
    stays silent about it. A skill the surface does not mention is not this
    function's business; the presence tests below own that.
    """
    claimed_without_key = sorted(name for name, shown in marked.items() if shown and name not in locked)
    key_without_claim = sorted(name for name, shown in marked.items() if not shown and name in locked)
    return claimed_without_key, key_without_claim


def index_entries(text: str) -> dict[str, bool]:
    """Every command linked from the catalog index, and whether its row locks it."""
    entries: dict[str, bool] = {}
    for match in _INDEX_LINK.finditer(text):
        rest = text[match.end():]
        stop = rest.find("<")
        tail = html.unescape(rest if stop < 0 else rest[:stop])
        entries[match.group(1)] = LOCK in tail
    return entries


def _index_entries() -> dict[str, bool]:
    return index_entries(CATALOG.read_text(encoding="utf-8"))


def _badged_cards() -> set[str]:
    """Cards whose badge shows the lock, entity or literal."""
    badged = set()
    for _page, text in _sources(_category_pages(), "a category page"):
        for anchor, badge in _CARD.findall(text):
            if LOCK in html.unescape(badge):
                badged.add(anchor)
    return badged


def _all_cards() -> set[str]:
    cards = set()
    for _page, text in _sources(_category_pages(), "a category page"):
        cards |= set(re.findall(r'<h3 id="s-([a-z0-9-]+)"', text))
    return cards


# --- the two directions of badge drift, on the cards ---------------------------

def test_no_card_claims_a_lock_the_skill_does_not_have():
    marked = {anchor: anchor in _badged_cards() for anchor in _all_cards()}
    wrong, _ = lock_badge_drift(marked, _locked_skills())
    assert wrong == [], (
        f"these cards show 🔒 but their SKILL.md has no "
        f"`disable-model-invocation: true`: {wrong}. Either the skill really "
        "auto-routes and the badge is wrong, or the control is missing and the "
        "badge is the only thing enforcing it."
    )


def test_every_locked_skill_that_has_a_card_shows_the_lock():
    marked = {anchor: anchor in _badged_cards() for anchor in _all_cards()}
    _, missing = lock_badge_drift(marked, _locked_skills())
    assert missing == [], (
        f"these skills cannot be triggered from natural language but their "
        f"card does not say so: {missing}"
    )


# --- and the same two directions on the catalog index table --------------------

def test_no_index_row_claims_a_lock_the_skill_does_not_have():
    wrong, _ = lock_badge_drift(_index_entries(), _locked_skills())
    assert wrong == [], (
        f"these rows of the catalog index show 🔒 but their SKILL.md has no "
        f"`disable-model-invocation: true`: {wrong}"
    )


def test_every_locked_skill_in_the_index_shows_the_lock():
    """The direction that was blind until 2026-08-29, and that /scrutinize fell
    through."""
    _, missing = lock_badge_drift(_index_entries(), _locked_skills())
    assert missing == [], (
        f"these skills cannot be triggered from natural language but their row "
        f"in {CATALOG.name} does not say so: {missing}. The index is the first "
        "page a reader opens, and it carries the legend that defines the glyph."
    )


def test_the_index_and_the_cards_agree_on_which_skills_are_locked():
    """A row and its card are two renderings of one key. When they disagree,
    both tests above can still pass, because each reads only its own surface."""
    index_locked = {name for name, shown in _index_entries().items() if shown}
    carded_and_indexed = _all_cards() & set(_index_entries())
    disagree = sorted((index_locked ^ _badged_cards()) & carded_and_indexed)
    assert disagree == [], (
        f"the catalog index and the per-category card disagree on the lock for: "
        f"{disagree}"
    )


def test_every_index_row_points_at_a_card_that_exists():
    """The index parser proves nothing if it is reading links to nowhere."""
    dangling = sorted(set(_index_entries()) - _all_cards())
    assert dangling == [], (
        f"the catalog index links to anchors no category page defines: {dangling}"
    )


# --- coverage: a skill with no card at all -------------------------------------

def test_every_skill_has_a_card_or_a_recorded_reason():
    on_disk = {p.parent.name for p in _skill_files()}
    missing = sorted(on_disk - _all_cards() - set(NO_CARD_BY_DECISION))
    assert missing == [], (
        f"skills with no card on any category page: {missing}. Add a card, or "
        "add the name to NO_CARD_BY_DECISION with the decision behind it."
    )


def test_the_deliberate_omissions_are_still_real_skills():
    """An allowlist that outlives its subject silently stops meaning anything."""
    on_disk = {p.parent.name for p in _skill_files()}
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

# Read at COLLECTION through the same `_sources` helper the rest of this file
# uses, and parametrize over the text. The walk ran when the decorator was
# evaluated and a per-case `page.read_text()` ran at execution -- minutes later
# under `-n auto` -- so a page removed inside that window raised
# FileNotFoundError from inside the glyph guard. This is a COMPLETENESS claim
# ("every page writes the lock literally"), so a skip would be exactly wrong:
# `_sources` retries once and then FAILS naming the file, which is this module's
# stated position on a corpus that shrank.
_PAGE_SOURCES = _sources(_all_pages(), "a skills page")


@pytest.mark.parametrize("page,text", _PAGE_SOURCES,
                         ids=[p.name for p, _ in _PAGE_SOURCES])
def test_the_lock_glyph_is_written_literally(page, text):
    """One page used `&#128274;`. Both render, but a mixed corpus means every
    future check has to remember to unescape, and one will forget."""
    assert "&#128274;" not in text, (
        f"{page.name} writes the lock as an HTML entity; the other pages use "
        "the literal 🔒"
    )


def test_the_legend_still_defines_the_badge():
    """If the sentence goes, the badge means nothing and these tests guard a
    symbol with no stated meaning."""
    # "every page carries the legend" is a completeness claim: a page skipped
    # because it vanished would be a page this test reports on without reading.
    for page, text in _sources(_all_pages(), "a skills page"):
        assert "explicit-invocation only" in text, (
            f"{page.name} lost the legend defining 🔒"
        )


# --- the predicate, on synthetic input, in both directions ---------------------
#
# Over a clean tree the tests above pass whether or not the drift is collected at
# all, so the collection itself is exercised here on input built to contain it.

def test_the_predicate_reports_a_claim_with_no_key():
    wrong, missing = lock_badge_drift({"backup": True, "osint": True}, {"backup"})
    assert (wrong, missing) == (["osint"], [])


def test_the_predicate_reports_a_key_with_no_claim():
    wrong, missing = lock_badge_drift({"backup": False, "osint": False}, {"backup"})
    assert (wrong, missing) == ([], ["backup"])


def test_the_predicate_reports_both_at_once():
    marked = {"backup": False, "osint": True, "recall": False, "sync": True}
    wrong, missing = lock_badge_drift(marked, {"backup", "sync"})
    assert (wrong, missing) == (["osint"], ["backup"])


def test_the_predicate_is_silent_when_the_surface_agrees():
    marked = {"backup": True, "osint": False}
    assert lock_badge_drift(marked, {"backup"}) == ([], [])


def test_the_predicate_ignores_a_locked_skill_the_surface_never_mentions():
    """`modem-tune` is locked and deliberately unpublished. A predicate that
    reported it would make the deliberate omission unrepresentable."""
    assert lock_badge_drift({"backup": True}, {"backup", "modem-tune"}) == ([], [])


def test_the_index_parser_reads_a_row_of_the_real_shape():
    row = (
        '<tr><td><a class="cmd-link" href="skills-operations-quality.html#s-create-plan">'
        '<code>/create-plan</code></a>, '
        '<a class="cmd-link" href="skills-operations-quality.html#s-canopus">'
        '<code>/canopus</code></a> \U0001f512</td><td>Plan then execute.</td></tr>'
    )
    assert index_entries(row) == {"create-plan": False, "canopus": True}


def test_the_index_parser_does_not_borrow_the_next_rows_glyph():
    """The window stops at the next tag on purpose. One that ran past it would
    read the glyph of the command after the comma and mark both."""
    row = (
        '<a class="cmd-link" href="p.html#s-alpha"><code>/alpha</code></a>, '
        '<a class="cmd-link" href="p.html#s-beta"><code>/beta</code></a> \U0001f512'
    )
    assert index_entries(row) == {"alpha": False, "beta": True}


def test_the_index_parser_unescapes_the_entity_form():
    row = '<a class="cmd-link" href="p.html#s-alpha"><code>/alpha</code></a> &#128274;'
    assert index_entries(row) == {"alpha": True}


# --- the detectors must be able to fail ----------------------------------------

def test_the_detectors_are_not_vacuous():
    assert len(_locked_skills()) > 15, "parsed almost no locked skills"
    assert len(_badged_cards()) > 15, "parsed almost no badged cards"
    assert len(_all_cards()) > 80, "parsed almost no cards"


def test_the_index_scan_reaches_a_real_corpus():
    """A sweep that parsed nothing is green, and that is a known defect shape
    here: this scan was structurally blind to this exact page until 2026-08-29."""
    entries = _index_entries()
    assert len(_all_pages()) == len(_category_pages()) + 1, (
        "the catalog is no longer one of the pages this scan reads"
    )
    assert len(entries) > 80, f"parsed only {len(entries)} rows out of the catalog index"
    assert sum(entries.values()) > 15, (
        f"only {sum(entries.values())} index rows carry the lock; the parser is "
        "probably missing the glyph rather than the catalog being unlocked"
    )
    assert len(_locked_skills() & set(entries)) > 15, (
        "almost no locked skill is reachable from the index, so the direction "
        "that /scrutinize fell through is not being measured"
    )
