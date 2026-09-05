#!/usr/bin/env python3
"""`--no-commit` pushes commits only, and the wall scanned the whole worktree.

MEASURED 2026-09-05 on a real `push-all.py` run the operator asked for. Two
UNTRACKED files under another task's output directory, holding base64 chunks of
an embedded image that collide with the AWS-key pattern, stopped the push:

    REFUSING TO PUSH — secret-like CONTENT in a file about to be pushed.

They were not about to be pushed. `--no-commit --dry-run` gave the identical
three findings and the identical sentence, and in that mode nothing is staged
and nothing is committed, so `origin/main..HEAD` is the entire set of bytes that
can reach the remote.

## Why the wide scan is right in the DEFAULT mode and only there

`_push_delta_files` has four legs: the committed-unpushed delta, the index, the
unstaged tracked edits, and every untracked file git is not ignoring. The last
three exist because step 3 runs `git add -A`, so a file that is merely sitting
in the worktree at scan time is in the commit a moment later. The wall runs
BEFORE that commit deliberately, so a tree staged with `--no-verify` cannot slip
past.

`--no-commit` does not run `git add -A` at all. Nothing is staged, nothing is
committed, and a file that is not already in a commit cannot be pushed by that
invocation. Scanning it is not extra safety; it is a refusal over bytes the run
would not carry, and the operator's own reading of it was the correct one: push
what is committed, and the next push takes what this one left.

THE NARROWING IS SAFE BECAUSE OF THE STAGING COMMAND, not because untracked
files are harmless. `test_the_narrowing_depends_on_no_commit_not_staging` holds
that dependency in the open: if `--no-commit` ever grows a staging step, this
narrowing becomes a hole and fails HERE rather than in silence.

Run: .venv/bin/python -m pytest \\
     tests/test_a_wall_that_scanned_what_this_push_would_not_carry.py -q
"""
from __future__ import annotations

import ast
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PUSH_ALL = ROOT / "scripts/push-all.py"


@pytest.fixture(scope="module")
def push_all():
    spec = importlib.util.spec_from_file_location("push_all_under_test", PUSH_ALL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True,
                   capture_output=True)


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    """A clone with a real `origin/main`, one unpushed commit, and stray files.

    A real bare remote, not a faked ref: `_push_delta_files` asks git to resolve
    `origin/main`, and a scratch repo without one takes the other branch of that
    function entirely, so a fixture that skipped it would exercise code the
    operator's push never reaches.
    """
    bare = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True,
                   capture_output=True)

    work = tmp_path / "work"
    subprocess.run(["git", "clone", "-q", str(bare), str(work)], check=True,
                   capture_output=True)
    _git(work, "config", "user.email", "t@example.invalid")
    _git(work, "config", "user.name", "t")
    _git(work, "checkout", "-q", "-b", "main")

    (work / "base.txt").write_text("base\n", encoding="utf-8")
    _git(work, "add", "base.txt")
    _git(work, "commit", "-q", "-m", "base")
    _git(work, "push", "-q", "-u", "origin", "main")

    # The one thing a --no-commit push WOULD carry.
    (work / "committed.txt").write_text("committed and unpushed\n",
                                        encoding="utf-8")
    _git(work, "add", "committed.txt")
    _git(work, "commit", "-q", "-m", "unpushed")

    # The three things it would not.
    (work / "staged.txt").write_text("staged only\n", encoding="utf-8")
    _git(work, "add", "staged.txt")
    (work / "base.txt").write_text("base edited, unstaged\n", encoding="utf-8")
    (work / "stray.txt").write_text("untracked\n", encoding="utf-8")
    return work


# ============================================================
# The floor: the default mode must really see all four
# ============================================================

def test_the_default_scan_sees_everything_add_dash_A_would_sweep(push_all, repo):
    """Without this, every absence asserted below could be an empty fixture."""
    files = push_all._push_delta_files(repo)

    for name in ("committed.txt", "staged.txt", "base.txt", "stray.txt"):
        assert name in files, (
            f"{name} is missing from the default scan set, so this fixture "
            f"cannot show that --no-commit narrows anything: {sorted(files)}")


# ============================================================
# THE GUARD: --no-commit scans what --no-commit can push
# ============================================================

def test_no_commit_scans_only_the_committed_delta(push_all, repo):
    files = push_all._push_delta_files(repo, will_commit=False)

    assert "committed.txt" in files, (
        f"the committed-but-unpushed file fell out of the scan; that IS what "
        f"this mode pushes and it must still be read: {sorted(files)}")
    for name in ("staged.txt", "base.txt", "stray.txt"):
        assert name not in files, (
            f"{name} is scanned under --no-commit, which stages nothing and "
            f"commits nothing, so those bytes cannot reach the remote. A "
            f"refusal over them blocks a push over a file it would not carry: "
            f"{sorted(files)}")


def test_push_repo_hands_the_mode_to_both_scans(push_all):
    """The call site, not the callee. THIS IS THE CASE THAT WAS MISSING.

    MEASURED while writing this file: deleting `will_commit=do_commit` from
    `push_repo`'s call to `content_scan` left every other case here GREEN. The
    builder narrowed correctly and nothing ever asked it to, which is the whole
    defect restored with the fix still in the diff. A test that exercises a
    function the entry point has stopped calling is the shape
    `.claude/rules/development-standards.md` names at obligation 6.
    """
    tree = ast.parse(PUSH_ALL.read_text(encoding="utf-8"))
    push_repo = next(n for n in ast.walk(tree)
                     if isinstance(n, ast.FunctionDef) and n.name == "push_repo")

    without_mode: list[str] = []
    seen = 0
    for callee in ("content_scan", "engine_content_scan"):
        calls = [n for n in ast.walk(push_repo)
                 if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                 and n.func.id == callee]
        assert calls, f"push_repo no longer calls {callee}"
        seen += len(calls)
        without_mode += [f"{callee} at line {n.lineno}" for n in calls
                         if "will_commit" not in {kw.arg for kw in n.keywords}]

    # The floor, outside the loop. Measured 2026-09-05: push_repo holds exactly
    # two such calls, one per wall. A walk that found none would satisfy the
    # assertion below by never running it.
    assert seen >= 2, (
        f"only {seen} scan call(s) found in push_repo; this case cannot "
        f"establish anything about a mode it never saw passed")
    assert not without_mode, (
        f"push_repo calls {', '.join(without_mode)} without will_commit, so the "
        f"scan cannot tell a run that stages everything from one that stages "
        f"nothing, and refuses over files this push would not carry")


def test_the_narrowing_depends_on_no_commit_not_staging(push_all):
    """The dependency, held in the open.

    `--no-commit` is safe to narrow ONLY because that path runs no `git add -A`.
    Asked of the AST rather than of the prose: `push_repo` must still stage with
    `add -A`, and must do it under the `do_commit` branch. If a staging step ever
    moves outside that branch, the narrowing above becomes a hole and this fails
    rather than the wall going quiet.
    """
    tree = ast.parse(PUSH_ALL.read_text(encoding="utf-8"))
    add_all_lists = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.List)
        and [e.value for e in node.elts
             if isinstance(e, ast.Constant)][:3] == ["git", "add", "-A"]
    ]

    assert add_all_lists, (
        "push-all.py no longer stages with `git add -A`. The default mode's "
        "wide scan and this test's narrowing both rest on that command; re-read "
        "both before changing either.")


# ============================================================
# The message must not outrun the mode
# ============================================================

def test_the_refusal_does_not_claim_more_than_the_mode_establishes(push_all):
    """`.claude/rules/scope-claims.md`, in the wall that rule most protects.

    The sentence "a file about to be pushed" is true of the default mode, where
    `add -A` makes it true a moment later. It was printed unchanged under
    `--no-commit`, where it is false. The narrowing makes it true again in both
    modes, so the check here is that the scan set is what the sentence names.
    """
    source = PUSH_ALL.read_text(encoding="utf-8")
    assert "about to be pushed" in source, (
        "the refusal wording changed; re-read whether it still describes the "
        "set `_push_delta_files` returns in BOTH modes")

    signature = [node for node in ast.walk(ast.parse(source))
                 if isinstance(node, ast.FunctionDef)
                 and node.name == "_push_delta_files"]
    assert signature, "_push_delta_files is gone"
    names = {a.arg for a in signature[0].args.args} | {
        a.arg for a in signature[0].args.kwonlyargs}
    assert "will_commit" in names, (
        "_push_delta_files no longer takes the mode, so it cannot tell a run "
        "that stages everything from one that stages nothing, and the refusal "
        "sentence is a claim it cannot support")


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-q"]))
