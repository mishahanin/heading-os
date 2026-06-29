"""Tests for scripts/utils/llm_fallback.py.

Covers happy path (anthropic succeeds), retriable error cascade (5xx ->
gemini fallback fires), permanent error re-raise (auth -> bubbles up),
prompt-flattening for both string and list-shaped system blocks, and
chain-exhausted error message.

The Gemini/Grok wrappers are patched via importlib loader so tests never
hit a live API.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

WS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WS))

from scripts.utils import llm_fallback as F  # noqa: E402


# Mimic the anthropic SDK exception hierarchy. llm_fallback._is_retriable_
# anthropic_error matches by class-name (MRO), so a local class named
# InternalServerError satisfies the config entry of the same name.
class APIError(Exception): pass
class APIConnectionError(APIError): pass
class APITimeoutError(APIError): pass
class RateLimitError(APIError): pass
class InternalServerError(APIError): pass
class AuthenticationError(APIError): pass
class BadRequestError(APIError): pass


class _Block:
    def __init__(self, text): self.text = text; self.type = "text"


class _Resp:
    def __init__(self, text):
        self.content = [_Block(text)]
        self.stop_reason = "end_turn"

        class _Usage:
            output_tokens = 42
            input_tokens = 100

        self.usage = _Usage()


class _MockClient:
    """Minimal anthropic.Anthropic stand-in."""

    def __init__(self, raise_exc=None, response="mocked anthropic answer"):
        self.raise_exc = raise_exc
        self.response = response
        self.calls = 0

        class _Messages:
            def __init__(self, parent): self.parent = parent

            def create(self, **kw):
                self.parent.calls += 1
                if self.parent.raise_exc:
                    raise self.parent.raise_exc
                return _Resp(self.parent.response)

        self.messages = _Messages(self)


def test_happy_path_returns_anthropic_vendor():
    client = _MockClient()
    result = F.call_anthropic_with_fallback(
        client=client,
        model="claude-haiku-4-5-20251001",
        max_tokens=100,
        system="You are helpful.",
        messages=[{"role": "user", "content": "Hi"}],
        skill_name="test",
    )
    assert result.vendor == "anthropic"
    assert result.text == "mocked anthropic answer"
    assert result.fallback_triggered is False
    assert client.calls == 1


def test_retriable_error_triggers_fallback():
    client = _MockClient(raise_exc=InternalServerError("503 Service Unavailable"))
    with patch.object(F, "_load_consult_fn") as mock_loader:
        mock_loader.return_value = (
            lambda prompt, model, temperature, max_tokens: f"GEMINI: {prompt[:30]}"
        )
        result = F.call_anthropic_with_fallback(
            client=client,
            model="claude-haiku-4-5-20251001",
            max_tokens=100,
            system="You are helpful.",
            messages=[{"role": "user", "content": "Hi"}],
            skill_name="test",
        )
    assert result.vendor == "gemini"
    assert result.fallback_triggered is True
    assert "InternalServerError" in (result.primary_error or "")
    assert result.text.startswith("GEMINI:")


def test_permanent_error_reraised_without_fallback():
    client = _MockClient(raise_exc=AuthenticationError("401 invalid api key"))
    with pytest.raises(AuthenticationError):
        F.call_anthropic_with_fallback(
            client=client,
            model="claude-sonnet-4-6",
            max_tokens=100,
            system="x",
            messages=[{"role": "user", "content": "y"}],
            skill_name="test",
        )


def test_flatten_string_system():
    out = F._flatten_to_prompt("You are X", [{"role": "user", "content": "Hi"}])
    assert "SYSTEM:\nYou are X" in out
    assert "USER:\nHi" in out


def test_flatten_list_system_with_cache_control_blocks():
    out = F._flatten_to_prompt(
        [
            {"type": "text", "text": "Part A", "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text": "Part B"},
        ],
        [{"role": "user", "content": "Hi"}],
    )
    assert "Part A" in out
    assert "Part B" in out
    assert "USER:\nHi" in out


def test_chain_exhausted_raises_runtime_error_with_attempt_summary():
    client = _MockClient(raise_exc=InternalServerError("503"))

    def always_fail_loader(*a, **kw):
        return lambda *a2, **k2: (_ for _ in ()).throw(RuntimeError("vendor down"))

    with patch.object(F, "_load_consult_fn", side_effect=always_fail_loader):
        with pytest.raises(RuntimeError) as exc_info:
            F.call_anthropic_with_fallback(
                client=client,
                model="claude-haiku-4-5",
                max_tokens=100,
                system="x",
                messages=[{"role": "user", "content": "y"}],
                skill_name="test",
            )
    msg = str(exc_info.value)
    assert "exhausted" in msg
    assert "gemini" in msg
    assert "grok" in msg


def test_tier_for_model_dispatches_by_substring():
    assert F._tier_for_model("claude-haiku-4-5-20251001") == "haiku"
    assert F._tier_for_model("claude-sonnet-4-6") == "sonnet"
    assert F._tier_for_model("claude-opus-4-7") == "opus"
    with pytest.raises(ValueError):
        F._tier_for_model("gpt-4")


def test_unknown_tier_raises_clear_error():
    client = _MockClient(raise_exc=InternalServerError("503"))
    with pytest.raises(ValueError, match="cannot derive tier"):
        F.call_anthropic_with_fallback(
            client=client,
            model="gpt-4-mystery",
            max_tokens=100,
            system="x",
            messages=[{"role": "user", "content": "y"}],
            skill_name="test",
        )


# ============================================================
# Track B: downgrade signal heuristic
# ============================================================
def _make_response(output_tokens: int, stop_reason: str = "end_turn",
                   has_tool_use: bool = False, text: str = "answer"):
    """Helper - build a mock Anthropic response with controlled signal shape."""
    class _ToolBlock:
        def __init__(self): self.type = "tool_use"
    blocks = [_Block(text)]
    if has_tool_use:
        blocks.append(_ToolBlock())
    resp = _Resp(text)
    resp.content = blocks
    resp.stop_reason = stop_reason
    resp.usage.output_tokens = output_tokens
    return resp


def test_downgrade_signal_flags_short_sonnet_response():
    """Sonnet producing <500 tokens, no tools, normal stop -> flag."""
    signals = F._compute_downgrade_signal(_make_response(200), "claude-sonnet-4-6")
    assert signals is not None
    assert signals["downgrade_candidate"] is True
    assert signals["output_tokens"] == 200
    assert signals["stop_reason"] == "end_turn"
    assert signals["has_tool_use"] is False


def test_downgrade_signal_skips_haiku_calls():
    """Already cheap -> never flag, even on a one-token answer."""
    signals = F._compute_downgrade_signal(_make_response(50), "claude-haiku-4-5-20251001")
    assert signals["downgrade_candidate"] is False


def test_downgrade_signal_skips_tool_use():
    """Tool use means structured agent flow - cheaper tier not necessarily fit."""
    signals = F._compute_downgrade_signal(
        _make_response(200, has_tool_use=True), "claude-sonnet-4-6"
    )
    assert signals["has_tool_use"] is True
    assert signals["downgrade_candidate"] is False


def test_downgrade_signal_skips_max_tokens_stop():
    """Truncated output - probably needed more room, not less model."""
    signals = F._compute_downgrade_signal(
        _make_response(8000, stop_reason="max_tokens"), "claude-opus-4-7"
    )
    assert signals["stop_reason"] == "max_tokens"
    assert signals["downgrade_candidate"] is False


def test_downgrade_signal_skips_long_outputs():
    """500+ tokens means the model used the room - keep the tier."""
    signals = F._compute_downgrade_signal(_make_response(750), "claude-sonnet-4-6")
    assert signals["downgrade_candidate"] is False


def test_downgrade_signal_returns_none_for_non_anthropic_shape():
    """Gemini/Grok wrappers return strings - signal heuristic n/a."""
    assert F._compute_downgrade_signal("plain string from gemini", "gemini-2.5-pro") is None
    assert F._compute_downgrade_signal(None, "grok-4") is None
