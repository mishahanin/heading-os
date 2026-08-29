"""The classification report swept the tree and never asked git what to skip.

Commit 76fc18c fixed twenty sweeps of this shape in TEST modules and left
production untouched. `scripts/classification-health.py::walk_workspace` is the
same defect in the tool the operator actually reads.

MEASURED 2026-08-29 on this repository, before the fix:

    Total files:  2363
    of those, git ignores: 427   (18%)
    Corporate:  2362
    CEO-only:   1                <- that one file is gitignored
    No explicit rule: 2362

Among the 427: `.claude/settings.local.json`, a stale
`.claude/settings.local.json.bak-20260807-151618~`, the marp web-font binaries,
and a scratch `.sample-deck.marp-src-r6gzqj9s.md`. Every one is a file no
engine/data split decision applies to, sitting in the report the operator reads
to judge whether the split is holding.

The `.claude` carve-out made it worse than a fixed 18%. The walk skips hidden
directories EXCEPT `.claude`, and `.claude/worktrees/` is where agent worktrees
are checked out. A worktree is a full second copy of the repository, so while
one exists the sweep counts the whole tree twice.

After the fix: 1937 files, 0 of them ignored by git. The CEO-only row went from
1 to 0, because its single entry was the gitignored file.

SECOND FINDING, same shard. The fix needed `ignored_paths`, which lived in
`tests/repo_files.py`. Production cannot import from `tests/`, so using it
meant copying it -- and "a fix that landed in one of two copies" is a defect
class this audit has already paid for twice. The implementation moved to
`scripts/utils/repo_files.py` and `tests/repo_files.py` is now a re-export, so
there is one implementation and the twenty migrated sweeps keep their import.
"""
from __future__ import annotations

import ast
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.utils.repo_files import ignored_paths, not_ignored  # noqa: E402

HEALTH = ROOT / "scripts" / "classification-health.py"


@pytest.fixture(scope="module")
def health():
    spec = importlib.util.spec_from_file_location("classification_health", HEALTH)
    module = importlib.util.module_from_spec(spec)
    sys.modules["classification_health"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def repo(tmp_path):
    """A real git repository, because the thing under test asks git."""
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / ".gitignore").write_text(
        "ignored-dir/\n*.woff2\n*~\nsettings.local.json\n", encoding="utf-8")

    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "real.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "kept.md").write_text("kept\n", encoding="utf-8")

    (tmp_path / "ignored-dir").mkdir()
    (tmp_path / "ignored-dir" / "junk.md").write_text("junk\n", encoding="utf-8")
    (tmp_path / "font.woff2").write_bytes(b"\x00")
    (tmp_path / "stale.md~").write_text("stale\n", encoding="utf-8")
    (tmp_path / ".claude" / "settings.local.json").write_text("{}", encoding="utf-8")
    return tmp_path


# ============================================================
# The sweep, both directions
# ============================================================

def test_the_sweep_drops_every_file_git_ignores(health, repo):
    swept = health.walk_workspace(repo)

    assert "ignored-dir/junk.md" not in swept
    assert "font.woff2" not in swept
    assert "stale.md~" not in swept
    assert ".claude/settings.local.json" not in swept, (
        "the .claude carve-out lets the walk into .claude, so a gitignored file "
        "there is only excluded by asking git")


def test_the_sweep_keeps_the_files_git_tracks(health, repo):
    """The mirror. A sweep that returned nothing would pass the test above."""
    swept = health.walk_workspace(repo)

    assert "scripts/real.py" in swept
    assert ".claude/kept.md" in swept


def test_a_worktree_copy_is_not_counted_twice(health, repo):
    """The worst shape of this defect, and the one 76fc18c measured.

    A worktree under `.claude/worktrees/` is a full second copy of the tree, and
    `.claude` is exempt from the hidden-directory rule, so nothing but git
    excludes it.
    """
    (repo / ".gitignore").write_text(
        (repo / ".gitignore").read_text(encoding="utf-8") + ".claude/worktrees/\n",
        encoding="utf-8")
    copy = repo / ".claude" / "worktrees" / "agent-probe" / "scripts"
    copy.mkdir(parents=True)
    (copy / "real.py").write_text("x = 1\n", encoding="utf-8")

    swept = health.walk_workspace(repo)

    assert [f for f in swept if f.endswith("scripts/real.py")] == ["scripts/real.py"]


def test_the_live_repository_report_names_nothing_git_ignores(health):
    """The sweep on the real tree, which is where the 427 were measured."""
    swept = health.walk_workspace(ROOT)
    assert swept, "the sweep returned nothing, so this asserts over an empty corpus"

    still = ignored_paths([ROOT / f for f in swept], ROOT)
    assert not still, f"{len(still)} gitignored file(s) in the report: {sorted(still)[:5]}"


# ============================================================
# git failure must raise, never degrade to "nothing is ignored"
# ============================================================

def test_a_directory_that_is_not_a_repository_raises(tmp_path):
    """Degrading here is the silent failure the module exists to prevent: it
    would restore the exact defect, and report it as a clean sweep."""
    (tmp_path / "a.md").write_text("x", encoding="utf-8")

    with pytest.raises(RuntimeError, match="check-ignore failed"):
        ignored_paths([tmp_path / "a.md"], tmp_path)


def test_the_sweep_propagates_that_failure_rather_than_reporting_clean(health, tmp_path):
    """The wiring. A helper that raises is worth nothing if the caller swallows
    it and prints a total."""
    (tmp_path / "a.md").write_text("x", encoding="utf-8")

    with pytest.raises(RuntimeError, match="check-ignore failed"):
        health.walk_workspace(tmp_path)


def test_an_empty_batch_asks_git_nothing_and_returns_nothing():
    assert ignored_paths([], ROOT) == set()
    assert not_ignored([], ROOT) == []


# ============================================================
# One implementation
# ============================================================

def test_the_test_side_module_defines_no_implementation_of_its_own():
    """`tests/repo_files.py` is a re-export. A function DEFINED there is the
    second copy, and the second copy is the one that stops being fixed."""
    tree = ast.parse((ROOT / "tests" / "repo_files.py").read_text(encoding="utf-8"))
    defined = [n.name for n in tree.body
               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]

    assert defined == [], (
        f"tests/repo_files.py defines {defined}; it must re-export "
        f"scripts/utils/repo_files.py instead of carrying an implementation")


@pytest.mark.parametrize("name", ["ignored_paths", "not_ignored", "tracked_paths",
                                  "tracked_python_files"])
def test_the_test_side_module_still_exports_the_helper(name):
    """The mirror. Twenty migrated sweeps import from `tests/repo_files.py`, so
    emptying it would break them all."""
    import tests.repo_files as shim
    import scripts.utils.repo_files as real

    assert getattr(shim, name) is getattr(real, name)


# The "one implementation" RULE is not here. It lives in
# `tests/test_a_walker_that_never_asked_git.py`, which already owned it for the
# test tree; on 2026-08-29 it was widened to `scripts/` and its substring
# detector was rewritten to read the AST. Writing a second detector here would
# have been the very duplication both rules exist to prevent, and the first
# draft of this file did exactly that before the older rule caught it.


def test_the_two_contracts_differ_and_both_are_reachable(tmp_path):
    """`_or_none` exists so a caller that must over-report can say so in one
    visible line, instead of copying the call and inverting its behaviour."""
    from scripts.utils.repo_files import ignored_paths_or_none

    assert ignored_paths_or_none([ROOT / "README.md"], ROOT) is not None

    probe = tmp_path / "a.md"
    probe.write_text("x", encoding="utf-8")
    assert ignored_paths_or_none([probe], tmp_path) is None, (
        "outside a git repository the question is unanswered, and saying so is "
        "the whole difference from 'nothing is ignored'")
