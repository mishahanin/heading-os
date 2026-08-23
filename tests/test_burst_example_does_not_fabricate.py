"""The /burst worked example must not model inventing a statistic.

Found by the 2026-08-23 audit. `references/examples.md` is the reference
implementation the skill points at for output shape, and its Variant 2 read:

    73% of national telco DPI deployments today run on foreign-owned probe
    stacks.

then the pick line doubled down:

    the data-led opener lands harder on the operator audience that recognizes
    the 73% number from their own RFPs.

Nothing sourced that figure. `.claude/rules/voice.md` says "NEVER fabricate
facts, statistics, names, or sources", and `/canopus`'s voice rule says "never
fabricate a metric, a threshold, or a behaviour". A burst variant is prose the
operator may ship, so a made-up percentage in the canonical example teaches the
exact failure the workspace forbids.

The example now carries a named placeholder and a `[NEEDS FIGURE: ...]` line,
and the pick states the variant is conditional on the figure arriving.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILL = ROOT / ".claude" / "skills" / "burst"
EXAMPLES = SKILL / "references" / "examples.md"


def test_the_invented_figure_is_gone():
    text = EXAMPLES.read_text(encoding="utf-8")
    assert "73%" not in text
    assert "recognizes the 73% number" not in text


def test_no_bare_percentage_survives_in_the_examples():
    """A different invented number would be the same defect."""
    text = EXAMPLES.read_text(encoding="utf-8")
    bare = [m.group(0) for m in re.finditer(r"\b\d+(\.\d+)?%", text)]
    assert bare == [], f"unsourced figures in the worked example: {bare}"


def test_the_data_led_variant_still_demonstrates_its_axis():
    """Deleting the variant would pass the tests above and lose the lesson."""
    text = EXAMPLES.read_text(encoding="utf-8")
    assert "data-led declaration" in text
    assert "{SHARE}" in text
    assert "{SOURCE}" in text
    assert "[NEEDS FIGURE:" in text


def test_the_pick_line_is_conditional_on_the_figure():
    text = EXAMPLES.read_text(encoding="utf-8")
    pick = next(l for l in text.splitlines() if l.startswith("My pick:"))
    assert "if you can supply the figure" in pick, pick


def test_the_skill_states_the_prohibition_itself():
    """The example teaches by showing; the NEVER list has to say it."""
    text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    never = text.split("## NEVER", 1)[1]
    assert "Never invent a statistic" in never
    assert "NEEDS FIGURE" in never
