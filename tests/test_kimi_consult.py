"""Unit tests for scripts/kimi-consult.py — arg validation + config (no API calls)."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "kimi-consult.py"
_spec = importlib.util.spec_from_file_location("kimi_consult", SCRIPT)
kc = importlib.util.module_from_spec(_spec)
sys.modules["kimi_consult"] = kc
_spec.loader.exec_module(kc)


def test_default_model_is_kimi_cloud():
    assert kc.DEFAULT_MODEL == "kimi-k2.6:cloud"


def test_base_url_is_local_ollama():
    assert kc.OLLAMA_BASE_URL == "http://localhost:11434/v1"


def test_independent_requires_question():
    with pytest.raises(SystemExit):
        kc.parse_args(["--mode", "independent"])


def test_critique_requires_draft():
    with pytest.raises(SystemExit):
        kc.parse_args(["--mode", "critique"])


def test_independent_ok():
    args = kc.parse_args(["--mode", "independent", "--question", "Q?"])
    assert args.mode == "independent" and args.question == "Q?"


def test_critique_temperature_default_lower():
    args = kc.parse_args(["--mode", "critique", "--draft", "D"])
    assert args.temperature == kc.DEFAULT_CRITIQUE_TEMPERATURE


def test_main_returns_2_on_missing_key(monkeypatch):
    def _raise(*_a, **_k):
        raise RuntimeError("OLLAMA_API_KEY not set. Add it to .env")

    monkeypatch.setattr(kc, "consult_kimi", _raise)
    assert kc.main(["--mode", "independent", "--question", "Q?"]) == 2


def test_main_returns_3_on_api_error(monkeypatch):
    def _raise(*_a, **_k):
        raise RuntimeError("ollama refused the connection at localhost:11434")

    monkeypatch.setattr(kc, "consult_kimi", _raise)
    assert kc.main(["--mode", "independent", "--question", "Q?"]) == 3


# ============================================================
# consult_kimi() finish_reason handling — reasoning-model truncation
#
# kimi-k2.6 is a thinking model: it streams its chain-of-thought into a separate
# `reasoning` field BEFORE the visible answer. When max_tokens is exhausted during
# that reasoning phase, `content` is empty and finish_reason == "length". The wrapper
# must disambiguate this from a genuine safety block (content_filter) or empty answer
# (stop), retry once at a higher budget on length-truncation, and never misattribute
# a budget truncation to "safety filters". These fakes drive that behaviour without
# any network call.
# ============================================================

class _FakeMsg:
    def __init__(self, content, reasoning=""):
        self.content = content
        self.reasoning = reasoning
        self.model_extra = {"reasoning": reasoning}


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
        self.model = "kimi-k2.6"


class _FakeCompletions:
    """Returns scripted responses in order; the last one repeats if calls exceed the script."""
    def __init__(self, scripted):
        self._scripted = list(scripted)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        idx = min(len(self.calls) - 1, len(self._scripted) - 1)
        return self._scripted[idx]


def _install_fake_kimi(monkeypatch, scripted):
    """Patch openai.OpenAI + load_api_key so consult_kimi runs offline. Returns the
    _FakeCompletions so a test can assert call count and per-call max_tokens."""
    import openai
    comp = _FakeCompletions(scripted)

    class _FakeChat:
        completions = comp

    class _FakeClient:
        def __init__(self, *a, **k):
            self.chat = _FakeChat()

    monkeypatch.setattr(openai, "OpenAI", _FakeClient)
    monkeypatch.setattr(kc, "load_api_key", lambda *a, **k: "test-key")
    return comp


def test_empty_length_retries_then_succeeds(monkeypatch):
    """Empty content + finish_reason=length -> retry once at a higher budget; succeed."""
    comp = _install_fake_kimi(monkeypatch, [
        _FakeResp("", "length", reasoning="x" * 600),
        _FakeResp("FINAL ANSWER", "stop", reasoning="x" * 600),
    ])
    out = kc.consult_kimi("q", max_tokens=120)
    assert out == "FINAL ANSWER"
    assert len(comp.calls) == 2
    # the retry must escalate the token budget, not repeat the same starved call
    assert comp.calls[1]["max_tokens"] > comp.calls[0]["max_tokens"]


def test_empty_length_exhausted_raises_precise_error(monkeypatch):
    """Truncation even after the retry -> accurate error, NOT a safety-filter claim."""
    _install_fake_kimi(monkeypatch, [
        _FakeResp("", "length", reasoning="x" * 600),
        _FakeResp("", "length", reasoning="x" * 600),
    ])
    with pytest.raises(RuntimeError) as ei:
        kc.consult_kimi("q", max_tokens=120)
    msg = str(ei.value).lower()
    assert "reasoning" in msg              # names the real mechanism
    assert "max-tokens" in msg             # gives the actionable fix
    assert "blocked by safety" not in msg  # must NOT misattribute to a safety block


def test_empty_content_filter_raises_safety_error(monkeypatch):
    """Empty + content_filter -> safety message, single call (no length retry)."""
    comp = _install_fake_kimi(monkeypatch, [_FakeResp("", "content_filter")])
    with pytest.raises(RuntimeError) as ei:
        kc.consult_kimi("q", max_tokens=8192)
    assert "safet" in str(ei.value).lower()
    assert len(comp.calls) == 1


def test_empty_stop_raises_empty_answer_error(monkeypatch):
    """Empty + stop -> 'empty answer', not a truncation or safety claim. No retry."""
    comp = _install_fake_kimi(monkeypatch, [_FakeResp("", "stop")])
    with pytest.raises(RuntimeError) as ei:
        kc.consult_kimi("q")
    m = str(ei.value).lower()
    assert "empty" in m
    assert "safety" not in m
    assert len(comp.calls) == 1


def test_nonempty_returns_without_retry(monkeypatch):
    """Content present + stop -> return immediately, exactly one call."""
    comp = _install_fake_kimi(monkeypatch, [_FakeResp("hello", "stop")])
    assert kc.consult_kimi("q") == "hello"
    assert len(comp.calls) == 1


def test_nonempty_length_returns_partial_without_retry(monkeypatch):
    """Content present + length (answer itself truncated) -> return it, no retry."""
    comp = _install_fake_kimi(monkeypatch, [_FakeResp("partial...", "length")])
    assert kc.consult_kimi("q") == "partial..."
    assert len(comp.calls) == 1
