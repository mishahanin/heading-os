"""Unit tests for scripts/utils/council_prompts.py (shared council prompt builders)."""
from __future__ import annotations

from scripts.utils.council_prompts import (
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
