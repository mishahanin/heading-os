#!/usr/bin/env python3
"""One implementation of "the files in this repository", for tree sweeps.

A sweep that walks the tree and holds the result against a registry needs to
know what git ignores, and a hand-written skip list cannot know that. This
module asks git once per sweep and shares the answer.

MEASURED 2026-08-29 on the CI-shaped suite: with an agent worktree checked out
at `.claude/worktrees/agent-probe`, a path `.gitignore` already covers, the
suite went from 15609 passed / 0 failed to 8 failed. A worktree is a full second
copy of the tree, so every sweep saw each file twice and named the copy as an
undeclared site, in a message pointing at a file the operator cannot fix.

WHY IT LIVES UNDER `scripts/utils/` AND NOT UNDER `tests/`. It was written in
`tests/repo_files.py` on 2026-08-29 and fixed the twenty test sweeps only. The
same defect was still live in PRODUCTION code the same day: measured on this
repository, `scripts/classification-health.py::walk_workspace` reported 2363
files, of which git ignores 427 -- eighteen percent of a report the operator
reads to decide whether the engine/data split is holding. Production cannot
import from `tests/`, so leaving the implementation there guaranteed a second
copy, and a second copy is the one that stops being fixed. `tests/repo_files.py`
is now a re-export of this module.

Callers pass glob patterns relative to the repository root; `**` matches zero or
more directories, so `scripts/**/*.py` covers `scripts/a.py` as well as
`scripts/utils/a.py`.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent


def ignored_paths_or_none(paths, root: Path | None = None) -> set[str] | None:
    """The subset of ``paths`` git ignores, or ``None`` when git could not say.

    One `git check-ignore` call for the whole batch. The parameter is a
    sequence, not a directory, so a caller that has already walked does not walk
    twice.

    WHY ``None`` AND NOT AN EMPTY SET. The two are the whole difference between
    "git says nothing here is ignored" and "git could not answer", and a caller
    that reads the second as the first filters nothing and reports a clean
    sweep. `.claude/rules/scope-claims.md` names this exact shape and its
    resolution: return None, so a caller must decide in a visible line.

    Two callers in this repository need opposite things, which is why both forms
    exist rather than one being imposed. `classification-health.py` must RAISE:
    an unfiltered classification report is 427 wrong rows presented as fact.
    `check-path-references.py` must CONTINUE unfiltered: not filtering makes it
    report MORE candidate dangling paths, which is the over-reporting direction
    scope-claims asks for. What must never happen again is each of them spelling
    the git call itself; the last time two copies existed they drifted to
    different contracts and only one got fixed.
    """
    repo = ROOT if root is None else root
    walked = [str(Path(p)) for p in paths]
    if not walked:
        return set()
    payload = b"\0".join(p.encode() for p in walked) + b"\0"
    proc = subprocess.run(
        ["git", "-C", str(repo), "check-ignore", "--stdin", "-z"],
        input=payload, capture_output=True, check=False,
    )
    # check-ignore exits 1 when nothing matched, which is a normal outcome here.
    # Anything else (128: not a git repository, git missing) means the question
    # went unanswered.
    if proc.returncode not in (0, 1):
        return None
    return {chunk.decode() for chunk in proc.stdout.split(b"\0") if chunk}


def ignored_paths(paths, root: Path | None = None) -> set[str]:
    """The subset of ``paths`` git ignores. Raises when git could not answer.

    The default, and what every sweep should use. Degrading into "nothing is
    ignored" is the silent failure this module exists to prevent, and it would
    restore the defect it was written for.
    """
    repo = ROOT if root is None else root
    result = ignored_paths_or_none(paths, repo)
    if result is None:
        raise RuntimeError(
            f"git check-ignore failed in {repo}: the sweep cannot know what to "
            f"skip, and reporting the unfiltered tree would present ignored "
            f"files as findings"
        )
    return result


def not_ignored(paths, root: Path | None = None) -> list[Path]:
    """``paths``, in sorted order, with everything git ignores removed.

    The shape a caller that has ALREADY walked needs. `tracked_paths` below
    globs for you; this one does not, so a sweep with its own traversal rules
    (a `rglob("*")` plus a hidden-directory rule, say) can keep them and still
    ask git the one question a skip list cannot answer.
    """
    repo = ROOT if root is None else root
    unique = sorted({Path(p).resolve() for p in paths})
    ignored = ignored_paths(unique, repo)
    return [p for p in unique if str(p) not in ignored]


def tracked_paths(patterns, root: Path | None = None, files_only: bool = True):
    """Every path matching ``patterns`` under ``root`` that git does not ignore.

    ``patterns`` are glob patterns relative to the repository root. The result is
    sorted, so a sweep reports its findings in a stable order.
    """
    repo = ROOT if root is None else root
    walked: list[Path] = []
    for pattern in patterns:
        walked.extend(repo.glob(pattern))
    if files_only:
        walked = [p for p in walked if p.is_file()]
    # A path can match two patterns; dedupe before asking git about it.
    return not_ignored(walked, repo)


def tracked_python_files(directories=("scripts", ".claude"), root: Path | None = None):
    """Every `.py` file under ``directories`` that git does not ignore."""
    return tracked_paths([f"{d}/**/*.py" for d in directories], root)
