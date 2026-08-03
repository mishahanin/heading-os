"""Fail-closed invariant for SENSITIVE_MODE (Plan 5 — vault removal).

The removed `_secure/` vault air-gapped observability *when present* (fail-closed:
forget it, you stay safe). Its successor flag must keep that property: a missing,
empty, or garbage ``SENSITIVE_MODE`` must degrade to "no telemetry", never to
"telemetry on". Telemetry flows ONLY when sensitivity is explicitly cleared.
"""

import pytest

from scripts.utils.sensitive import (
    is_sensitive,
    sanitize_prompt_guidance,
    sensitivity_is_declared,
)
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


# --- sensitivity_is_declared(): the additive sibling ------------------------
#
# Added by the egress-proof slice (2026-08-03). `is_sensitive()` cannot tell an
# unset flag from a typed one -- both answer True -- and that is correct for
# every consumer above, which must fail closed. A caller holding a per-payload
# PROOF needs the narrower question: did a person actually say be careful?
# Unset is the machine's default and a proof may govern it; a typed value is a
# human knowing something no denylist can, and a machine proof must not overrule
# it. Nothing here may change what `is_sensitive()` answers.

def test_unset_sensitivity_was_not_declared(monkeypatch):
    monkeypatch.delenv("SENSITIVE_MODE", raising=False)
    assert sensitivity_is_declared() is False


def test_empty_sensitivity_was_not_declared(monkeypatch):
    """Empty is still fail-closed for `is_sensitive`, but it is not a person
    typing a value, so it does not outrank a proof."""
    monkeypatch.setenv("SENSITIVE_MODE", "")
    assert sensitivity_is_declared() is False
    assert is_sensitive() is True


@pytest.mark.parametrize("declared", ["on", "1", "yes", "maybe"])
def test_a_typed_sensitivity_is_declared(monkeypatch, declared):
    monkeypatch.setenv("SENSITIVE_MODE", declared)
    assert sensitivity_is_declared() is True


@pytest.mark.parametrize("cleared", ["off", "0", "false", "no", "cleared"])
def test_an_explicit_clear_is_not_a_declaration_of_sensitivity(monkeypatch, cleared):
    monkeypatch.setenv("SENSITIVE_MODE", cleared)
    assert sensitivity_is_declared() is False


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
