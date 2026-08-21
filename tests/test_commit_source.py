#!/usr/bin/env python3
"""The commit source: what becomes an index row, and what must never.

Commit messages are the largest body of written reasoning in this workspace and
nothing retrieved them before this: no FTS, no vector, no CodeGraph edge. This
module turns `git log` into rows the existing memory-index store already accepts.

Three properties carry real risk and are tested here rather than assumed:

1. **The air gap.** A commit that touches a `personal` path (or the `_secure/`
   prefix) must not enter ANY persistent index -- not its message, not its paths,
   not a partial row. A commit that touches one denied file and nine allowed ones
   is skipped WHOLE, because indexing the message of a personal change leaks the
   change.
2. **Backup noise is excluded.** 153 of 1,257 commits are `chore: workspace
   backup <date>`; on the data side that is a fifth of all history. They answer no
   question and their near-identical vectors crowd real hits.
3. **Both body variants exist and differ.** The spec calls for measuring the
   changed-path list in the body against leaving it out, so the flag must actually
   change the text that gets embedded.

Design fixtures build real git repositories in tmp_path. A mocked `git log` would
test the parser against my own idea of git's output, which is the assumption most
worth not making.
"""
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.utils.commit_source import iter_commits, BACKUP_SUBJECT_RE  # noqa: E402


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=True
    ).stdout


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "r"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "Test")
    return repo


def _commit(repo: Path, rel: str, subject: str, body: str = "") -> str:
    f = repo / rel
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(f"{subject}\n", encoding="utf-8")
    _git(repo, "add", "-A")
    msg = subject if not body else f"{subject}\n\n{body}"
    _git(repo, "commit", "-q", "-m", msg)
    return _git(repo, "rev-parse", "HEAD").strip()


# --- the air gap -----------------------------------------------------------

def test_a_commit_touching_a_personal_path_is_never_indexed(tmp_path):
    repo = _repo(tmp_path)
    _commit(repo, "docs/ok.md", "public change")
    secret = _commit(repo, "chronicle/personal/2026-08-21.md", "private note")
    shas = {c["sha"] for c in iter_commits(repo, repo_label="data")}
    assert secret not in shas


def test_a_mixed_commit_is_skipped_whole_not_partially(tmp_path):
    """One denied file among many denies the commit. Its message describes it."""
    repo = _repo(tmp_path)
    (repo / "docs").mkdir()
    (repo / "docs" / "a.md").write_text("a\n", encoding="utf-8")
    (repo / "chronicle" / "personal").mkdir(parents=True)
    (repo / "chronicle" / "personal" / "b.md").write_text("b\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "touches both")
    rows = list(iter_commits(repo, repo_label="data"))
    assert rows == []


def test_the_vault_prefix_is_denied_too(tmp_path):
    repo = _repo(tmp_path)
    vault = _commit(repo, "_secure/x.md", "vault change")
    assert vault not in {c["sha"] for c in iter_commits(repo, repo_label="data")}


def test_case_does_not_open_the_air_gap(tmp_path):
    repo = _repo(tmp_path)
    upper = _commit(repo, "chronicle/Personal/x.md", "capitalised personal")
    assert upper not in {c["sha"] for c in iter_commits(repo, repo_label="data")}


# --- backup noise ----------------------------------------------------------

def test_backup_commits_are_excluded(tmp_path):
    repo = _repo(tmp_path)
    real = _commit(repo, "a.md", "feat: a real change")
    noise = _commit(repo, "b.md", "chore: workspace backup 2026-08-21 18:33")
    shas = {c["sha"] for c in iter_commits(repo, repo_label="engine")}
    assert real in shas
    assert noise not in shas


def test_the_backup_pattern_is_anchored_not_a_substring_match():
    """`fix: undo the workspace backup regression` is a real commit, not noise."""
    assert BACKUP_SUBJECT_RE.match("chore: workspace backup 2026-08-21 18:33")
    assert not BACKUP_SUBJECT_RE.match("fix: undo the workspace backup regression")


# --- row shape -------------------------------------------------------------

def test_a_row_carries_what_the_store_needs(tmp_path):
    repo = _repo(tmp_path)
    sha = _commit(repo, "a.md", "feat: the subject", "The reason it was done.")
    (row,) = list(iter_commits(repo, repo_label="engine"))
    assert row["sha"] == sha
    assert row["id"] == f"commit:engine:{sha}"
    assert row["path"] == f"engine@{sha}"
    assert row["title"] == "feat: the subject"
    assert row["ntype"] == "commit"
    assert isinstance(row["mtime"], float) and row["mtime"] > 0
    assert "The reason it was done." in row["body"]


def test_both_body_variants_exist_and_differ(tmp_path):
    repo = _repo(tmp_path)
    _commit(repo, "scripts/action-queue.py", "feat: something")
    with_paths = list(iter_commits(repo, repo_label="engine", include_paths=True))[0]
    without = list(iter_commits(repo, repo_label="engine", include_paths=False))[0]
    assert "scripts/action-queue.py" in with_paths["body"]
    assert "scripts/action-queue.py" not in without["body"]
    assert with_paths["body"] != without["body"]


def test_a_merge_commit_with_no_changed_paths_still_yields_a_row(tmp_path):
    """An empty commit has no paths, so it cannot be denied -- and must survive."""
    repo = _repo(tmp_path)
    _commit(repo, "a.md", "first")
    _git(repo, "commit", "-q", "--allow-empty", "-m", "chore: empty on purpose")
    subjects = {c["title"] for c in iter_commits(repo, repo_label="engine")}
    assert "chore: empty on purpose" in subjects


# --- incremental -----------------------------------------------------------

def test_since_limits_the_walk_to_new_commits(tmp_path):
    repo = _repo(tmp_path)
    first = _commit(repo, "a.md", "first")
    second = _commit(repo, "b.md", "second")
    rows = list(iter_commits(repo, repo_label="engine", since=first))
    assert [r["sha"] for r in rows] == [second]


def test_an_unknown_since_falls_back_to_a_full_walk(tmp_path):
    """A rewritten history must rebuild, not silently index nothing."""
    repo = _repo(tmp_path)
    _commit(repo, "a.md", "first")
    _commit(repo, "b.md", "second")
    rows = list(iter_commits(repo, repo_label="engine", since="0" * 40))
    assert len(rows) == 2


def test_a_repo_with_no_commits_yields_nothing_and_does_not_raise(tmp_path):
    repo = _repo(tmp_path)
    assert list(iter_commits(repo, repo_label="engine")) == []


def test_a_path_that_is_not_a_git_repo_raises_plainly(tmp_path):
    with pytest.raises(ValueError, match="not a git repository"):
        list(iter_commits(tmp_path / "nope", repo_label="engine"))
