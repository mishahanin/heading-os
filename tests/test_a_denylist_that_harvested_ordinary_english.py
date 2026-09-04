"""A content gate that harvested two ordinary English words and blocked every push.

MEASURED 2026-09-04. Two CRM rows written that afternoon carried employers of
the shape ``<ordinary English word> Technologies`` and ``<ordinary English word>
Solutions``. Both tails are in ``_ORG_GENERIC``, so ``_org_token_forms`` reduced
each name to its opening word and emitted that word BARE. Within the hour
``scripts/content-guard.py --all`` was refusing the tree over 128 lines of
ordinary engine prose -- 42 tracked files holding one of the two words, 27 the
other, among them ``.claude/hooks/_dispatch.py`` and five skills -- and every
push on the machine was blocked. A third token of the same shape had been living
behind five inline ``content-guard: ok`` annotations since before that.

The rule that emitted them ("a bare head-word only when stripping generic words
leaves exactly the first word") is necessary and not sufficient: it counts the
words of the name and never asks what the surviving one MEANS. The fix is a
derived floor, ``_apply_ordinary_english_floor``, over the vocabulary in
``config/ordinary-english.txt``. The two rejected repairs are recorded here
because both restore green and leave the defect in place: renaming the two
companies trades a true record for a passing check, and adding two words to
``STOPWORDS`` grows a hand-maintained security list that falls behind in silence.

BOTH DIRECTIONS, and the halves were RUN against the pre-fix module rather than
reasoned about. Three of the four "must not poison" assertions fail there: the
bare word is emitted, ``scan_text`` fires on ordinary prose, and there is no
``withheld`` to report the coverage change with. The fourth, that the multi-word
phrase survives, passes at HEAD and must keep passing -- it is the invariant the
fix must not trade away, not a defect. All three "must still be guarded"
assertions pass at HEAD, which is what makes them an anchor: they say the fix
took nothing away. An entity whose single word is genuinely distinctive keeps
its bare token, verified on the live overlay against four organisations from the
same CRM batch (their names stay in the DATA overlay and are not repeated here).

Run: python3 -m pytest tests/test_a_denylist_that_harvested_ordinary_english.py
"""
from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.utils import content_denylist as cd  # noqa: E402
from scripts.utils.content_denylist import build_denylist, is_ordinary_english  # noqa: E402
from scripts.utils.engine_guard import CONTENT_SCAN_EXEMPT, engine_text_rels  # noqa: E402

ARTIFACT = ROOT / "config" / "ordinary-english.txt"

# Placeholder vocabulary. Every word below was checked on 2026-09-04 against the
# live denylist and is not a real token, so this file names nobody.
ORDINARY = "gentle"          # in config/ordinary-english.txt
ORDINARY_SOLO = "harbor"     # in the artifact, used as a ONE-WORD organisation
DISTINCTIVE = "zenthavik"    # in no English vocabulary


def _overlay(tmp_path: Path, orgs: dict[str, str]) -> Path:
    """A synthetic DATA overlay carrying only the organisations given.

    Never the real overlay: these assertions are about the RULE, and a test that
    reads the operator's contacts changes its verdict every time he adds one.
    """
    data = tmp_path / "data"
    contacts = data / "crm" / "contacts"
    contacts.mkdir(parents=True)
    for slug, org in orgs.items():
        (contacts / f"{slug}.md").write_text(
            f"---\npipeline_company: {org}\n---\n\n# note\n", encoding="utf-8")
    return data


# ============================================================
# The direction that must FAIL against the pre-fix module
# ============================================================

def test_an_ordinary_english_head_word_is_not_a_bare_token(tmp_path):
    """`<ordinary word> Technologies` must not donate the bare word.

    This is the assertion the pre-fix module fails: it emitted the word and the
    gate then fired on every piece of engine prose that used it.
    """
    dl = build_denylist(_overlay(tmp_path, {"ida-ordinary": f"{ORDINARY} Technologies"}))
    assert ORDINARY not in dl.tokens
    assert dl.withheld.get(ORDINARY) == "crm-org"


def test_the_multi_word_phrase_form_survives(tmp_path):
    """The match is NARROWED, never removed. Losing the phrase too would be the
    quiet failure this floor exists to avoid."""
    dl = build_denylist(_overlay(tmp_path, {"ida-ordinary": f"{ORDINARY} Technologies"}))
    assert f"{ORDINARY} technologies" in dl.tokens


def test_the_gate_no_longer_fires_on_prose_but_still_fires_on_the_name(tmp_path):
    """Driven through `scan_text`, the observable consequence, not the token set."""
    dl = build_denylist(_overlay(tmp_path, {"ida-ordinary": f"{ORDINARY} Technologies"}))
    assert dl.scan_text(f"a {ORDINARY} reminder about the release\n") == []
    hits = dl.scan_text(f"we met {ORDINARY.title()} Technologies at the summit\n")
    assert [h[1].lower() for h in hits] == [f"{ORDINARY} technologies"]


def test_the_floor_reports_what_it_withheld(tmp_path):
    """A floor that removes coverage in silence is worse than the noise it fixes,
    so the removal is data on the object and printed on the clean line."""
    dl = build_denylist(_overlay(tmp_path, {"ida-ordinary": f"{ORDINARY} Solutions"}))
    assert dict(dl.withheld) == {ORDINARY: "crm-org"}


# ============================================================
# The direction that must still PASS -- no real coverage dropped
# ============================================================

def test_a_distinctive_one_word_organisation_still_yields_its_bare_token(tmp_path):
    """The whole gap the organisation harvest closes. Four organisations from the
    same live CRM batch were checked against the real overlay on 2026-09-04 and
    all four keep their bare token; this is the same shape with an invented name.
    """
    dl = build_denylist(_overlay(tmp_path, {"ida-distinct": DISTINCTIVE.title()}))
    assert DISTINCTIVE in dl.tokens
    assert dl.withheld == {}
    assert [h[1].lower() for h in dl.scan_text(f"contact at {DISTINCTIVE.title()}\n")] \
        == [DISTINCTIVE]


def test_a_distinctive_head_word_with_a_generic_tail_still_yields_it(tmp_path):
    """`Zenthavik Technologies` keeps BOTH forms. Only the meaning of the word
    decides, never the shape of the name."""
    dl = build_denylist(_overlay(tmp_path, {"ida-d2": f"{DISTINCTIVE.title()} Technologies"}))
    assert DISTINCTIVE in dl.tokens
    assert f"{DISTINCTIVE} technologies" in dl.tokens


def test_an_ordinary_word_with_no_longer_form_is_kept(tmp_path):
    """A one-word organisation that IS an ordinary English word keeps its token.

    Withholding it would delete the entity's only form, and the floor refuses
    that even at the cost of a noisy gate. Over-reporting is the safe direction;
    a silent hole is not.
    """
    dl = build_denylist(_overlay(tmp_path, {"ida-solo": ORDINARY_SOLO.title()}))
    assert ORDINARY_SOLO in dl.tokens
    assert dl.withheld == {}


def test_an_unrelated_phrase_ending_in_the_word_is_not_coverage(tmp_path):
    """`Alem Harbor` is a different entity, so it cannot license withholding
    `harbor`. Measured on the live overlay: a containment test read exactly this
    as coverage and deleted one organisation's only form."""
    dl = build_denylist(_overlay(tmp_path, {
        "ida-solo": ORDINARY_SOLO.title(),
        "ida-other": f"Alem {ORDINARY_SOLO.title()}",
    }))
    assert ORDINARY_SOLO in dl.tokens


def test_a_handle_is_never_withheld_even_when_the_members_name_opens_with_it(tmp_path):
    """A handle is an IDENTIFIER, not a fragment of the member's name.

    MEASURED 2026-09-04: without `_FRAGMENT_CATEGORIES` the floor withheld the
    handle of a roster record whose member name opened with the same ordinary
    word, on the strength of a name token that is a different identifier of the
    same person. That re-opens the leak class the roster harvest was added for,
    where two real handles and a real full name sat in a tracked engine test
    while the gate reported the surface clean.
    """
    import json
    data = _overlay(tmp_path, {})
    state = data / "datastore" / "operations" / "tribe" / "fireside-state"
    state.mkdir(parents=True)
    (state / "tribe-roster.json").write_text(json.dumps({
        ORDINARY: {"name": f"{ORDINARY.title()} Person", "active": True}
    }), encoding="utf-8")
    dl = build_denylist(data)
    assert ORDINARY in dl.tokens
    assert dl.tokens[ORDINARY] == "handle"
    assert dl.withheld == {}


def test_every_harvest_category_is_classified():
    """The floor's three sets must not fall behind a new harvester.

    Read out of the MODULE's AST rather than from a fixture, so a category
    introduced by a harvest no test happens to exercise still lands here. A
    category in no set would be kept by omission, and silence about which of the
    three reasons applies is how a hand-maintained security list goes stale.
    """
    import ast

    src = ast.parse((ROOT / "scripts" / "utils" / "content_denylist.py")
                    .read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(src):
        # `_add(tokens, value, "category")`
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "_add" and len(node.args) >= 3
                and isinstance(node.args[2], ast.Constant)
                and isinstance(node.args[2].value, str)):
            found.add(node.args[2].value)
        # `tokens[...] = "category"` and `self.tokens.get(..., "category")`
        elif isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) \
                and isinstance(node.value.value, str) \
                and any(isinstance(t, ast.Subscript) for t in node.targets):
            found.add(node.value.value)
    # A dated floor, so an AST walk that stops matching cannot pass silently.
    assert len(found) >= 9, f"only {len(found)} categories found; the walk broke"
    classified = (cd._FRAGMENT_CATEGORIES | cd._PERSON_NAME_CATEGORIES
                  | cd._IDENTIFIER_CATEGORIES)
    unclassified = {c for c in found
                    if not c.startswith("curated:") and c not in classified}
    assert unclassified == set(), (
        f"unclassified harvest categories: {sorted(unclassified)}. Decide for each "
        f"whether its bare token is a FRAGMENT of a longer form the same harvest "
        f"emits (withholdable), a PERSON NAME (kept by the 2026-08-24 decision), "
        f"or an IDENTIFIER in its own right (kept by construction).")
    sets = [cd._FRAGMENT_CATEGORIES, cd._PERSON_NAME_CATEGORIES,
            cd._IDENTIFIER_CATEGORIES]
    assert sum(len(s) for s in sets) == len(classified), "the three sets overlap"
    # Only the fragment set can lose a token, so it is the one whose growth
    # must be deliberate rather than incidental.
    assert set(cd._FRAGMENT_CATEGORIES) == {"crm-org"}


def test_an_executives_ordinary_given_name_is_still_a_default_token(tmp_path):
    """The floor does not get to re-decide 2026-08-24.

    An executive whose GIVEN name is an ordinary English word keeps that bare
    token, even though the full name covers it and the word is in the
    vocabulary. The bare form is the one that leaks: two real executives' given
    names were found in tracked engine files that day, one already pushed to the
    public repo, and the gate had called the surface clean because only the full
    name was a token. `tests/test_a_colleagues_given_name_is_engine_data.py`
    owns that guarantee; this test is the half that says the floor respects it.
    """
    import json
    data = _overlay(tmp_path, {})
    (data / "admin").mkdir(parents=True)
    (data / "admin" / "executives.json").write_text(json.dumps({"executives": [
        {"slug": "gentle-zenthavik", "name": f"{ORDINARY.title()} {DISTINCTIVE.title()}"}
    ]}), encoding="utf-8")
    dl = build_denylist(data)
    assert ORDINARY in dl.tokens
    assert dl.tokens[ORDINARY] == "exec-name"
    assert dl.withheld == {}
    assert f"{ORDINARY} {DISTINCTIVE}" in dl.tokens
    assert DISTINCTIVE in dl.tokens


# ============================================================
# The vocabulary itself
# ============================================================

def test_the_artifact_is_the_one_committed_here():
    """The floor can only ever REMOVE tokens, so whoever edits this file edits
    what the gate stops seeing. Pinning the digest makes a hand edit fail the
    suite and a regeneration a visible re-pin in review.

    MEASURED 2026-09-04: 22862 words, cut at rank 27598, the point where the
    frequency source's cumulative counts reach 95% of running English text.
    Regenerate with scripts/dev/build-ordinary-english.py and update both
    numbers below in the same change.
    """
    digest = hashlib.sha256(ARTIFACT.read_bytes()).hexdigest()
    # pragma: allowlist secret - a SHA-256 of config/ordinary-english.txt, a
    # committed public artifact. Verified 2026-09-04 by recomputing the digest
    # over the staged file: it matches. `detect-secrets` reads it as a hex
    # high-entropy string, which is what a digest is.
    assert digest == "be038a00e70f555f65d89bd577a58596e4715db73d004dd36ad6175efb18eb1e", (  # pragma: allowlist secret
        "config/ordinary-english.txt changed; if that was a deliberate "
        f"regeneration, re-pin this digest: {digest}")


def test_the_artifact_can_hold_nothing_but_single_lowercase_words():
    """Bounds what can hide in a file the content gate does not scan: no e-mail,
    no slug, no Telegram id, no multi-word name can be written here."""
    words = [l for l in ARTIFACT.read_text(encoding="utf-8").splitlines()
             if l and not l.startswith("#")]
    assert len(words) > 20000, "a truncated vocabulary would pass every assertion below"
    # `[a-z]+` and not a minimum length: the frequency list's top ranks include
    # single letters, which are harmless here (`_MIN_WORD` is 5, so no bare token
    # that short exists) and excluding them would mean the artifact no longer
    # matches what the generator produces.
    bad = [w for w in words if not re.fullmatch(r"[a-z]+", w)]
    assert bad == []


def test_the_vocabulary_holds_ordinary_words_and_no_invented_ones():
    """A floor over an empty or wrong corpus is green and does nothing."""
    assert is_ordinary_english(ORDINARY)
    assert is_ordinary_english("precision") and is_ordinary_english("dynamic")
    assert not is_ordinary_english(DISTINCTIVE)
    assert not is_ordinary_english("solana"), "a proper noun is not ordinary English"


def test_a_missing_vocabulary_makes_the_gate_louder_and_never_blinder(tmp_path, monkeypatch):
    """The chosen failure direction, driven rather than argued.

    With no artifact the floor withholds nothing, so every bare token stays and
    the gate can only refuse MORE. Failing the other way -- reading an
    unreadable file as "everything is ordinary" -- would empty the denylist.
    """
    monkeypatch.setattr(cd, "_ORDINARY_ENGLISH_PATH", tmp_path / "absent.txt")
    monkeypatch.setattr(cd, "_ordinary_english", None)
    dl = build_denylist(_overlay(tmp_path, {"ida-ordinary": f"{ORDINARY} Technologies"}))
    assert ORDINARY in dl.tokens
    assert dl.withheld == {}


# ============================================================
# The exemption that keeps the artifact out of the content scan
# ============================================================

def test_exactly_one_path_is_exempt_from_the_content_scan():
    """An exemption in a security wall is a hole unless it is pinned. A second
    entry here must argue its own case and change this assertion."""
    assert set(CONTENT_SCAN_EXEMPT) == {"config/ordinary-english.txt"}


def test_the_exempt_path_is_actually_skipped_by_both_content_gates():
    """Asked of the shared selector both gates use, not of either gate's source."""
    assert engine_text_rels(["config/ordinary-english.txt"]) == []
    assert engine_text_rels(["config/tool-risk.json"]) == ["config/tool-risk.json"]


@pytest.mark.parametrize("rel", sorted(CONTENT_SCAN_EXEMPT))
def test_every_exempt_path_exists(rel):
    """An exemption for a file that is not there guards nothing and hides a typo."""
    assert (ROOT / rel).is_file()
