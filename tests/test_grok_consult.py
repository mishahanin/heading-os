"""Unit tests for scripts/grok-consult.py prompt builders.

API calls are NOT tested here - those are smoke-tested manually in Task 7.
This file tests only the pure prompt-construction functions.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

# Load the script as a module (it has a hyphen in its filename)
SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "grok-consult.py"
_spec = importlib.util.spec_from_file_location("grok_consult", SCRIPT)
gc = importlib.util.module_from_spec(_spec)
sys.modules["grok_consult"] = gc
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
    # Independent mode must instruct Grok NOT to defer to Claude
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
# consult_grok() finish_reason handling — parity with kimi-consult
#
# grok-4.3 has built-in reasoning. xAI keeps the chain-of-thought in a separate
# `reasoning_content` field, so empirically the visible `content` is rarely starved
# (a tiny max_tokens truncates the answer but still returns content). These tests pin
# the SHARED defect fix anyway: empty content must be disambiguated by finish_reason,
# retried once on length-truncation, and never misattributed to a safety block. Fakes
# drive every branch with no network call.
# ============================================================

class _FakeMsg:
    def __init__(self, content, reasoning=""):
        self.content = content
        self.reasoning_content = reasoning
        self.model_extra = {"reasoning_content": reasoning}


class _FakeChoice:
    def __init__(self, content, finish_reason, reasoning=""):
        self.message = _FakeMsg(content, reasoning)
        self.finish_reason = finish_reason


class _FakeUsage:
    prompt_tokens = 10
    completion_tokens = 10


class _FakeResp:
    def __init__(self, content, finish_reason, reasoning=""):
        self.choices = [_FakeChoice(content, finish_reason, reasoning)]
        self.usage = _FakeUsage()
        self.model = "grok-4.3"


class _FakeCompletions:
    """Returns scripted responses in order; the last one repeats if calls exceed the script."""
    def __init__(self, scripted):
        self._scripted = list(scripted)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        idx = min(len(self.calls) - 1, len(self._scripted) - 1)
        return self._scripted[idx]


def _install_fake_grok(monkeypatch, scripted):
    """Patch openai.OpenAI + load_api_key so consult_grok runs offline. Returns the
    _FakeCompletions so a test can assert call count and per-call max_tokens."""
    import openai
    comp = _FakeCompletions(scripted)

    class _FakeChat:
        completions = comp

    class _FakeClient:
        def __init__(self, *a, **k):
            self.chat = _FakeChat()

    monkeypatch.setattr(openai, "OpenAI", _FakeClient)
    monkeypatch.setattr(gc, "load_api_key", lambda *a, **k: "test-key")
    return comp


def test_empty_length_retries_then_succeeds(monkeypatch):
    """Empty content + finish_reason=length -> retry once at a higher budget; succeed."""
    comp = _install_fake_grok(monkeypatch, [
        _FakeResp("", "length", reasoning="x" * 600),
        _FakeResp("FINAL ANSWER", "stop", reasoning="x" * 600),
    ])
    out = gc.consult_grok("q", max_tokens=120)
    assert out == "FINAL ANSWER"
    assert len(comp.calls) == 2
    assert comp.calls[1]["max_tokens"] > comp.calls[0]["max_tokens"]


def test_empty_length_exhausted_raises_precise_error(monkeypatch):
    """Truncation even after the retry -> accurate error, NOT a safety-filter claim."""
    _install_fake_grok(monkeypatch, [
        _FakeResp("", "length", reasoning="x" * 600),
        _FakeResp("", "length", reasoning="x" * 600),
    ])
    with pytest.raises(RuntimeError) as ei:
        gc.consult_grok("q", max_tokens=120)
    msg = str(ei.value).lower()
    assert "reasoning" in msg
    assert "max-tokens" in msg
    assert "blocked by safety" not in msg


def test_empty_content_filter_raises_safety_error(monkeypatch):
    """Empty + content_filter -> safety message, single call (no length retry)."""
    comp = _install_fake_grok(monkeypatch, [_FakeResp("", "content_filter")])
    with pytest.raises(RuntimeError) as ei:
        gc.consult_grok("q", max_tokens=8192)
    assert "safet" in str(ei.value).lower()
    assert len(comp.calls) == 1


def test_empty_stop_raises_empty_answer_error(monkeypatch):
    """Empty + stop -> 'empty answer', not a truncation or safety claim. No retry."""
    comp = _install_fake_grok(monkeypatch, [_FakeResp("", "stop")])
    with pytest.raises(RuntimeError) as ei:
        gc.consult_grok("q")
    m = str(ei.value).lower()
    assert "empty" in m
    assert "safety" not in m
    assert len(comp.calls) == 1


def test_nonempty_returns_without_retry(monkeypatch):
    """Content present + stop -> return immediately, exactly one call."""
    comp = _install_fake_grok(monkeypatch, [_FakeResp("hello", "stop")])
    assert gc.consult_grok("q") == "hello"
    assert len(comp.calls) == 1


def test_nonempty_length_returns_partial_without_retry(monkeypatch):
    """Content present + length (grok's normal truncation) -> return it, no retry."""
    comp = _install_fake_grok(monkeypatch, [_FakeResp("partial...", "length")])
    assert gc.consult_grok("q") == "partial..."
    assert len(comp.calls) == 1
