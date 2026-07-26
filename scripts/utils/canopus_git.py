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
from typing import NamedTuple, Optional, Tuple

from scripts.utils.canopus_freeze import (
    ANCHOR_MISSING,
    ANCHOR_PREFIX,
    ANCHOR_RECORDED,
    ANCHOR_UNRECORDED,
    APPROVAL_UNVERIFIED,
    anchor_state,
    approval_state,
)

COMMITTED = "committed"
UNCOMMITTED = "uncommitted"
NO_REPO = "no_repo"
NO_GIT = "no_git"


def git_output(root: Path, *arguments: str) -> Optional[str]:
    """Run a git command in *root*, or None when git cannot answer.

    ValueError is in the handler because subprocess.run raises it, and it is
    neither an OSError nor a SubprocessError. Two routes reach it, both measured
    rather than imagined: an argument carrying an embedded NUL byte (a manifest
    whose anchor directory holds one made freeze_gate raise
    `ValueError: embedded null byte`), and `text=True` decoding, where a
    non-UTF-8 gate artifact makes git's stdout raise UnicodeDecodeError, a
    ValueError subclass. On the second route read_anchor reaches the same file
    first, and that is not a mask: it was the crash, by a different door, until
    read_anchor's own handler was widened to (OSError, ValueError). Both doors
    are shut now, and each is pinned by its own gate test. Calling one of them
    masked would tell a maintainer the route is dormant when it was live.

    This module's contract is that every function ANSWERS, and freeze_gate runs
    at every pytest session start. An exception escaping here does not report a
    broken lock, it crashes the harness that was supposed to report it.
    """
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), *arguments],
            capture_output=True, text=True, timeout=30, check=False,
        )
    except (OSError, subprocess.SubprocessError, ValueError):
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


class AnchorResolution(NamedTuple):
    """Everything four call sites need about one manifest's anchor.

    Derived once, in one place. Two hand-rolled copies of this precedence is how
    `verify` and the test gate come to disagree about whether a lock is held.

    `source` is the raw read_committed_anchor status, and it is here because a
    caller cannot otherwise tell WHICH copy `value` came from: under COMMITTED
    the hash is HEAD's, and under NO_REPO or NO_GIT it is the working file's,
    and both spell ANCHOR_RECORDED. `cmd_verify` labels its detail line with a
    working-tree path, so it has to say which copy it read or an operator who
    opens that file finds a different hash and no explanation.

    SIX fields, and read them by NAME. `source` has a default so existing
    construction sites did not have to change, but a default does not restore
    tuple arity: whole-tuple unpacking into five names raises, and equality
    against a five-tuple is False. No consumer does either today, and none
    should start; the reason this is a NamedTuple rather than a dataclass is
    that three call sites read `.anchor, .status, .value` and nothing more.
    """
    anchor: str
    status: str
    value: Optional[str]
    approval: str
    approval_reason: str
    source: str = ""


def resolve_anchor(manifest: dict, override: Optional[str] = None) -> AnchorResolution:
    """Resolve the anchor, with the COMMITTED value governing when it exists.

    Precedence, and it is the load-bearing decision of this slice: the REPOSITORY
    governs whenever there is one, and the working file governs only under
    no_repo and no_git, where there is nothing to consult.

    The first half closes the hole this wire exists to close, where a line
    appended to the working file reached LOCK HELD. The second half is what keeps
    the tool usable for an operator whose gate artifact is a file in a folder. The
    approval axis states which path was taken, so the fallback can never be read
    as the verified one.

    The approval axis binds to the manifest's STORED root, while the lock axis
    binds to the RECOMPUTED one, and the asymmetry is chosen rather than
    accidental. Approval is a fact about the freeze that was taken; the lock is a
    fact about the tree right now. So `verify` can legitimately print APPROVED
    beside LOSS OF LOCK, and that pair reads "a human approved this freeze, and
    the contract has moved since". Binding both to the recomputed root would
    collapse the two into one undifferentiated red and lose which of them failed.
    """
    anchor, working_status, working_value = anchor_state(manifest, override)
    if not anchor:
        return AnchorResolution(anchor, working_status, working_value,
                                APPROVAL_UNVERIFIED,
                                "the manifest carries no anchor", "")
    committed_status, committed_hash = read_committed_anchor(Path(anchor))
    approval, reason = approval_state(
        manifest.get("root") or "", committed_status, committed_hash
    )
    if committed_status == COMMITTED and committed_hash:
        if working_status == ANCHOR_MISSING:
            # `git show HEAD:<rel>` is existence-blind, so a committed approval
            # survives the artifact being deleted. Letting the committed value
            # govern here would report LOCK HELD over a vanished anchor and make
            # cmd_verify's "anchor is gone" line unreachable inside a repository.
            # A missing anchor is evidence and stays red; the axis still reports
            # what HEAD holds.
            return AnchorResolution(
                anchor, ANCHOR_MISSING, None, approval,
                f"{reason or 'the committed artifact records ' + committed_hash}; "
                f"the artifact itself is gone",
                committed_status,
            )
        return AnchorResolution(anchor, ANCHOR_RECORDED, committed_hash,
                                approval, reason, committed_status)
    if committed_status == UNCOMMITTED:
        # Deliberately NOT the working file. There is a repository, it was asked,
        # and the approval is not in it. Falling back here is what would let a
        # line appended to the working copy reach LOCK HELD, in exactly the
        # situation where the tool can do better. ANCHOR_UNRECORDED resolves to
        # the existing amber LOCK UNCONFIRMED, which is already the vocabulary
        # for "no durable approval has been written down".
        return AnchorResolution(anchor, ANCHOR_UNRECORDED, None, approval, reason,
                                committed_status)
    return AnchorResolution(anchor, working_status, working_value, approval, reason,
                            committed_status)
