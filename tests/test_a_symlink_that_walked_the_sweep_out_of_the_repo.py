"""One symlink took every tree sweep out of the repository, and git refused.

`not_ignored` resolved each walked path before asking git what to skip.
`Path.resolve()` follows symlinks, and a venv's `bin/python3.11` is a symlink to
an interpreter outside the tree. `git check-ignore` exits 128 on a path outside
the repository, and `ignored_paths` reads any non-(0,1) exit as "git could not
answer" and raises — correctly, because reporting an unfiltered tree would
present ignored files as findings. One such path poisoned the whole batch.

MEASURED 2026-08-29. With the operator's `hdr` worktree checked out inside the
engine tree, its `.venv` carried that symlink, the corpus was 31776 files, and
four sweeps died at once:

    test_the_scan_actually_reads_files
    test_no_tracked_code_names_a_provider_endpoint
    test_only_one_module_holds_the_proxy_address
    test_every_anchor_pointer_into_docs_resolves

The sweep is about paths in THIS tree, so the link is what it walks and the link
is what git should be asked about. `os.path.abspath` normalises without
following, and a path that is still outside afterwards is simply not something
git ignores, so it is left out of the question rather than allowed to fail it.

The second defect here was introduced by the first fix and caught by the full
suite twenty minutes later. Both forms of path reach this module: the tree
sweeps pass absolute paths, `check-path-references.py` passes repo-relative
ones. Testing the raw string against the repository prefix answered False for
every relative path, so the entire batch looked outside the repo and NOTHING was
filtered — a filter that silently stopped filtering, which is the exact shape
this module exists to prevent.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.utils.repo_files import (  # noqa: E402
    ignored_paths,
    ignored_paths_or_none,
    not_ignored,
)


@pytest.fixture()
def repo(tmp_path):
    work = tmp_path / "repo"
    work.mkdir()
    subprocess.run(["git", "init", "-q", str(work)], check=True)
    (work / ".gitignore").write_text("ignored/\n*.log\n", encoding="utf-8")
    (work / "kept.py").write_text("x = 1\n", encoding="utf-8")
    (work / "noisy.log").write_text("noise\n", encoding="utf-8")
    (work / "ignored").mkdir()
    (work / "ignored" / "inside.py").write_text("y = 2\n", encoding="utf-8")
    return work


# ============================================================
# A symlink pointing out of the tree
# ============================================================

def test_a_symlink_out_of_the_repo_does_not_break_the_batch(repo, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "python3.11").write_text("#!/bin/sh\n", encoding="utf-8")
    (repo / "ignored" / "python3.11").symlink_to(outside / "python3.11")

    kept = not_ignored(repo.rglob("*"), repo)

    assert repo / "kept.py" in kept
    assert (repo / "noisy.log") not in kept
    assert (repo / "ignored" / "inside.py") not in kept
    # The link itself lives under an ignored directory, so it is filtered by
    # its own path rather than by its target.
    assert (repo / "ignored" / "python3.11") not in kept


def test_the_link_is_judged_by_its_own_path_not_its_target(repo, tmp_path):
    """A symlink in a KEPT directory pointing outside stays kept: git has no
    opinion about where a link goes, only about where the link is."""
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "tool.py").write_text("z = 3\n", encoding="utf-8")
    link = repo / "tool.py"
    link.symlink_to(outside / "tool.py")

    assert link in not_ignored(repo.rglob("*"), repo)


def test_a_path_outside_the_repo_is_not_reported_as_ignored(repo, tmp_path):
    outside = tmp_path / "elsewhere.py"
    outside.write_text("q = 4\n", encoding="utf-8")
    answer = ignored_paths([str(outside), str(repo / "noisy.log")], repo)
    assert str(outside) not in answer
    assert str(repo / "noisy.log") in answer


def test_a_batch_entirely_outside_the_repo_is_answerable(repo, tmp_path):
    """Empty, not a raise. Nothing outside the repository is a thing git
    ignores, so the honest answer is the empty set."""
    outside = tmp_path / "elsewhere.py"
    outside.write_text("q = 4\n", encoding="utf-8")
    assert ignored_paths([str(outside)], repo) == set()


# ============================================================
# Both spellings of a path reach this module
# ============================================================

def test_a_relative_path_is_still_asked_about(repo):
    """The defect the first fix introduced: a repo-relative path failed the
    inside test, the whole batch looked outside, and the filter silently
    stopped filtering."""
    answer = ignored_paths(["noisy.log", "kept.py", "ignored/inside.py"], repo)
    assert "noisy.log" in answer
    assert "ignored/inside.py" in answer
    assert "kept.py" not in answer


def test_the_two_spellings_agree(repo):
    """Same three files, absolute and relative, must produce the same verdict.

    A rule that answered one form and not the other is how the filter went
    quiet without failing anything.
    """
    relative = ignored_paths(["noisy.log", "kept.py"], repo)
    absolute = ignored_paths([str(repo / "noisy.log"), str(repo / "kept.py")], repo)
    assert ("noisy.log" in relative) == (str(repo / "noisy.log") in absolute)
    assert ("kept.py" in relative) == (str(repo / "kept.py") in absolute)


def test_a_mixed_batch_answers_both(repo):
    answer = ignored_paths(["noisy.log", str(repo / "ignored" / "inside.py")], repo)
    assert "noisy.log" in answer
    assert str(repo / "ignored" / "inside.py") in answer


# ============================================================
# The raise is still a raise
# ============================================================

def test_a_directory_that_is_not_a_repo_still_cannot_answer(tmp_path):
    """The whole point of this module: "git could not say" must never quietly
    become "nothing is ignored"."""
    plain = tmp_path / "plain"
    plain.mkdir()
    (plain / "a.py").write_text("x = 1\n", encoding="utf-8")
    assert ignored_paths_or_none([str(plain / "a.py")], plain) is None
    with pytest.raises(RuntimeError, match="check-ignore failed"):
        ignored_paths([str(plain / "a.py")], plain)


def test_an_empty_batch_is_an_empty_answer(repo):
    assert ignored_paths([], repo) == set()


def test_the_live_repository_still_filters_something():
    """A guard over an empty corpus proves nothing, so this asserts the real
    tree still has both kinds of file in it."""
    # `scripts/`, not `.claude/`: a sibling rule forbids a test from walking the
    # repository root or `.claude/`, because a worktree under
    # `.claude/worktrees/` doubles the corpus. `scripts/` carries `__pycache__`,
    # which git ignores, so it still has both kinds of file in it.
    walked = list((ROOT / "scripts").rglob("*"))
    kept = not_ignored(walked, ROOT)
    assert kept, "the live sweep kept nothing, which cannot be right"
    assert len(kept) < len(walked), "the live sweep filtered nothing"


def test_the_module_does_not_resolve_symlinks_again():
    """Pins the mechanism, not just the outcome: `resolve()` is what carried a
    path out of the tree, and it reads like an innocent tidy-up."""
    source = (ROOT / "scripts" / "utils" / "repo_files.py").read_text(encoding="utf-8")
    assert ".resolve()" not in source.split("ROOT = ")[1].split("\n", 1)[1], (
        "repo_files resolves a walked path again; that follows symlinks out of "
        "the repository and git refuses to answer about what it finds")
    assert "os.path.abspath" in source
