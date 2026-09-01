"""Tests for the two slop detectors added to scripts/humanization-check.py.

Both cover the founder-blog register our catalogue previously missed: literal
throat-clearing / emphasis / meta-commentary phrases, and false agency (an
inanimate subject taking a verb only a person can take). The module is loaded
by path because its filename is kebab-case (not importable as
scripts.humanization_check).
"""
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_spec = importlib.util.spec_from_file_location(
    "humanization_check", ROOT / "scripts" / "humanization-check.py"
)
hc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hc)


def categories(findings):
    return {f.get("category") for f in findings}


# ============================================================
# check_slop_phrases
# ============================================================

@pytest.mark.parametrize("text,category", [
    ("Here's the thing: the migration failed twice.", "throat_clearing"),
    ("Here's why the build broke on Tuesday.", "throat_clearing"),
    ("The uncomfortable truth is that nobody read the spec.", "throat_clearing"),
    ("It turns out the index was never rebuilt.", "throat_clearing"),
    ("Let me be clear about the deadline.", "throat_clearing"),
    ("We shipped it in four days. Let that sink in.", "emphasis_crutch"),
    ("Make no mistake, the quota is binding.", "emphasis_crutch"),
    ("The gate blocks the push. Full stop.", "emphasis_crutch"),
    ("Plot twist: the daemon was never running.", "meta_commentary"),
    ("The rest of this essay explains the tradeoff.", "meta_commentary"),
    ("Let me walk you through the routing map.", "meta_commentary"),
    ("Slow tests are a feature, not a bug.", "meta_commentary"),
    ("Think about it: nobody reads the logs.", "rhetorical_setup"),
    ("What if I told you the cache was cold?", "rhetorical_setup"),
    ("We missed the window. And that's okay.", "rhetorical_setup"),
    ("The implications are significant.", "vague_declarative"),
    ("The stakes are high for the Q3 rollout.", "vague_declarative"),
    ("The reasons are structural.", "vague_declarative"),
])
def test_slop_phrase_detected(text, category):
    findings = hc.check_slop_phrases(text)
    assert findings, f"expected a finding for: {text}"
    assert category in categories(findings)
    assert all(f["severity"] == "error" for f in findings)


def test_curly_apostrophe_matches_same_as_straight():
    """U+2019 is a 1:1 substitution for ASCII apostrophe, so offsets are preserved."""
    straight = hc.check_slop_phrases("Here's what I find interesting about the queue.")
    curly = hc.check_slop_phrases("Here’s what I find interesting about the queue.")
    assert len(straight) == len(curly) == 1
    assert straight[0]["position"] == curly[0]["position"]


def test_standalone_period_emphasis_detected():
    findings = hc.check_slop_phrases("The gate never opens. Period.")
    assert any(f["category"] == "emphasis_crutch" for f in findings)


def test_theres_this_does_not_match_heres_this():
    """Regression: 'There's this' contains the literal substring "here's this"."""
    assert hc.check_slop_phrases("There's this joy in shipping on a Friday.") == []


@pytest.mark.parametrize("text,expected", [
    ("It turns out the index was never rebuilt.", 1),
    ("We rewrote it. It turns out the cache was cold.", 1),
    ("If it turns out well, I'll share it in the channel.", 0),
    ("Let us see how it turns out next quarter.", 0),
    # The two above are lowercase, and SLOP_REGEXES is compiled WITHOUT
    # re.IGNORECASE, so they were refused by the letter case and said nothing
    # about the `(?:^|[.!?]\s+|\n)` anchor the test's own name is about.
    # Measured 2026-09-01: deleting that anchor left all 44 tests in this file
    # green. These two are the realistic near-miss - a capitalised occurrence
    # that is not an opener, which is what quoted speech and a mid-sentence
    # clause produce.
    ('Marlow wrote: "It turns out the cache was cold." Nobody replied.', 0),
    ("Whether the relay holds is a question of how It turns out.", 0),
])
def test_it_turns_out_only_fires_as_an_opener(text, expected):
    findings = [f for f in hc.check_slop_phrases(text) if f["phrase"] == "it turns out"]
    assert len(findings) == expected


@pytest.mark.parametrize("text", [
    # Lowercase, so the case-sensitive pattern refuses it whatever the anchor is.
    "We closed the books for the reporting period. Next quarter starts Monday.",
    # Capitalised and mid-sentence, which is the case the `[.!?]\s+` anchor
    # exists for: title-cased prose and headings write "Reporting Period."
    # routinely, and without the anchor every one of them reads as manufactured
    # emphasis. Measured 2026-09-01: with the anchor deleted the file stayed
    # green at 44 passed, because the only negative case was the lowercase one.
    "We closed the books for the Reporting Period. Next quarter starts Monday.",
    "The heading read Q3 Reporting Period. Nobody objected.",
])
def test_reporting_period_is_not_emphasis(text):
    """'Period.' as a common noun at sentence end must not fire."""
    assert hc.check_slop_phrases(text) == []


def test_clean_prose_yields_no_slop_findings():
    text = (
        "Marlow rebuilt the relay topology on 3 July and the packet loss dropped to "
        "0.4 percent. Tamsin confirmed the figure from the operator side two days later."
    )
    assert hc.check_slop_phrases(text) == []


# ============================================================
# check_false_agency
# ============================================================

@pytest.mark.parametrize("text", [
    "The data tells us the deploy failed.",
    "The decision emerged after three weeks of silence.",
    "The culture shifted once the quota landed.",
    "The market rewards vendors who ship on time.",
    "The conversation moved toward pricing.",
    "That bet died in eleven days.",
])
def test_false_agency_detected(text):
    findings = hc.check_false_agency(text)
    assert findings, f"expected a finding for: {text}"
    assert all(f["type"] == "false_agency" for f in findings)
    assert all(f["severity"] == "warning" for f in findings)


@pytest.mark.parametrize("text", [
    "Marlow decided after three weeks of silence.",
    "The dashboard shows the queue depth.",
    "The daemon writes the state file atomically.",
    "Tamsin told us the demo was cancelled.",
    "The pipeline runs every fifteen minutes.",
])
def test_false_agency_not_fired_on_legitimate_prose(text):
    assert hc.check_false_agency(text) == []


@pytest.mark.parametrize("text", [
    "The decision told by the board was final.",
    "The culture punished by the market recovered.",
])
def test_passive_agent_is_not_false_agency(text):
    """A trailing 'by' marks a past participle, not an inanimate actor."""
    assert hc.check_false_agency(text) == []


def test_false_agency_finding_names_the_verb():
    findings = hc.check_false_agency("The report believes the number is wrong.")
    assert findings
    assert findings[0]["verb"].lower().startswith("believe")
    assert "report" in findings[0]["subject"].lower()


# ============================================================
# Wiring into audit()
# ============================================================

def test_audit_surfaces_both_new_checks():
    text = (
        "Here's the thing: the rollout slipped.\n\n"
        "The decision emerged from a thread nobody read.\n"
    )
    result = hc.audit(text)
    types = {f["type"] for f in result["findings"]}
    assert "slop_phrase" in types
    assert "false_agency" in types


def test_slop_phrase_blocks_but_false_agency_does_not():
    """Literal phrases are errors; heuristic agency detection is advisory."""
    phrase_only = hc.audit("Let that sink in.")
    assert phrase_only["passed"] is False

    agency_only = hc.audit("The market rewards patience.")
    assert agency_only["passed"] is True
    assert agency_only["passed"] is not hc.audit("The market rewards patience.", strict=True)["passed"]


def test_code_spans_do_not_trigger_slop_findings():
    """Prose checks run on markdown-stripped text, so quoted patterns stay clean."""
    result = hc.audit("The catalogue bans `Here's the thing` in outbound prose.\n")
    assert not [f for f in result["findings"] if f["type"] == "slop_phrase"]
