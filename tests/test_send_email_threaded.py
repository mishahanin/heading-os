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

import pytest

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


def test_single_newlines_inside_a_paragraph_become_line_breaks():
    """The 2026-08-25 fix, which nothing held.

    HTML collapses a bare newline inside a `<p>` to one space, so a plain-text
    body written as separate lines (an address block, a numbered list, a
    sign-off the operator typed) reached the recipient as one run-on line.
    The builder escapes first and then turns the surviving single newlines into
    `<br>`.

    MEASURED 2026-09-01: deleting `.replace("\\n", "<br>")` from the builder
    left this file green, and left `test_send_email_contract.py`,
    `test_send_email_finalizer.py`,
    `tests/security/test_SEC_001_email_html_injection.py` and
    `test_an_outbound_helper_that_dropped_its_cc_list.py` green with it. The
    defect had already shipped once; nothing in the tree would have caught it
    coming back.
    """
    out = send_email._build_full_html("Line one\nLine two\n\nSecond para", "SIG")
    assert "Line one<br>Line two" in out, out
    # A blank line is still a paragraph break, not two `<br>`.
    assert "<p>Second para</p>" in out, out
    assert "<br><br>" not in out.replace("</div><br>SIG", ""), out


def test_a_carriage_return_does_not_survive_into_the_break():
    """CRLF is normalised BEFORE the escape, so no stray `\\r` sits in front of
    the `<br>` and no `&#13;` reaches the recipient."""
    out = send_email._build_full_html("Line one\r\nLine two", "SIG")
    assert "Line one<br>Line two" in out, out
    assert "\r" not in out
    assert "&#13;" not in out


def test_the_shared_builder_strips_a_typed_signoff_before_the_branded_one():
    """Otherwise the recipient gets two sign-offs, the operator's and the block.

    `strip_trailing_signoff` is called on the way in; nothing asserted it, so
    the call could be dropped with every send-email test in the tree still
    green (measured 2026-09-01 across the five files named above).
    """
    typed = "Here is the update.\n\nBest regards,\nMisha"
    out = send_email._build_full_html(typed, "SIGBLOCK")
    assert "Here is the update" in out
    assert "Best regards" not in out, (
        "the manually typed sign-off survived alongside the branded signature: "
        + out)
    assert out.endswith("SIGBLOCK")


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


# --- the sign-off list, measured rather than read ---

# Every form the shipped `_SIGNOFF_KEYWORDS` claims to handle. Written out
# rather than derived FROM that constant: a test that builds its cases from the
# list it is checking passes for any list, including an empty one.
SIGNOFFS = ["Best regards", "Kind regards", "Warm regards", "Many thanks",
            "Best", "Thanks", "Regards", "Cheers", "Sincerely", "Warmly", "BR"]


@pytest.mark.parametrize("signoff", SIGNOFFS)
def test_every_documented_signoff_form_is_stripped(signoff):
    """The three commonest two-word forms were the three that went out doubled.

    MEASURED 2026-09-01 against the shipped list, before the fix:

        Best regards  -> SURVIVES        Kind regards -> stripped
        Warm regards  -> SURVIVES        Best         -> stripped
        Many thanks   -> SURVIVES        Regards      -> stripped

    `Kind regards` was the only multi-word form present, and it is the only one
    sharing no prefix with a single-word entry, which is exactly what made the
    gap look like a complete list. An email signed "Best regards, Misha" reached
    the recipient with that sign-off AND the branded block under it - the
    doubling `strip_trailing_signoff` exists to prevent.
    """
    assert send_email.strip_trailing_signoff(
        f"Here is the update.\n\n{signoff},\nMisha") == "Here is the update."


@pytest.mark.parametrize("signoff", ["Best regards", "Many thanks", "Best"])
def test_the_two_html_shapes_strip_the_same_forms_as_plain_text(signoff):
    """The plain-text and HTML patterns share one keyword list, so a form added
    for one must work in all three. Until 2026-08-25 they genuinely disagreed."""
    for html_body in (f"<p>Here is the update.</p><p>{signoff},<br>Misha</p>",
                      f"<p>Here is the update.</p><p>{signoff},</p><p>Misha</p>"):
        out = send_email.strip_trailing_signoff(html_body)
        assert "Misha" not in out, (signoff, html_body, out)
        assert "Here is the update" in out


@pytest.mark.parametrize("body", [
    "Here is the update.\n\nBest,",                     # a sign-off with no name
    "Best regards on the deal are due Friday.",         # the words, as prose
    "Thanks for the file. Sending the rest Monday.",    # the words, mid-sentence
])
def test_the_stripper_leaves_prose_and_a_nameless_signoff_alone(body):
    """The anti-vacuity jaw. Without it, widening the keyword list is safe to
    the point of uselessness: a pattern that ate everything would pass every
    test above. A bare "Best," with no name is preserved on purpose, per
    `strip_trailing_signoff`'s own docstring."""
    assert send_email.strip_trailing_signoff(body) == body.rstrip()


@pytest.mark.parametrize("body", [
    "Here is the update.\n\nBest,\n",           # a nameless sign-off, newline-terminated
    "Here is the update.\n\nThanks,\n\n",        # and with the blank line an editor adds
    "Here is the update.\n\nBest regards,\n",    # the two-word form, still nameless
])
def test_a_nameless_signoff_survives_even_with_a_trailing_newline(body):
    """The jaw the case above cannot close, and the one a widening slips past.

    `strip_trailing_signoff`'s docstring says a bare "Best," with no name is
    preserved. The existing nameless case is `"...\\n\\nBest,"` with no trailing
    newline, and the plain-text pattern REQUIRES a `\\n` after the sign-off
    before it will look for a name, so that case is refused by the newline and
    never reaches the name token at all. MEASURED 2026-09-01 on a copy of the
    module: making `_NAME_TOKEN` optional, and separately letting it match an
    empty string, both left this whole file green.

    What that costs: a body ending "Thanks,\\n" is what an editor leaves behind
    on almost every draft, and eating it deletes the operator's own closing line
    from an email to a real person, silently, on the way out.
    """
    assert send_email.strip_trailing_signoff(body) == body.rstrip()


def test_the_stripper_wants_a_capitalised_name():
    """`_NAME_TOKEN` starts `[A-Z]`, and nothing said so. Loosening it to any
    letter makes the pattern eat an ordinary lowercase word that happens to sit
    alone on the line after a sign-off word."""
    kept = "Please review.\n\nThanks,\nsee the attached\n"
    assert send_email.strip_trailing_signoff(kept) == kept.rstrip()
    assert send_email.strip_trailing_signoff(
        "Please review.\n\nThanks,\nMisha") == "Please review."
