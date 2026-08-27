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

    # --- Mock the langfuse 4.x client: capture every update_current_span call ---
    #
    # This captured `langfuse.decorators.langfuse_context.update_current_observation`
    # until 2026-08-27. That is the 3.x API, and `observability_safe.py` says so
    # in its own docstring: "The old langfuse.decorators.langfuse_context module
    # does not exist in 4.x and raises ImportError". The code calls
    # `_get_langfuse_client()` then `client.update_current_span(metadata=meta)`.
    # So nothing was ever captured, `all_captured_metadata` stayed empty, and the
    # three leak assertions below were `MARKER not in "[]"` - true forever.
    #
    # The file's docstring promises "If this test breaks, it means sovereign data
    # is leaking into Langfuse". It could not break.
    mock_client = MagicMock()

    def capture_update_span(*args, **kwargs):
        meta = kwargs.get("metadata", {})
        if args:
            meta = args[0] if isinstance(args[0], dict) else meta
        all_captured_metadata.append(meta)

    mock_client.update_current_span.side_effect = capture_update_span

    mock_langfuse_mod = MagicMock()
    mock_langfuse_mod.observe = mock_observe
    mock_langfuse_mod.get_client.return_value = mock_client

    with (
        patch.dict("sys.modules", {"langfuse": mock_langfuse_mod}),
    ):
        import scripts.utils.observability_safe as obs_mod
        # Only ONE cache exists: the observe decorator. `_get_langfuse_client()`
        # imports fresh on every call, so there is nothing to reset for it. The
        # old code cleared `_langfuse_context_cache`, a name the module has
        # never had - setting a nonexistent module attribute is silent, which is
        # why nobody noticed the mock was aimed at a dead API.
        obs_mod._langfuse_observe_cache = None

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

    # The floor, and it is the whole reason this test can now fail. Every
    # assertion below is "marker NOT in the blob", which an EMPTY blob satisfies.
    # The sibling `test_observability_safe.py` has carried this line all along;
    # this file did not.
    assert len(all_captured_metadata) >= 1, (
        "update_current_span was never called, so nothing was inspected and "
        "these leak assertions measured an empty list"
    )

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
