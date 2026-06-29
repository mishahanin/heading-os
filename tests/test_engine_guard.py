#!/usr/bin/env python3
"""Tests for the shared engine/data leak detector (scripts/utils/engine_guard.py).

The pure find_data_artifacts() branches are also exercised via
test_engine_tree_clean.py; here we cover the repo-scanning entry points
(repo_carried_paths, scan_engine_repo) against a real temp git tree so the
push-time wall's data source is proven, not just the pure filter.
"""
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.utils.engine_guard import (  # noqa: E402
    find_data_artifacts,
    repo_carried_paths,
    scan_engine_repo,
)


def _git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True)


def _init_repo(tmp_path) -> Path:
    repo = tmp_path / "engine"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    return repo


def _write(repo, rel, body="x"):
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")
    return p


def test_scan_clean_engine_tree(tmp_path):
    repo = _init_repo(tmp_path)
    _write(repo, "scripts/foo.py", "print(1)\n")
    _write(repo, "tests/test_x.py", "def test_x(): pass\n")
    _git(repo, "add", "-A")
    assert scan_engine_repo(repo) == []


def test_scan_flags_tracked_data_artifact(tmp_path):
    repo = _init_repo(tmp_path)
    _write(repo, "scripts/ok.py", "print(1)\n")
    _write(repo, "crm/contacts/john.md", "name: John\n")  # routes private
    _git(repo, "add", "-A")
    assert scan_engine_repo(repo) == ["crm/contacts/john.md"]


def test_scan_flags_untracked_not_ignored(tmp_path):
    # An untracked-but-not-ignored data file would be swept in by `git add -A` on the
    # next commit -- the scan must catch it before it is even staged.
    repo = _init_repo(tmp_path)
    _write(repo, "outputs/operations/leak.md", "secret plan\n")  # private, untracked
    assert "outputs/operations/leak.md" in scan_engine_repo(repo)


def test_scan_respects_gitignore(tmp_path):
    # A data path that IS gitignored is not carried by git, so it is not a leak risk.
    repo = _init_repo(tmp_path)
    _write(repo, ".gitignore", "outputs/\n")
    _write(repo, "outputs/operations/ignored.md", "x\n")
    _git(repo, "add", ".gitignore")
    assert scan_engine_repo(repo) == []


def test_repo_carried_paths_includes_tracked_and_untracked(tmp_path):
    repo = _init_repo(tmp_path)
    _write(repo, "a.py", "1\n")
    _git(repo, "add", "a.py")
    _write(repo, "b.py", "2\n")  # untracked, not ignored
    carried = repo_carried_paths(repo)
    assert "a.py" in carried and "b.py" in carried


def test_docs_superpowers_regression(tmp_path):
    # The exact 2026-06-22 leak: top-level 'docs' is not a data-dir name, yet
    # docs/superpowers/ routes private. Detector must flag it.
    assert find_data_artifacts(["docs/superpowers/specs/x.md"]) == ["docs/superpowers/specs/x.md"]
