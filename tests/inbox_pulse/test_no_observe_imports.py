"""Boundary test: no direct langfuse imports or bare @observe in daemon source.

Sovereignty constraint: the inbox-pulse daemon MUST route all Anthropic SDK
calls through scripts/utils/observability_safe.py (the metadata-only wrapper).
A bare @observe decorator from langfuse captures input args and return values
by default, which would leak email body content + sender identifiers to
Langfuse Cloud -- a Principle 5 (Data Sovereignty Always) violation.

This static grep test complements test_runtime_sovereignty.py:
- runtime test: catches leakage THROUGH the wrapper (someone accidentally
  passes raw email data into the metadata= field)
- this test: catches BYPASSING the wrapper entirely (direct langfuse import
  or bare @observe decorator in daemon source)

Together they jointly enforce: no email-body bytes can flow to Langfuse.

The test gracefully passes when scripts/inbox_pulse/ does not yet exist
(Phase 0 -- daemon source is created in Phase 1). Once the directory exists,
every .py file in it is scanned.
"""

from __future__ import annotations

import re
from pathlib import Path

# Daemon source directory to scan (may not exist yet in Phase 0)
DAEMON_SRC = Path(__file__).resolve().parent.parent.parent / "scripts" / "inbox_pulse"

# Patterns that must NOT appear in daemon source files.
# Each entry is (compiled_regex, human_readable_label).
PROHIBITED_PATTERNS = [
    (re.compile(r"\bfrom\s+langfuse\b"), "direct langfuse import"),
    (re.compile(r"\bimport\s+langfuse\b"), "direct langfuse import"),
    (re.compile(r"^\s*@observe\b", re.MULTILINE), "bare @observe decorator"),
]


def _iter_daemon_py_files():
    """Yield all .py source files under DAEMON_SRC, skipping __pycache__."""
    if not DAEMON_SRC.exists():
        return
    for path in DAEMON_SRC.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        yield path


def test_no_prohibited_langfuse_imports_in_daemon():
    """No direct langfuse imports or bare @observe decorators in daemon source.

    Scans every .py file under scripts/inbox_pulse/ recursively.
    Passes silently when the directory does not yet exist.
    Fails with a precise file:line:text listing when any violation is found.
    """
    violations: list[str] = []

    for py_file in _iter_daemon_py_files():
        try:
            source = py_file.read_text(encoding="utf-8")
        except OSError as exc:
            violations.append(f"Could not read {py_file}: {exc}")
            continue

        lines = source.splitlines()
        for lineno, line in enumerate(lines, start=1):
            for pattern, label in PROHIBITED_PATTERNS:
                if pattern.search(line):
                    rel = py_file.relative_to(DAEMON_SRC.parent.parent)
                    violations.append(
                        f"Prohibited {label} in daemon source: "
                        f"{rel}:{lineno}: {line.strip()}"
                    )

    assert not violations, (
        "Sovereignty boundary violated -- direct langfuse usage found in daemon source.\n"
        "Use scripts/utils/observability_safe.py (@observe_metadata_only) instead.\n\n"
        + "\n".join(violations)
    )
