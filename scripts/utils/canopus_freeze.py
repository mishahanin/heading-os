#!/usr/bin/env python3
"""Canopus freeze primitive: hashing, manifest construction, verification.

The Canopus standard freezes the test contract before a build starts so the
builder cannot move the target it is measured against. This module is the pure
logic half.

Three layers, and the distinctions are load-bearing:

  * The PreToolUse deny is a CONVENIENCE. It only sees Write, Edit, MultiEdit,
    and NotebookEdit tool calls, so a Bash `sed -i`, a `python -c`, or a subagent
    with its own toolset walks straight past it. It exists to refuse the write at
    the moment of the attempt and save a wasted loop iteration.
  * The manifest is the GUARANTEE. Verification recomputes digests from disk and
    catches a change made by any route, including routes nobody anticipated.
  * The test gate is what makes the guarantee FIRE. tests/conftest.py runs the
    check at pytest session start and scripts/run-tests.py runs it before the
    suite, so a build cannot reach green while its contract is moved — not even
    through the bare `pytest tests/test_thing.py` inner loop. An unrun verify
    fails 100% of the time no matter how well its expected value is protected.

Never reason that the deny makes verification optional.

The realistic adversary is not an evader. The builder is Claude, and the failure
this catches is tampering by helpfulness: the model hits a red assertion,
concludes in good faith that the assertion is wrong, and edits it. A verification
that merely runs catches that completely.

Recipe `canopus-freeze-v3`, named in every manifest so a future algorithm change
breaks loudly instead of silently:

    file digest = sha256(LF-normalized bytes)
    dir digest  = sha256("".join(f"{relpath}\\n" for relpath in sorted members))
    root hash   = sha256(canonical JSON of {recipe, anchor, files, dirs, baseline})

where a directory's members are those whose BASENAME matches one of the entry's
recorded `names` patterns.

Per-file bytes are LF-normalized (\\r\\n -> \\n) so a CRLF working copy and a
fresh LF checkout agree, matching the recipe already proven in
scripts/verify-skills-lock.py. The root hash covers the recipe, the anchor path,
and the content maps. Neither the label, the freeze timestamp, nor the recorded
git sha enters it, so re-freezing identical content against the same anchor
yields an identical root hash. That is deliberate: identical content means
nothing was tampered with, and the re-freeze is recorded in the ledger anyway.

Stdlib only (plus scripts.utils.atomic, itself stdlib only), and never
subprocess: .claude/hooks/_dispatch.py imports this module on every Write/Edit
and must not drag the workspace utility chain in.
"""
from __future__ import annotations

import fnmatch
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Iterable, Optional, Sequence, Tuple

from scripts.utils.atomic import atomic_write_text

RECIPE = "canopus-freeze-v3"
FREEZE_DIRNAME = ".canopus"
FREEZE_FILENAME = "freeze.json"
HISTORY_FILENAME = "history.jsonl"
ANCHOR_PREFIX = "canopus-anchor:"

# Tool-generated caches that live INSIDE a source tree. A recursive freeze that
# captured these would bind the lock to artifacts no version control tracks: the
# build loop rewrites them on its own, and any fresh checkout or cache clean
# removes them, so the composition digest would report LOSS OF LOCK for a change
# nobody made. Measured at the first real use of the tool on itself, 2026-07-25.
#
# A named set rather than "ask the VCS what it ignores": this module is imported
# by the PreToolUse dispatcher on every write and stays stdlib-only, never
# calling subprocess. The boundary is deliberate and stated rather than implied.
# An ignored artifact outside this set still freezes, and discovering one is a
# reason to widen the set, never a reason to route around the lock.
CACHE_DIRNAMES = frozenset({
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
})
CACHE_SUFFIXES = frozenset({".pyc", ".pyo"})

# Which basenames a directory guard watches, as fnmatch patterns.
#
# A guard is a question, and the three questions are different. A frozen
# directory asks "did anything at all move in here", so it watches everything.
# An ANCESTOR of a frozen path asks the narrow question the guard was invented
# for: did a conftest.py appear above the contract. That one file is what pytest
# imports without being told to, and it is where a stub reaches sys.path. The
# TREE ROOT asks a third question, because pyproject declares it on sys.path
# (`pythonpath = ["."]`): did an importable module appear at the entry the
# contract's own imports resolve against.
#
# The narrowing is not timidity, it is what makes the guard usable. Watching the
# full composition of an ancestor put 201 of this repository's 296 test files
# under a write deny and made the builder's next ordinary unit test report LOSS
# OF LOCK. A guard that fires on the builder doing its job is a guard that gets
# routed around, and a routed-around guard protects nothing.
GUARD_NAMES_ALL = ("*",)
GUARD_NAMES_ANCESTOR = ("conftest.py",)
GUARD_NAMES_TREE_ROOT = ("*.py",)

# Stated rather than implied, because the boundary is where the next hole lives:
# composition lists FILES, so a PACKAGE DIRECTORY appearing at the tree root
# (`target/__init__.py` shadowing an installed `target`) is not caught, and
# neither is a module dropped into another in-tree sys.path entry such as
# tests/. Both are closed by practice rather than by this primitive: the
# contract lives in its own directory under tests/contract/, which freezes
# RECURSIVELY, so anything appearing beside it is caught by content and by
# composition alike. Widening the guard to cover them is a reason to change this
# set, never a reason to route around the lock.


class FreezeError(Exception):
    """A freeze operation was refused."""


# ============================================================
# Hashing
# ============================================================

def file_digest(path: Path) -> str:
    """sha256 over LF-normalized file bytes."""
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def _members(
    directory: Path, *, recursive: bool, names: Sequence[str] = GUARD_NAMES_ALL
) -> list[Path]:
    """Regular files in *directory* whose basename matches *names*, sorted.

    Symlinks are excluded (the workspace forbids them), anything under the
    freeze state directory is excluded so the manifest never hashes itself, and
    tool-generated caches are excluded so the lock never binds to an artifact the
    build regenerates (see CACHE_DIRNAMES).

    *names* is a tuple of fnmatch patterns matched against the BASENAME, so a
    recursive guard's default `("*",)` keeps every nested member while an
    ancestor guard keeps only the one file it has a reason to watch. Matching the
    basename rather than the relative path is what lets the same filter serve a
    flat listing and a nested one without two spellings of every pattern.
    """
    skipped_dirs = CACHE_DIRNAMES | {FREEZE_DIRNAME}
    candidates = directory.rglob("*") if recursive else directory.iterdir()
    files = [
        p for p in candidates
        if p.is_file()
        and not p.is_symlink()
        and skipped_dirs.isdisjoint(p.relative_to(directory).parts)
        and p.suffix not in CACHE_SUFFIXES
        and matches_guard(p.name, names)
    ]
    return sorted(files, key=lambda p: p.relative_to(directory).as_posix())


def matches_guard(name: str, names: Sequence[str]) -> bool:
    """True when *name* matches any of the guard's fnmatch patterns.

    One predicate shared by the measurement path (_members) and the write-deny
    path (frozen_reason). Two hand-rolled copies is how the hook ends up denying
    a file verify no longer watches, or watching one it never denies.
    """
    return any(fnmatch.fnmatch(name, pattern) for pattern in names)


def dir_members_digest(
    directory: Path, *, recursive: bool, names: Sequence[str] = GUARD_NAMES_ALL
) -> str:
    """sha256 over the sorted POSIX relative paths of a directory's members.

    Composition only, not content: this is what detects a file appearing beside
    a frozen one (the conftest.py case), while per-file digests detect edits.
    """
    lines = "".join(
        f"{p.relative_to(directory).as_posix()}\n"
        for p in _members(directory, recursive=recursive, names=names)
    )
    return hashlib.sha256(lines.encode("utf-8")).hexdigest()


def dir_member_rels(
    directory: Path, root: Path, *, recursive: bool, names: Sequence[str] = GUARD_NAMES_ALL
) -> list[str]:
    """The directory's members as sorted root-relative POSIX paths.

    Recorded in the manifest beside the composition digest. A digest proves
    something moved; it cannot say WHAT, because a hash is not invertible. The
    guard on a file's parent covers members that were never frozen individually
    (a sibling test), so `added` and `removed` cannot be derived from the file
    map alone — without this list every pre-existing sibling reads as newly
    added and the guard cries wolf on its first use.
    """
    return sorted(
        p.relative_to(root).as_posix()
        for p in _members(directory, recursive=recursive, names=names)
    )


def root_hash(manifest: dict) -> str:
    """sha256 over recipe, anchor path, sorted files, sorted dirs, sorted baseline.

    The baseline is in here deliberately. Outside the hash it could be edited
    down to 1 with no indicator moving, and a per-file expected item count that
    can be silently lowered is worse than none: it reports rigour it is not
    delivering.
    """
    payload = {
        "recipe": manifest["recipe"],
        "anchor": manifest.get("anchor") or "",
        "files": dict(sorted(manifest["files"].items())),
        "dirs": dict(sorted(manifest["dirs"].items())),
        "baseline": dict(sorted((manifest.get("baseline") or {}).items())),
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ============================================================
# Path validation
# ============================================================

def validate_freeze_path(path: Path, root: Path) -> Path:
    """Resolve *path* and refuse anything that cannot be safely frozen."""
    resolved_root = Path(root).resolve()
    path = Path(path)
    if path.is_symlink():
        raise FreezeError(f"{path} is a symlink; symlinks cannot be frozen")
    resolved = path.resolve()
    if not resolved.exists():
        raise FreezeError(
            f"{path} does not exist; a contract cannot freeze a path that is not there"
        )
    try:
        rel = resolved.relative_to(resolved_root)
    except ValueError:
        raise FreezeError(
            f"{path} resolves outside the working tree at {resolved_root}"
        ) from None
    if rel.parts and rel.parts[0] == FREEZE_DIRNAME:
        raise FreezeError(
            f"{FREEZE_DIRNAME}/ holds the freeze state itself and cannot be frozen"
        )
    return resolved


def validate_anchor_path(path: Path, root: Path) -> Path:
    """Resolve an anchor artifact and refuse one the build could own.

    An anchor inside the working tree is not an anchor: the build writes there.
    """
    resolved_root = Path(root).resolve()
    resolved = Path(path).resolve()
    if not resolved.is_file():
        raise FreezeError(f"anchor artifact {path} does not exist or is not a file")
    if resolved.is_relative_to(resolved_root):
        raise FreezeError(
            f"anchor artifact {path} lies inside the working tree; an anchor inside "
            f"the build's own tree is not an anchor"
        )
    return resolved


# ============================================================
# Manifest construction
# ============================================================

CONFTEST_NAME = "conftest.py"


def _conftest_chain(target: Path, root: Path) -> list[Path]:
    """Every conftest.py from *target*'s directory up to *root*, inclusive.

    Walks the resolved ancestry rather than string prefixes, stops at the tree
    root, and skips symlinks (the workspace forbids them) so the chain can never
    reach outside the tree it is protecting.
    """
    start = target if target.is_dir() else target.parent
    found: list[Path] = []
    current = start
    while True:
        candidate = current / CONFTEST_NAME
        if candidate.is_file() and not candidate.is_symlink():
            found.append(candidate)
        if current == root:
            break
        parent = current.parent
        if parent == current:  # defensive: never loop at a filesystem root
            break
        current = parent
    return found


def _guard_ancestors(target: Path, root: Path, dirs: dict) -> None:
    """Install a filtered composition guard on every ancestor of *target*.

    Measured at the wire 2 intent audit, and this is the hole it closes. A
    directory freeze guarded only the target directory, so a `conftest.py`
    created in an ANCESTOR was invisible: verify held with nothing changed,
    added or removed. That conftest can put a stub module on `sys.path`, and
    because the mandated authoring rule resolves the code under test INSIDE the
    test body at RUN time, the frozen contract goes from red to green with every
    frozen byte intact. The item count is unchanged, so the baseline matches and
    the run attests: LOCK HELD and ATTESTED over a hijacked contract.

    Composition ONLY, never content. The guard answers "did an importable file
    appear above the contract", not "did anything under tests/ change" --
    freezing an ancestor's contents would stop a builder editing its own unit
    tests, and a primitive that forbids that gets routed around in its first
    week.

    The walk INCLUDES the tree root, under a different filter. The first version
    of this guard stopped below the root, reasoning that guarding the root's
    composition would deny every new top-level file. The reasoning was sound and
    the conclusion was wrong: pyproject declares `pythonpath = ["."]`, so the
    root is the first sys.path entry the contract's own imports resolve against,
    and stopping there left a plain `target.py` at the root able to flip the
    contract green while verify printed LOCK HELD. Watching `*.py` there answers
    the objection instead of surrendering to it: a note or a lockfile written
    during the slice is not importable and does not move the guard.

    An ancestor that already carries a recursive guard keeps it.
    """
    for ancestor in target.parents:
        if not ancestor.is_relative_to(root):
            break  # defensive: never walk above the tree being protected
        at_root = ancestor == root
        rel = "" if at_root else ancestor.relative_to(root).as_posix()
        names = GUARD_NAMES_TREE_ROOT if at_root else GUARD_NAMES_ANCESTOR
        if rel not in dirs:
            dirs[rel] = {
                "mode": "members",
                "names": list(names),
                "hash": dir_members_digest(ancestor, recursive=False, names=names),
                "members": dir_member_rels(ancestor, root, recursive=False, names=names),
            }
        if at_root:
            break


def build_manifest(
    paths: Iterable[Path],
    root: Path,
    *,
    label: str,
    frozen_at: str,
    anchor: Optional[Path] = None,
    content_only: Iterable[Path] = (),
    baseline: Optional[dict] = None,
) -> dict:
    """Build a freeze manifest over *paths*, all relative to *root*.

    A directory freezes recursively: every member file gets a content digest and
    the directory gets a recursive composition digest.

    A file freezes itself and additionally installs a NON-recursive composition
    guard on its parent directory, which is what catches a conftest.py dropped
    beside a frozen test. The guard is skipped when the parent is the working
    tree root, because guarding the root's composition would deny every new
    top-level file and make the tool something people route around.

    A `content_only` path freezes its BYTES and installs no composition guard on
    its parent. This is how the enforcer files are frozen. Freezing
    scripts/run-tests.py as an ordinary file would guard scripts/, and a build
    that cannot create a file under scripts/ cannot build anything, so the
    required practice would be unenforceable. A new file appearing beside
    run-tests.py does not change what run-tests.py does; an edit to its bytes
    does. The composition guard answers a question the enforcers do not ask.

    Every `conftest.py` on the path from each frozen path up to the tree root is
    added by CONTENT. A composition guard records member paths only, so a
    conftest sitting beside a frozen test was listed and never hashed, and that
    file is precisely where a good-faith edit changes what the contract measures
    without moving anything the guard watches: filtering inside
    `pytest_collection_modifyitems` fires no deselection hook, so the
    attestation's arithmetic still balances on a shrunken set. The tree root is
    included here even though the composition guard skips it, because a
    repository-root conftest is the cheapest place to filter collection from.
    """
    resolved_root = Path(root).resolve()
    files: dict[str, str] = {}
    dirs: dict[str, dict] = {}

    for raw in paths:
        target = validate_freeze_path(Path(raw), resolved_root)
        rel = target.relative_to(resolved_root).as_posix()
        if target.is_dir():
            dirs[rel] = {
                "mode": "recursive",
                "names": list(GUARD_NAMES_ALL),
                "hash": dir_members_digest(target, recursive=True),
                "members": dir_member_rels(target, resolved_root, recursive=True),
            }
            for member in _members(target, recursive=True):
                files[member.relative_to(resolved_root).as_posix()] = file_digest(member)
        else:
            files[rel] = file_digest(target)
        _guard_ancestors(target, resolved_root, dirs)
        for conftest in _conftest_chain(target, resolved_root):
            files.setdefault(
                conftest.relative_to(resolved_root).as_posix(), file_digest(conftest)
            )

    # Positional paths are processed first, deliberately: a path given BOTH ways
    # keeps the parent guard its positional form installed, and the digest
    # written twice is the same value.
    for raw in content_only:
        target = validate_freeze_path(Path(raw), resolved_root)
        if target.is_dir():
            raise FreezeError(
                f"{target} is a directory; --content freezes file bytes only. "
                f"Pass it positionally to freeze it recursively."
            )
        files[target.relative_to(resolved_root).as_posix()] = file_digest(target)
        for conftest in _conftest_chain(target, resolved_root):
            files.setdefault(
                conftest.relative_to(resolved_root).as_posix(), file_digest(conftest)
            )

    manifest = {
        "recipe": RECIPE,
        "label": label,
        "frozen_at": frozen_at,
        "anchor": str(validate_anchor_path(anchor, resolved_root)) if anchor else "",
        "git_sha": "",
        "baseline": dict(sorted((baseline or {}).items())),
        "files": dict(sorted(files.items())),
        "dirs": dict(sorted(dirs.items())),
    }
    manifest["root"] = root_hash(manifest)
    return manifest


# ============================================================
# Verification
# ============================================================

LOCK_HELD = "LOCK HELD"
LOSS_OF_LOCK = "LOSS OF LOCK"
LOCK_UNCONFIRMED = "LOCK UNCONFIRMED"


def recompute(manifest: dict, root: Path) -> dict:
    """Rebuild the manifest's content keys from current disk state.

    Same key set as *manifest*: a file that vanished is simply absent from the
    result, which is what makes the recomputed root hash differ.
    """
    resolved_root = Path(root).resolve()
    files: dict[str, str] = {}
    for rel in manifest["files"]:
        candidate = resolved_root / rel
        if candidate.is_file() and not candidate.is_symlink():
            files[rel] = file_digest(candidate)
    dirs: dict[str, dict] = {}
    for rel, entry in manifest["dirs"].items():
        candidate = resolved_root / rel
        recursive = entry["mode"] == "recursive"
        # Read from the manifest, never re-derived from the path. Re-deriving
        # would let a re-measurement widen or narrow a guard the approval was
        # taken over, which is the one thing recompute must not be able to do.
        names = entry["names"]
        alive = candidate.is_dir()
        dirs[rel] = {
            "mode": entry["mode"],
            "names": list(names),
            "hash": (
                dir_members_digest(candidate, recursive=recursive, names=names)
                if alive else ""
            ),
            "members": (
                dir_member_rels(candidate, resolved_root, recursive=recursive, names=names)
                if alive else []
            ),
        }
    return {
        "recipe": manifest["recipe"],
        "anchor": manifest.get("anchor") or "",
        "files": dict(sorted(files.items())),
        "dirs": dict(sorted(dirs.items())),
        # Carried through verbatim: the baseline is a recorded expectation, not
        # something disk can be re-measured for.
        "baseline": dict(sorted((manifest.get("baseline") or {}).items())),
    }


def verify_manifest(manifest: dict, root: Path) -> dict:
    """Compare disk against *manifest* and report what moved.

    `held` is True only when the recomputed root hash matches AND no file
    changed, was added, or was removed. Both conditions are checked rather than
    inferred from each other, so a future recipe change cannot quietly turn a
    real difference into a pass.

    `added` and `removed` are diffed against each guarded directory's RECORDED
    member list, never against the file map. A guard on a frozen file's parent
    deliberately covers siblings that were not frozen individually, so a file
    map comparison would report every pre-existing sibling as newly added.
    """
    resolved_root = Path(root).resolve()
    current = recompute(manifest, resolved_root)

    changed = sorted(
        rel for rel, digest in current["files"].items()
        if manifest["files"][rel] != digest
    )

    added: set[str] = set()
    vanished: set[str] = set()
    for rel, entry in manifest["dirs"].items():
        was = set(entry["members"])
        now = set(current["dirs"][rel]["members"])
        added |= now - was
        vanished |= was - now

    removed = sorted((set(manifest["files"]) - set(current["files"])) | vanished)

    recomputed = root_hash(current)
    return {
        "recomputed_root": recomputed,
        "changed": changed,
        "added": sorted(added),
        "removed": removed,
        "held": recomputed == manifest["root"] and not (changed or added or removed),
    }


# ============================================================
# The anchor
# ============================================================

ANCHOR_NONE = "none"
ANCHOR_MISSING = "missing"
ANCHOR_UNRECORDED = "unrecorded"
ANCHOR_RECORDED = "recorded"


def read_anchor(anchor_path: Path) -> Tuple[str, Optional[str]]:
    """Read the expected root hash out of a committed anchor artifact.

    Returns (ANCHOR_MISSING, None) when the artifact is gone,
    (ANCHOR_UNRECORDED, None) when it exists but carries no `canopus-anchor:`
    line, and (ANCHOR_RECORDED, hash) otherwise.

    The distinction matters. Unrecorded is the expected state between freezing
    and writing the hash down, so it is amber. Missing means a recorded anchor
    disappeared, which is a stronger signal than one that was never written, so
    it is red.
    """
    path = Path(anchor_path)
    if not path.is_file():
        return (ANCHOR_MISSING, None)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        # Unreadable is not "absent": treat it like a vanished anchor.
        return (ANCHOR_MISSING, None)
    found: Optional[str] = None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(ANCHOR_PREFIX):
            value = stripped[len(ANCHOR_PREFIX):].strip().lower()
            if value:
                # LAST wins. A replaced anchor appends rather than overwriting,
                # so the artifact keeps the whole approval trail; pinning to the
                # first line would make every legitimate re-freeze read as a
                # disagreement forever.
                found = value
    return (ANCHOR_RECORDED, found) if found else (ANCHOR_UNRECORDED, None)


def anchor_state(
    manifest: dict, override: Optional[str] = None
) -> Tuple[str, str, Optional[str]]:
    """Resolve (anchor path, status, recorded hash) for a manifest.

    One producer for the status string, shared by the CLI and the test gate. A
    second hand-rolled copy of this derivation is how a typo in one of them
    silently degrades every state to LOSS OF LOCK, because `lock_state` reads
    an unrecognised status as "recorded but disagreeing".

    ANCHOR_NONE is the manifest-carries-no-anchor case. The CLI refuses an
    anchorless freeze, so it is reachable only from a manifest written by the
    library directly or by an older CLI.
    """
    anchor = override or manifest.get("anchor") or ""
    if not anchor:
        return ("", ANCHOR_NONE, None)
    status, value = read_anchor(Path(anchor))
    return (anchor, status, value)


def lock_state(report: dict, anchor_status: str, anchor_value: Optional[str]) -> str:
    """Resolve the three-state indicator from a verify report plus the anchor.

    No prefix comparison anywhere: a truncated digest that looks rigorous and is
    not is worse than a full one, because a builder with a shell can brute-force
    a short prefix by appending whitespace to a frozen file.
    """
    if not report["held"]:
        return LOSS_OF_LOCK
    if anchor_status == ANCHOR_MISSING:
        return LOSS_OF_LOCK
    if anchor_status in (ANCHOR_NONE, ANCHOR_UNRECORDED):
        return LOCK_UNCONFIRMED
    return LOCK_HELD if anchor_value == report["recomputed_root"] else LOSS_OF_LOCK


# ============================================================
# Membership (consumed by the PreToolUse dispatcher)
# ============================================================

def frozen_reason(rel_posix: str, manifest: dict) -> Optional[str]:
    """Why *rel_posix* is frozen, or None. Pure string work, no disk access.

    The dispatcher calls this on every Write/Edit, so it must stay cheap: no
    hashing, no stat calls.
    """
    if rel_posix in manifest["files"]:
        return f"{rel_posix} is a frozen contract file"
    parent, _, name = rel_posix.rpartition("/")
    for dir_rel, entry in manifest["dirs"].items():
        if entry["mode"] == "recursive":
            if rel_posix == dir_rel or rel_posix.startswith(dir_rel + "/"):
                return f"{rel_posix} is inside the frozen directory {dir_rel}/"
        elif parent == dir_rel and matches_guard(name, entry["names"]):
            # The same filter verify measures with. A deny wider than the
            # measurement refuses writes nothing would have reported, which is
            # how a discipline tool becomes an obstacle.
            return f"{rel_posix} would join the guarded composition of {dir_rel}/"
    return None


# ============================================================
# On-disk state
# ============================================================

class FreezeCorrupt(FreezeError):
    """A freeze manifest exists but is unreadable or carries an unknown recipe.

    Handled fail-closed by the dispatcher: a corrupt manifest denies every write
    rather than silently unlocking the contract, matching the house convention
    where a broken routing map forces `private` and an unknown action type floors
    at `gated`.

    The escape is `release --force`, which is LOGGED. An escape that leaves no
    record turns every false alarm into a routine of unlogged deletions, and
    after the third one the operator stops reading the alarm at all. Alert
    fatigue is the adversary here, not the model.
    """


def freeze_state_path(root: Path) -> Path:
    return Path(root) / FREEZE_DIRNAME / FREEZE_FILENAME


def history_state_path(root: Path) -> Path:
    return Path(root) / FREEZE_DIRNAME / HISTORY_FILENAME


_DIR_MODES = ("members", "recursive")
_STR_SCALAR_KEYS = ("label", "frozen_at", "anchor", "git_sha", "root")


def _require(condition: bool, message: str) -> None:
    """Raise FreezeCorrupt with *message* unless *condition* holds."""
    if not condition:
        raise FreezeCorrupt(message)


def _validate_manifest_shape(manifest: dict, path: Path) -> None:
    """Validate every key and value type build_manifest() ever produces.

    read_freeze() calls this right after JSON decoding and the recipe check,
    before recompute()/verify_manifest()/frozen_reason() dereference a single
    key.

    The rule is validate the whole closed shape, not one escape at a time.
    build_manifest()'s output is small and closed, so a single complete pass is
    proportionate, and it is what stops the next unchecked corner from becoming
    the next defect. `entry["hash"]` is validated even though no caller in this
    repo reads it: leaving one sibling key unchecked is precisely the gap this
    function exists to close.

    Every violation raises FreezeCorrupt naming the offending key or path and
    the type expected, so a syntactically valid-but-wrong-shaped manifest
    fails here -- not as an uncaught TypeError/AttributeError deep in a caller
    that only expects FreezeCorrupt/OSError (the PreToolUse dispatcher catches
    exactly those two and denies fail-closed; anything else falls through its
    outer catch-all, logs an advisory, and continues -- fail OPEN).
    """
    for key in (*_STR_SCALAR_KEYS, "files", "dirs", "baseline"):
        _require(key in manifest, f"freeze manifest at {path} is missing {key!r}")

    for key in _STR_SCALAR_KEYS:
        value = manifest[key]
        _require(
            isinstance(value, str),
            f"freeze manifest at {path} has a non-string {key!r} value "
            f"({type(value).__name__}), expected a string",
        )

    files = manifest["files"]
    _require(
        isinstance(files, dict),
        f"freeze manifest at {path} has a non-dict 'files' value "
        f"({type(files).__name__}), expected a dict",
    )
    for rel, digest in files.items():
        _require(
            isinstance(rel, str),
            f"freeze manifest at {path} has a non-string key in 'files' "
            f"({type(rel).__name__}), expected a string",
        )
        _require(
            isinstance(digest, str),
            f"freeze manifest at {path} has a non-string value for files[{rel!r}] "
            f"({type(digest).__name__}), expected a string",
        )

    dirs = manifest["dirs"]
    _require(
        isinstance(dirs, dict),
        f"freeze manifest at {path} has a non-dict 'dirs' value "
        f"({type(dirs).__name__}), expected a dict",
    )
    for rel, entry in dirs.items():
        _require(
            isinstance(rel, str),
            f"freeze manifest at {path} has a non-string key in 'dirs' "
            f"({type(rel).__name__}), expected a string",
        )
        # A dir entry without its recorded member list would make every existing
        # member read as newly added. Refuse it rather than report a false alarm.
        _require(
            isinstance(entry, dict)
            and "mode" in entry and "hash" in entry
            and "members" in entry and "names" in entry,
            f"freeze manifest at {path} has an incomplete entry for directory {rel!r}",
        )
        mode = entry["mode"]
        # mode gates the recompute()/frozen_reason() branch between "recursive"
        # and "members" handling. An unrecognised mode string does not crash --
        # recompute() just checks `entry["mode"] == "recursive"` -- it silently
        # falls through to shallow handling, downgrading a recursive directory
        # freeze to a shallow one without any error. That silent weakening of
        # the guarantee is worse than a crash, so it is refused here explicitly.
        _require(
            isinstance(mode, str) and mode in _DIR_MODES,
            f"freeze manifest at {path} has an unrecognised 'mode' {mode!r} for "
            f"directory {rel!r}, expected one of {_DIR_MODES}",
        )
        _require(
            isinstance(entry["hash"], str),
            f"freeze manifest at {path} has a non-string 'hash' for directory "
            f"{rel!r} ({type(entry['hash']).__name__}), expected a string",
        )
        # An empty or non-list 'names' would make matches_guard() answer False
        # for every basename, silently reducing the guard to nothing while the
        # manifest still reports a guarded directory. Refused, like an
        # unrecognised mode, because a guard that watches nothing and says it
        # watches something is worse than no guard at all.
        names = entry["names"]
        _require(
            isinstance(names, list) and names,
            f"freeze manifest at {path} has an empty or non-list 'names' for "
            f"directory {rel!r}, expected a non-empty list of patterns",
        )
        for pattern in names:
            _require(
                isinstance(pattern, str),
                f"freeze manifest at {path} has a non-string pattern in 'names' "
                f"for directory {rel!r} ({type(pattern).__name__}), expected a string",
            )

        members = entry["members"]
        _require(
            isinstance(members, list),
            f"freeze manifest at {path} has a non-list 'members' for directory "
            f"{rel!r} ({type(members).__name__}), expected a list",
        )
        for member in members:
            _require(
                isinstance(member, str),
                f"freeze manifest at {path} has a non-string member in 'members' "
                f"for directory {rel!r} ({type(member).__name__}), expected a string",
            )

    baseline = manifest["baseline"]
    _require(
        isinstance(baseline, dict),
        f"freeze manifest at {path} has a non-dict 'baseline' value "
        f"({type(baseline).__name__}), expected a dict",
    )
    for rel, count in baseline.items():
        _require(
            isinstance(rel, str),
            f"freeze manifest at {path} has a non-string key in 'baseline' "
            f"({type(rel).__name__}), expected a string",
        )
        # bool is a subclass of int; a JSON `true` here would silently compare
        # equal to a collected count of 1.
        _require(
            isinstance(count, int) and not isinstance(count, bool),
            f"freeze manifest at {path} has a non-integer 'baseline' value for "
            f"{rel!r} ({type(count).__name__}), expected an integer",
        )


def read_freeze(root: Path) -> Optional[dict]:
    """Load the active freeze manifest, or None when none is active.

    Raises FreezeCorrupt when a manifest exists but cannot be trusted. The
    caller must never treat that as "no freeze".
    """
    path = freeze_state_path(root)
    if not path.exists():
        return None
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise FreezeCorrupt(f"freeze manifest at {path} is unreadable: {exc}") from exc
    if not isinstance(manifest, dict):
        raise FreezeCorrupt(f"freeze manifest at {path} is not a JSON object")
    if manifest.get("recipe") != RECIPE:
        raise FreezeCorrupt(
            f"freeze manifest at {path} carries recipe {manifest.get('recipe')!r}, "
            f"expected {RECIPE!r}"
        )
    _validate_manifest_shape(manifest, path)
    return manifest


def write_freeze(root: Path, manifest: dict) -> None:
    """Write the manifest atomically (tmp file plus os.replace)."""
    atomic_write_text(freeze_state_path(root), json.dumps(manifest, indent=2) + "\n")


def clear_freeze(root: Path) -> None:
    """Remove the active manifest AND the attestation that spoke for it.

    Idempotent, and never parses either file, so it works on a damaged one.

    The attestation goes with the manifest because the root hash is
    deterministic over frozen content plus the anchor path: leaving the record
    behind meant that re-freezing identical test content revived it, and a
    brand-new freeze with zero runs since printed ATTESTED. Reproduced, not
    theorised. A record that outlives the contract it attests is a false green
    waiting for a coincidence.
    """
    freeze_state_path(root).unlink(missing_ok=True)
    attest_state_path(root).unlink(missing_ok=True)


def append_history(
    root: Path,
    event: str,
    *,
    digest: str,
    label: str,
    reason: str = "",
) -> None:
    """Append one line to the ledger. Never rewrites, never truncates.

    A separate file from the manifest on purpose: the logged escape has to work
    when the manifest cannot be parsed.

    WHAT THE LEDGER RECORDS, stated so the gap is a known property rather than
    a discovered one. It records operator intent through the CLI: `freeze`,
    `release`, `force_release`, and a failing `verify`. It does NOT record the
    test gate. A passing gate writes nothing, so the ABSENCE of a `verify_fail`
    line is ambiguous between "verified clean many times" and "never verified
    at all" — the gate's evidence is its exit code in the test output, not a
    line here, and adding a line per gate run would bury the four events that
    matter under noise.

    It also lives in the same gitignored directory as the manifest, so it is
    evidence against an EDIT to freeze.json, never against deletion of the
    directory: `rm -rf .canopus` takes the ledger with it. The durable record
    of an approved contract is the anchor artifact, committed in another
    repository.
    """
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "event": event,
        "root": digest,
        "label": label,
        "reason": reason,
    }
    path = history_state_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Deliberately not atomic_write_text: that primitive is a full-file
    # tmp-plus-replace overwrite, incompatible with append-only growth, and
    # append-only is the entire point of this ledger.
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, sort_keys=True) + "\n")


# ============================================================
# Attestation: did the frozen tests actually run?
# ============================================================
#
# The manifest answers "did the contract move". It cannot answer "did the
# contract run", and a builder that cannot edit a frozen test can simply decline
# to run it: pytest -k, --deselect, --ignore, --lf and a bare path argument all
# reach green with every frozen byte intact.
#
# From wire 2 a bare path or a node id is ALSO caught, for any file carrying a
# freeze-time baseline: the record compares what was collected against how many
# items that file yields when collected whole, so a subset reports 1 of 7. A
# frozen test file with no baseline entry keeps the wire 1 comparison.
#
# This records, it does not block, and nothing here is fatal. Failing a filtered
# run would charge every inner-loop iteration for a hole that a passive record
# closes at the point of comparison, and a primitive that forbids `pytest -k`
# gets routed around in its first week. An earlier revision kept one fatal case
# and measurement removed it: telling "removed from collection" apart from "the
# operator named one path" needs option sniffing, and option sniffing is exactly
# what proved unreliable.

ATTEST_FILENAME = "attest.json"
ATTEST_RECIPE = "canopus-attest-v1"
ATTESTED = "ATTESTED"
NOT_ATTESTED = "NOT ATTESTED"


def attest_state_path(root: Path) -> Path:
    """Where the attestation record lives, beside the manifest it attests."""
    return Path(root).resolve() / FREEZE_DIRNAME / ATTEST_FILENAME


def frozen_test_files(manifest: dict, patterns: Sequence[str]) -> list[str]:
    """Frozen paths that pytest would collect as test modules.

    Patterns come from the running pytest config (`python_files`) rather than a
    hardcoded `test_*.py`, so a repo that renames its convention does not
    silently attest an empty set while reporting green.
    """
    files = manifest.get("files") or {}
    return sorted(
        rel for rel in files
        if any(fnmatch.fnmatch(PurePosixPath(rel).name, pattern) for pattern in patterns)
    )


def tally_collection(frozen: Sequence[str], collected_rels: Iterable[str]) -> dict:
    """Seed the per-file counters from what pytest actually collected.

    Every frozen test file gets an entry even when nothing was collected for it,
    because a zero is the signal that matters: it means the file was filtered
    out, ignored, or removed from collection entirely.
    """
    counts = {
        rel: {"collected": 0, "passed": 0, "failed": 0, "skipped": 0, "deselected": 0}
        for rel in frozen
    }
    for rel in collected_rels:
        if rel in counts:
            counts[rel]["collected"] += 1
    return counts


def build_attestation(
    *,
    root_digest: str,
    frozen_tests: dict,
    exit_status: int,
    attested_at: str,
    baseline: Optional[dict] = None,
) -> dict:
    """Assemble the record written at session finish. Pure: no disk, no pytest.

    Measures COLLECTION, never INVOCATION. An earlier revision classified pytest
    options (-k, -m, --ignore and friends) and shipped four defects: it marked
    the canonical gate's own `-m "not acceptance"` as a filter and so could never
    attest anything, it could not see a bare path argument at all, its fatal
    branch fired on ordinary single-test runs, and it knew nothing about parallel
    workers. Counting what was collected, deselected, and reported answers all
    four without knowing how pytest was called.

    Every false condition leaves a plain-language string in `reasons`, because an
    operator reading NOT ATTESTED at the sign-off gate needs to know which one it
    was.

    Skips are counted and deliberately do not void the record: a frozen test's
    own platform guard is legitimate and its bytes are frozen, while a skip
    injected from an unfrozen sibling conftest is the composition guard's job.

    A freeze carrying no test files attests nothing rather than everything. The
    same rule already governs verify, which refuses to print a green line when
    there is no contract to check.

    The node-id subset case IS caught, from wire 2 onward. A contract frozen with
    --contract carries a per-file item count taken at freeze time, so `pytest
    tests/contract/s/test_a.py::test_one` reports 1 against 7 and does not
    attest. A frozen test file with no baseline entry keeps the wire 1
    behaviour, where collected is compared only against what was reported.
    """
    reasons: list[str] = []
    if not frozen_tests:
        reasons.append("the freeze contains no test files to attest")
    expected_counts = baseline or {}
    for rel, counts in sorted(frozen_tests.items()):
        collected = counts.get("collected", 0)
        reported = counts.get("passed", 0) + counts.get("skipped", 0)
        expected = expected_counts.get(rel)
        if not collected:
            reasons.append(f"frozen test file collected nothing: {rel}")
        elif counts.get("deselected", 0):
            reasons.append(
                f"frozen test file had {counts['deselected']} items deselected: {rel}"
            )
        elif expected is not None and collected != expected:
            reasons.append(
                f"frozen test file collected {collected} of {expected}: {rel}"
            )
        if counts.get("failed", 0):
            reasons.append(f"frozen test file reported failures: {rel}")
        elif collected and reported != collected:
            reasons.append(
                f"frozen test file reported an incomplete tally: {rel} "
                f"({reported} of {collected})"
            )
    if exit_status != 0:
        reasons.append(f"pytest exited {exit_status}")

    return {
        "recipe": ATTEST_RECIPE,
        "root": root_digest,
        "attested": not reasons,
        "reasons": reasons,
        "exit_status": exit_status,
        "attested_at": attested_at,
        "frozen_tests": {rel: dict(counts) for rel, counts in sorted(frozen_tests.items())},
    }


def _counters_are_numeric(frozen_tests) -> bool:
    """True when `frozen_tests` is the dict-of-int-counters shape readers assume.

    The reporting side sums these counters (`sum(entry["passed"] ...)`), and a
    record whose counter is a string turned `canopus verify` into a raw
    TypeError traceback: the reporter's exception type is outside the
    FreezeError/FreezeCorrupt/OSError set main() catches, so it escaped the
    handler that exists precisely to stop the guarantee layer printing a stack
    trace. Validating the shape here keeps that fix in ONE place for every
    reader, and matches this function's stated posture -- damage reads as
    absence, which can only ever be NOT ATTESTED.
    """
    if not isinstance(frozen_tests, dict):
        return False
    for entry in frozen_tests.values():
        if not isinstance(entry, dict):
            return False
        if any(not isinstance(value, int) for value in entry.values()):
            return False
    return True


def read_attestation(root: Path) -> Optional[dict]:
    """Read the attestation record, or None when absent or unusable.

    Unlike the manifest, a damaged attestation is NOT fail-closed. It can never
    make a state greener than NOT ATTESTED, so treating damage as absence is both
    safe and quieter than raising on a path that only ever reports.
    """
    path = attest_state_path(root)
    try:
        data = json.loads(path.read_bytes().decode("utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        print(f"canopus: unusable attestation at {path}: {exc}", file=sys.stderr)
        return None
    if not isinstance(data, dict):
        print(f"canopus: unusable attestation at {path}: not an object", file=sys.stderr)
        return None
    if "frozen_tests" in data and not _counters_are_numeric(data["frozen_tests"]):
        print(f"canopus: unusable attestation at {path}: 'frozen_tests' is not a "
              f"mapping of integer counters", file=sys.stderr)
        return None
    return data


def write_attestation(root: Path, attestation: dict) -> None:
    """Persist the record atomically, beside the manifest."""
    path = attest_state_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, json.dumps(attestation, indent=2, sort_keys=True) + "\n")


def attestation_state(
    attestation: Optional[dict], recomputed_root: str
) -> Tuple[str, str]:
    """The second indicator axis, as (state, reason). Never raises.

    Binding the record to the recomputed root is what makes it perishable: edit
    any frozen file after a green run and the root moves, so the attestation
    stops applying without anyone having to remember to delete it.
    """
    if not isinstance(attestation, dict) or not attestation:
        return NOT_ATTESTED, "no run has attested this freeze yet"
    if attestation.get("recipe") != ATTEST_RECIPE:
        return NOT_ATTESTED, "the attestation was written by a different recipe"
    if attestation.get("root") != recomputed_root:
        return NOT_ATTESTED, "the attestation was recorded against a different root hash"
    if not attestation.get("attested"):
        return NOT_ATTESTED, "the attesting run did not qualify"
    return ATTESTED, ""
