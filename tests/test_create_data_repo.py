"""create-data-repo.py bootstraps a private data repo without touching GitHub.

These tests cover the local, network-free path: directory scaffold, the
self-describing repo files (.gitignore + README), git init/commit, and the
end-to-end --no-remote flow. The GitHub-creating path (gh repo create + verified
push) is not exercised here — it requires an authenticated gh and a live remote.
"""
import importlib.util
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("create_data_repo", ROOT / "scripts" / "create-data-repo.py")
cdr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cdr)


def _git_available() -> bool:
    return subprocess.run(["git", "--version"], capture_output=True).returncode == 0


pytestmark = pytest.mark.skipif(not _git_available(), reason="git not on PATH")


def test_scaffold_creates_the_data_tree(tmp_path):
    target = tmp_path / ".heading-os-data"
    assert cdr.scaffold(target, dry_run=False) == 0
    for d in ("crm/contacts", "knowledge", "outputs", "threads/business", "threads/personal", "context"):
        assert (target / d).is_dir(), f"missing scaffolded dir: {d}"
    assert (target / ".schema-version").is_file()


def test_scaffold_refuses_nonempty_target(tmp_path):
    target = tmp_path / "data"
    target.mkdir()
    (target / "stray.txt").write_text("x", encoding="utf-8")
    assert cdr.scaffold(target, dry_run=False) != 0  # non-zero -> refused


def test_repo_files_written(tmp_path):
    target = tmp_path / "data"
    target.mkdir()
    cdr.write_repo_files(target, dry_run=False)
    gitignore = (target / ".gitignore").read_text(encoding="utf-8")
    assert ".memory-index/" in gitignore
    assert ".env" in gitignore and "!.env.example" in gitignore
    assert "*.session" in gitignore
    assert (target / "README.md").read_text(encoding="utf-8").startswith("# HEADING OS")


def test_git_init_commit_creates_repo(tmp_path):
    target = tmp_path / "data"
    target.mkdir()
    (target / "a.txt").write_text("hello", encoding="utf-8")
    # Local identity so commit works in a clean CI environment.
    subprocess.run(["git", "init", "-b", "main"], cwd=target, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=target, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=target, check=True, capture_output=True)
    assert cdr.git_init_commit(target, dry_run=False) == 0
    log = subprocess.run(["git", "log", "--oneline"], cwd=target, capture_output=True, text=True)
    assert "initialize private data overlay" in log.stdout


def test_dry_run_makes_no_changes(tmp_path):
    target = tmp_path / ".heading-os-data"
    cdr.scaffold(target, dry_run=True)
    assert not target.exists(), "dry-run must not create the target tree"


def test_no_remote_flow_is_local_only(tmp_path, monkeypatch):
    """main() with --no-remote scaffolds, writes files, and inits git — no gh."""
    target = tmp_path / ".heading-os-data"
    monkeypatch.setattr("sys.argv", ["create-data-repo.py", "--path", str(target), "--no-remote"])
    # Provide a git identity for the commit step.
    monkeypatch.setenv("GIT_AUTHOR_NAME", "Test")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "t@example.com")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "Test")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "t@example.com")
    rc = cdr.main()
    assert rc == 0
    assert (target / "context").is_dir()
    assert (target / ".gitignore").is_file()
    assert (target / ".git").is_dir()
    # No origin remote should have been created.
    origin = subprocess.run(["git", "remote", "get-url", "origin"], cwd=target, capture_output=True)
    assert origin.returncode != 0, "--no-remote must not wire an origin"
