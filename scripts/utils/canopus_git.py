#!/usr/bin/env python3
"""The one place Canopus talks to git.

Approval under this standard is a human's COMMIT of the gate artifact, so the
question "was this hash approved" is a question about a repository, not about a
file. Answering it needs subprocess, and subprocess is exactly what
canopus_freeze.py may never import: the PreToolUse dispatcher loads that module
on every Write and Edit. So the git half lives here, and the hashing half stays
where it is.

Every function answers rather than raising. A missing git, a directory that is
not a repository, and a command that fails are all ordinary states of the world
for a tool that must run on a fresh public clone with no data overlay behind it.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Optional, Tuple

from scripts.utils.canopus_freeze import ANCHOR_PREFIX

COMMITTED = "committed"
UNCOMMITTED = "uncommitted"
NO_REPO = "no_repo"
NO_GIT = "no_git"


def git_output(root: Path, *arguments: str) -> Optional[str]:
    """Run a git command in *root*, or None when git cannot answer."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), *arguments],
            capture_output=True, text=True, timeout=30, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return proc.stdout if proc.returncode == 0 else None


def read_committed_anchor(artifact: Path) -> Tuple[str, Optional[str]]:
    """The approved hash recorded in the artifact's COMMITTED state.

    Four statuses, each kept distinct so the message can name the real reason
    rather than a generic one:

      COMMITTED    the artifact is tracked and HEAD carries a canopus-anchor line
      UNCOMMITTED  the artifact is untracked, or HEAD carries no such line
      NO_REPO      the artifact is not inside a git working tree
      NO_GIT       git is unavailable, or the command failed

    LAST committed line wins, matching read_anchor: a replaced approval appends
    rather than overwriting, so the artifact keeps the whole trail and the newest
    approval governs.
    """
    artifact = Path(artifact)
    directory = artifact.parent
    top = git_output(directory, "rev-parse", "--show-toplevel")
    if top is None:
        # Distinguishing "no git binary" from "not a repository" needs a second
        # call, and the caller reports both as APPROVAL UNVERIFIED. One extra
        # subprocess buys a truer message, and this path runs once per command.
        #
        # The probe runs WITHOUT -C, because `git -C <dir>` chdirs before it
        # parses anything else: with -C, a gate artifact whose directory has been
        # removed since the freeze fails both calls and reports "git is
        # unavailable" on a machine where git is fine.
        if git_output(Path.cwd(), "--version") is None:
            return (NO_GIT, None)
        return (NO_REPO, None)
    repo = Path(top.strip())
    try:
        rel = artifact.resolve().relative_to(repo.resolve()).as_posix()
    except ValueError:
        return (NO_REPO, None)
    blob = git_output(repo, "show", f"HEAD:{rel}")
    if blob is None:
        return (UNCOMMITTED, None)
    found: Optional[str] = None
    for line in blob.splitlines():
        stripped = line.strip()
        if stripped.startswith(ANCHOR_PREFIX):
            value = stripped[len(ANCHOR_PREFIX):].strip().lower()
            if value:
                found = value
    return (COMMITTED, found) if found else (UNCOMMITTED, None)
