"""Tests for scripts/utils/observability_safe.py.

Verifies three core contracts:
1. The decorated function still returns its value unchanged.
2. Only whitelisted metadata keys reach Langfuse - never sovereign payload.
3. INBOX_PULSE_DEBUG_TRACE=true writes the full payload to a local file.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Ensure the workspace root is importable from within tests/
# ---------------------------------------------------------------------------
_WORKSPACE = Path(__file__).resolve().parent.parent.parent
if str(_WORKSPACE) not in sys.path:
    sys.path.insert(0, str(_WORKSPACE))

from scripts.utils.observability_safe import observe_metadata_only  # noqa: E402

# Whitelisted keys that are allowed to appear in Langfuse metadata
_WHITELIST = frozenset({
    "latency_ms",
    "input_tokens",
    "output_tokens",
    "model",
    "tier",
    "confidence",
    "sender_domain",
    "subject_length",
    "language",
})


# ---------------------------------------------------------------------------
# Helper: force LANGFUSE_ENABLED so the tracing path is exercised
# ---------------------------------------------------------------------------

def _enable_langfuse_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SENSITIVE_MODE", "off")  # fail-closed: clear sensitivity to exercise tracing
    monkeypatch.setenv("LANGFUSE_ENABLED", "true")
    monkeypatch.delenv("INBOX_PULSE_DEBUG_TRACE", raising=False)


# ---------------------------------------------------------------------------
# Test 1 - decorated function returns its original value unchanged
# ---------------------------------------------------------------------------

def test_decorated_function_returns_correct_value(monkeypatch: pytest.MonkeyPatch) -> None:
    """A decorated function must return its value exactly as if undecorated."""
    # Disable real Langfuse to keep this test unit-scoped
    monkeypatch.setenv("LANGFUSE_ENABLED", "false")

    @observe_metadata_only("test_passthrough")
    def say_hello() -> str:
        return "hello"

    assert say_hello() == "hello"


# ---------------------------------------------------------------------------
# Test 2 - only whitelisted keys reach Langfuse; sovereign data absent
# ---------------------------------------------------------------------------

def test_metadata_only_keys_captured(monkeypatch: pytest.MonkeyPatch) -> None:
    """Langfuse receives ONLY whitelisted metadata keys.

    Sovereign data (email body, subject text, full sender address) must not
    appear in any value passed to client.update_current_span.
    """
    _enable_langfuse_env(monkeypatch)

    captured_metadata: list[dict] = []

    # Mock langfuse.observe so it is a no-op decorator that still calls through
    mock_observe = MagicMock()

    def fake_observe(name: str, capture_input: bool, capture_output: bool):
        def decorator(fn):
            return fn  # pass through - we just want to capture the metadata call
        return decorator

    mock_observe.side_effect = lambda **kw: fake_observe(**kw)

    # Mock langfuse client with update_current_span (langfuse 4.x API)
    mock_client = MagicMock()

    def capture_update_span(*args, **kwargs):
        meta = kwargs.get("metadata", {})
        captured_metadata.append(meta)

    mock_client.update_current_span.side_effect = capture_update_span

    mock_langfuse_mod = MagicMock()
    mock_langfuse_mod.observe = mock_observe
    mock_langfuse_mod.get_client.return_value = mock_client

    with (
        patch("scripts.utils.observability_safe._langfuse_observe_cache", None),
        patch.dict("sys.modules", {"langfuse": mock_langfuse_mod}),
    ):
        # Reset cache so the patch takes effect
        import scripts.utils.observability_safe as obs_mod
        obs_mod._langfuse_observe_cache = None

        @observe_metadata_only("test_classify")
        def classify(email_addr: str, subject: str, body: str) -> dict:
            return {"tier": "CRITICAL", "confidence": 0.95}

        result = classify(
            email_addr="victor@northgate.com",
            subject="Re: deal",
            body="Long body here...",
        )

    # Function return value must be preserved
    assert result == {"tier": "CRITICAL", "confidence": 0.95}

    # At least one metadata call must have been made
    assert len(captured_metadata) >= 1, "update_current_span was not called"

    # Merge all captured metadata dicts for inspection
    merged: dict = {}
    for m in captured_metadata:
        merged.update(m)

    # All keys must be in the whitelist
    extra_keys = set(merged.keys()) - _WHITELIST
    assert not extra_keys, f"Non-whitelisted keys in metadata: {extra_keys}"

    # Specific whitelisted values must be correct
    assert merged.get("tier") == "CRITICAL"
    assert merged.get("confidence") == pytest.approx(0.95)
    assert merged.get("sender_domain") == "northgate.com"
    assert merged.get("subject_length") == len("Re: deal")  # 8
    assert merged.get("language") == "en"

    # Sovereign data must be absent from all metadata dicts combined
    all_meta_json = json.dumps(captured_metadata)
    assert "Long body here..." not in all_meta_json, "Body text leaked into metadata"
    assert "Re: deal" not in all_meta_json, "Subject text leaked into metadata"
    assert "victor@northgate.com" not in all_meta_json, "Full sender address leaked"


# ---------------------------------------------------------------------------
# Test 3 - INBOX_PULSE_DEBUG_TRACE=true writes full payload to disk
# ---------------------------------------------------------------------------

def test_debug_trace_env_var_writes_local_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """When INBOX_PULSE_DEBUG_TRACE=true, the full payload is appended to
    state/email-triage/debug-trace.jsonl under INBOX_PULSE_STATE_DIR.
    """
    monkeypatch.setenv("LANGFUSE_ENABLED", "false")  # skip real tracing
    monkeypatch.setenv("INBOX_PULSE_DEBUG_TRACE", "true")
    monkeypatch.setenv("INBOX_PULSE_STATE_DIR", str(tmp_path))

    @observe_metadata_only("test_debug")
    def simple_fn(x: int, y: int) -> int:
        return x + y

    result = simple_fn(3, 4)
    assert result == 7

    trace_file = tmp_path / "debug-trace.jsonl"
    assert trace_file.exists(), "debug-trace.jsonl was not created"

    lines = trace_file.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1, f"Expected 1 line in debug trace, got {len(lines)}"

    payload = json.loads(lines[0])
    assert payload["func"] == "test_debug"
    assert "latency_ms" in payload
    assert "args" in payload
    assert "kwargs" in payload
    assert "result" in payload
    # Full input and output must be present in debug mode
    assert payload["args"] == [3, 4]
    assert payload["result"] == 7


# ---------------------------------------------------------------------------
# Test 4 - integration guard: langfuse 4.x API our wrapper depends on exists
# ---------------------------------------------------------------------------

def test_langfuse_real_api_available() -> None:
    """Sanity check: the langfuse API our wrapper depends on actually exists.

    This is a guard against API drift between langfuse versions. If langfuse
    removes or renames the get_client() / update_current_span() API in a
    future version, this test fails loudly rather than letting metadata
    silently drop in production.

    This test uses the real langfuse import with NO mocking - the point is to
    verify the installed package exposes the API surface we call.
    """
    from langfuse import get_client  # real import, not mocked
    client = get_client()
    assert hasattr(client, "update_current_span"), (
        "langfuse client missing update_current_span — metadata injection path is "
        "broken. Check the installed langfuse version and update _get_langfuse_client() "
        "in scripts/utils/observability_safe.py to match the current API."
    )
