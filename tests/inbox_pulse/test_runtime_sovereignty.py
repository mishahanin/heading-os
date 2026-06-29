"""Runtime sovereignty guard for the Inbox Pulse daemon.

This test catches the class of bug where future code accidentally passes
sovereign email payload data (body, subject text, full sender address) through
the ``metadata=`` field of langfuse_context.update_current_observation.

It does NOT rely on static analysis - it wires up a real call stack, captures
every metadata dict that reaches the Langfuse mock at runtime, and asserts that
distinctive marker strings from the synthetic payload never appear in any
serialized form of those dicts.

If this test breaks, it means sovereign data is leaking into Langfuse.
Fix the leak before merging - do not relax the assertion.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Workspace root on sys.path
# ---------------------------------------------------------------------------
_WORKSPACE = Path(__file__).resolve().parent.parent.parent
if str(_WORKSPACE) not in sys.path:
    sys.path.insert(0, str(_WORKSPACE))

from scripts.utils.observability_safe import observe_metadata_only  # noqa: E402

# Distinctive markers that must NEVER appear in Langfuse metadata
_BODY_MARKER = "SENSITIVE_BODY_MARKER_XYZ_12345"
_SUBJECT_MARKER = "confidential-subject-marker-67890"
_SENDER = "alice@example.com"


def test_no_email_content_in_trace_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    """No sovereign email payload reaches Langfuse metadata at runtime.

    Constructs a synthetic email with distinctive marker strings in body,
    subject, and sender.  Captures every metadata dict passed to
    langfuse_context.update_current_observation.  Asserts that markers are
    absent from the full JSON serialization of all captured metadata.
    """
    monkeypatch.setenv("SENSITIVE_MODE", "off")  # fail-closed: clear sensitivity to exercise tracing
    monkeypatch.setenv("LANGFUSE_ENABLED", "true")
    monkeypatch.delenv("INBOX_PULSE_DEBUG_TRACE", raising=False)

    all_captured_metadata: list[dict] = []

    # --- Mock langfuse.observe: call-through decorator, capture_input/output flags respected ---
    def fake_observe(name: str = "", capture_input: bool = True, capture_output: bool = True):
        def decorator(fn):
            return fn  # pass through; the wrapper handles timing / metadata injection
        return decorator

    mock_observe = MagicMock(side_effect=lambda **kw: fake_observe(**kw))

    # --- Mock langfuse_context: capture every update_current_observation call ---
    mock_ctx = MagicMock()

    def capture_update(*args, **kwargs):
        meta = kwargs.get("metadata", {})
        if args:
            # In case called positionally
            meta = args[0] if isinstance(args[0], dict) else meta
        all_captured_metadata.append(meta)

    mock_ctx.update_current_observation.side_effect = capture_update

    with (
        patch.dict("sys.modules", {
            "langfuse": MagicMock(observe=mock_observe),
            "langfuse.decorators": MagicMock(langfuse_context=mock_ctx),
        }),
    ):
        import scripts.utils.observability_safe as obs_mod
        obs_mod._langfuse_observe_cache = None
        obs_mod._langfuse_context_cache = None

        @observe_metadata_only("test_classify")
        def classify(email_addr: str, subject: str, body: str) -> dict:
            return {"tier": "CRITICAL"}

        result = classify(
            email_addr=_SENDER,
            subject=f"{_SUBJECT_MARKER} a proposed offer",
            body=f"{_BODY_MARKER} sensitive content here",
        )

    # Function must still return the correct value
    assert result == {"tier": "CRITICAL"}, f"Unexpected return value: {result!r}"

    # Serialize ALL captured metadata dicts to a single JSON blob
    serialized = json.dumps(all_captured_metadata, default=str)

    # Assert markers are absent
    assert _BODY_MARKER not in serialized, (
        f"Sovereign body marker '{_BODY_MARKER}' found in Langfuse metadata: "
        f"{serialized[:500]}"
    )
    assert _SUBJECT_MARKER not in serialized, (
        f"Sovereign subject marker '{_SUBJECT_MARKER}' found in Langfuse metadata: "
        f"{serialized[:500]}"
    )
    # Full sender must also be absent (only domain is allowed)
    assert _SENDER not in serialized, (
        f"Full sender address '{_SENDER}' found in Langfuse metadata: "
        f"{serialized[:500]}"
    )
