"""Unit tests for scripts/gemini-consult.py prompt builders.

API calls are NOT tested here - those are smoke-tested manually in Task 7.
This file tests only the pure prompt-construction functions.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

# Load the script as a module (it has a hyphen in its filename)
SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "gemini-consult.py"
_spec = importlib.util.spec_from_file_location("gemini_consult", SCRIPT)
gc = importlib.util.module_from_spec(_spec)
sys.modules["gemini_consult"] = gc
_spec.loader.exec_module(gc)


# ------------------------------------------------------------
# build_independent_prompt
# ------------------------------------------------------------

def test_independent_prompt_includes_question():
    prompt = gc.build_independent_prompt("Should we partner with X?")
    assert "Should we partner with X?" in prompt


def test_independent_prompt_includes_31c_block():
    prompt = gc.build_independent_prompt("anything")
    assert "ODUN.ONE" in prompt
    assert "Tribe" in prompt
    assert "DPI+" in prompt


def test_independent_prompt_omits_context_section_when_empty():
    prompt = gc.build_independent_prompt("Q?", context="")
    assert "## Context" not in prompt


def test_independent_prompt_includes_context_section_when_provided():
    prompt = gc.build_independent_prompt("Q?", context="Background facts.")
    assert "## Context" in prompt
    assert "Background facts." in prompt


def test_independent_prompt_role_says_independent():
    prompt = gc.build_independent_prompt("Q?")
    # Independent mode must instruct Gemini NOT to defer to Claude
    assert "first principles" in prompt.lower() or "independent" in prompt.lower()
    assert "do not defer" in prompt.lower()


# ------------------------------------------------------------
# build_critique_prompt
# ------------------------------------------------------------

def test_critique_prompt_includes_draft():
    prompt = gc.build_critique_prompt("Draft proposal text here.")
    assert "Draft proposal text here." in prompt


def test_critique_prompt_role_says_critical_reviewer():
    prompt = gc.build_critique_prompt("Draft.")
    assert "critical reviewer" in prompt.lower()
    assert "find flaws" in prompt.lower() or "flaws" in prompt.lower()


def test_critique_prompt_includes_31c_block():
    prompt = gc.build_critique_prompt("Draft.")
    assert "ODUN.ONE" in prompt
    assert "sanctions" in prompt.lower()


def test_critique_prompt_omits_context_when_empty():
    prompt = gc.build_critique_prompt("Draft.", context="")
    assert "## Context" not in prompt


def test_critique_prompt_includes_context_when_provided():
    prompt = gc.build_critique_prompt("Draft.", context="Background.")
    assert "## Context" in prompt
    assert "Background." in prompt
