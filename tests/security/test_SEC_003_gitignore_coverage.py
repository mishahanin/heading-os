#!/usr/bin/env python3
"""SEC-003: Verify .gitignore covers all sensitive paths.

Vulnerability: Missing gitignore entries allow sensitive data to be committed.
Expected safe behavior: All sensitive paths are listed in .gitignore.
"""

import pytest

from tests.security.conftest import read_file_content


REQUIRED_GITIGNORE_ENTRIES = [
    ".env",
    ".sessions/",
    "outputs/browser/cookies.json",
    "outputs/browser/firecrawl-cache/",
    "outputs/_sync/",
    ".sentinel/",
    "outputs/clipboard/",
]


def test_gitignore_covers_sensitive_paths(gitignore_path):
    """All known sensitive paths must be in .gitignore."""
    content = read_file_content(gitignore_path)
    lines = [line.strip() for line in content.split("\n") if not line.startswith("#")]

    missing = []
    for entry in REQUIRED_GITIGNORE_ENTRIES:
        if entry not in lines and not any(entry in line for line in lines):
            missing.append(entry)

    assert not missing, (
        f"Missing .gitignore entries for sensitive paths: {', '.join(missing)}"
    )
