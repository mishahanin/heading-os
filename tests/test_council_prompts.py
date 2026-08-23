"""Unit tests for scripts/utils/council_prompts.py (shared council prompt builders)."""
from __future__ import annotations

import pytest

from scripts.utils.council_prompts import (
    DEFAULT_LENGTH_HINT,
    THIRTY_ONE_C_BLOCK,
    build_independent_prompt,
    build_critique_prompt,
)


def test_block_has_31c_anchors():
    assert "ODUN.ONE" in THIRTY_ONE_C_BLOCK
    assert "Tribe" in THIRTY_ONE_C_BLOCK
    assert "DPI+" in THIRTY_ONE_C_BLOCK
    assert "sanctions" in THIRTY_ONE_C_BLOCK.lower()


def test_independent_includes_question_and_block():
    p = build_independent_prompt("Should we partner with X?")
    assert "Should we partner with X?" in p
    assert "ODUN.ONE" in p
    assert "do not defer" in p.lower()
    # the question is placed under its own heading, not just present anywhere
    assert "## Question" in p
    assert p.index("## Question") < p.index("Should we partner with X?")
    # the independent-role signal is present
    assert "first principles" in p.lower()


def test_independent_context_toggle():
    assert "## Context" not in build_independent_prompt("Q?", context="")
    p = build_independent_prompt("Q?", context="Background facts.")
    assert "## Context" in p and "Background facts." in p


def test_critique_includes_draft_and_role():
    p = build_critique_prompt("Draft proposal text here.")
    assert "Draft proposal text here." in p
    assert "critical reviewer" in p.lower()
    assert "flaws" in p.lower()
    # the draft is placed under its own heading, not just present anywhere
    assert "## Draft to critique" in p
    assert p.index("## Draft to critique") < p.index("Draft proposal text here.")


def test_critique_context_toggle():
    assert "## Context" not in build_critique_prompt("Draft.", context="")
    p = build_critique_prompt("Draft.", context="Background.")
    assert "## Context" in p and "Background." in p


# ------------------------------------------------------------- the length cap
#
# "Aim for 200-400 words." was hardcoded into both builders until 2026-08-23.
# The engine audit of that date ran its per-file shards through
# `kimi-consult --mode independent`, so every shard carried a 400-word cap while
# its own question said "list EVERY defect". The cap is correct for a council
# consult and wrong for enumeration, so it is now the caller's choice.

@pytest.mark.parametrize("build,arg", [
    (build_independent_prompt, "Q?"),
    (build_critique_prompt, "Draft."),
])
def test_the_length_cap_is_still_the_default(build, arg):
    """A council consult must not silently lose its brevity instruction."""
    assert DEFAULT_LENGTH_HINT in build(arg)


@pytest.mark.parametrize("build,arg", [
    (build_independent_prompt, "List every defect."),
    (build_critique_prompt, "List every defect."),
])
@pytest.mark.parametrize("off", ["", None, "   "])
def test_an_enumerating_caller_can_drop_the_cap(build, arg, off):
    p = build(arg, length_hint=off)
    assert "200-400" not in p
    assert "words" not in p.split("## Output")[-1], p.split("## Output")[-1]


@pytest.mark.parametrize("build,arg", [
    (build_independent_prompt, "Q?"),
    (build_critique_prompt, "Draft."),
])
def test_dropping_the_cap_keeps_the_rest_of_the_output_instruction(build, arg):
    """Omitting the hint must not swallow the instruction it was appended to."""
    capped = build(arg)
    uncapped = build(arg, length_hint="")
    assert "## Output" in uncapped
    # everything except the trailing hint survives
    assert uncapped.replace("## Output", "").strip() != ""
    assert len(uncapped) == len(capped) - len(" " + DEFAULT_LENGTH_HINT)


@pytest.mark.parametrize("build,arg", [
    (build_independent_prompt, "Q?"),
    (build_critique_prompt, "Draft."),
])
def test_a_custom_hint_replaces_the_default(build, arg):
    p = build(arg, length_hint="Be exhaustive; length is not a constraint.")
    assert "Be exhaustive; length is not a constraint." in p
    assert "200-400" not in p
