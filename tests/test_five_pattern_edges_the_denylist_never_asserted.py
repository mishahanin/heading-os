"""Five edges of the content-leak denylist that no test ever stood on.

`scripts/utils/content_denylist.py` is the CONTENT layer of the engine leak wall:
the six structural layers ask WHERE a file routes, this one asks WHAT is inside
it. It is well covered on its harvesters and its suppression marker. Its compiled
PATTERN, and the one gate that decides which tokens survive, were not: a
full-scope mutation run on 2026-08-29 mutated twenty-six lines of the module and
five survived, every one of them in those two places.

Each finding below was measured by running the mutated form against real text
before a line of test was written.

1. LONGEST-FIRST ORDERING WAS NEVER OBSERVED. `_compile` sorts tokens by length
   descending, and a comment says why: "a full name wins over its component word
   in reporting". Reversed, the alternation reports the SHORTER token. Measured
   on `{"krellide", "krellide technologies"}` against "We met Krellide
   Technologies today.": longest-first reports `Krellide Technologies`,
   shortest-first reports `Krellide`. The file is refused either way, so nothing
   red turns green -- what degrades is the sentence the operator reads while
   deciding whether the hit is a real leak or a false alarm.

2. `re.escape` WAS NEVER EXERCISED ON A TOKEN THAT NEEDS IT. Curated tokens are
   free text the operator types, so they carry regex metacharacters. Measured
   with the escape removed: the token `q3 (emea) push` stopped matching its own
   real text "the Q3 (EMEA) push slipped" and started matching "the Q3 EMEA push
   slipped" instead, because the parentheses became a group. The token
   `a.b@c.com` matched `aXb@cYcom`. So the failure is BOTH directions at once --
   blind to the entity it was curated for, loud about text that is not it.

3. AN EMPTY DENYLIST HAD NO PINNED BEHAVIOUR. `_compile` short-circuits to
   `_pattern = None` when there are no tokens, which is the state of every public
   clone (no DATA overlay -> degraded and empty). Without the short-circuit the
   alternation is built from an empty string, and `(?:)` matches the EMPTY STRING
   at every boundary: measured on "-- end.", positions 0, 1, 2 and 7 all hit. A
   public clone would then report every line carrying punctuation as a leak.

4. THE LENGTH GATE'S EXEMPTION WAS NEVER TESTED. `_add` applies the five-character
   minimum to BARE single words only; anything holding a space, `@`, `-` or `.`
   is exempt, because a short slug or a short address is still a person. With the
   exemption removed, the four-character contact slug `j-lo` is silently dropped
   and the wall stops guarding that person entirely.

5. THE CURATED LOADER'S ALLOWLIST CHECK WAS NEVER TESTED. `_harvest_curated`
   bypasses the length and stopword gates on purpose (the operator chose these
   tokens) but still honours ALLOW_IDENTITY / ALLOW_FICTIONAL. Remove that and a
   curated list naming the engine's own public identity turns the wall against
   the repo: "31c" is in tracked engine prose by design, so every push would be
   refused until someone edited the gate.

Every test below asserts BOTH directions where a direction exists: the edge holds
AND the opposite input still behaves.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils.content_denylist import (  # noqa: E402
    ALLOW_FICTIONAL,
    ALLOW_IDENTITY,
    Denylist,
    _add,
    _harvest_curated,
)


def _compiled(tokens: dict[str, str] | None = None) -> Denylist:
    dl = Denylist(tokens=dict(tokens or {}))
    dl._compile()
    return dl


# --- 1. longest-first ordering -------------------------------------------------

def test_a_full_name_is_reported_over_its_component_word():
    dl = _compiled({"krellide": "curated:companies",
                     "krellide technologies": "curated:companies"})
    hits = dl.scan_text("We met Krellide Technologies today.")
    assert [h[1] for h in hits] == ["Krellide Technologies"], (
        "shortest-first ordering reports the component word and hides which "
        "entity actually leaked"
    )


def test_the_component_word_alone_is_still_reported():
    """The other direction: ordering must not cost the shorter token its match."""
    dl = _compiled({"krellide": "curated:companies",
                     "krellide technologies": "curated:companies"})
    hits = dl.scan_text("Krellide were late.")
    assert [h[1] for h in hits] == ["Krellide"]


# --- 2. re.escape --------------------------------------------------------------

@pytest.mark.parametrize("token, text", [
    ("q3 (emea) push", "the Q3 (EMEA) push slipped"),
    ("delta+ programme", "the Delta+ programme is live"),
    ("a.b@c.com", "write to a.b@c.com today"),
])
def test_a_token_carrying_regex_metacharacters_matches_itself(token, text):
    dl = _compiled({token: "curated:codenames"})
    assert [h[1].lower() for h in dl.scan_text(text)] == [token], (
        f"{token!r} must be matched as literal text, not compiled as a pattern"
    )


@pytest.mark.parametrize("token, impostor", [
    # Unescaped, the parentheses become a group and the token silently changes
    # into a DIFFERENT string that the real entity does not contain.
    ("q3 (emea) push", "the Q3 EMEA push slipped"),
    # Unescaped, '.' is any character.
    ("a.b@c.com", "mail aXb@cYcom now"),
])
def test_a_metacharacter_token_does_not_match_text_that_is_not_the_entity(token, impostor):
    dl = _compiled({token: "curated:codenames"})
    assert dl.scan_text(impostor) == [], (
        f"{token!r} matched {impostor!r}: the metacharacters were interpreted, "
        "so the gate fires on text naming nobody"
    )


@pytest.mark.parametrize("token, text, expected", [
    # `delta+` unescaped is "delt" then one-or-more "a", so it matches the four
    # characters "delta" and reports THEM. The token is still found, and the
    # operator is told a different string leaked than the one that did.
    ("delta+", "the delta+ programme is live", ["delta+"]),
    # `q3(emea)` unescaped turns the parentheses into a group, so the pattern
    # becomes `q3emea`, which its own real text does not contain. Blind to the
    # entity it was curated for.
    ("q3(emea)", "the q3(emea) push slipped", ["q3(emea)"]),
])
def test_a_single_word_metacharacter_token_needs_the_strict_pattern_escaped(
        token, text, expected):
    """The escape on the STRICT alternation, which had no case that could see it.

    MEASURED 2026-09-01 by deleting `re.escape` from the `alts` join: this
    module and eight sibling denylist files stayed green. Two things hid it.
    Every metacharacter token above this test carries a SPACE, so it is also in
    `_loose_pattern`, whose own `re.escape` is a separate call and still
    matched; and the impostor cases are stopped one layer earlier by the
    substring prefilter in `scan_text`, which needs the token verbatim and so
    can never reach a text that only an unescaped pattern would match.

    A single-word token has neither cover: it is absent from the loose pattern,
    and the case below keeps the token verbatim in the text so the prefilter
    passes and the strict pattern is what decides.
    """
    dl = _compiled({token: "curated:codenames"})
    assert [h[1].lower() for h in dl.scan_text(text)] == expected


def test_the_wrapped_name_pattern_also_escapes_its_words():
    """The second, whitespace-widened pattern builds its own alternation."""
    dl = _compiled({"q3 (emea) push": "curated:codenames"})
    wrapped = "the Q3 (EMEA)\npush slipped"
    assert [h[1].split() for h in dl.scan_text(wrapped)] == [["Q3", "(EMEA)", "push"]]
    assert dl.scan_text("the Q3 EMEA\npush slipped") == []


# --- 3. the empty denylist -----------------------------------------------------

def test_an_empty_denylist_compiles_no_pattern():
    dl = _compiled()
    assert dl._pattern is None
    assert dl._loose_pattern is None


@pytest.mark.parametrize("text", [
    "-- end.",
    "a line with punctuation: yes, really.",
    "",
    "|---|---|",
])
def test_an_empty_denylist_reports_nothing_over_any_text(text):
    """A public clone has no overlay, so this IS the shipped configuration."""
    assert _compiled().scan_text(text) == [], (
        "an empty alternation matches the empty string at every boundary, so a "
        "clone with no DATA overlay would refuse every file carrying punctuation"
    )


def test_a_one_token_denylist_still_compiles_and_fires():
    """The other direction: the short-circuit must not swallow a real list."""
    dl = _compiled({"krellide": "curated:companies"})
    assert dl._pattern is not None
    assert [h[1] for h in dl.scan_text("Krellide called.")] == ["Krellide"]


# --- 4. the length gate and its exemption --------------------------------------

@pytest.mark.parametrize("value", ["j-lo", "a.b", "wu li", "e@f.gh"])
def test_a_short_token_holding_a_separator_is_exempt_from_the_length_gate(value):
    tokens: dict[str, str] = {}
    _add(tokens, value, "crm-slug")
    assert tokens == {value: "crm-slug"}, (
        f"{value!r} is a slug, an address or a two-word name, so the "
        "five-character minimum for BARE words must not reach it"
    )


@pytest.mark.parametrize("value, separator", [
    # "J Lo", the same person the `j-lo` case above is about, arriving by the
    # OTHER route: `_harvest_person_slugs` emits both the slug and
    # `slug.replace("-", " ")`, so one contact produces `j-lo` AND `j lo`.
    ("j lo", "space"),
    ("a b", "space"),
    ("a@b", "at-sign"),
])
def test_a_separator_shorter_than_the_gate_is_still_exempt(value, separator):
    """The space and `@` arms of the exemption, which nothing could observe.

    The four values in the case above are all FIVE characters or longer once the
    hyphen and dot arms are set aside (`wu li` is 5, `e@f.gh` is 6), and the gate
    only refuses below five. So both conjuncts could be deleted with every
    denylist test green. MEASURED 2026-09-01 across the nine test files naming
    this module: dropping `" " not in v` survived, and so did dropping
    `"@" not in v`.

    The space arm is the one with a live route behind it: a two-word name whose
    parts are short is under five characters with the space counted, and the
    harvest emits exactly that form because it is the form prose contains. The
    `@` arm is asserted as the contract `_add`'s comment states rather than as a
    reachable harvest, since a valid address is longer than the gate.
    """
    tokens: dict[str, str] = {}
    _add(tokens, value, "crm-name")
    assert tokens == {value: "crm-name"}, (
        f"{value!r} carries a {separator}, so the five-character minimum for "
        "BARE words must not reach it"
    )


@pytest.mark.parametrize("value", ["abcd", "1234567", "9"])
def test_a_short_bare_word_is_still_refused(value):
    """The other direction: the gate the exemption carves out of must still bite."""
    tokens: dict[str, str] = {}
    _add(tokens, value, "crm-name")
    assert tokens == {}


def test_a_long_bare_word_without_a_letter_is_refused():
    tokens: dict[str, str] = {}
    _add(tokens, "1234567890", "crm-name")
    assert tokens == {}


# --- 5. the curated loader's allowlist -----------------------------------------

def _curated(tmp_path: Path, body: str) -> dict[str, str]:
    path = tmp_path / "content-denylist.yaml"
    path.write_text(body, encoding="utf-8")
    tokens: dict[str, str] = {}
    _harvest_curated(tmp_path, tokens, path)
    return tokens


@pytest.mark.parametrize("allowed", ["31c", "ODUN.ONE", "Acme", "Misha Hanin"])
def test_a_curated_entry_naming_public_identity_is_not_a_token(tmp_path, allowed):
    assert allowed.lower() in (ALLOW_IDENTITY | ALLOW_FICTIONAL)
    tokens = _curated(tmp_path, f"companies:\n  - {allowed}\n")
    assert tokens == {}, (
        f"{allowed!r} is deliberately public and appears throughout the tracked "
        "engine tree; making it a token turns the wall against the repo itself"
    )


def test_a_curated_entry_naming_a_real_entity_is_still_a_token(tmp_path):
    """The other direction: the allowlist check must not empty the curated list."""
    tokens = _curated(tmp_path, "companies:\n  - Krellide Technologies\n")
    assert tokens == {"krellide technologies": "curated:companies"}


def test_the_curated_loader_keeps_bypassing_the_length_and_stopword_gates(tmp_path):
    """A curated token is the operator's choice; only the allowlist overrides it."""
    tokens = _curated(tmp_path, "codenames:\n  - Odin\n  - price\n")
    assert tokens == {"odin": "curated:codenames", "price": "curated:codenames"}
