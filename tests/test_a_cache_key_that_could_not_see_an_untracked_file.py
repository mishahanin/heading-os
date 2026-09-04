#!/usr/bin/env python3
"""The cache key measures the working tree, not git's index, and the difference
is the whole failure mode.

`scripts/utils/repo_files.py::tracked_paths` does NOT read the git index despite
its name. It globs the working tree and subtracts only what `git check-ignore`
names, so UNTRACKED, non-ignored files are IN the corpus that roughly a hundred
of this suite's tree sweeps read. A verdict cache keyed on `git ls-files`
content would therefore hand back yesterday's green for a tree that is not
yesterday's tree: a scratch `.py` dropped under `scripts/` by a parallel agent, a
half-written test, a `.md` left by a crashed tool.

Not hypothetical in this repository. `read_sources`' own docstring records
2026-08-30, when a parallel agent's scratch file appeared and vanished mid-walk
and a guard reported a violation that had not occurred.

The load-bearing test here is
`test_an_untracked_file_moves_the_key_and_an_index_keyed_digest_misses_it`,
which builds BOTH keys over the same tree and asserts they disagree. Its
index-keyed half is the previous version of this design, and it is required to
FAIL to notice the file: a test that only asserted the new key moves would pass
just as happily against the old one.
"""
from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils.repo_files import (  # noqa: E402
    IndexUnreadable, tracked_paths, working_tree_paths,
)
from scripts.utils.test_cache import KeyUnavailable, corpus_key  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

#: Floor under the live-tree sweep below. MEASURED 2026-09-04 on this checkout:
#: 2341 files in the working tree that git does not ignore. A test whose only
#: assertions sit inside a loop over a discovered corpus passes when the corpus
#: is empty (`.claude/rules/development-standards.md`, obligation 7), so the
#: size is asserted outside the loop with the measured number beside it.
LIVE_CORPUS_FLOOR = 1800


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True,
                   capture_output=True)


@pytest.fixture
def scratch_repo(tmp_path: Path) -> Path:
    """A real git repository holding one of each kind of path.

    A real one and not a fake, because the whole question is what GIT considers
    ignored, and a stub that answered that question would be answering it with
    the same assumption the code under test makes.
    """
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q", ".")
    _git(root, "config", "user.email", "test@example.invalid")
    _git(root, "config", "user.name", "Test")
    (root / ".gitignore").write_text("ignored/\n", encoding="utf-8")
    (root / "tracked.py").write_text("x = 1\n", encoding="utf-8")
    (root / "ignored").mkdir()
    (root / "ignored" / "junk.txt").write_text("junk\n", encoding="utf-8")
    _git(root, "add", ".gitignore", "tracked.py")
    _git(root, "commit", "-q", "-m", "initial")
    return root


def _index_keyed_digest(root: Path) -> str:
    """The key this design REPLACES: a digest over `git ls-files` content.

    Kept in the test rather than in the module because it must never be
    importable as an option. It exists to be shown failing.
    """
    out = subprocess.run(["git", "-C", str(root), "ls-files", "-z"],
                         capture_output=True, check=True)
    names = sorted(p for p in out.stdout.decode().split("\0") if p)
    digest = hashlib.sha256()
    for name in names:
        path = root / name
        if not path.is_file():
            continue
        digest.update(name.encode())
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


# ============================================================
# What the corpus is
# ============================================================

def test_the_corpus_is_the_working_tree_minus_what_git_ignores(scratch_repo):
    assert set(working_tree_paths(scratch_repo)) == {".gitignore", "tracked.py"}

    (scratch_repo / "scratch.py").write_text("# dropped by a parallel agent\n",
                                             encoding="utf-8")

    assert set(working_tree_paths(scratch_repo)) == {
        ".gitignore", "tracked.py", "scratch.py"}, (
        "an untracked, non-ignored file is in the corpus the tree sweeps read, "
        "so it must be in the corpus the key measures")


def test_a_gitignored_file_is_not_in_the_corpus(scratch_repo):
    assert "ignored/junk.txt" not in working_tree_paths(scratch_repo)

    (scratch_repo / "ignored" / "more.txt").write_text("more\n", encoding="utf-8")

    assert not [p for p in working_tree_paths(scratch_repo)
                if p.startswith("ignored/")]


def test_a_tracked_file_deleted_from_disk_leaves_the_corpus(scratch_repo):
    assert "tracked.py" in working_tree_paths(scratch_repo)

    (scratch_repo / "tracked.py").unlink()

    assert "tracked.py" not in working_tree_paths(scratch_repo), (
        "git still has it in the index; the working tree does not, and the "
        "working tree is what the sweeps read")


def test_the_corpus_matches_the_glob_the_sweeps_actually_use():
    """The two definitions agree on the live tree, so they cannot drift apart.

    Stated with git's own directory excluded, and only with that exclusion,
    because `git check-ignore` reports nothing under `.git` as ignored and what
    the glob finds there depends on whether this checkout is a main clone (a
    directory, descended into) or a worktree (a one-line file). See
    `working_tree_paths`.
    """
    fast = set(working_tree_paths(ROOT))
    globbed = {p.relative_to(ROOT).as_posix() for p in tracked_paths(["**/*"], ROOT)}
    globbed = {p for p in globbed if p != ".git" and not p.startswith(".git/")}

    assert len(fast) >= LIVE_CORPUS_FLOOR, (
        f"only {len(fast)} files in the corpus; measured 2341 on 2026-09-04. "
        f"A key over a collapsed corpus is a key that never moves.")
    # A parallel agent can create or delete a file between the two
    # enumerations, which is the very race this module exists to respect. Only
    # paths BOTH calls could have seen are compared: a symmetric difference is
    # re-checked once, and only a stable disagreement fails.
    if fast != globbed:
        fast_again = set(working_tree_paths(ROOT))
        globbed_again = {p.relative_to(ROOT).as_posix()
                         for p in tracked_paths(["**/*"], ROOT)}
        globbed_again = {p for p in globbed_again
                         if p != ".git" and not p.startswith(".git/")}
        stable = (fast & fast_again) ^ (globbed & globbed_again)
        assert not stable, (
            f"the key's corpus and the sweeps' corpus disagree on {sorted(stable)[:20]}")


# ============================================================
# What moves the key
# ============================================================

def test_an_unchanged_tree_gives_the_same_key(scratch_repo):
    assert corpus_key(scratch_repo) == corpus_key(scratch_repo)


def test_editing_a_tracked_file_moves_the_key(scratch_repo):
    before = corpus_key(scratch_repo)

    (scratch_repo / "tracked.py").write_text("x = 2\n", encoding="utf-8")

    assert corpus_key(scratch_repo) != before


def test_renaming_a_file_moves_the_key(scratch_repo):
    before = corpus_key(scratch_repo)

    (scratch_repo / "tracked.py").rename(scratch_repo / "renamed.py")

    assert corpus_key(scratch_repo) != before, (
        "the NAME is hashed as well as the content; a pure rename changes what "
        "a sweep reports even when every byte in the tree is the same")


def test_deleting_a_tracked_file_moves_the_key(scratch_repo):
    before = corpus_key(scratch_repo)

    (scratch_repo / "tracked.py").unlink()

    assert corpus_key(scratch_repo) != before


def test_a_gitignored_file_does_not_move_the_key(scratch_repo):
    """The other direction, and it is what makes the cache worth having.

    Without this the store's own file, every `__pycache__` entry and every
    `.coverage` would move the key on each run, and nothing would ever be
    skipped. A guard that refuses everything satisfies every refusal test.
    """
    before = corpus_key(scratch_repo)

    (scratch_repo / "ignored" / "written-by-the-run.txt").write_text(
        "output\n", encoding="utf-8")

    assert corpus_key(scratch_repo) == before


def test_an_untracked_file_moves_the_key_and_an_index_keyed_digest_misses_it(
        scratch_repo):
    """The whole failure mode, both halves, over one tree.

    A scratch `.py` a parallel agent drops under a swept directory is read by
    every sweep in this suite. The key must move. An index-keyed digest -- the
    obvious design, and the wrong one -- cannot see the file at all, because it
    is not in the index.
    """
    key_before = corpus_key(scratch_repo)
    index_before = _index_keyed_digest(scratch_repo)

    (scratch_repo / "scratch_from_a_parallel_agent.py").write_text(
        "raise SystemExit('half written')\n", encoding="utf-8")

    assert corpus_key(scratch_repo) != key_before, (
        "an untracked, non-ignored file is in the corpus the sweeps read; a "
        "key that does not move here hands back a green for a different tree")

    assert _index_keyed_digest(scratch_repo) == index_before, (
        "this is the half that must FAIL to notice. If the index-keyed digest "
        "has started moving too, this test has stopped distinguishing the two "
        "designs and proves nothing about either.")


# ============================================================
# When git cannot be asked
# ============================================================

def test_the_corpus_refuses_rather_than_reporting_an_empty_tree(
        scratch_repo, monkeypatch, tmp_path):
    """No git, no answer. Never "no files", which reads as "nothing changed"."""
    empty_bin = tmp_path / "no-tools"
    empty_bin.mkdir()
    monkeypatch.setenv("PATH", str(empty_bin))

    with pytest.raises(IndexUnreadable):
        working_tree_paths(scratch_repo)


def test_the_key_refuses_rather_than_guessing_when_git_cannot_be_asked(
        scratch_repo, monkeypatch, tmp_path):
    empty_bin = tmp_path / "no-tools"
    empty_bin.mkdir()
    monkeypatch.setenv("PATH", str(empty_bin))

    with pytest.raises(KeyUnavailable):
        corpus_key(scratch_repo)


def test_the_key_refuses_over_a_directory_that_is_not_a_repository(tmp_path):
    (tmp_path / "lonely.txt").write_text("no git here\n", encoding="utf-8")

    with pytest.raises(KeyUnavailable):
        corpus_key(tmp_path)
