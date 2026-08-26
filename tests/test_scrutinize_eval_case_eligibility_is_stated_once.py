"""The eval-case promotion gate must have exactly one answer per target type.

Found by the 2026-08-23 audit. `eval-case-template.md` carried two
irreconcilable gates in the same file:

- Rule 1, "Promotion-eligibility rules": "Target is a skill. ... Findings
  against rules, scripts, or other workspace files do NOT promote to eval
  cases." The quick-reference table repeated it in two rows.
- R10 (2026-05-27), same file: defines a `tests/regression/scrutinize/` pytest
  artefact for script findings and a `.claude/rules/_regression/*.yaml` pack for
  rule findings, and says the gate "applies uniformly across skill / script /
  rule targets".

Phase 4.5 reads this file to decide whether a finding promotes. With both
statements present the answer was whichever paragraph the run happened to weigh,
so whether a script finding got a regression artefact was a coin flip per
invocation. R10 is the live design; rule 1 was the stale one.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOC = ROOT / ".claude" / "skills" / "scrutinize" / "references" / "eval-case-template.md"
TEXT = DOC.read_text(encoding="utf-8")


def _section(heading: str) -> str:
    """Return the body under a heading, up to the next heading of any level."""
    match = re.search(
        rf"^#+\s+{re.escape(heading)}\s*$(.*?)(?=^#+\s)", TEXT, re.M | re.S
    )
    assert match, f"section not found: {heading}"
    return match.group(1)


def test_the_file_is_the_one_place_the_gate_is_stated():
    """A second copy elsewhere would reintroduce the drift by another route."""
    paths = sorted((ROOT / ".claude" / "skills" / "scrutinize").rglob("*.md"))
    # "no second copy" is green over zero files, so a renamed skill directory or a
    # changed suffix would switch this guard off without failing anything.
    # Measured 2026-08-26: 17 markdown files under .claude/skills/scrutinize/.
    assert len(paths) >= 10, f"the scan collapsed to {len(paths)} files"
    others = []
    for path in paths:
        if path == DOC:
            continue
        if "Promotion-eligibility" in path.read_text(encoding="utf-8"):
            others.append(path.relative_to(ROOT).as_posix())
    assert others == [], f"the gate is restated in: {others}"


def test_rule_one_admits_all_three_target_types():
    rules = _section("Promotion-eligibility rules")
    assert "skill, a script, or a rule" in rules, rules[:400]
    assert ".claude/rules/" in rules
    assert "scripts/" in rules


def test_the_stale_skill_only_wording_is_gone():
    rules = _section("Promotion-eligibility rules")
    assert "Findings against rules, scripts" not in rules
    assert "Target is a skill." not in rules


def test_the_quick_reference_agrees_with_rule_one():
    """The table taught the obsolete rule in two rows."""
    table = _section("Eligibility quick reference")
    assert "rules don't have eval cases" not in table
    assert "No - covered by integration tests" not in table
    rule_row = next(l for l in table.splitlines() if "voice.md" in l)
    script_row = next(l for l in table.splitlines() if "dashboard.py" in l)
    assert rule_row.count("| Yes"), rule_row
    assert script_row.count("| Yes"), script_row


def test_r10_still_defines_the_three_artefact_shapes():
    """The other direction: deleting R10 would also satisfy the tests above."""
    shapes = _section("Target-type artefact shapes (R10, 2026-05-27)")
    assert "tests/regression/scrutinize/" in shapes
    assert ".claude/rules/_regression/" in shapes
    assert "evals/cases/" in shapes


def test_r10_does_not_restate_the_gate():
    """The duplicate statement is what allowed the two to drift apart."""
    unchanged = _section("Eligibility unchanged")
    assert "severity >= MEDIUM" not in unchanged, unchanged
    assert "stated once above" in unchanged
