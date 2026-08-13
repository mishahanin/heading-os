"""Message assembly for gmail-draft.py.

The network path is not exercised here. What is exercised is everything that
decides what the operator will be asked to send: which text becomes the body,
which files ride along, and whether a reply lands inside its thread. A draft
that quietly lost its attachments looks identical to one that never had any,
and the loss is only visible after the send.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _load():
    """Import the hyphenated CLI script by path."""
    path = PROJECT_ROOT / "scripts" / "gmail-draft.py"
    spec = importlib.util.spec_from_file_location("gmail_draft_cli", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["gmail_draft_cli"] = mod
    spec.loader.exec_module(mod)
    return mod


gmail_draft = _load()
body_text = gmail_draft.body_text
reply_subject = gmail_draft.reply_subject
build_message = gmail_draft.build_message
DraftBuildError = gmail_draft.DraftBuildError


LETTER = """# Draft header

**To:** them@example.com
**Subject:** Claim AB-1234-567

---

Dear Team,

The enclosed report covers the period under review.

Kind regards,
Sample Sender
"""


def test_whole_file_is_the_body_by_default(tmp_path):
    path = tmp_path / "letter.md"
    path.write_text(LETTER, encoding="utf-8")
    assert body_text(path).startswith("# Draft header")


def test_separator_flag_drops_the_header_block(tmp_path):
    path = tmp_path / "letter.md"
    path.write_text(LETTER, encoding="utf-8")
    body = body_text(path, after_separator=True)
    assert body.startswith("Dear Team,")
    assert "**To:**" not in body


def test_separator_flag_without_a_separator_raises(tmp_path):
    path = tmp_path / "plain.md"
    path.write_text("Dear Team,\n", encoding="utf-8")
    with pytest.raises(DraftBuildError, match="no '---' line"):
        body_text(path, after_separator=True)


def test_reply_subject_prefixes_once():
    assert reply_subject("Memo - AB-1234-567") == "Re: Memo - AB-1234-567"
    assert reply_subject("Re: Memo - AB-1234-567") == "Re: Memo - AB-1234-567"
    assert reply_subject("RE: Memo") == "RE: Memo"


def _attachment(tmp_path, name, size=16):
    path = tmp_path / name
    path.write_bytes(b"x" * size)
    return path


def test_attachments_keep_their_filename_and_type(tmp_path):
    pdf = _attachment(tmp_path, "report.pdf")
    jpg = _attachment(tmp_path, "photo.jpg")
    msg = build_message(["them@example.com"], [], [], "Claim", "Dear Team,\n", [pdf, jpg])
    parts = {p.get_filename(): p.get_content_type() for p in msg.iter_attachments()}
    assert parts == {"report.pdf": "application/pdf", "photo.jpg": "image/jpeg"}


def test_unknown_extension_falls_back_to_octet_stream(tmp_path):
    blob = _attachment(tmp_path, "scan.unknownext")
    msg = build_message(["them@example.com"], [], [], "Claim", "body\n", [blob])
    assert [p.get_content_type() for p in msg.iter_attachments()] == ["application/octet-stream"]


def test_missing_attachment_raises_before_any_api_call(tmp_path):
    with pytest.raises(DraftBuildError, match="attachment not found"):
        build_message(["them@example.com"], [], [], "Claim", "body\n", [tmp_path / "gone.pdf"])


def test_oversized_attachments_raise(tmp_path, monkeypatch):
    monkeypatch.setattr(gmail_draft, "MAX_TOTAL_BYTES", 32)
    big = _attachment(tmp_path, "large.pdf", size=64)
    with pytest.raises(DraftBuildError, match="25 MB limit"):
        build_message(["them@example.com"], [], [], "Claim", "body\n", [big])


def test_reply_headers_are_set_for_threading():
    parent = "<9edb83d3@mail.example.com>"
    msg = build_message(
        ["them@example.com"], ["cc@example.com"], [], "Re: Memo", "body\n", [], in_reply_to=parent
    )
    assert msg["In-Reply-To"] == parent
    assert msg["References"] == parent
    assert msg["Cc"] == "cc@example.com"


def test_no_reply_headers_when_not_replying():
    msg = build_message(["them@example.com"], [], [], "Memo", "body\n", [])
    assert msg["In-Reply-To"] is None
    assert msg["Cc"] is None


class _Recorder:
    """A Gmail service stand-in that records the API path it was driven down.

    Every attribute returns another recorder, so any call chain resolves; what
    the test reads afterwards is the list of names traversed.
    """

    def __init__(self, calls, name=""):
        self._calls = calls
        self._name = name

    def __getattr__(self, item):
        self._calls.append(item)
        return _Recorder(self._calls, item)

    def __call__(self, *args, **kwargs):
        return self

    def execute(self):
        if self._name == "getProfile":
            return {"emailAddress": "operator@example.com"}
        return {"id": "draft-id-1"}


def test_the_script_creates_a_draft_and_never_calls_send(tmp_path, monkeypatch, capsys):
    """The one invariant this script exists to hold (`.claude/rules/lethal-trifecta.md`).

    `gmail-draft.py` composes and stops. Its docstring says so and the operator
    relies on it, but until this test nothing failed if `drafts().create` became
    `drafts().send` or `messages().send` - a one-word edit that turns a
    human-gated compose step into an autonomous outbound send. Asserting the
    absence is the point: a passing suite is what would otherwise be read as
    evidence the gate is intact.
    """
    letter = tmp_path / "letter.md"
    letter.write_text("body\n", encoding="utf-8")

    calls: list[str] = []
    fake_auth = type(sys)("scripts.utils.gmail_auth")
    fake_auth.get_service = lambda: _Recorder(calls)
    monkeypatch.setitem(sys.modules, "scripts.utils.gmail_auth", fake_auth)
    monkeypatch.setattr(
        sys, "argv",
        ["gmail-draft.py", "--to", "them@example.com", "--subject", "Memo",
         "--body-file", str(letter)],
    )

    assert gmail_draft.main() == 0
    capsys.readouterr()

    assert "drafts" in calls and "create" in calls
    assert "send" not in calls, f"gmail-draft.py reached a send API: {calls}"
