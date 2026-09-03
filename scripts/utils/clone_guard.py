#!/usr/bin/env python3
"""The single source of truth for "am I in the main clone, or in a worktree?".

HELM is the main clone of the engine and the only place that may publish, install
or restart a daemon, or run a maintenance pass over the live data overlay. A YARD
is a git worktree of the same repository, on its own branch, checked out outside
the engine clone. Every guard in the HELM/YARD design rests on telling the two
apart, so the predicate lives here once and is imported, never reimplemented.

The signal is a property of git rather than an agreement of ours: it cannot be
faked, it cannot be forgotten, and it works in a worktree created a year from
now. MEASURED 2026-09-03 on this repository:

    main clone   <root>/.git                     is a DIRECTORY
                 rev-parse --git-dir             == rev-parse --git-common-dir

    worktree     <root>/.git                     is a FILE (a gitdir: pointer)
                 rev-parse --git-dir             <HELM>/.git/worktrees/<name>
                 rev-parse --git-common-dir      <HELM>/.git

An environment variable can be lost by a new shell and a marker file can be
absent because the step that writes it failed. Neither is used here. The earlier
revisions of the HELM/YARD plan built the write guard on a `.helm-root` marker
and on `CLAUDE_PROJECT_DIR`; both produce "this is HELM, allow it" when the thing
they read is simply missing, which is fail-OPEN in the one place that must fail
closed.

Shell callers go through ``scripts/lib/require-main-clone.sh``, which passes the
running script's own ``$0``. Never pass ``__file__`` of this module: a copy of
this file imported by a YARD script that was launched by absolute path from HELM
would answer for HELM and let the guard through. That is the exact scenario the
19 shell installers are guarded against.

Tests: tests/test_clone_guard.py
"""

import subprocess
import sys
from pathlib import Path


class CloneGuardError(RuntimeError):
    """The clone type could not be established.

    A `RuntimeError` rather than an `OSError` subclass on purpose: callers wrap
    filesystem work in `except OSError`, and a handler written for a missing
    file must not swallow "I cannot tell whether this is a worktree". Every
    caller in this module's contract treats it as a refusal, never as a pass.
    """


def _rev_parse(flag: str, path: Path) -> Path:
    """Run one `git rev-parse <flag>` with `cwd=path` and resolve the answer.

    git answers relative to the working directory it was given (`.git` at a
    repository root, `../.git` one level down) and absolutely from inside a
    worktree, so both shapes are normalised here rather than at each call site.
    """
    try:
        completed = subprocess.run(
            ["git", "rev-parse", flag],
            cwd=str(path), capture_output=True, text=True, check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError, OSError,
            NotADirectoryError) as exc:
        raise CloneGuardError(
            f"git rev-parse {flag} failed in {path}: {exc}") from exc
    answer = completed.stdout.strip()
    if not answer:
        raise CloneGuardError(f"git rev-parse {flag} returned nothing in {path}")
    candidate = Path(answer)
    if not candidate.is_absolute():
        candidate = Path(path) / candidate
    return candidate.resolve()


def is_main_clone(path: Path | str | None = None) -> bool:
    """True in the main clone, False in a worktree.

    Raises `CloneGuardError` when git cannot answer. There is deliberately no
    third return value and no default: "I could not tell" must reach the caller
    as an exception it has to handle, because a caller that reads a False-y
    answer as "not a worktree, carry on" is the fail-open shape this predicate
    exists to remove.
    """
    path = Path(path) if path is not None else Path.cwd()
    git_path = path / ".git"
    # Fast path, ~0 ms and no subprocess. Correct for a repository root, which
    # is what every caller in this workspace passes.
    if git_path.is_dir():
        return True
    if git_path.is_file():
        return False
    # No `.git` entry at all: a subdirectory, or somewhere outside a repository.
    # git is the authority.
    return _rev_parse("--git-common-dir", path) == _rev_parse("--git-dir", path)


def main_clone_path(path: Path | str | None = None) -> Path:
    """Absolute path of the main clone this checkout belongs to.

    Derived from `--git-common-dir`, so it is correct from HELM, from a YARD,
    and from any subdirectory of either. Never assembled by walking `..` a fixed
    number of times: the plan's second revision did that and produced the parent
    of the workspace root, which then blocked every write including the YARD's
    own.
    """
    return _rev_parse(
        "--git-common-dir", Path(path) if path is not None else Path.cwd()).parent


def require_main_clone(script_path: str) -> None:
    """Exit 2 unless this process is running from the main clone.

    `script_path` is the path of the SCRIPT BEING GUARDED (`__file__` of the
    caller, or `$0` from a shell wrapper), never this module's own `__file__`.

    Two locations are checked and both must be the main clone:

      * the script's own checkout, which catches `bash <YARD>/scripts/x.sh` run
        from a HELM shell. Without it, the guard reads the ambient cwd, answers
        "main clone", and a systemd unit is written pointing at a checkout that
        is deleted two days later. This is the authoritative signal, and an
        indeterminate answer here REFUSES.
      * the current working directory, which catches the mirror case: HELM's own
        script invoked from a shell sitting in a YARD.

    The two are not symmetric, and the asymmetry is deliberate. A cwd that is
    not inside any git repository at all cannot be a YARD, so it is skipped
    rather than refused. Refusing it would be fail-closed against a threat that
    does not exist while breaking every honest caller that runs from a temporary
    directory, the suite included, and a guard that refuses everything satisfies
    each refusal test and is removed within the week. Nothing is weakened: the
    script's own location is checked first and unconditionally, so there is no
    cwd a caller can choose that turns a YARD script into an allowed one.

    Refusal is loud. A silent exit is not acceptable here: a guard that never
    speaks is indistinguishable from a guard that never fires.
    """
    name = Path(script_path).name
    script_root = Path(script_path).resolve().parent.parent
    for location, label in ((script_root, "script"), (Path.cwd(), "cwd")):
        try:
            in_main = is_main_clone(location)
        except CloneGuardError as exc:
            if label == "cwd":
                continue  # outside any repository, so not a YARD. See docstring.
            print(f"{name}: cannot determine clone type ({label}={location}): "
                  f"{exc}\n  Refusing rather than guessing.", file=sys.stderr)
            sys.exit(2)
        if in_main:
            continue
        try:
            helm = str(main_clone_path(location))
        except CloneGuardError:
            helm = "<could not resolve>"
        print(
            f"{name}: this script runs from HELM (the main clone) only, not "
            f"from a YARD worktree.\n"
            f"  Detected {label}: {location}\n"
            f"  HELM: {helm}\n"
            f"  Change to HELM and run it there.",
            file=sys.stderr,
        )
        sys.exit(2)


if __name__ == "__main__":
    # Hand-run diagnostic. `python scripts/utils/clone_guard.py`
    print(f"cwd:             {Path.cwd()}")
    try:
        print(f"is_main_clone:   {is_main_clone()}")
        print(f"main_clone_path: {main_clone_path()}")
    except CloneGuardError as exc:
        print(f"undetermined:    {exc}")
        sys.exit(1)
