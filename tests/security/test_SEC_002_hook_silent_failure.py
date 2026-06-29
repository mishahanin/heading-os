#!/usr/bin/env python3
"""SEC-002: Verify session-start.py has no bare exception swallowing.

Vulnerability: 'except Exception: pass' hides all errors silently.
Attack vector: CRM health, sync status, stale data checks fail without indication.
Expected safe behavior: All exception handlers log to stderr or handle specifically.
"""

import ast
from pathlib import Path

import pytest

from tests.security.conftest import read_file_content


@pytest.fixture
def session_start_path(hooks_dir):
    return hooks_dir / "session-start.py"


def _is_broad_exception(handler):
    """Check if an exception handler catches broad Exception or bare except."""
    if handler.type is None:
        return True  # bare except:
    if isinstance(handler.type, ast.Name) and handler.type.id == "Exception":
        return True
    return False


def test_no_bare_except_pass(session_start_path):
    """No 'except Exception: pass' or 'except: pass' blocks allowed.

    Specific typed exceptions (e.g., except ValueError) are allowed since
    they indicate intentional handling of a known error type.
    """
    content = read_file_content(session_start_path)
    tree = ast.parse(content)

    violations = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler):
            if not _is_broad_exception(node):
                continue  # Specific typed exceptions are fine
            # Check if body is just 'pass' or 'continue'
            if len(node.body) == 1 and isinstance(node.body[0], (ast.Pass, ast.Continue)):
                line = node.lineno
                violations.append(
                    f"Line {line}: bare exception handler with pass/continue"
                )

    assert not violations, (
        f"Found {len(violations)} bare exception handler(s) that silently swallow errors:\n"
        + "\n".join(violations)
    )


def test_exception_handlers_log_to_stderr(session_start_path):
    """Exception handlers should include stderr output or re-raise."""
    content = read_file_content(session_start_path)
    tree = ast.parse(content)

    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler):
            if not _is_broad_exception(node):
                continue  # Specific typed exceptions are fine
            handler_lines = content.split("\n")[node.lineno - 1:node.end_lineno]
            handler_text = "\n".join(handler_lines)

            has_output = (
                "sys.stderr" in handler_text
                or "print(" in handler_text
                or "logging." in handler_text
                or "raise" in handler_text
                or "return" in handler_text
            )
            is_bare_swallow = (
                len(node.body) == 1
                and isinstance(node.body[0], (ast.Pass, ast.Continue))
            )

            if is_bare_swallow:
                assert has_output, (
                    f"Line {node.lineno}: exception handler swallows error "
                    f"without logging to stderr"
                )
