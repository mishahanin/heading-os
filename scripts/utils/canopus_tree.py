#!/usr/bin/env python3
"""The working tree's state: what an attestation perishes on.

An attestation used to bind to the FROZEN bytes and to nothing else, and the
code under test is by design not frozen, so a green record survived every change
to the thing the contract exists to judge. Measured: break the implementation,
run nothing at all, and `verify` still read ATTESTED.

The first design recorded the files the run IMPORTED. It was withdrawn before
any freeze on a measurement taken in this repository:
`tests/test_alert_no_import_cycle.py` deletes every module whose name contains
`alert`, including its own, so a file that ran was already absent from
`sys.modules` at session finish. `exec(open(path).read())`, a fixture that tears
a module down, a script invoked as a subprocess, and a JSON fixture are the same
shape. There is no set to arrange here, because the state is the tree.

Defined relative to git rather than by walking the filesystem, because a walk
cannot know what is ignored, and a state that carried `.venv` and every build
artifact would be permanently red. The cost of that choice is stated in the
design's ceiling: a change to a gitignored file the run reads does not perish
the record.
"""
import hashlib
from pathlib import Path
from typing import Optional

from scripts.utils.canopus_freeze import TREE_RECIPE
from scripts.utils.canopus_git import git_output


def _porcelain_paths(raw: str) -> list[str]:
    """Every path in a `--porcelain=v1 -z` status, renames counted at both ends.

    The `-z` form exists so a path never has to be quoted or escaped, which is
    why this parser splits on NUL and never on whitespace: `a file.py` and a
    path with a newline in it both survive.

    A rename or a copy ships TWO fields, the new path then the old one. A parser
    that read only the first would lose the old path, and the old path is the
    one whose disappearance a reader needs to see.
    """
    fields = [field for field in raw.split("\0") if field]
    paths: list[str] = []
    index = 0
    while index < len(fields):
        entry = fields[index]
        index += 1
        if len(entry) < 4:
            continue
        code, path = entry[:2], entry[3:]
        paths.append(path)
        if ("R" in code or "C" in code) and index < len(fields):
            paths.append(fields[index])
            index += 1
    return paths


def tree_state(root: Path) -> Optional[dict]:
    """`{"recipe", "head", "dirty"}`, or None when git cannot answer.

    `dirty` maps every path git reports as changed, added, deleted or untracked
    to the sha256 of its current bytes, or to None for a path that is gone or
    unreadable. Together with `head` that is a complete description of the tree
    relative to git, covering Python, YAML, JSON, templates and markdown alike.

    `--untracked-files=all` is load-bearing rather than thorough. Default
    porcelain collapses an untracked directory to `newdir/` and stops, and a
    directory name carries no hash, so a state recording it would not move when
    the file inside it changed. A new module dropped into a new package is
    exactly the shape a builder reaches for.

    None rather than an exception, and None rather than an empty state: this is
    read from the recorder at session finish, where a raise takes the session's
    exit code with it, and `build_attestation` reads None as "this run could not
    describe the tree it ran against" and refuses.
    """
    root = Path(root)
    head = git_output(root, "rev-parse", "HEAD")
    if head is None:
        return None
    status = git_output(root, "status", "--porcelain=v1",
                        "--untracked-files=all", "-z")
    if status is None:
        return None
    dirty: dict = {}
    for rel in _porcelain_paths(status):
        try:
            dirty[rel] = hashlib.sha256((root / rel).read_bytes()).hexdigest()
        except (OSError, ValueError):
            # A deleted path, an unreadable one, and a directory all land here.
            # None is a recorded GAP rather than a dropped entry: dropping it
            # would make a deletion read as a smaller, wholly clean tree, which
            # is the greener of the two.
            dirty[rel] = None
    return {"recipe": TREE_RECIPE, "head": head.strip(), "dirty": dirty}
