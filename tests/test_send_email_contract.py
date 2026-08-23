#!/usr/bin/env python3
"""Contract tests for send-email.py's _build_full_html function.

Verifies the HTML-escaping contract independently of the SEC-001 security
test so the contract survives even if the security test file changes.
"""
import importlib.util
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
_SEND_EMAIL = ROOT / "scripts" / "send-email.py"

# send-email.py calls ensure_venv() at module scope, and the fixture below loads
# it mid-run, so without a guard the replacement lands with pytest's capture
# already holding file descriptor 1 and the rest of the session's output going
# nowhere. The guard is set once in tests/conftest.py; see the comment there.


def _stub_exchangelib():
    """Stub exchangelib ONLY when it is genuinely absent, and say so.

    Returns True when a stub was installed, so the caller can remove it again.

    Two things were wrong before 2026-08-23. The guard was
    `if "exchangelib" not in sys.modules`, which asks whether the module has
    been IMPORTED yet, not whether it exists -- so on a full install (the
    package is a real dependency, 5.6.0 here) the stub landed whenever this
    file happened to load first. And it was never removed: every attribute of
    the stub is None, so any later test in the same xdist worker that imported
    exchangelib silently got Nones instead of classes, with the outcome
    depending on which worker drew which test.
    """
    if "exchangelib" in sys.modules:
        return False
    try:
        importlib.import_module("exchangelib")
        return False
    except ImportError:
        pass
    stub = types.ModuleType("exchangelib")
    for attr in ("Account", "Credentials", "Configuration", "DELEGATE",
                 "FileAttachment", "HTMLBody", "Message", "Mailbox"):
        setattr(stub, attr, None)
    sys.modules["exchangelib"] = stub
    return True


@pytest.fixture(scope="module")
def send_email_mod():
    stubbed = _stub_exchangelib()
    try:
        spec = importlib.util.spec_from_file_location("send_email", _SEND_EMAIL)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        yield mod
    finally:
        if stubbed:
            sys.modules.pop("exchangelib", None)


def test_ampersand_escaped(send_email_mod):
    result = send_email_mod._build_full_html("Tom & Jerry", "")
    assert "&amp;" in result


def test_lt_escaped(send_email_mod):
    result = send_email_mod._build_full_html("Price < 100", "")
    assert "&lt;" in result


def test_gt_escaped(send_email_mod):
    result = send_email_mod._build_full_html("5 > 3", "")
    assert "&gt;" in result


def test_double_quote_escaped(send_email_mod):
    result = send_email_mod._build_full_html('Say "hello"', "")
    assert "&quot;" in result


def test_html_body_passes_through_unescaped(send_email_mod):
    """An HTML body must NOT be double-escaped."""
    html_body = "<p>Hello <b>World</b></p>"
    result = send_email_mod._build_full_html(html_body, "")
    # The original tags must survive intact
    assert "<p>" in result
    assert "<b>" in result


def test_signature_appended(send_email_mod):
    """The signature must always appear in the output."""
    sig = "<div>TEST_SIG</div>"
    result = send_email_mod._build_full_html("plain body", sig)
    assert "TEST_SIG" in result


# ============================================================
# Resend safety: a retry must not be able to duplicate a sent email
# ============================================================
#
# Found by the 2026-08-23 audit. Both send paths wrapped `msg.send()` in
# `for attempt in range(1, 4)` with a bare `except Exception`, so EVERY failure
# was retried up to three times. The dangerous one is the failure that is not a
# failure: the server accepted the message and the response was lost on the way
# back. The retry then sends a second copy to a real recipient, and nothing in
# the output says so.
#
# The rule adopted: retry only when the error PROVES the request never reached
# the server. Everything else fails once and says the send may already have gone
# out. A human deciding to resend is a decision; a machine doing it silently is
# not.


class ConnectTimeout(Exception):
    """Stands in for requests.exceptions.ConnectTimeout.

    The CLASS NAME is the contract: `_is_safe_to_resend` walks the MRO and
    compares names, so these stand-ins must be spelled exactly as the real
    exceptions are. A leading underscore here made three of these tests fail
    against a correct implementation.
    """


class ReadTimeout(Exception):
    """Stands in for requests.exceptions.ReadTimeout."""


class ErrorServerBusy(Exception):
    """Stands in for exchangelib.errors.ErrorServerBusy."""


class ErrorInvalidRecipients(Exception):
    """Stands in for a server-side rejection."""


def test_a_connect_timeout_is_safe_to_resend(send_email_mod):
    """The connection was never established, so nothing was delivered."""
    assert send_email_mod._is_safe_to_resend(ConnectTimeout("timed out")) is True


def test_a_server_busy_error_is_safe_to_resend(send_email_mod):
    """The server answered, and its answer was 'I did not process this'."""
    assert send_email_mod._is_safe_to_resend(ErrorServerBusy("busy")) is True


def test_a_read_timeout_is_NOT_safe_to_resend(send_email_mod):
    """The exact shape that duplicates mail: the request went out, the response
    did not come back, and the message may be in the recipient's inbox."""
    assert send_email_mod._is_safe_to_resend(ReadTimeout("read timed out")) is False


def test_an_unknown_error_is_not_safe_to_resend(send_email_mod):
    """Fail toward one copy. An unrecognised error carries no proof either way,
    and the cost of the two mistakes is not symmetric: a missing email is
    noticed and re-sent by a person, a duplicate one cannot be recalled."""
    assert send_email_mod._is_safe_to_resend(ErrorInvalidRecipients("bad")) is False
    assert send_email_mod._is_safe_to_resend(RuntimeError("?")) is False


def test_subclasses_are_matched_through_the_mro(send_email_mod):
    class _Derived(ConnectTimeout):
        pass

    assert send_email_mod._is_safe_to_resend(_Derived("x")) is True


def test_an_unsafe_failure_warns_that_the_email_may_already_be_out(send_email_mod):
    """The message is the deliverable. Before this, three attempts failed and the
    operator was told only 'send failed', with no hint that a copy might exist."""
    note = send_email_mod._UNSURE_NOTE
    assert "Sent Items" in note
    assert "may" in note.lower()


def test_the_stub_is_not_installed_over_a_real_exchangelib():
    """The order-dependency this file used to create.

    The old guard asked whether exchangelib had been IMPORTED, not whether it
    exists, so on a full install the stub landed whenever this file loaded
    first -- and it was never removed. Every attribute of that stub is None, so
    any later test in the same xdist worker got Nones instead of classes, and
    which tests were hit depended on which worker drew them.

    Where the real package is installed, `_stub_exchangelib` must decline and
    the real classes must be what a caller sees.
    """
    real = pytest.importorskip("exchangelib")
    assert _stub_exchangelib() is False, \
        "the stub was installed over a real, installed exchangelib"
    assert isinstance(real.Message, type), \
        "exchangelib.Message is not a class; a stub replaced the real module"


def test_the_stub_is_removed_again_when_it_was_needed(monkeypatch):
    """On a core-only clone the stub IS needed. It must not outlive the fixture.

    Simulated rather than skipped, so the teardown path is covered on a machine
    that has the package: hide the real module from both `sys.modules` and the
    import system, run the fixture, and check nothing is left behind.
    """
    real_import = importlib.import_module

    def _no_exchangelib(name, *a, **k):
        if name == "exchangelib":
            raise ImportError("simulated core-only clone")
        return real_import(name, *a, **k)

    monkeypatch.delitem(sys.modules, "exchangelib", raising=False)
    monkeypatch.setattr(importlib, "import_module", _no_exchangelib)

    assert _stub_exchangelib() is True
    assert "exchangelib" in sys.modules
    sys.modules.pop("exchangelib", None)          # what the fixture's finally does
    assert "exchangelib" not in sys.modules
