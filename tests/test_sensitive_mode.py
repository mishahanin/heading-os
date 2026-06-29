"""Fail-closed invariant for SENSITIVE_MODE (Plan 5 — vault removal).

The removed `_secure/` vault air-gapped observability *when present* (fail-closed:
forget it, you stay safe). Its successor flag must keep that property: a missing,
empty, or garbage ``SENSITIVE_MODE`` must degrade to "no telemetry", never to
"telemetry on". Telemetry flows ONLY when sensitivity is explicitly cleared.
"""

import pytest

from scripts.utils.sensitive import is_sensitive, sanitize_prompt_guidance
from scripts.utils import observability as obs


# --- is_sensitive(): the fail-closed core ----------------------------------

def test_unset_is_sensitive(monkeypatch):
    monkeypatch.delenv("SENSITIVE_MODE", raising=False)
    assert is_sensitive() is True


def test_empty_is_sensitive(monkeypatch):
    monkeypatch.setenv("SENSITIVE_MODE", "")
    assert is_sensitive() is True


def test_garbage_is_sensitive(monkeypatch):
    monkeypatch.setenv("SENSITIVE_MODE", "maybe")
    assert is_sensitive() is True


def test_truthy_is_sensitive(monkeypatch):
    monkeypatch.setenv("SENSITIVE_MODE", "on")
    assert is_sensitive() is True


@pytest.mark.parametrize("cleared", ["off", "0", "false", "no", "cleared", "OFF", "False"])
def test_explicit_clear_is_not_sensitive(monkeypatch, cleared):
    monkeypatch.setenv("SENSITIVE_MODE", cleared)
    assert is_sensitive() is False


# --- observability.is_enabled() inherits the fail-closed gate ----------------

def test_telemetry_suppressed_when_sensitive_even_if_langfuse_on(monkeypatch):
    monkeypatch.delenv("SENSITIVE_MODE", raising=False)  # missing -> sensitive
    monkeypatch.setenv("LANGFUSE_ENABLED", "true")
    assert obs.is_enabled() is False


def test_telemetry_suppressed_on_garbage_flag(monkeypatch):
    monkeypatch.setenv("SENSITIVE_MODE", "yes-please")
    monkeypatch.setenv("LANGFUSE_ENABLED", "true")
    assert obs.is_enabled() is False


def test_telemetry_enabled_only_when_explicitly_cleared(monkeypatch):
    monkeypatch.setenv("SENSITIVE_MODE", "off")
    monkeypatch.setenv("LANGFUSE_ENABLED", "true")
    assert obs.is_enabled() is True


def test_cleared_but_langfuse_off_still_disabled(monkeypatch):
    monkeypatch.setenv("SENSITIVE_MODE", "off")
    monkeypatch.setenv("LANGFUSE_ENABLED", "false")
    assert obs.is_enabled() is False


# --- sanitization guidance is non-empty and names the forbidden detail -------

def test_sanitize_guidance_names_forbidden_detail():
    g = sanitize_prompt_guidance()
    assert "codename" in g.lower()
    assert "company" in g.lower()
