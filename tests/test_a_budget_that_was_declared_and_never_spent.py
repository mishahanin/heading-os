"""Shard 07-p4: four numbers and two rationales that described a different tool.

`PROPOSE_DEFAULT_BUDGET_USD = 2.00` sat in `scripts/heading_cli.py` under a
comment calling it "4x the runner's normal $0.50" for the propose tier. Nothing
referenced it. `--budget` defaulted to $0.50 for every tier, so the only
propose-tier skill on the allowlist ran at a quarter of its documented budget
unless the operator typed the number themselves. A cost gate that reads as
decided and is not is worse than an absent one.

`_resolve` decided path-versus-bare-name on `"/" in target` alone, one line
below a branch that reasons about Windows absolutes. `scripts\\health.py` was
therefore a bare name, rewritten to `scripts/scripts\\health.py`, and reported
missing.

In `scripts/humanization-check.py`, four checks measured something other than
what their own prose claimed:

- `check_sentence_start_additionally` said "or paragraph start" and compiled
  without re.MULTILINE, so `^` anchored at position 0 of the document and no
  paragraph opener after the first word could ever match.
- "cultivating" was in BANNED_VOCAB (blanket) AND BANNED_FIGURATIVE (context
  gated). The blanket pass runs first, so the context gate was unreachable:
  literal uses were hard errors and figurative ones were counted twice.
- `check_burstiness` gated its blocking systemic error on `word_count(text)` --
  the RAW text, code fences and all -- while measuring paragraphs from the
  stripped text.
- `check_title_case_headings` ran on raw text because "headings live outside
  code blocks", which is false for every file that demonstrates markdown.

Tests: this file.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts import heading_cli as hc  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "humanization_check_07p4", ROOT / "scripts" / "humanization-check.py")
hz = importlib.util.module_from_spec(_spec)
sys.modules["humanization_check_07p4"] = hz
_spec.loader.exec_module(hz)


# ==========================================================================
# 1 - the budget that was declared and never spent
# ==========================================================================

@pytest.fixture()
def captured_budget(monkeypatch):
    """Capture the budget `run_skill` hands to build_skill_command."""
    seen = {}
    real_build = hc.build_skill_command

    def spy(skill, args, *, tier, budget_usd, model=None):
        seen["tier"] = tier
        seen["budget"] = budget_usd
        return real_build(skill, args, tier=tier, budget_usd=budget_usd, model=model)

    monkeypatch.setattr(hc, "build_skill_command", spy)
    monkeypatch.setattr(hc.shutil, "which", lambda _name: "/usr/bin/claude")

    class _Done:
        returncode = 0
        stdout = ""
        stderr = ""

    monkeypatch.setattr(hc.subprocess, "run", lambda *a, **k: _Done())
    return seen


def test_the_propose_tier_gets_the_budget_its_comment_declares(captured_budget):
    assert hc.run_skill("odin", ["reflect", "--propose"]) == 0
    assert captured_budget["tier"] == "propose"
    assert captured_budget["budget"] == hc.PROPOSE_DEFAULT_BUDGET_USD, \
        "the propose tier ran on the generic default it was written to replace"


def test_a_non_propose_tier_keeps_the_generic_default(captured_budget):
    assert hc.run_skill("state-check", []) == 0
    assert captured_budget["budget"] == hc.DEFAULT_BUDGET_USD


def test_an_explicit_budget_always_wins(captured_budget):
    hc.run_skill("odin", ["reflect", "--propose"], budget_usd=0.25)
    assert captured_budget["budget"] == 0.25, \
        "a tier default overrode a number the operator typed"


def test_an_explicit_budget_equal_to_a_default_is_still_honoured(captured_budget):
    """`--budget 0.50` on a propose skill must not be read as 'unset'."""
    hc.run_skill("odin", ["reflect", "--propose"], budget_usd=hc.DEFAULT_BUDGET_USD)
    assert captured_budget["budget"] == hc.DEFAULT_BUDGET_USD


def test_the_declared_constant_is_actually_wired_in():
    assert hc.TIER_DEFAULT_BUDGET_USD["propose"] == hc.PROPOSE_DEFAULT_BUDGET_USD
    assert hc.PROPOSE_DEFAULT_BUDGET_USD > hc.DEFAULT_BUDGET_USD


def test_the_budget_reaches_the_argv(captured_budget):
    """Not just the call: the number must appear in what claude is run with."""
    hc.run_skill("odin", ["reflect", "--propose"])
    cmd = hc.build_skill_command("odin", ["reflect", "--propose"],
                                 tier="propose",
                                 budget_usd=captured_budget["budget"])
    assert "--max-budget-usd" in cmd
    value = cmd[cmd.index("--max-budget-usd") + 1]
    assert float(value) == hc.PROPOSE_DEFAULT_BUDGET_USD


def test_a_refused_skill_never_reaches_a_budget_decision(captured_budget):
    assert hc.run_skill("not-allowlisted", []) == 2
    assert captured_budget == {}


# ==========================================================================
# 2 - the relative path that was read as a bare name
# ==========================================================================

def test_a_backslash_relative_path_is_taken_as_a_path():
    resolved = hc._resolve("scripts\\workspace-health.py", ROOT)
    assert "scripts/scripts" not in resolved.as_posix(), \
        "a relative path was rewritten as if it were a bare name"


def test_a_forward_slash_relative_path_is_unchanged():
    assert hc._resolve("scripts/utils/paths.py", ROOT) == \
        (ROOT / "scripts" / "utils" / "paths.py").resolve()


def test_a_bare_name_still_goes_under_scripts():
    assert hc._resolve("paths.py", ROOT) == (ROOT / "scripts" / "paths.py").resolve()


def test_containment_still_holds_for_a_backslash_target():
    """Reading `\\` as a separator must not open a way out of the root."""
    with pytest.raises(hc.OutsideWorkspace):
        hc._resolve("C:\\Windows\\system32\\cmd.exe", ROOT)


def test_a_relative_target_cannot_climb_out_of_the_root():
    """The absolute guard does not cover this; only the containment check does.

    The mutation that disabled containment survived a suite whose only escape
    attempt was an ABSOLUTE path, which a different branch refuses one line
    earlier. A `../` walk is the case that reaches the check.
    """
    with pytest.raises(hc.OutsideWorkspace):
        hc._resolve("../../etc/passwd", ROOT)


def test_a_relative_target_that_stays_inside_is_allowed():
    assert hc._resolve("scripts/../scripts/utils/paths.py", ROOT) == \
        (ROOT / "scripts" / "utils" / "paths.py").resolve()


# ==========================================================================
# 3 - the transition check that saw only the first word of a document
# ==========================================================================

def _types(findings):
    return [f["type"] for f in findings]


def test_a_paragraph_opener_is_caught():
    text = "First para ends here.\n\nAdditionally, the second paragraph starts."
    hits = hz.check_sentence_start_additionally(text)
    assert "transition_at_sentence_start" in _types(hits), \
        "a paragraph opener after the first line was invisible"
    assert hits[0]["word"] == "Additionally"


@pytest.mark.parametrize("lead", [
    "# Some heading",
    "Three reasons follow:",
    "a bullet with no full stop",
])
def test_a_paragraph_opener_after_an_unpunctuated_line_is_caught(lead):
    """This is the case the line anchor actually buys, and only this one.

    The audit report's own reproduce case -- a paragraph after a sentence
    ending in a period -- was ALREADY caught, because `\\n\\n` satisfies the
    `[.!?]\\s+` branch. Removing re.MULTILINE therefore left that test green,
    and the mutation survived. What `^` adds is the opener whose preceding line
    carries no sentence punctuation: after a heading, after a colon, after a
    bullet. Those are common in exactly the documents this tool audits.
    """
    hits = hz.check_sentence_start_additionally(f"{lead}\n\nAdditionally, it goes on.")
    assert len(hits) == 1, f"an opener after {lead!r} was invisible"


def test_a_document_initial_opener_is_still_caught():
    hits = hz.check_sentence_start_additionally("Additionally, this opens the file.")
    assert len(hits) == 1


def test_a_mid_sentence_occurrence_is_not_flagged():
    hits = hz.check_sentence_start_additionally(
        "We shipped it and additionally we wrote the note.")
    assert hits == [], "a lowercase mid-sentence use was flagged"


def test_every_watched_word_is_caught_at_a_paragraph_start():
    for word in ("Additionally", "Moreover", "Furthermore", "Subsequently"):
        text = f"Opening line.\n\n{word}, the point continues."
        hits = hz.check_sentence_start_additionally(text)
        assert len(hits) == 1, f"{word} was not caught at a paragraph start"


# ==========================================================================
# 4 - the context gate that the blanket ban made unreachable
# ==========================================================================

def _cultivat_types(text):
    findings = hz.check_banned_vocab(text)
    return sorted(f["type"] for f in findings
                  if "cultivat" in str(f.get("word", "")).lower())


def test_a_literal_use_is_not_an_error():
    assert _cultivat_types("She spent the spring cultivating the vineyard rows.") == [], \
        "the literal use the figurative list exists to spare was still flagged"


def test_a_figurative_use_is_reported_exactly_once():
    assert _cultivat_types("They kept cultivating community all year.") == \
        ["banned_vocab_figurative"]


def test_the_past_tense_is_not_left_unchecked():
    """Removing it from the blanket list must not remove it from every list."""
    assert _cultivat_types("He cultivated relationships across the region.") == \
        ["banned_vocab_figurative"]


def test_the_past_tense_literal_use_is_spared():
    assert _cultivat_types("They cultivated barley on the terraces.") == []


def test_the_word_appears_in_exactly_one_vocabulary_list():
    blanket = {w for w in hz.BANNED_VOCAB if w.startswith("cultivat")}
    assert blanket == set(), \
        f"the blanket list still shadows the context gate: {blanket}"
    assert "cultivating" in hz.BANNED_FIGURATIVE


# ==========================================================================
# 5 - the size gate that counted the code block
# ==========================================================================

def _prose_block(sentences=4, paragraphs=4):
    one = " ".join(
        ["The system runs a check and returns a value each time."] * sentences)
    return "\n\n".join([one] * paragraphs)


def test_a_code_block_does_not_push_short_prose_over_the_size_gate():
    body = _prose_block()
    fenced = body + "\n\n```\n" + ("token " * 200) + "\n```\n"
    systemic = [f for f in hz.check_burstiness(fenced)
                if f["type"] == "burstiness_systemic"]
    assert systemic == [], \
        "a fenced code block was counted as outbound prose and forced an error"


def test_real_prose_over_the_gate_still_reports_systemically():
    body = _prose_block(sentences=6, paragraphs=8)
    assert hz.word_count(hz.strip_markdown_noise(body)) > 200
    systemic = [f for f in hz.check_burstiness(body)
                if f["type"] == "burstiness_systemic"]
    assert systemic, "the systemic check stopped firing on real prose"


def test_per_paragraph_warnings_are_unaffected_by_the_gate():
    body = _prose_block()
    per_para = [f for f in hz.check_burstiness(body)
                if f["type"] == "burstiness_violation"]
    assert per_para, "the paragraph-level check went silent"


def test_the_two_size_gates_now_agree():
    """`check_over_fragmentation` always counted stripped prose; match it."""
    body = _prose_block() + "\n\n```\n" + ("token " * 200) + "\n```\n"
    assert hz.check_over_fragmentation(body) == []
    assert [f for f in hz.check_burstiness(body)
            if f["type"] == "burstiness_systemic"] == []


# ==========================================================================
# 6 - the heading check that read the example inside the fence
# ==========================================================================

def _headings(text):
    return [f["heading"] for f in hz.audit(text)["findings"]
            if f["type"] == "title_case_heading"]


def test_a_heading_quoted_inside_a_fence_is_not_a_finding():
    text = ("Some body text for the file.\n\n"
            "```markdown\n# Some Example Widget Configuration\n```\n")
    assert _headings(text) == [], \
        "quoted example markdown was audited as if it were a real heading"


def test_a_real_heading_is_still_a_finding():
    text = "# Some Real Widget Configuration Heading\n\nBody text follows here.\n"
    assert _headings(text) == ["Some Real Widget Configuration Heading"]


def test_a_heading_inside_an_audit_skip_block_is_not_a_finding():
    text = ("<!-- audit-skip-start -->\n"
            "# Some Banned Title Case Example\n"
            "<!-- audit-skip-end -->\n\n"
            "Body text follows here.\n")
    assert _headings(text) == []


def test_a_sentence_case_heading_is_never_a_finding():
    text = "# Some real widget configuration heading\n\nBody text follows here.\n"
    assert _headings(text) == []
