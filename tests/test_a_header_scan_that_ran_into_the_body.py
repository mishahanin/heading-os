"""``approvals._parse_headers`` kept parsing headers after the headers ended.

The scan applied ``_HDR_RE`` to every line until it met a standalone ``---``.
When a draft had no separator, or its separator sat past the 4,096 bytes
``list_approvals`` reads, a header-shaped line in the BODY overwrote the real
one and the approvals queue displayed a recipient the draft was never addressed
to. Last-write-wins across a whole file is not a header parser.

Measured 2026-08-29 before the fix, on a draft addressed to one recipient whose
body quoted another::

    {'to': 'wrong@example.com', 'subject': 'Wrong subject', '_body_offset': 0}

The fix makes the header block contiguous: capture starts at the first
``**Key:**`` line and ends at the first ordinary prose line after it, or at
``---``. Blank lines inside the block are tolerated, and an H1 above the block
still does not end a block that has not begun.
"""
import pytest

from scripts.bridge_daemon.sources import approvals


NO_SEPARATOR = (
    "**To:** bond@example.com\n"
    "**Subject:** Acme Telecom introduction\n"
    "\n"
    "Following up on the call.\n"
    "\n"
    "**To:** moneypenny@example.com\n"
    "**Subject:** Quoted from an earlier thread\n"
)

WITH_SEPARATOR = (
    "**To:** bond@example.com\n"
    "**Subject:** Acme Telecom introduction\n"
    "---\n"
    "**To:** moneypenny@example.com\n"
)

H1_ABOVE_THE_BLOCK = (
    "# Acme Telecom introduction\n"
    "\n"
    "**To:** bond@example.com\n"
    "**Cc:** q@example.com\n"
    "**Subject:** Acme Telecom introduction\n"
    "\n"
    "---\n"
    "\n"
    "Body.\n"
)

BLANK_LINE_INSIDE_THE_BLOCK = (
    "**To:** bond@example.com\n"
    "\n"
    "**Subject:** Acme Telecom introduction\n"
    "\n"
    "Body prose.\n"
)


# ============================================================
# The defect: body lines overwriting real headers
# ============================================================

def test_a_body_line_does_not_overwrite_the_real_recipient():
    headers = approvals._parse_headers(NO_SEPARATOR)
    assert headers["to"] == "bond@example.com"


def test_a_body_line_does_not_overwrite_the_real_subject():
    headers = approvals._parse_headers(NO_SEPARATOR)
    assert headers["subject"] == "Acme Telecom introduction"


def test_no_value_from_below_the_header_block_reaches_the_result():
    headers = approvals._parse_headers(NO_SEPARATOR)
    assert "moneypenny@example.com" not in repr(headers)
    assert "Quoted from an earlier thread" not in repr(headers)


def test_a_body_key_absent_from_the_header_block_is_not_invented():
    """A header key the real block never carried must not appear from the body."""
    text = (
        "**To:** bond@example.com\n"
        "\n"
        "Body prose.\n"
        "\n"
        "**Cc:** planted@example.com\n"
    )
    headers = approvals._parse_headers(text)
    assert headers["to"] == "bond@example.com"
    assert "cc" not in headers


# ============================================================
# What must not regress
# ============================================================

def test_the_separator_still_ends_the_block():
    headers = approvals._parse_headers(WITH_SEPARATOR)
    assert headers["to"] == "bond@example.com"


def test_the_body_offset_points_past_a_separator_that_was_found():
    headers = approvals._parse_headers(WITH_SEPARATOR)
    assert headers["_body_offset"] > 0
    assert WITH_SEPARATOR[: headers["_body_offset"]].endswith("---\n")


def test_a_zero_body_offset_means_there_was_no_separator():
    assert approvals._parse_headers(NO_SEPARATOR)["_body_offset"] == 0


def test_an_h1_above_the_headers_does_not_end_a_block_that_never_began():
    headers = approvals._parse_headers(H1_ABOVE_THE_BLOCK)
    assert headers["to"] == "bond@example.com"
    assert headers["cc"] == "q@example.com"
    assert headers["subject"] == "Acme Telecom introduction"


def test_a_blank_line_inside_the_header_block_is_tolerated():
    headers = approvals._parse_headers(BLANK_LINE_INSIDE_THE_BLOCK)
    assert headers["to"] == "bond@example.com"
    assert headers["subject"] == "Acme Telecom introduction"


# ============================================================
# Through the surface that showed the wrong recipient
# ============================================================

@pytest.fixture
def workspace(tmp_path):
    drafts = tmp_path / approvals.EMAIL_DRAFTS_DIR
    drafts.mkdir(parents=True)
    (drafts / "acme-intro.md").write_text(NO_SEPARATOR, encoding="utf-8")
    return tmp_path


def test_the_approvals_queue_shows_the_address_the_draft_carries(workspace):
    items = approvals.list_approvals(workspace)["items"]
    assert len(items) == 1, "empty corpus proves nothing"
    assert items[0]["to"] == "bond@example.com"
    assert items[0]["subject"] == "Acme Telecom introduction"
