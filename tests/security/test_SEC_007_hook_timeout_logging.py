#!/usr/bin/env python3
"""SEC-007: Verify post-write-sanitize logs timeouts instead of swallowing them.

Vulnerability: Timeout exceptions caught and silently passed.
Expected safe behavior: Timeout logged to stderr with file path context.
"""

import ast

import pytest

from tests.security.conftest import read_file_content


def test_timeout_handler_logs_to_stderr(hooks_dir):
    """post-write-sanitize.py must log TimeoutExpired to stderr, not silently pass."""
    path = hooks_dir / "post-write-sanitize.py"
    content = read_file_content(path)
    tree = ast.parse(content)

    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler):
            # Check if this catches TimeoutExpired
            handler_type = ""
            if node.type:
                if isinstance(node.type, ast.Attribute):
                    handler_type = node.type.attr
                elif isinstance(node.type, ast.Name):
                    handler_type = node.type.id

            if "Timeout" in handler_type:
                # This is a timeout handler - verify it doesn't just pass
                if len(node.body) == 1 and isinstance(node.body[0], ast.Pass):
                    pytest.fail(
                        f"Line {node.lineno}: TimeoutExpired handler silently passes. "
                        f"Must log to stderr."
                    )
                # Verify it has some form of output
                handler_lines = content.split("\n")[node.lineno - 1:node.end_lineno]
                handler_text = "\n".join(handler_lines)
                assert "stderr" in handler_text or "print(" in handler_text, (
                    f"Line {node.lineno}: TimeoutExpired handler must log to stderr"
                )
