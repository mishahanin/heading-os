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

import os
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
    # Ask git ONLY about paths inside the repository. `git check-ignore`
    # exits 128 on a path outside it, which this function reports as "git
    # could not answer" -- and one such path poisoned the whole batch, so
    # every sweep raised. MEASURED 2026-08-29: with a worktree checked out
    # at `.claude/worktrees/hdr`, its `.venv/bin/python3.11` is a SYMLINK
    # to the uv interpreter under ~/.local, `not_ignored` resolved it, and
    # four sweeps died on a corpus of 31776 files. A path outside the repo
    # is not a path git ignores, so it simply is not in the answer.
    # A RELATIVE path is inside by construction, and both forms reach here:
    # `check-path-references.py` passes repo-relative strings while the tree
    # sweeps pass absolute ones. Testing the raw string against the repo prefix
    # answered False for every relative path, so the whole batch looked outside
    # and nothing was filtered. Git is handed the caller's own spelling either
    # way, because `-C repo` resolves a relative path against the repo anyway
    # and the answer must come back in the form the caller can match on.
    inside, walked = [], []
    repo_str = str(repo)
    for candidate in paths:
        text = str(Path(candidate))
        walked.append(text)
        # `Path(...) / text`, not `os.path.join`: the workspace forbids the
        # latter on any path it did not author, and this one comes from a
        # caller's walk. `Path.__truediv__` returns `text` unchanged when it is
        # already absolute, which is the same behaviour with a safer name.
        absolute = str(Path(repo_str) / text)
        if absolute == repo_str or absolute.startswith(repo_str + os.sep):
            inside.append(text)
    if not walked or not inside:
        return set()
    walked = inside
    payload = b"\0".join(p.encode() for p in walked) + b"\0"
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo), "check-ignore", "--stdin", "-z"],
            input=payload, capture_output=True, check=False,
        )
    except OSError:
        # Git could not be RUN at all, which is the same unanswered question as
        # a 128 and returns down the same path: `ignored_paths` turns it into
        # its named RuntimeError and `check-path-references.py` continues
        # unfiltered, both instead of a traceback out of a tree sweep.
        #
        # Retired 2026-09-02. The comment below used to read "Anything else
        # (128: not a git repository, git missing) means the question went
        # unanswered", filing a missing git among the RETURN CODES. There is no
        # return code when the executable is absent: `subprocess.run` raises
        # before a process is ever started, so the sentence claiming the case
        # was handled is precisely why nobody handled it. MEASURED 2026-09-02
        # with `PATH=/nonexistent`: `ignored_paths_or_none`, `ignored_paths`,
        # `not_ignored` and `tracked_paths` each died on `FileNotFoundError:
        # [Errno 2] No such file or directory: 'git'`.
        #
        # `OSError`, not `FileNotFoundError` alone, because "cannot ask git"
        # also arrives as a `PermissionError` (a git on PATH that is not
        # executable) and a `NotADirectoryError` (a file where a PATH component
        # should be a directory). All three are the same non-answer.
        #
        # Caught HERE, at this module's ONE git call site, rather than at each
        # of the four public functions: every one of them reaches git through
        # this line, and a per-call-site guard is what let the gap survive.
        return None
    # check-ignore exits 1 when nothing matched, which is a normal outcome here.
    # Anything else (128: not a git repository) means the question went
    # unanswered.
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
    # `abspath`, not `resolve`. `resolve` follows symlinks, and a symlink
    # inside the tree pointing outside it (a venv's interpreter) turned an
    # in-repo path into an out-of-repo one before git ever saw it. The
    # sweep is about paths in this tree, so the link is what it walks and
    # the link is what git should be asked about.
    unique = sorted({Path(os.path.abspath(p)) for p in paths})
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


class IndexUnreadable(Exception):
    """git could not be asked, or answered with nothing. No verdict is possible."""


def git_index_paths(root: Path | None = None) -> list[str]:
    """Every path in git's INDEX, read without losing a byte.

    Different question from `tracked_paths` above, which globs the filesystem
    and asks git what to ignore. This asks git what it is tracking, so a file
    staged but not yet committed is included -- the property a pre-commit gate
    needs, since pre-commit runs against staged content.

    Three details, each of which has its own way of going wrong:

    * ``-z``. Without it git C-quotes any path holding a non-ASCII byte, a quote
      or a newline, and a reader splitting on newlines drops those files while
      reporting a clean sweep over a corpus narrower than the one it names.
    * NO ``text=True``. Text mode applies universal newlines, so a filename
      containing a carriage return arrives with it silently turned into a line
      feed: a different path, reported as if it were the real one.
      `tests/test_a_reader_that_lost_a_byte_on_the_way_in.py` holds this.
    * ``surrogateescape``. A path is bytes on POSIX and need not be valid UTF-8.
      Strict decoding raises on a tree that git handles fine; ``replace`` would
      corrupt the name into one that matches nothing.

    Written 2026-09-02 as ONE implementation because there were about to be two.
    `check-gate-integrity.py` and `audit-rotation.py` had each grown their own
    copy of this reader within an hour, and both carried the same two defects.
    A fix that lands in one of N copies is this repository's dominant defect
    shape, 35 commits of the September campaign.
    """
    repo = ROOT if root is None else root
    try:
        proc = subprocess.run(["git", "ls-files", "-z"], cwd=repo,
                              capture_output=True, check=True)
    except (OSError, subprocess.CalledProcessError) as exc:
        raise IndexUnreadable(f"git ls-files failed: {exc}") from exc
    raw = proc.stdout.decode("utf-8", errors="surrogateescape")
    paths = [part for part in raw.split("\0") if part]
    if not paths:
        # Not "an empty repository". A listing that came back with nothing is a
        # failure, and reading it as "no paths to check" is how a scanner
        # reports clean over a tree it never enumerated.
        raise IndexUnreadable("git ls-files returned nothing")
    return paths


def read_sources(paths, vanished: list | None = None, *, errors: str = "strict"):
    """Yield `(path, text)` for each path still on disk, skipping and reporting
    the ones that disappeared between the walk and the read.

    A tree sweep gathers its paths first and reads them afterwards. In a
    workspace where several agents work against one checkout, a file can be
    created and deleted inside that window, and `read_text` then raises
    FileNotFoundError from inside the guard. MEASURED 2026-08-30: a scratch test
    file a parallel agent wrote under `tests/` and removed moments later killed
    `tests/test_subprocess_interpreter_guard.py` mid-walk, and the suite reported
    a guard violation where nothing had been violated.

    Skipping quietly would be the other half of the same defect. The sweep would
    then hold its verdict over a corpus that shrank underneath it and say
    nothing, which is the claim `.claude/rules/scope-claims.md` forbids: a
    narrowed check that prints like a complete one. So a skip WARNS, naming the
    files, and a caller that wants to put the count in its own assertion message
    passes a list to collect them.

    Only the vanished case is caught. A permission error, a decoding failure or a
    directory handed in where a file was expected is a real fault about a file
    that IS there, and still raises.
    """
    skipped: list[Path] = [] if vanished is None else vanished
    for path in paths:
        try:
            text = path.read_text(encoding="utf-8", errors=errors)
        except FileNotFoundError:
            skipped.append(path)
            continue
        yield path, text
    if skipped:
        import warnings

        names = ", ".join(str(p) for p in skipped)
        warnings.warn(
            f"{len(skipped)} path(s) vanished between the walk and the read and "
            f"were not scanned: {names}",
            stacklevel=2,
        )
