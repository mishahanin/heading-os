"""Tests for the threaded reply / reply-all / forward additions to send-email.py.

Covers the pure, offline-testable surface: subject derivation, the shared HTML
body builder, and find_message's folder scan (match by subject + sender, newest
first, exact-id path, no-match). The Exchange send path itself needs a live EWS
account and is not unit-tested here. The module is loaded by path because its
filename is kebab-case.
"""
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_spec = importlib.util.spec_from_file_location("send_email", ROOT / "scripts" / "send-email.py")
send_email = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(send_email)


# --- subject derivation ---

def test_derive_subject_reply_prefixes():
    assert send_email._derive_subject("reply", "31C / Globex") == "RE: 31C / Globex"
    assert send_email._derive_subject("reply_all", "Deal terms") == "RE: Deal terms"


def test_derive_subject_no_double_prefix():
    assert send_email._derive_subject("reply", "RE: 31C") == "RE: 31C"
    assert send_email._derive_subject("forward", "FWD: x") == "FWD: x"
    assert send_email._derive_subject("forward", "FW: y") == "FW: y"


def test_derive_subject_forward_and_override_and_empty():
    assert send_email._derive_subject("forward", "Acme Group") == "FW: Acme Group"
    assert send_email._derive_subject("reply", "anything", "Custom Subject") == "Custom Subject"
    assert send_email._derive_subject("forward", "") == "FW:"
    assert send_email._derive_subject("reply", "") == "RE:"


# --- shared HTML builder ---

def test_build_full_html_escapes_plain_text_and_appends_signature():
    out = send_email._build_full_html("Tom & Jerry said hi", "SIGBLOCK")
    assert "Tom &amp; Jerry" in out      # plain text is HTML-escaped
    assert out.endswith("SIGBLOCK")      # signature appended last
    assert "Segoe UI" in out             # font stack wrapper applied


def test_build_full_html_passes_through_real_html():
    out = send_email._build_full_html("<p>Hello</p>", "SIG")
    assert "<p>Hello</p>" in out
    assert "&lt;p&gt;" not in out        # not re-escaped


# --- find_message folder scan (mocked Exchange) ---

class _FakeMailbox:
    def __init__(self, email):
        self.email_address = email


class _FakeMsg:
    def __init__(self, subject, sender, dt, _id=None):
        self.subject = subject
        self.sender = _FakeMailbox(sender)
        self.datetime_received = dt
        self.id = _id


class _FakeQS:
    def __init__(self, items):
        self._items = list(items)

    def all(self):
        return _FakeQS(self._items)

    def filter(self, **kw):
        sub = kw.get("subject__icontains")
        if sub is None:
            return _FakeQS(self._items)
        return _FakeQS([i for i in self._items if sub.lower() in (i.subject or "").lower()])

    def order_by(self, key):
        rev = key.startswith("-")
        field = key.lstrip("-")
        return _FakeQS(sorted(self._items, key=lambda i: getattr(i, field), reverse=rev))

    def get(self, id=None):
        for i in self._items:
            if i.id == id:
                return i
        raise KeyError(id)

    def __getitem__(self, sl):
        return self._items[sl]


class _FakeAccount:
    def __init__(self, inbox, sent=None):
        self.inbox = _FakeQS(inbox)
        self.sent = _FakeQS(sent or [])


def _sample_inbox():
    return [
        _FakeMsg("31C / Globex Systems", "hannah@globex.com", 10, _id="A"),
        _FakeMsg("RE: 31C / Globex Systems", "pat.nolan@globex.com", 30, _id="B"),
        _FakeMsg("Unrelated promo", "spam@x.com", 40, _id="C"),
        _FakeMsg("31C / Globex Systems older", "pat.nolan@globex.com", 20, _id="D"),
    ]


def test_find_message_by_subject_returns_newest():
    acc = _FakeAccount(_sample_inbox())
    msg = send_email.find_message(acc, match_subject="Globex")
    assert msg.id == "B"   # newest (dt=30) among the three Globex-subject items


def test_find_message_by_sender_and_subject():
    acc = _FakeAccount(_sample_inbox())
    msg = send_email.find_message(acc, match_from="pat.nolan@globex.com",
                                  match_subject="Globex")
    assert msg.id == "B"   # newest Pat+Globex match


def test_find_message_no_match_returns_none():
    acc = _FakeAccount(_sample_inbox())
    assert send_email.find_message(acc, match_subject="nonexistent") is None
    assert send_email.find_message(acc, match_from="ghost@nowhere.com",
                                   match_subject="Globex") is None


def test_find_message_by_exact_id():
    acc = _FakeAccount(_sample_inbox())
    msg = send_email.find_message(acc, match_id="D")
    assert msg.id == "D"
