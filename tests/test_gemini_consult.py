"""Unit tests for scripts/gemini-consult.py prompt builders.

API calls are NOT tested here - those are smoke-tested manually in Task 7.
This file tests only the pure prompt-construction functions.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

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


# ============================================================
# DEFAULT_MODEL wiring + main() exit-code branch + thin-delegate forwarding
# ============================================================

def test_default_model_from_council_config():
    from scripts.utils.council_models import get_model
    assert get_model("gemini") == gc.DEFAULT_MODEL
    assert gc.DEFAULT_MODEL == "gemini-3-flash"


def test_main_returns_2_on_missing_key(monkeypatch):
    def _raise(*_a, **_k):
        raise RuntimeError("CLIPROXY_API_KEY is missing from .env.")
    monkeypatch.setattr(gc, "consult_gemini", _raise)
    assert gc.main(["--mode", "independent", "--question", "Q?"]) == 2


def test_main_returns_3_on_api_error(monkeypatch):
    def _raise(*_a, **_k):
        raise RuntimeError("Proxy connection failed for gemini-3-flash")
    monkeypatch.setattr(gc, "consult_gemini", _raise)
    assert gc.main(["--mode", "independent", "--question", "Q?"]) == 3


def test_main_returns_3_on_auth_failure(monkeypatch):
    # Auth failure mentions CLIPROXY_API_KEY but is NOT a missing key -> exit 3,
    # not 2. Guards the narrow "is missing from .env" sentinel.
    def _raise(*_a, **_k):
        raise RuntimeError("Proxy auth failed for gemini-3-flash: 401. "
                           "Check CLIPROXY_API_KEY in .env.")
    monkeypatch.setattr(gc, "consult_gemini", _raise)
    assert gc.main(["--mode", "independent", "--question", "Q?"]) == 3


# ------------------------------------------------------------
# --length-hint must REACH the prompt, in both modes
#
# `tests/test_kimi_consult.py` has held these three since the flag landed on
# 2026-08-23; the two sibling wrappers had none. Measured 2026-09-01: dropping
# `length_hint=args.length_hint` from BOTH build calls in `main()` left 189
# tests green across every file that names a consult wrapper. A silently
# ignored `--length-hint ""` caps an enumerating task ("list every defect") at
# "Aim for 200-400 words." while the caller believes it uncapped, which is the
# exact defect the flag was added to end.
# ------------------------------------------------------------

def test_the_council_word_cap_is_still_the_default(monkeypatch):
    captured = {}
    monkeypatch.setattr(gc, "consult_gemini",
                        lambda prompt, **k: captured.setdefault("p", prompt) or "answer")
    assert gc.main(["--mode", "independent", "--question", "Q?"]) == 0
    assert gc.DEFAULT_LENGTH_HINT in captured["p"]


def test_an_empty_length_hint_removes_the_cap_from_the_prompt(monkeypatch):
    captured = {}
    monkeypatch.setattr(gc, "consult_gemini",
                        lambda prompt, **k: captured.setdefault("p", prompt) or "answer")
    assert gc.main(["--mode", "independent", "--question", "List every defect.",
                    "--length-hint", ""]) == 0
    assert "200-400" not in captured["p"]
    assert "List every defect." in captured["p"], "the question itself was lost"


def test_the_length_hint_reaches_critique_mode_too(monkeypatch):
    captured = {}
    monkeypatch.setattr(gc, "consult_gemini",
                        lambda prompt, **k: captured.setdefault("p", prompt) or "answer")
    assert gc.main(["--mode", "critique", "--draft", "D", "--length-hint", ""]) == 0
    assert "200-400" not in captured["p"]


def test_consult_gemini_forwards_kwargs_to_call_model(monkeypatch):
    # The thin delegate must pass model/temperature/max_tokens straight through.
    captured = {}
    def _fake(model, prompt, *, temperature, max_tokens, timeout=120.0):
        captured.update(model=model, prompt=prompt,
                        temperature=temperature, max_tokens=max_tokens)
        return "ok"
    monkeypatch.setattr(gc, "call_model", _fake)  # gc imported call_model by name
    out = gc.consult_gemini("the draft", model="gemini-3-flash",
                            temperature=0.4, max_tokens=1234)
    assert out == "ok"
    assert captured == {"model": "gemini-3-flash", "prompt": "the draft",
                        "temperature": 0.4, "max_tokens": 1234}
