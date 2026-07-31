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


def test_the_allowlist_token_suppresses_redaction_where_it_suppresses_scanning():
    from scripts.utils.secret_patterns import ALLOWLIST_TOKEN, redact

    line = "sample " + _connection_string() + "  # " + ALLOWLIST_TOKEN
    assert redact(line) == line


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
