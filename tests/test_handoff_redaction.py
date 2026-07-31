"""The redactor, and the hook that must never write an unscannable handoff.

Every credential-shaped sample here is assembled at runtime. None is written
whole into this file: it is tracked, the engine repository is public, and the
prevent-secrets hook refuses the write. That refusal is correct and the
assembly is the workspace convention, not a workaround.
"""
import subprocess
import sys
from pathlib import Path

import pytest

ENGINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ENGINE))


def _connection_string() -> str:
    """A URL carrying a userinfo pair. The literal that started this slice."""
    return "https://" + "x-access-token" + ":" + "not-a-real-token-value" + "@" + "github.com/owner/repo.git"


def _api_key() -> str:
    return "sk-ant-" + ("A" * 24)


def test_a_credential_shaped_span_is_replaced_by_a_named_marker():
    from scripts.utils.secret_patterns import redact

    out = redact("the remote was " + _connection_string() + " at the time")

    assert "not-a-real-token-value" not in out
    assert "[REDACTED: connection string with inline credentials]" in out


def test_only_the_span_is_replaced_and_the_prose_survives():
    """A handoff gutted by redaction fails at its only job, which is to let the
    next session resume."""
    from scripts.utils.secret_patterns import redact

    out = redact("the remote was " + _connection_string() + " at the time")

    assert out.startswith("the remote was ")
    assert out.endswith(" at the time")
    assert "not-a-real-token-value" not in out


def test_every_pattern_family_is_redacted_not_only_the_one_that_bit_us():
    from scripts.utils.secret_patterns import redact

    out = redact("key " + _api_key() + " end")
    assert _api_key() not in out
    assert "[REDACTED: Anthropic API key]" in out


def test_text_carrying_no_secret_is_returned_unchanged():
    """Byte-identical, not merely equivalent. A redactor that reflows every
    handoff it touches is a redactor nobody will trust."""
    from scripts.utils.secret_patterns import redact

    original = "# Handoff\n\nNothing secret here.\n\n  indented\ttabbed\n"
    assert redact(original) == original


# The eleven line-break code points at issue: the three universal-newline
# forms readlines() honours, plus the eight str.splitlines() over-splits on
# and must therefore NOT be treated as a break here.
_LINE_BREAK_CODE_POINTS = [
    "\r", "\r\n", "\n",                     # universal newlines (readlines())
    "\x0b", "\x0c",                         # vertical tab, form feed
    "\x1c", "\x1d", "\x1e",                 # ASCII file/group/record separators
    "\x85",                                 # NEL
    "\u2028", "\u2029",                     # Unicode LINE/PARAGRAPH SEPARATOR
]


@pytest.mark.parametrize("code_point", _LINE_BREAK_CODE_POINTS)
def test_the_round_trip_is_byte_identical_across_every_line_break_code_point(code_point):
    """No secret anywhere, so the only question is whether redact() reproduces
    its input exactly regardless of which line-break code point it carries."""
    from scripts.utils.secret_patterns import redact

    original = "before" + code_point + "after" + code_point + "tail"
    assert redact(original) == original


def _allowlist_case_token_then_cr_then_secret() -> str:
    """The reviewer's exact repro: token on one universal-newline segment, a
    lone "\\r" boundary, then the secret on the next segment."""
    from scripts.utils.secret_patterns import ALLOWLIST_TOKEN

    return ("Reviewed the scanner. Lines carrying " + ALLOWLIST_TOKEN + " are skipped."
            + "\r" + "The remote was " + _connection_string() + " at the time.\n")


def _allowlist_case_secret_then_cr_then_token() -> str:
    """Same repro, reversed order."""
    from scripts.utils.secret_patterns import ALLOWLIST_TOKEN

    return ("The remote was " + _connection_string() + " at the time."
            + "\r" + "Reviewed the scanner. Lines carrying " + ALLOWLIST_TOKEN
            + " are skipped.\n")


def _allowlist_case_crlf_control() -> str:
    """Control: a real "\\r\\n" break between token and secret. `str.split("\\n")`
    already separates these onto two elements before any `\\r`-aware fix, so this
    case must pass both before and after the fix."""
    from scripts.utils.secret_patterns import ALLOWLIST_TOKEN

    return ("Reviewed the scanner. Lines carrying " + ALLOWLIST_TOKEN + " are skipped.\r\n"
            + "The remote was " + _connection_string() + " at the time.\n")


def _allowlist_case_plain_single_line_control() -> str:
    """Control: token and secret share one real line, exactly as the scanner
    sees it. Must pass both before and after the fix."""
    from scripts.utils.secret_patterns import ALLOWLIST_TOKEN

    return "sample " + _connection_string() + "  # " + ALLOWLIST_TOKEN


@pytest.mark.parametrize("make_text", [
    _allowlist_case_token_then_cr_then_secret,
    _allowlist_case_secret_then_cr_then_token,
    _allowlist_case_crlf_control,
    _allowlist_case_plain_single_line_control,
], ids=[
    "lone_cr_token_then_secret",
    "lone_cr_secret_then_token",
    "crlf_control",
    "plain_single_line_control",
])
def test_the_allowlist_token_suppresses_redaction_where_it_suppresses_scanning(
        make_text, tmp_path):
    """The allowlist decision must match the REAL scanner's line boundaries,
    not redact()'s own idea of a line. A lone "\\r" ends a line for scan_file's
    readlines() (universal newlines) but was, before this fix, invisible to
    redact()'s str.split("\\n"), so an allowlist token on one side of a lone
    "\\r" wrongly suppressed redaction of a secret on the other side.

    Asserted through the real scanner subprocess, not through redact()'s own
    notion of what it did: an identity function would satisfy a bare
    `redact(x) == x` assertion here and hide exactly this bug.
    """
    from scripts.utils.secret_patterns import redact

    text = make_text()
    target = tmp_path / "handoff.md"
    target.write_text(redact(text), encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(ENGINE / "scripts" / "secret-scanner.py"), str(target)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stdout


def test_the_markdown_password_pattern_eats_its_whole_line():
    """The documented exception to "the span, never the line", pinned.

    Pattern 15 ends in a greedy run to end of line, so a line opening with a
    bolded Password label loses its prose too. That is the correct trade: text
    after that label is exactly where a password sits, and narrowing the pattern
    to spare prose would weaken detection.

    It is pinned rather than left implicit because this is the shape a handoff
    summarising a credentials discussion actually produces, and a future author
    who narrows the pattern must change this assertion deliberately instead of
    finding out from a handoff that quietly lost a paragraph.
    """
    from scripts.utils.secret_patterns import redact

    label = "**" + "Password:" + "**"
    out = redact(label + " we discussed the quarterly rotation policy\ntail line\n")

    assert out.startswith("[REDACTED: Plaintext password in markdown]")
    assert "quarterly rotation policy" not in out
    assert "tail line" in out          # the damage stops at the line boundary


def test_a_line_with_two_secrets_loses_both():
    from scripts.utils.secret_patterns import redact

    out = redact(_api_key() + " and " + _connection_string())
    assert _api_key() not in out
    assert "not-a-real-token-value" not in out


def test_the_output_of_the_redactor_passes_the_scanner(tmp_path):
    """The property that actually matters, asserted through the REAL scanner
    rather than through the redactor's own idea of what a secret is."""
    from scripts.utils.secret_patterns import redact

    target = tmp_path / "handoff.md"
    target.write_text(redact("remote " + _connection_string() + "\nkey " + _api_key() + "\n"),
                      encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(ENGINE / "scripts" / "secret-scanner.py"), str(target)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stdout


def test_no_redaction_marker_reintroduces_a_prefilter_needle():
    """Pins the data property that `iter_patterns` in `redact` relies on for
    correctness rather than proving it structurally.

    `iter_patterns(line)` closes over the string it was called with, but
    `redact`'s loop rebinds that same name as it substitutes matches in. If
    some future pattern's own redaction marker (`REDACTED: {description}`)
    ever contained a `REQUIRED_SUBSTRING` needle, a match on pattern A could
    inject that needle into the text pattern B's prefilter already decided
    against on the ORIGINAL string, silently reviving a pattern the prefilter
    had ruled out for this line. This test catches that the day a new needle
    or a new description makes it true, not after.
    """
    from scripts.utils.secret_patterns import (
        REDACTION_TEMPLATE, REQUIRED_SUBSTRING, SECRET_PATTERNS)

    descriptions = [description for _pattern, description in SECRET_PATTERNS]
    for needle in REQUIRED_SUBSTRING.values():
        for description in descriptions:
            marker = REDACTION_TEMPLATE.format(description=description)
            assert needle not in marker, (
                f"prefilter needle {needle!r} appears in the redaction marker "
                f"for {description!r}: {marker!r}")
