"""Boundary test: no direct langfuse imports or bare @observe in daemon source.

Sovereignty constraint: the inbox-pulse daemon MUST route all Anthropic SDK
calls through scripts/utils/observability_safe.py (the metadata-only wrapper).
A bare @observe decorator from langfuse captures input args and return values
by default, which would leak email body content + sender identifiers to
Langfuse Cloud -- a Principle 5 (Data Sovereignty Always) violation.

This static test complements test_runtime_sovereignty.py:
- runtime test: catches leakage THROUGH the wrapper (someone accidentally
  passes raw email data into the metadata= field)
- this test: catches BYPASSING the wrapper entirely (direct langfuse import
  or bare @observe decorator in daemon source)

Together they jointly enforce: no email-body bytes can flow to Langfuse.

REWRITTEN 2026-08-30, twice over.

It was a raw line scan with no awareness of comments or string literals, so a
docstring explaining the rule -- this one, had it lived beside the daemon --
"violated" it, and the fix a reader reaches for is to stop writing the
explanation down. This repository treats punishing a file for documenting its
own trap as a defect in the test. The scan is now over the parse tree, where an
import is an import and a mention is prose.

It also "gracefully passed" when `scripts/inbox_pulse/` did not exist, a
Phase-0 allowance that outlived Phase 0 by months: a guard that iterates an
empty corpus asserts nothing at all. The corpus is now required to be
non-empty, and the detector itself carries the negative cases that show it can
still refuse.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

# Daemon source directory to scan.
DAEMON_SRC = Path(__file__).resolve().parent.parent.parent / "scripts" / "inbox_pulse"

# The one decorator that is allowed to wrap a model call in this tree.
SAFE_DECORATOR = "observe_metadata_only"


def _iter_daemon_py_files():
    """Yield all .py source files under DAEMON_SRC, skipping __pycache__."""
    for path in sorted(DAEMON_SRC.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        yield path


def _decorator_name(node: ast.expr) -> str:
    """The dotted name a decorator expression resolves to, best effort."""
    if isinstance(node, ast.Call):
        return _decorator_name(node.func)
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{_decorator_name(node.value)}.{node.attr}"
    return ""


def violations_in(source: str, label: str) -> list[str]:
    """Every sovereignty breach in one module's parse tree.

    A `SyntaxError` is a violation rather than a skip: a daemon file this test
    cannot parse is a daemon file it has not checked, and reporting nothing for
    it is the coverage claim `.claude/rules/scope-claims.md` forbids.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return [f"{label}: could not be parsed, so it was NOT checked: {exc}"]

    out: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "langfuse" or alias.name.startswith("langfuse."):
                    out.append(f"{label}:{node.lineno}: direct langfuse import "
                               f"(`import {alias.name}`)")
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if mod == "langfuse" or mod.startswith("langfuse."):
                out.append(f"{label}:{node.lineno}: direct langfuse import "
                           f"(`from {mod} import ...`)")
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            for dec in node.decorator_list:
                name = _decorator_name(dec)
                if name == "observe" or name.endswith(".observe"):
                    out.append(f"{label}:{dec.lineno}: bare @{name} decorator on "
                               f"{node.name} (use @{SAFE_DECORATOR})")
    return out


def test_the_daemon_corpus_is_not_empty():
    """The scan below is worthless over zero files, and used to allow it.

    `if not DAEMON_SRC.exists(): return` dated from Phase 0, when the daemon
    had not been written. It stayed after the daemon shipped, so a rename or a
    move of `scripts/inbox_pulse/` would have turned the sovereignty guard into
    a no-op that still reported green.
    """
    assert DAEMON_SRC.is_dir(), f"{DAEMON_SRC} is gone; the guard scans nothing"
    files = list(_iter_daemon_py_files())
    assert len(files) >= 5, [str(p) for p in files]


def test_no_prohibited_langfuse_imports_in_daemon():
    """No direct langfuse imports or bare @observe decorators in daemon source.

    Every .py file under scripts/inbox_pulse/ is parsed, not grepped.
    """
    violations: list[str] = []
    for py_file in _iter_daemon_py_files():
        rel = py_file.relative_to(DAEMON_SRC.parent.parent)
        try:
            source = py_file.read_text(encoding="utf-8")
        except OSError as exc:
            violations.append(f"Could not read {rel}: {exc}")
            continue
        violations.extend(violations_in(source, str(rel)))

    assert not violations, (
        "Sovereignty boundary violated -- direct langfuse usage found in daemon source.\n"
        "Use scripts/utils/observability_safe.py (@observe_metadata_only) instead.\n\n"
        + "\n".join(violations)
    )


# --------------------------------------------------------------------------
# The detector's own negative and positive cases. Without these, a scan that
# had quietly stopped matching anything would report a clean daemon forever.
# --------------------------------------------------------------------------

@pytest.mark.parametrize("source", [
    "import langfuse\n",
    "import langfuse.decorators\n",
    "from langfuse import observe\n",
    "from langfuse.decorators import observe\n",
    "@observe\ndef summarise(body):\n    return body\n",
    "@observe()\nasync def summarise(body):\n    return body\n",
    "@langfuse.observe\ndef summarise(body):\n    return body\n",
    "@observe\nclass Summariser:\n    pass\n",
    "def outer():\n    @observe\n    def inner():\n        pass\n",
])
def test_the_detector_refuses_each_prohibited_shape(source):
    assert violations_in(source, "planted.py"), source


@pytest.mark.parametrize("source", [
    # Prose about the rule. The line scan this replaced flagged all three.
    '"""Never write `import langfuse` in this tree."""\n',
    "# from langfuse import observe  <- forbidden, see the module docstring\n",
    'BANNED = ["import langfuse", "@observe"]\n',
    # The sanctioned wrapper, and a same-prefix name that is not it.
    "from scripts.utils.observability_safe import observe_metadata_only\n"
    "@observe_metadata_only\ndef summarise(body):\n    return body\n",
    "@observed\ndef summarise(body):\n    return body\n",
    # A module that merely mentions the vendor in an identifier.
    "langfuse_enabled = False\n",
])
def test_the_detector_leaves_prose_and_the_safe_wrapper_alone(source):
    assert violations_in(source, "planted.py") == [], source


def test_an_unparseable_daemon_file_is_reported_not_skipped():
    """Unchecked must never read as clean."""
    found = violations_in("def broken(:\n", "planted.py")
    assert found and "NOT checked" in found[0], found


def test_the_detector_finds_a_planted_violation_on_disk(tmp_path, monkeypatch):
    """End to end through the same walker the real scan uses."""
    monkeypatch.setattr(sys.modules[__name__], "DAEMON_SRC", tmp_path)
    (tmp_path / "leaky.py").write_text("import langfuse\n", encoding="utf-8")
    found = [v for p in _iter_daemon_py_files()
             for v in violations_in(p.read_text(encoding="utf-8"), p.name)]
    assert found and "leaky.py" in found[0], found
