#!/usr/bin/env python3
"""Behavioral smoke test for _dispatch.py's check routing."""
import importlib.util
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from scripts.utils.workspace import get_workspace_root

_HOOK_PATH = get_workspace_root() / ".claude" / "hooks" / "_dispatch.py"


@pytest.fixture(scope="module")
def dispatch():
    spec = importlib.util.spec_from_file_location("_dispatch", _HOOK_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_protect_corporate_allows_ceo_workspace(dispatch, tmp_path):
    """CEO workspace (no .workspace-identity.json) must not be blocked."""
    payload = {
        "tool_name": "Write",
        "tool_input": {"file_path": "corporate/content.md", "content": "x"},
        "cwd": str(tmp_path),
    }
    result = dispatch.check_protect_corporate(payload)
    assert result is None


def test_protect_corporate_allows_bash(dispatch):
    """Bash payloads must always pass through check_protect_corporate."""
    payload = {
        "tool_name": "Bash",
        "tool_input": {"command": "cat corporate/content.md"},
    }
    result = dispatch.check_protect_corporate(payload)
    assert result is None


def test_personal_threads_read_blocked_via_fixture(dispatch):
    """Regression: Read of personal thread must block (F-H5 guard)."""
    payload = {
        "tool_name": "Read",
        "tool_input": {"file_path": "threads/personal/test.md"},
    }
    result = dispatch.check_protect_personal_threads(payload)
    assert result is not None
    assert result.get("decision") == "block"
