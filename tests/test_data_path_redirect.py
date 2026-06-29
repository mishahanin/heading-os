#!/usr/bin/env python3
"""Tests for the PreToolUse data-path-redirect hook.

Verifies the tool-layer twin of the get_*_dir() seam: when the data root differs
from the workspace root (engine clone + data sibling, simulated here with the
HEADING_OS_DATA env override), the hook rewrites Claude's own Read/Write/Edit/
Grep/Glob paths that target a data dir to the data root -- and leaves engine
paths, absolute paths, and the no-op (in-tree) case untouched.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HOOK = ROOT / ".claude" / "hooks" / "data-path-redirect.py"


def _run(payload: dict, data_root: str) -> dict:
    """Invoke the hook as a subprocess; return parsed stdout JSON ({} if empty)."""
    import os
    env = dict(os.environ, HEADING_OS_DATA=data_root)
    proc = subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps(payload), capture_output=True, text=True, env=env,
    )
    assert proc.returncode == 0, proc.stderr
    out = proc.stdout.strip()
    return json.loads(out) if out else {}


def _updated(result: dict) -> dict | None:
    return result.get("hookSpecificOutput", {}).get("updatedInput")


@pytest.fixture
def data_sibling(tmp_path) -> Path:
    """A real, distinct data root so get_data_root() != get_workspace_root()."""
    d = tmp_path / ".heading-os-data"
    d.mkdir()
    return d


def test_read_data_path_redirected(data_sibling):
    res = _run(
        {"tool_name": "Read", "tool_input": {"file_path": "context/strategy.md"}},
        str(data_sibling),
    )
    upd = _updated(res)
    assert upd is not None
    assert upd["file_path"] == str(data_sibling / "context/strategy.md")


def test_engine_path_not_redirected(data_sibling):
    res = _run(
        {"tool_name": "Read",
         "tool_input": {"file_path": "scripts/prime-health-parallel.py"}},
        str(data_sibling),
    )
    # Engine path -> no rewrite -> hook emits nothing (cheap gate exits first).
    assert res == {}


def test_absolute_path_not_redirected(data_sibling):
    abs = "/etc/hostname"
    res = _run(
        {"tool_name": "Read", "tool_input": {"file_path": abs}},
        str(data_sibling),
    )
    assert res == {}


@pytest.mark.parametrize("d", ["crm", "outputs", "knowledge", "threads",
                               "plans", "datastore", "_archive", "corporate"])
def test_all_data_dirs_redirected(data_sibling, d):
    res = _run(
        {"tool_name": "Read", "tool_input": {"file_path": f"{d}/x.md"}},
        str(data_sibling),
    )
    upd = _updated(res)
    assert upd is not None
    assert upd["file_path"] == str(data_sibling / f"{d}/x.md")


def test_write_redirected_preserves_other_fields(data_sibling):
    res = _run(
        {"tool_name": "Write",
         "tool_input": {"file_path": "outputs/x.md", "content": "hello"}},
        str(data_sibling),
    )
    upd = _updated(res)
    assert upd is not None
    assert upd["file_path"] == str(data_sibling / "outputs/x.md")
    assert upd["content"] == "hello"  # full input preserved, only path replaced


def test_grep_path_redirected(data_sibling):
    res = _run(
        {"tool_name": "Grep",
         "tool_input": {"pattern": "TODO", "path": "crm/contacts"}},
        str(data_sibling),
    )
    upd = _updated(res)
    assert upd is not None
    assert upd["path"] == str(data_sibling / "crm/contacts")
    assert upd["pattern"] == "TODO"  # regex pattern never touched


def test_glob_pattern_only_anchored_at_data_root(data_sibling):
    res = _run(
        {"tool_name": "Glob", "tool_input": {"pattern": "outputs/**/*.md"}},
        str(data_sibling),
    )
    upd = _updated(res)
    assert upd is not None
    assert upd["path"] == str(data_sibling)
    assert upd["pattern"] == "outputs/**/*.md"  # pattern resolves under path


def test_glob_engine_pattern_not_anchored(data_sibling):
    res = _run(
        {"tool_name": "Glob", "tool_input": {"pattern": "scripts/**/*.py"}},
        str(data_sibling),
    )
    assert res == {}


def test_no_op_when_data_in_tree():
    """data_root == workspace_root (ceo-main in-tree): nothing is rewritten."""
    res = _run(
        {"tool_name": "Read", "tool_input": {"file_path": "context/strategy.md"}},
        str(ROOT),  # point HEADING_OS_DATA at the workspace root itself
    )
    assert res == {}


def test_non_path_tool_ignored(data_sibling):
    res = _run(
        {"tool_name": "Bash", "tool_input": {"command": "cat context/x.md"}},
        str(data_sibling),
    )
    assert res == {}
