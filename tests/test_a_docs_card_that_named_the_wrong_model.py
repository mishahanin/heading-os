#!/usr/bin/env python3
"""A published card named a model the skill stopped running on a month earlier.

`docs/skills-intel.html` said of `/notebooklm`: "Runs on the Sonnet model,
driving the `notebooklm-mcp-cli` (`nlm`) tool". Its frontmatter says
`model: haiku`.

MEASURED 2026-08-29. The sentence was written in commit 258e928 (2026-07-08),
when it was true. Commit cbd4ef7 (2026-08-09) changed the frontmatter to haiku
and did not touch the card, so the public catalog spent a month quoting the
model the skill used to run on. Nothing compared the two: the docs suite checked
the lock badge against `disable-model-invocation`, the mode table against the
generator CLI, and the hooks page against the hook registry, but no test read a
model claim at all.

The claim is not decorative. The model a skill runs on is what a reader budgets
cost and latency against, and it is the first thing anyone forking the engine
changes. A card that names the wrong one sends them to edit a value that is
already something else.

Scope, measured on the same day: 11 of the 94 cards mention a model word, and 10
of those are claims about the skill's own model, in three phrasings that this
file registers. The eleventh is `/osint` describing "a Haiku tool-use
extraction" inside `scripts/resolve_entity.py`, which is a script's model and not
the skill's; a scan that read every occurrence of the word would report it
forever, so the phrasings are matched rather than the word, and the leftovers are
declared.

The reverse direction is deliberately narrow. 54 skills set `model:` in
frontmatter and 10 cards state it, so "every skill must document its model" is
not the convention and asserting it would invent a policy nobody adopted. What
IS enforced in the other direction is the shape that bit `/checkpoint` in the
badge audit: a card asserting a model for a skill whose frontmatter sets none,
where the documentation is the only place the value exists.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tests.repo_files import tracked_paths  # noqa: E402

CATALOG = ROOT / "docs" / "skills-mcp-plugins.html"

_CARD_ANCHOR = re.compile(r'<h3 id="s-([a-z0-9-]+)"')
_FRONTMATTER_MODEL = re.compile(r"^model:\s*(\S+)\s*$", re.M)

# The three ways a card states the model its skill runs on. Matched as phrases,
# not as the bare word, so prose that mentions a model for another reason does
# not become a claim about the skill.
_MODEL_CLAIM = re.compile(
    r"on the (?P<prose>Opus|Sonnet|Haiku) model"
    r"|an? (?P<compound>Opus|Sonnet|Haiku)-model skill"
    r"|<code>model:\s*(?P<quoted>opus|sonnet|haiku)</code>",
    re.I,
)

# Any occurrence of a model word, used only to find phrasings the registry above
# would miss.
_MODEL_WORD = re.compile(r"\b(opus|sonnet|haiku)\b", re.I)

# Card mentions of a model word that are NOT claims about the skill's own model,
# each with the reason. Anything else unmatched is a new phrasing that slipped
# past `_MODEL_CLAIM`, not a licence to ignore it.
NOT_A_SKILL_MODEL_CLAIM = {
    "osint": "the Haiku named here belongs to the tool-use extraction inside "
             "scripts/resolve_entity.py, not to the skill",
}


def _skill_files() -> list[Path]:
    return tracked_paths((".claude/skills/*/SKILL.md",))


def _category_pages() -> list[Path]:
    """The pages that carry cards. The catalog index carries no prose about
    models, so it has nothing for this scan to read."""
    return [p for p in tracked_paths(("docs/skills-*.html",)) if p != CATALOG]


def frontmatter_models() -> dict[str, str | None]:
    """Every skill on disk, mapped to its declared model or None."""
    models: dict[str, str | None] = {}
    for skill in _skill_files():
        text = skill.read_text(encoding="utf-8")
        block = text.split("---", 2)[1] if text.startswith("---") else ""
        found = _FRONTMATTER_MODEL.search(block)
        models[skill.parent.name] = found.group(1).strip().lower() if found else None
    return models


def _cards(text: str) -> list[tuple[str, str]]:
    """(anchor, body) for every card on one page."""
    hits = [(m.start(), m.group(1)) for m in _CARD_ANCHOR.finditer(text)]
    return [
        (name, text[start:hits[i + 1][0] if i + 1 < len(hits) else len(text)])
        for i, (start, name) in enumerate(hits)
    ]


def documented_models() -> dict[str, str]:
    """Every card that states which model its skill runs on."""
    claims: dict[str, str] = {}
    for page in _category_pages():
        for name, body in _cards(page.read_text(encoding="utf-8")):
            stated = {m.group(m.lastgroup).lower() for m in _MODEL_CLAIM.finditer(body)}
            if stated:
                # A card naming two different models contradicts itself; keep both
                # so the comparison below reports it instead of picking one.
                claims[name] = "/".join(sorted(stated))
    return claims


def undeclared_model_mentions() -> dict[str, list[str]]:
    """Cards whose model word no registered phrasing explains."""
    loose: dict[str, list[str]] = {}
    claimed = documented_models()
    for page in _category_pages():
        for name, body in _cards(page.read_text(encoding="utf-8")):
            if name in NOT_A_SKILL_MODEL_CLAIM or name in claimed:
                continue
            words = sorted({m.group(1).lower() for m in _MODEL_WORD.finditer(body)})
            if words:
                loose[name] = words
    return loose


def model_claim_drift(claims: dict[str, str], models: dict[str, str | None]) -> list[str]:
    """The predicate: one line per card whose stated model is not the real one.

    ``claims`` maps a card anchor to the model its prose names; ``models`` maps a
    skill directory to the model its frontmatter sets, or None when it sets none.
    A skill with a model and no claim is not a violation, because stating the
    model is not required of a card. A claim with no matching key is, because
    then the prose is the only place the value exists.
    """
    drift: list[str] = []
    for name in sorted(claims):
        stated = claims[name]
        if name not in models:
            drift.append(f"{name}: card says {stated}, no such skill on disk")
        elif models[name] is None:
            drift.append(f"{name}: card says {stated}, frontmatter sets no model")
        elif models[name] != stated:
            drift.append(f"{name}: card says {stated}, frontmatter says {models[name]}")
    return drift


# --- the live tree -------------------------------------------------------------

def test_no_card_names_a_model_the_skill_does_not_run_on():
    drift = model_claim_drift(documented_models(), frontmatter_models())
    assert drift == [], (
        "these cards state a model that disagrees with the skill's frontmatter: "
        f"{drift}. The frontmatter is what the harness reads, so the card is the "
        "one to fix unless the model itself is wrong."
    )


def test_every_model_word_in_a_card_is_either_a_claim_or_declared():
    """A new phrasing would otherwise be invisible: the scan would go on passing
    while the sentence it cannot parse drifts."""
    loose = undeclared_model_mentions()
    assert loose == {}, (
        f"these cards name a model in prose no registered phrasing matches: "
        f"{loose}. Either write it in one of the registered forms so it is "
        "checked, or add the card to NOT_A_SKILL_MODEL_CLAIM with the reason."
    )


def test_the_declared_exceptions_still_mention_a_model():
    """An exemption whose subject has gone stops meaning anything, and the next
    reader trusts it anyway."""
    bodies = {
        name: body
        for page in _category_pages()
        for name, body in _cards(page.read_text(encoding="utf-8"))
    }
    stale = sorted(
        name for name in NOT_A_SKILL_MODEL_CLAIM
        if name not in bodies or not _MODEL_WORD.search(bodies[name])
    )
    assert stale == [], (
        f"NOT_A_SKILL_MODEL_CLAIM exempts cards that no longer name a model: {stale}"
    )


# --- the predicate, on synthetic input, in both directions ---------------------
#
# Over the now-clean tree, deleting the line that appends a violation changes no
# result above. These build the input that contains one.

def test_the_predicate_reports_a_card_naming_the_wrong_model():
    drift = model_claim_drift({"notebooklm": "sonnet"}, {"notebooklm": "haiku"})
    assert drift == ["notebooklm: card says sonnet, frontmatter says haiku"]


def test_the_predicate_reports_a_claim_the_frontmatter_does_not_back():
    drift = model_claim_drift({"osint": "sonnet"}, {"osint": None})
    assert drift == ["osint: card says sonnet, frontmatter sets no model"]


def test_the_predicate_reports_a_card_for_a_skill_that_is_gone():
    drift = model_claim_drift({"retired": "opus"}, {"osint": None})
    assert drift == ["retired: card says opus, no such skill on disk"]


def test_the_predicate_accepts_a_card_that_agrees():
    assert model_claim_drift({"notebooklm": "haiku"}, {"notebooklm": "haiku"}) == []


def test_the_predicate_does_not_demand_that_every_skill_be_documented():
    """The direction this file deliberately does not enforce. 44 skills set a
    model no card states; a predicate that reported them would be reporting the
    convention."""
    models = {"osint": None, "crm": "sonnet", "backup": "haiku"}
    assert model_claim_drift({"crm": "sonnet"}, models) == []


def test_the_predicate_reports_a_self_contradicting_card():
    """Two models in one card arrive joined, so they cannot equal either value
    and the card is reported rather than half-read."""
    drift = model_claim_drift({"crm": "haiku/sonnet"}, {"crm": "sonnet"})
    assert drift == ["crm: card says haiku/sonnet, frontmatter says sonnet"]


# --- the claim parser reads the three real phrasings ---------------------------

def test_the_parser_reads_the_prose_phrasing():
    page = '<h3 id="s-alpha"><p>Runs on the Sonnet model, driving a CLI.</p>'
    assert dict(_extract(page)) == {"alpha": "sonnet"}


def test_the_parser_reads_the_compound_phrasing():
    page = '<h3 id="s-alpha"><p>Drives a script (a Haiku-model skill) with subcommands.</p>'
    assert dict(_extract(page)) == {"alpha": "haiku"}


def test_the_parser_reads_the_quoted_frontmatter_phrasing():
    page = '<h3 id="s-alpha"><p><code>model: sonnet</code> is set in frontmatter.</p>'
    assert dict(_extract(page)) == {"alpha": "sonnet"}


def test_the_parser_does_not_read_a_model_named_for_another_reason():
    page = '<h3 id="s-alpha"><p>Runs a Haiku tool-use extraction to expand aliases.</p>'
    assert dict(_extract(page)) == {}


def test_the_parser_keeps_cards_apart():
    page = (
        '<h3 id="s-alpha"><p>Runs on the Sonnet model.</p>'
        '<h3 id="s-beta"><p>Runs on the Haiku model.</p>'
    )
    assert dict(_extract(page)) == {"alpha": "sonnet", "beta": "haiku"}


def _extract(page_text: str) -> list[tuple[str, str]]:
    """`documented_models()` over one string instead of the tree."""
    out = []
    for name, body in _cards(page_text):
        stated = {m.group(m.lastgroup).lower() for m in _MODEL_CLAIM.finditer(body)}
        if stated:
            out.append((name, "/".join(sorted(stated))))
    return out


# --- the sweep must reach a real corpus ----------------------------------------

def test_the_sweep_reaches_a_real_corpus():
    """A rule that is green because it scanned nothing is a known defect shape
    here, and this one has three ways to scan nothing: no pages, no cards, no
    claims."""
    pages = _category_pages()
    models = frontmatter_models()
    claims = documented_models()
    assert len(pages) >= 8, f"found only {len(pages)} category pages: {[p.name for p in pages]}"
    assert len(models) > 80, f"read frontmatter for only {len(models)} skills"
    assert sum(1 for m in models.values() if m) > 40, (
        "almost no skill declares a model, so the comparison has nothing to hold "
        "the cards against"
    )
    assert len(claims) >= 8, f"found only {len(claims)} cards stating a model: {sorted(claims)}"
    assert set(claims) <= set(models), (
        f"cards without a skill: {sorted(set(claims) - set(models))}"
    )


def test_the_notebooklm_card_states_the_model_its_frontmatter_sets():
    """The original defect, named, so a silent regression on this card cannot
    hide inside a passing aggregate."""
    assert frontmatter_models()["notebooklm"] == "haiku"
    assert documented_models()["notebooklm"] == "haiku"
