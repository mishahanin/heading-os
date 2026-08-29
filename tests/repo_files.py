#!/usr/bin/env python3
"""One implementation of "the files in this repository", for tree-sweeping tests.

Many tests sweep the whole tree and hold the result against a declared registry:
every frontmatter reader, every provider endpoint, every path-mangling slug rule.
Each one walked the tree with its own `rglob` and its own hand-written skip list.

A hand-written skip list cannot know what git ignores, and the gap is not
theoretical. MEASURED 2026-08-29 on the CI-shaped suite: with an agent worktree
checked out at `.claude/worktrees/agent-probe` (a path `.gitignore` line 347
already covers), the suite went from **15609 passed, 0 failed** to **8 failed**.
A worktree is a full second copy of the tree, so every sweep saw each file twice
and reported the copy as a new, undeclared site:

    new frontmatter regex disagreeing with the shared grammar:
      .claude/worktrees/agent-probe/scripts/merge-contacts.py on ['CRLF throughout']

That message names a file the operator cannot fix and does not mention the
worktree, so the next reader chases a defect that is not there. The quieter half
is worse: while the copy is present the corpus is doubled, and a real new defect
in the real tree is one line inside twice the noise.

`git check-ignore` is the only thing that knows the answer, so it is asked once
per sweep and the answer is shared. Callers pass glob patterns relative to the
repository root; `**` matches zero or more directories, so `scripts/**/*.py`
covers `scripts/a.py` as well as `scripts/utils/a.py`.

Consumed by the tree-sweeping tests; `tests/test_a_walker_that_never_asked_git.py`
holds the rule that a new one uses this module rather than its own walk.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def ignored_paths(paths, root: Path | None = None) -> set[str]:
    """The subset of ``paths`` git ignores, as absolute path strings.

    One `git check-ignore` call for the whole batch. The parameter is a
    sequence, not a directory, so a caller that has already walked does not walk
    twice.
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
    # Anything else (128: not a git repository, git missing) is NOT degraded
    # into "nothing is ignored" -- that is the silent failure this module
    # exists to prevent, and it would restore the defect it was written for.
    if proc.returncode not in (0, 1):
        raise RuntimeError(
            f"git check-ignore failed ({proc.returncode}) in {repo}: "
            f"{proc.stderr.decode(errors='replace')}"
        )
    return {chunk.decode() for chunk in proc.stdout.split(b"\0") if chunk}


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
    unique = sorted({p.resolve() for p in walked})
    ignored = ignored_paths(unique, repo)
    return [p for p in unique if str(p) not in ignored]


def tracked_python_files(directories=("scripts", ".claude"), root: Path | None = None):
    """Every `.py` file under ``directories`` that git does not ignore."""
    return tracked_paths([f"{d}/**/*.py" for d in directories], root)
