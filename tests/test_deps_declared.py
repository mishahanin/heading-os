#!/usr/bin/env python3
"""Assert that key dependencies are explicitly pinned in the requirements files.

Prevents silent drift where a library is imported but not pinned, so the next
`pip install -r` on a fresh machine picks an unknown version.
"""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parent.parent

# Each tuple: (package_name_pattern, requirements_file_relative_to_root)
_REQUIRED_PINS = [
    ("fastapi", "scripts/bridge_daemon/requirements.txt"),
    ("firecrawl-py", "scripts/bridge_daemon/requirements.txt"),
    ("uvicorn", "scripts/bridge_daemon/requirements.txt"),
]


def _is_pinned(req_path: Path, pkg_pattern: str) -> bool:
    """Return True if req_path contains an exact == pin for pkg_pattern."""
    if not req_path.is_file():
        return False
    content = req_path.read_text(encoding="utf-8")
    # Allow optional PEP 508 extras suffix like [standard] before ==
    pattern = re.compile(
        rf"^\s*{re.escape(pkg_pattern)}(\[[^\]]*\])?\s*==\s*\S+",
        re.IGNORECASE | re.MULTILINE,
    )
    return bool(pattern.search(content))


def test_bridge_daemon_deps_pinned():
    """All critical bridge-daemon dependencies must have exact == pins."""
    violations = []
    for pkg, rel_path in _REQUIRED_PINS:
        req_file = ROOT / rel_path
        if not _is_pinned(req_file, pkg):
            violations.append(f"{pkg!r} not pinned with == in {rel_path}")
    assert not violations, (
        "Missing exact-version pins in requirements files:\n  "
        + "\n  ".join(violations)
    )
