#!/usr/bin/env python3
"""Shared fixtures for security tests."""

import re
from pathlib import Path

import pytest


@pytest.fixture
def workspace_root() -> Path:
    """Return the workspace root directory."""
    return Path(__file__).resolve().parent.parent.parent


@pytest.fixture
def scripts_dir(workspace_root: Path) -> Path:
    """Return the scripts/ directory."""
    return workspace_root / "scripts"


@pytest.fixture
def hooks_dir(workspace_root: Path) -> Path:
    """Return the .claude/hooks/ directory."""
    return workspace_root / ".claude" / "hooks"


@pytest.fixture
def gitignore_path(workspace_root: Path) -> Path:
    """Return the .gitignore file path."""
    return workspace_root / ".gitignore"


def read_file_content(path: Path) -> str:
    """Read a file and return its content."""
    return path.read_text(encoding="utf-8")


def file_contains_pattern(path: Path, pattern: str) -> bool:
    """Check if a file contains a regex pattern."""
    content = read_file_content(path)
    return bool(re.search(pattern, content))


def extract_patterns_from_scanner(path: Path) -> list[str]:
    """Extract regex patterns from a secret scanner file."""
    content = read_file_content(path)
    patterns = re.findall(r"""r['"](.+?)['"]""", content)
    return patterns
