"""Shard scripts-13-p4: the one script allowed to put a message on the wire.

* ``is_html`` tested for ``<`` + a letter + a later ``>``, which fires on
  ordinary prose. "if x<b and y>z then ship it" was classified HTML and sent
  VERBATIM, and a mail client reads ``<b and y>`` as a ``<b>`` start tag: the
  words "and y" never reach the recipient. Silent deletion from an outbound
  message, with no error anywhere.

* ``build_file_attachments`` never validated the ``attach`` field of the batch
  JSON, so ``"attach": 7`` or ``["/nonexistent/a.pdf", 5]`` raised TypeError - not an
  ``AttachmentError`` - which escaped the per-message handler and aborted the
  whole batch AFTER earlier messages had already gone out.

* ``_replyall_recipients`` and the plain-reply branch read the original's
  ``sender``. exchangelib addresses a reply to its ``author``. For a
  delegate-sent message those differ, so the reply went to one mailbox and the
  CRM recorded a conversation with another.

* The ``save_draft`` stage told the operator "No draft was saved" for every
  exception out of ``msg.save()``, including a read timeout, which establishes
  only that the ANSWER did not come back.

Nothing here sends. Every exchangelib object is a stand-in.

Run: python3 -m pytest tests/test_a_reply_addressed_to_someone_the_log_never_named.py
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


@pytest.fixture(scope="module")
def se():
    spec = importlib.util.spec_from_file_location("se_under_test",
                                                  ROOT / "scripts" / "send-email.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["se_under_test"] = module
    spec.loader.exec_module(module)
    return module


# ============================================================
# The words that never reached the recipient
# ============================================================

@pytest.mark.parametrize("body", [
    "if x<b and y>z then ship it",
    "the range is 3<n and n>7",
    "use <Ctrl> to cancel",
    "compare <p and q> carefully",
    "a < b and c > d",
    "plain sentence with no brackets",
    "cost <under budget> as agreed",
])
def test_prose_is_not_mistaken_for_html(se, body):
    """Classified HTML, it is inserted raw and a tag-shaped run is swallowed."""
    assert se.is_html(body) is False


@pytest.mark.parametrize("body", [
    "<p>real html</p>",
    "<br>", "<br/>", "<br />",
    "</p>",
    "<div style='font-size:11pt'>y</div>",
    "<TABLE><TR><TD>x</TD></TR></TABLE>",
    "text with <a href='https://example.invalid'>a link</a>",
    "<ul><li>one</li><li>two</li></ul>",
])
def test_real_html_is_still_recognised(se, body):
    assert se.is_html(body) is True


def test_the_shipped_signature_is_recognised_as_html(se):
    """The one HTML this script always appends. A regression here escapes the
    signature into the body as literal source."""
    if not se.signature_path().exists():
        pytest.skip("no signature on this clone")

    assert se.is_html(se.signature_path().read_text(encoding="utf-8")) is True


def test_prose_survives_the_round_trip_into_the_body(se):
    """The consequence, not the classifier: the words must still be there."""
    body = "if x<b and y>z then ship it"

    html = se._build_full_html(body, signature="")

    assert "and y" in html
    assert "&lt;b and y&gt;" in html, "the brackets were not escaped"


# ============================================================
# The batch that stopped halfway
# ============================================================

@pytest.mark.parametrize("bad", [7, True, 3.5, {"a": 1}, object()])
def test_a_wrong_typed_attach_raises_the_error_the_caller_catches(se, bad):
    """TypeError escaped the per-message handler and killed the whole batch."""
    with pytest.raises(se.AttachmentError):
        se.build_file_attachments(bad)


@pytest.mark.parametrize("bad", [["/nonexistent/a.pdf", 5], [None], [{"p": 1}]])
def test_a_list_holding_a_non_path_is_refused(se, bad):
    with pytest.raises(se.AttachmentError):
        se.build_file_attachments(bad)


def test_the_refusal_names_what_was_wrong(se):
    with pytest.raises(se.AttachmentError) as exc:
        se.build_file_attachments(["/nonexistent/a.pdf", 5])

    assert "not paths" in str(exc.value)
    assert "int" in str(exc.value)


@pytest.mark.parametrize("empty", [None, [], ""])
def test_no_attachments_is_still_no_attachments(se, empty):
    assert se.build_file_attachments(empty) == []


def test_a_missing_path_is_still_the_same_error(se, tmp_path):
    """The case the guard already covered must not change shape."""
    with pytest.raises(se.AttachmentError, match="not found"):
        se.build_file_attachments([str(tmp_path / "nope.pdf")])


def test_a_single_path_string_is_still_accepted(se, tmp_path):
    """The string form is documented; the type guard must not eat it."""
    target = tmp_path / "f.txt"
    target.write_text("x", encoding="utf-8")

    attachments = se.build_file_attachments(str(target))

    assert len(attachments) == 1


# ============================================================
# The reply addressed to someone the log never named
# ============================================================

class _Mailbox:
    def __init__(self, email):
        self.email_address = email


class _Original:
    """Shaped like an exchangelib Message: `author` and `sender` are distinct."""

    def __init__(self, author=None, sender=None, to=None, cc=None):
        self.author = author
        self.sender = sender
        self.to_recipients = to or []
        self.cc_recipients = cc or []


class _Account:
    primary_smtp_address = "me@example.invalid"


BOSS = "boss@example.invalid"
ASSISTANT = "assistant@example.invalid"


def test_a_delegate_sent_message_is_logged_to_the_author(se):
    """exchangelib replies to `author`; this read `sender` and logged that."""
    original = _Original(author=_Mailbox(BOSS), sender=_Mailbox(ASSISTANT))

    assert se._reply_target(original).email_address == BOSS


def test_an_ordinary_message_is_unchanged(se):
    """author == sender for almost every message, which is why this hid."""
    original = _Original(author=_Mailbox("alice@example.invalid"),
                         sender=_Mailbox("alice@example.invalid"))

    assert se._reply_target(original).email_address == "alice@example.invalid"


def test_a_message_with_no_author_falls_back_to_sender(se):
    original = _Original(author=None, sender=_Mailbox("only@example.invalid"))

    assert se._reply_target(original).email_address == "only@example.invalid"


def test_a_message_with_neither_resolves_to_nothing(se):
    assert se._reply_target(_Original()) is None


def test_reply_all_counts_the_author_not_the_delegate(se):
    original = _Original(author=_Mailbox(BOSS), sender=_Mailbox(ASSISTANT),
                         to=[_Mailbox("me@example.invalid"),
                             _Mailbox("x@example.invalid")])

    recipients = sorted(se._replyall_recipients(_Account(), original))

    assert recipients == [BOSS, "x@example.invalid"]
    assert ASSISTANT not in recipients


def test_reply_all_still_drops_the_account_itself(se):
    original = _Original(author=_Mailbox(BOSS),
                         to=[_Mailbox("ME@Example.invalid")])

    assert se._replyall_recipients(_Account(), original) == [BOSS]


# ============================================================
# The draft that may or may not exist
# ============================================================

def test_the_save_draft_stage_does_not_claim_the_draft_is_absent(se):
    """A read timeout answers the reply, not the write."""
    guidance = se._STAGE_GUIDANCE["save_draft"]

    assert "No draft was saved" not in guidance
    assert "UNKNOWN" in guidance
    assert "Nothing was sent" in guidance


def test_the_docstring_agrees_with_the_guidance(se):
    doc = se._send_email_core.__doc__

    assert "no draft exists" not in doc
    assert "whether a draft exists is unknown" in doc


@pytest.mark.parametrize("stage,must_say", [
    ("attachments", "Nothing was saved and nothing was sent"),
    ("attach", "saved but NOT sent"),
    ("send", "Check Sent Items"),
])
def test_the_other_three_stages_are_unchanged(se, stage, must_say):
    assert must_say in se._STAGE_GUIDANCE[stage]


def test_an_unstamped_failure_still_says_unknown(se):
    assert "UNKNOWN" in se._STAGE_GUIDANCE_UNKNOWN
