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


def _escapes(node: ast.AST) -> bool:
    """True when this expression is (or contains) an escaping call.

    `html.escape(x)` and a bare `escape(x)` both count; so does any expression
    built out of one, e.g. `f"<b>{html.escape(x)}</b>"`.
    """
    for child in ast.walk(node):
        if not isinstance(child, ast.Call):
            continue
        func = child.func
        if isinstance(func, ast.Attribute) and func.attr == "escape":
            return True
        if isinstance(func, ast.Name) and func.id == "escape":
            return True
    return False


def _concat_operands(node: ast.AST) -> list[ast.AST]:
    """Flatten a chain of `+` string concatenations into its operands."""
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _concat_operands(node.left) + _concat_operands(node.right)
    return [node]


def _paragraph_wrappers(tree: ast.AST):
    """Yield (node, interpolated parts) for every expression building `<p>...`.

    Covers both shapes the file has used: an f-string, and a `+` concatenation
    of literals with expressions.
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr):
            literals = [v for v in node.values if isinstance(v, ast.Constant)]
            if not any("<p>" in str(v.value) for v in literals):
                continue
            parts = [v.value for v in node.values
                     if isinstance(v, ast.FormattedValue)
                     and not isinstance(v.value, ast.Constant)]
            yield node, parts
        elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            operands = _concat_operands(node)
            literals = [o for o in operands
                        if isinstance(o, ast.Constant) and isinstance(o.value, str)]
            if not any("<p>" in o.value for o in literals):
                continue
            parts = [o for o in operands if not isinstance(o, ast.Constant)]
            yield node, parts


def test_send_email_no_raw_paragraph_wrapping(send_email_path):
    """Nothing puts an unescaped value inside <p> tags.

    This used to read one physical line of source text:

        line = content.split("\\n")[node.lineno - 1]
        assert "html.escape" in line

    `node.lineno` is the f-string's OPENING line, so the check enforced
    line-locality of a token rather than escaping, and it was wrong in both
    directions. It false-PASSED on `f"<p>{p}</p>"  # html.escape applied
    upstream`, where a comment satisfies the substring over a raw
    interpolation. It false-FAILED on safe code whose escape sat on another
    line, which is every multi-line f-string and every
    `escaped = html.escape(p)` two-step.

    Measured 2026-08-30 while replacing it, and this is the larger finding:
    `send-email.py` has no `<p>` f-string at all any more. The wrapping is
    `"<p>" + html.escape(para).replace("\\n", "<br>") + "</p>"`, a `+`
    concatenation. The old loop's body therefore never executed on ANY input,
    so this guard had already stopped inspecting the paragraph path entirely
    and reported a pass over nothing. The floor at the end refuses to let that
    happen silently again.

    The question asked is whether the non-literal parts of a `<p>` wrapper are
    escaped, which is a property of the expression tree.
    """
    content = read_file_content(send_email_path)
    tree = ast.parse(content)
    checked = 0
    for node, parts in _paragraph_wrappers(tree):
        for part in parts:
            checked += 1
            assert _escapes(part), (
                f"Line {getattr(part, 'lineno', node.lineno)}: "
                f"{ast.unparse(part)!r} is wrapped in <p> tags without "
                f"html.escape()")
    # An AST scan that matched nothing is not a pass.
    assert checked, (
        f"no interpolated <p> wrapper found in {send_email_path}; this guard "
        f"no longer inspects the paragraph-wrapping path and must be retargeted")


def test_the_paragraph_wrapper_detector_can_actually_fail():
    """Drive the detector with unsafe and safe source of both shapes."""
    unsafe_fstring = ast.parse('x = f"<p>{p}</p>"  # html.escape upstream')
    unsafe_concat = ast.parse('x = "<p>" + p + "</p>"')
    safe_fstring = ast.parse('x = f"<p>{html.escape(p)}</p>"')
    safe_concat = ast.parse('x = "<p>" + html.escape(p).replace("a", "b") + "</p>"')

    for label, tree in (("f-string", unsafe_fstring), ("concat", unsafe_concat)):
        parts = [p for _, ps in _paragraph_wrappers(tree) for p in ps]
        assert parts, f"the {label} wrapper was not detected at all"
        assert not any(_escapes(p) for p in parts), f"unsafe {label} read as escaped"

    for label, tree in (("f-string", safe_fstring), ("concat", safe_concat)):
        parts = [p for _, ps in _paragraph_wrappers(tree) for p in ps]
        assert parts, f"the safe {label} wrapper was not detected at all"
        assert all(_escapes(p) for p in parts), f"safe {label} read as unescaped"


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
