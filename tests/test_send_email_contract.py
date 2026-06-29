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


def _stub_exchangelib():
    """Stub exchangelib so send-email.py loads in a test context."""
    if "exchangelib" not in sys.modules:
        stub = types.ModuleType("exchangelib")
        for attr in ("Account", "Credentials", "Configuration", "DELEGATE",
                     "FileAttachment", "HTMLBody", "Message", "Mailbox"):
            setattr(stub, attr, None)
        sys.modules["exchangelib"] = stub


@pytest.fixture(scope="module")
def send_email_mod():
    _stub_exchangelib()
    spec = importlib.util.spec_from_file_location("send_email", _SEND_EMAIL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


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
