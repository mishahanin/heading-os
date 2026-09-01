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


@pytest.mark.parametrize("padded", ["off ", " off", "  off\t", "\noff\n"])
def test_a_padded_clear_still_clears(monkeypatch, padded):
    """A trailing space on a hand-edited `.env` line is the ordinary way a value
    picks up whitespace, and the same mechanism already cost this workspace a
    silent alerting outage in `sentinel.resolve_notify_target` (2026-08-07).

    MEASURED 2026-09-01: dropping `.strip()` from `is_sensitive` left this whole
    file green. Its consequence is fail-CLOSED, which is why it went unnoticed
    and why it is worth pinning rather than shrugging at: the operator who typed
    `SENSITIVE_MODE=off ` gets no telemetry and no explanation.
    """
    monkeypatch.setenv("SENSITIVE_MODE", padded)
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


@pytest.mark.parametrize("blank", [" ", "   ", "\t", "\n"])
def test_whitespace_is_the_same_absence_as_empty_not_a_declaration(monkeypatch, blank):
    """"Empty is NOT a declaration; it is the same absence as unset, spelled
    shorter" is the docstring's own rule, and whitespace is that same absence
    spelled longer still.

    This is the half that is NOT fail-closed. A declaration outranks a
    per-payload egress proof, so a `SENSITIVE_MODE=` line that picked up a
    trailing space would silently make every proof-holding caller behave as if a
    person had typed a value. MEASURED 2026-09-01: dropping `.strip()` from
    `sensitivity_is_declared` left this file green.
    """
    monkeypatch.setenv("SENSITIVE_MODE", blank)
    assert sensitivity_is_declared() is False
    assert is_sensitive() is True


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


# --- the SECOND copy of the same gate, on the sovereign-data path ------------
#
# `observability_safe._is_enabled` is a byte-for-byte copy of
# `observability.is_enabled` above, sitting under a comment that says so:
# "same fail-closed logic as observability.py". It guards the daemon that
# processes EMAIL - the path Principle 5 exists for - and nothing measured it.
#
# MEASURED 2026-09-01 by mutation, with tests/test_sensitive_mode.py,
# tests/test_observability.py and the whole tests/inbox_pulse/ suite running
# together:
#
#     observability.is_enabled       drop `if is_sensitive()`     CAUGHT
#     observability_safe._is_enabled drop `if is_sensitive()`     SURVIVED
#     observability.is_enabled       drop `.strip()`              CAUGHT
#     observability_safe._is_enabled drop `.strip()`              SURVIVED
#
# So the whole fail-closed property could be deleted from the copy that handles
# sovereign data with the suite green. `tests/inbox_pulse/test_observability_safe.py`
# sets `SENSITIVE_MODE=off` in its fixture to switch tracing ON; it never asks
# what happens when sensitivity is not cleared. This is the campaign's dominant
# pattern on a security gate: one fix, two copies, and the untested copy is the
# one carrying email.

from scripts.utils import observability_safe as obs_safe  # noqa: E402


@pytest.mark.parametrize("mode", [None, "", "on", "1", "yes", "maybe", " "],
                         ids=["unset", "empty", "on", "1", "yes", "garbage", "space"])
def test_the_sovereign_tracer_is_off_unless_sensitivity_is_cleared(monkeypatch, mode):
    """The fail-closed half. `LANGFUSE_ENABLED` is deliberately ON throughout,
    so the only thing that can be answering False is the sensitivity gate."""
    monkeypatch.setenv("LANGFUSE_ENABLED", "true")
    if mode is None:
        monkeypatch.delenv("SENSITIVE_MODE", raising=False)
    else:
        monkeypatch.setenv("SENSITIVE_MODE", mode)

    assert obs_safe._is_enabled() is False


def test_the_sovereign_tracer_turns_on_when_sensitivity_is_cleared(monkeypatch):
    """The anti-vacuity jaw. A `_is_enabled` hard-wired to False satisfies every
    case above and silently disables tracing for good."""
    monkeypatch.setenv("SENSITIVE_MODE", "off")
    monkeypatch.setenv("LANGFUSE_ENABLED", "true")
    assert obs_safe._is_enabled() is True


@pytest.mark.parametrize("padded", ["false ", " false", "off\t", "\n0\n", " no "],
                         ids=["trail", "lead", "tab", "newlines", "both"])
def test_a_padded_langfuse_disable_still_disables_both_copies(monkeypatch, padded):
    """The fail-OPEN direction, which is why this one matters most.

    Sensitivity is cleared, so the only thing left between the daemon and
    Langfuse is `LANGFUSE_ENABLED`. Without `.strip()`, `"false "` is not in the
    disable tuple and traces ship despite the operator having turned them off.
    A trailing space on a hand-edited `.env` line is the ordinary way a value
    picks up whitespace, and this workspace has already paid for it twice:
    `sentinel.resolve_notify_target` (2026-08-07) and `is_sensitive` above.

    Both copies are asserted in one test on purpose. Asserting them separately
    is how the first copy came to be guarded and the second did not.
    """
    monkeypatch.setenv("SENSITIVE_MODE", "off")
    monkeypatch.setenv("LANGFUSE_ENABLED", padded)
    assert obs.is_enabled() is False, padded
    assert obs_safe._is_enabled() is False, padded


@pytest.mark.parametrize("gate", [lambda: obs.is_enabled(),
                                  lambda: obs_safe._is_enabled()],
                         ids=["observability", "observability_safe"])
def test_an_unset_langfuse_flag_defaults_to_on_once_sensitivity_is_cleared(
        monkeypatch, gate):
    """The documented default ("LANGFUSE_ENABLED (default on)"), unbound in both
    copies: every case in this file and in test_observability.py sets the
    variable, so flipping the default to "false" left them all green. The cost
    is fail-closed rather than fail-open, but a telemetry stack that is off for
    a reason nobody wrote down is the silent no-op `_warn_if_degraded` exists to
    refuse.
    """
    monkeypatch.delenv("LANGFUSE_ENABLED", raising=False)
    monkeypatch.setenv("SENSITIVE_MODE", "off")
    assert gate() is True
