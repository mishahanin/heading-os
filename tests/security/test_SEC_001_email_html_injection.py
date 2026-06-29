#!/usr/bin/env python3
"""SEC-001: Verify send-email.py escapes HTML in plain text bodies.

Vulnerability: Plain text wrapped in <p> tags without html.escape().
Attack vector: User input containing <script> or other HTML tags gets injected.
Expected safe behavior: All < > & " characters escaped before HTML wrapping.
"""

import ast
from pathlib import Path

import pytest

from tests.security.conftest import read_file_content


@pytest.fixture
def send_email_path(scripts_dir):
    return scripts_dir / "send-email.py"


def test_send_email_imports_html_module(send_email_path):
    """send-email.py must import the html module for escaping."""
    content = read_file_content(send_email_path)
    assert "import html" in content, (
        "send-email.py must import the html module for HTML escaping"
    )


def test_send_email_uses_html_escape_in_paragraph_wrapping(send_email_path):
    """The plain-text-to-HTML conversion must use html.escape()."""
    content = read_file_content(send_email_path)
    # Must NOT have raw f"<p>{p}</p>" without escaping
    assert "html.escape(" in content, (
        "send-email.py must use html.escape() when wrapping text in HTML tags"
    )


def test_send_email_no_raw_fstring_paragraph_wrapping(send_email_path):
    """There must be no f'<p>{p}</p>' without html.escape()."""
    content = read_file_content(send_email_path)
    tree = ast.parse(content)
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr):  # f-string
            # Convert to source-like representation
            for value in node.values:
                if isinstance(value, ast.Constant) and "<p>" in str(value.value):
                    # Found an f-string with <p> - check if html.escape is in the same expression
                    # Get the line content
                    line = content.split("\n")[node.lineno - 1]
                    assert "html.escape" in line, (
                        f"Line {node.lineno}: f-string wrapping text in <p> tags "
                        f"without html.escape(): {line.strip()}"
                    )


# ---- Behavioral (runtime) ----

def test_build_full_html_escapes_special_chars():
    """_build_full_html must escape &, <, >, \" in plain-text bodies at runtime."""
    import sys
    from pathlib import Path

    # Import send_email module via importlib to avoid triggering check_dependencies()
    # at module load time (which requires exchangelib in the venv).
    import importlib.util

    scripts_dir_path = Path(__file__).resolve().parent.parent.parent / "scripts"
    spec = importlib.util.spec_from_file_location(
        "send_email", scripts_dir_path / "send-email.py"
    )
    mod = importlib.util.module_from_spec(spec)
    # Stub the top-level exchangelib import so the module loads in test context.
    # _build_full_html itself uses only the `html` stdlib module.
    import types
    stub = types.ModuleType("exchangelib")
    for attr in ("Account", "Credentials", "Configuration", "DELEGATE",
                 "FileAttachment", "HTMLBody", "Message", "Mailbox"):
        setattr(stub, attr, None)
    sys.modules.setdefault("exchangelib", stub)
    spec.loader.exec_module(mod)

    # Input must NOT match is_html() — no complete <letter...> pattern.
    # 'Price: 5 < 10 & "good deal" > expected' has standalone < and > but
    # no <letter> sequence, so is_html() returns False and html.escape() runs.
    body = 'Price: 5 < 10 & "good deal" > expected'
    result = mod._build_full_html(body, "")
    assert "&amp;" in result, f"& not escaped in: {result!r}"
    assert "&lt;" in result, f"< not escaped in: {result!r}"
    assert "&gt;" in result, f"> not escaped in: {result!r}"
    assert "&quot;" in result, f'\" not escaped in: {result!r}'
