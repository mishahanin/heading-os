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

Both of those claims were FALSE when wire 2.1 shipped, and are recorded here as
repairs rather than left reading like they were always true: `scripts/canopus.py`
ran its own `rev-parse HEAD` subprocess until wire 2.2 moved it here as
`head_sha`, and `_git_available` called `Path.cwd()` unguarded, which raises
FileNotFoundError when the process working directory has been deleted.
"""
from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path
from typing import NamedTuple, Optional, Tuple

from scripts.utils.canopus_freeze import (
    ANCHOR_MISSING,
    ANCHOR_PREFIX,
    ANCHOR_RECORDED,
    ANCHOR_UNBOUND,
    ANCHOR_UNRECORDED,
    APPROVAL_UNVERIFIED,
    BINDING_BROKEN,
    REPO_ABSENT,
    REPO_PRESENT,
    REPO_UNKNOWN,
    anchor_state,
    approval_state,
    parse_anchor_waiver,
    read_anchor_waiver,
    repo_binding_state,
)

COMMITTED = "committed"
UNCOMMITTED = "uncommitted"
NO_REPO = "no_repo"
NO_GIT = "no_git"


def _child_env() -> dict:
    """The parent environment with every GIT_* variable removed.

    A blanket prefix, not a named denylist. Naming the variables you happened to
    think of is the defect this project has hit seven times: a guard that covers
    the case in front of its author rather than the class. GIT_DIR is merely the
    cheapest of them, and GIT_WORK_TREE, GIT_COMMON_DIR, GIT_INDEX_FILE,
    GIT_OBJECT_DIRECTORY, GIT_CEILING_DIRECTORIES and
    GIT_DISCOVERY_ACROSS_FILESYSTEM redirect discovery just as well. The next
    release of git may add another, and a prefix covers it in advance.

    This is a CORRECTNESS fix before it is a security one. When canopus runs
    inside a git hook, git itself exports GIT_DIR and GIT_INDEX_FILE pointing at
    the HOOK's repository. The engine's pre-commit and pre-push hooks run the
    suite, the suite starts the gate, and the gate then asked the wrong
    repository about the anchor.

    Nothing is lost by the scrub: every command this module runs is a local read
    (rev-parse, show, rev-list) and none touches the network.
    """
    return {key: value for key, value in os.environ.items()
            if not key.startswith("GIT_")}


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
            env=_child_env(),
        )
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    return proc.stdout if proc.returncode == 0 else None


def _git_available() -> bool:
    """Is there a git binary at all, asked away from the directory that failed.

    Be exact about the mechanism, because the comment this replaces in
    read_committed_anchor said "the probe runs WITHOUT -C" and git_output ALWAYS
    inserts `-C <root>`. What actually matters is WHICH directory -C names.
    `git -C <dir>` chdirs before it parses anything else, so probing with the
    directory that just failed fails again and reports "git is unavailable" on a
    machine where git is fine. The probe therefore names a directory known to
    exist.

    Path.cwd() is inside the guard because it RAISES FileNotFoundError when the
    process working directory has been deleted, and this module's contract is
    that every function answers. The filesystem root is the fallback because it
    is the one directory that cannot have been removed underneath us.
    """
    try:
        probe = Path.cwd()
    except OSError:
        probe = Path(os.sep)
    return git_output(probe, "--version") is not None


def repo_identity(directory: Path) -> Tuple[str, str]:
    """Which repository *directory* belongs to, identified path-independently.

    Returns (REPO_PRESENT, digest), (REPO_PRESENT, "") for a repository with no
    commits, (REPO_ABSENT, "") outside a repository, or (REPO_UNKNOWN, "") when
    there is no git binary.

    The identity is sha256 over the sorted root commits, newline-joined. A
    toplevel PATH would have been cheaper and is wrong: a relocated repository is
    the same repository, and this workspace has been relocated once already. A
    merged history can carry more than one root commit, so the whole sorted set
    is hashed rather than one line of output.

    An empty identity for a repository with no commits is deliberate and is NOT
    a digest of the empty set. The first commit into such a repository is the
    approval act itself; recording a digest now would change it at the exact
    moment a human approved. Callers refuse to freeze against it.

    What this identity is NOT, named rather than discovered later. The walk is
    HEAD-scoped (`rev-list --max-parents=0 HEAD`), not `--all`, so it is stable
    against new refs and it CHANGES in three cases that are not a substituted
    repository: checking out an orphan branch with its own root, merging in a
    history that carries another root commit, and a shallow clone, whose grafted
    boundary commit reads as a root until `git fetch --unshallow`. Each reads as
    BINDING_BROKEN and costs the operator one release-and-re-freeze. `--all` was
    the alternative and is worse: every fetched branch would move the identity.
    """
    top = git_output(Path(directory), "rev-parse", "--show-toplevel")
    if top is None or not top.strip():
        # Empty output on exit 0 is answered as "not a repository" rather than
        # passed on. `Path("")` is `Path(".")`, so the rev-list below would run
        # against whatever repository the PROCESS happens to sit in and hand back
        # an unrelated identity labelled REPO_PRESENT. No git release on this
        # machine was found to produce it, which is the point: the guard costs one
        # comparison and removes the whole class rather than the case in front of
        # its author.
        return (REPO_ABSENT, "") if _git_available() else (REPO_UNKNOWN, "")
    roots = git_output(Path(top.strip()), "rev-list", "--max-parents=0", "HEAD")
    lines = sorted(line.strip() for line in (roots or "").splitlines() if line.strip())
    if not lines:
        return (REPO_PRESENT, "")
    return (REPO_PRESENT,
            hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest())


def head_sha(root: Path) -> str:
    """Current HEAD, or "" outside a repository. A secondary anchor only.

    Moved here from scripts/canopus.py, which ran its own subprocess and made
    this module's "the one place Canopus talks to git" docstring false. The old
    copy printed a diagnostic on OSError; git_output answers None instead, and
    the value is explicitly secondary, recorded in the manifest and never
    compared against anything.
    """
    out = git_output(Path(root), "rev-parse", "HEAD")
    return out.strip() if out else ""


def read_committed_text(artifact: Path) -> Tuple[str, Optional[str]]:
    """The artifact's text as HEAD holds it, with the status that explains a None.

      COMMITTED    HEAD carries a copy of this file; the text is its blob
      UNCOMMITTED  the artifact is untracked, or HEAD has no copy of it
      NO_REPO      the artifact is not inside a git working tree
      NO_GIT       git is unavailable, or the command failed

    Extracted so the anchor hash and the waiver marker are read from the SAME
    copy by the same route. They were not: the hash came from HEAD and the waiver
    from the working file, so `sed -i` on one line of an uncommitted diff took
    CONTRACT WAIVED off the evidence page while LOCK HELD and APPROVED stood.
    Two readers of one artifact is how the halves of one approval come to
    disagree.

    Note what COMMITTED means here and does not: HEAD carries the FILE, not
    necessarily any canopus line in it. The callers below draw that second
    distinction themselves, because they draw it differently.
    """
    artifact = Path(artifact)
    directory = artifact.parent
    top = git_output(directory, "rev-parse", "--show-toplevel")
    if top is None or not top.strip():
        # `not top.strip()` is the same guard `repo_identity` carries, and it is
        # here because wire 2.2 added it there and not to its sibling. That is
        # the NINTH time on this project that a guard has been applied to one
        # function and not the one beside it, which is why the class matters
        # more than the case: empty output on exit 0 makes `Path("")` into
        # `Path(".")`, so `git show HEAD:<rel>` below would read the AMBIENT
        # repository and hand back a hash labelled COMMITTED. The call site is
        # live rather than theoretical: `cmd_approve` calls read_committed_anchor
        # directly, so it is not covered by the binding check `resolve_anchor`
        # runs first.
        #
        # Distinguishing "no git binary" from "not a repository" needs a second
        # call, and the caller reports both as APPROVAL UNVERIFIED. One extra
        # subprocess buys a truer message, and this path runs once per command.
        #
        # The probe names a directory known to exist rather than the one that
        # just failed, because `git -C <dir>` chdirs before it parses anything
        # else: probing with a removed directory fails both calls and reports
        # "git is unavailable" on a machine where git is fine. _git_available
        # holds that reasoning, and guards Path.cwd() as well.
        if not _git_available():
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
    return (COMMITTED, blob)


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
    status, blob = read_committed_text(artifact)
    if status != COMMITTED or blob is None:
        return (status, None)
    found: Optional[str] = None
    for line in blob.splitlines():
        stripped = line.strip()
        if stripped.startswith(ANCHOR_PREFIX):
            value = stripped[len(ANCHOR_PREFIX):].strip().lower()
            if value:
                found = value
    # A tracked artifact whose HEAD copy carries no anchor line is UNCOMMITTED on
    # this axis: the approval is what is being asked about, not the file.
    return (COMMITTED, found) if found else (UNCOMMITTED, None)


def resolve_anchor_waiver(artifact: Path, root_digest: str) -> str:
    """The waiver recorded beside *root_digest*, COMMITTED copy first.

    Mirrors how the anchor hash itself is resolved: the repository governs
    whenever it can answer, and the working file governs only where there is no
    committed copy to consult (an untracked artifact, a folder outside any
    repository, no git). The fallback is what keeps a plain-folder operator's
    waiver visible; it is never preferred over HEAD.

    Bound to a digest, whichever copy answers, so a waiver from an earlier retake
    is never reported against today's freeze. Full digests, compared whole.

    Answers rather than raising, like everything else in this module: the surfaces
    that call it are reporting surfaces, and one of them is the evidence page an
    operator signs off from.
    """
    status, blob = read_committed_text(artifact)
    if status == COMMITTED and blob is not None:
        return parse_anchor_waiver(blob, root_digest)
    return read_anchor_waiver(Path(artifact), root_digest)


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

    ONE branch breaks that rule, and it is named here rather than left for the
    next reader to discover. Under ANCHOR_UNBOUND, `source` carries the REPOSITORY
    status from repo_identity, because read_committed_anchor is never reached on
    that branch: the binding is judged first, deliberately. The two vocabularies
    already share `no_repo` and `no_git`; `in_repo` is the one value that has no
    counterpart in the read_committed_anchor set. `cmd_verify`'s only reader of
    this field sits inside an ANCHOR_RECORDED branch, so nothing misreads it
    today, and a field whose documented meaning is false is how the next one does.

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

    `verify --anchor <override>` is bound too, and that is the intended answer
    rather than an oversight. The override travels through anchor_state, so an
    override naming an artifact in a DIFFERENT repository than the freeze recorded
    reads BINDING_BROKEN and reddens. The binding is a fact about the artifact the
    freeze was TAKEN against, and an artifact in another repository is not that
    one, however plausible its contents look.
    """
    anchor, working_status, working_value = anchor_state(manifest, override)
    if not anchor:
        return AnchorResolution(anchor, working_status, working_value,
                                APPROVAL_UNVERIFIED,
                                "the manifest carries no anchor", "")
    repo_status, repo_id = repo_identity(Path(anchor).parent)
    binding, binding_reason = repo_binding_state(manifest, repo_status, repo_id)
    if binding == BINDING_BROKEN:
        # Before any fallback, deliberately. The working-copy fallback is the
        # thing being closed, and a fallback that runs first cannot be closed by
        # a check that runs after it. The approval axis goes UNVERIFIED too: a
        # committed hash read out of a repository the freeze was not taken
        # against is not an approval of this freeze.
        return AnchorResolution(anchor, ANCHOR_UNBOUND, None,
                                APPROVAL_UNVERIFIED, binding_reason, repo_status)
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
