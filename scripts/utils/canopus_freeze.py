#!/usr/bin/env python3
"""Canopus freeze primitive: hashing, manifest construction, verification.

The Canopus standard freezes the test contract before a build starts so the
builder cannot move the target it is measured against. This module is the pure
logic half.

Three layers, and the distinctions are load-bearing:

  * The PreToolUse deny is a CONVENIENCE. It only sees Write/Edit tool calls, so
    a Bash `sed -i`, a `python -c`, or a subagent with its own toolset walks
    straight past it. It exists to refuse the write at the moment of the attempt
    and save a wasted loop iteration.
  * The manifest is the GUARANTEE. Verification recomputes digests from disk and
    catches a change made by any route, including routes nobody anticipated.
  * The test gate is what makes the guarantee FIRE. scripts/run-tests.py runs the
    check before the suite, so a build cannot reach green while its contract is
    moved. An unrun verify fails 100% of the time no matter how well its expected
    value is protected.

Never reason that the deny makes verification optional.

The realistic adversary is not an evader. The builder is Claude, and the failure
this catches is tampering by helpfulness: the model hits a red assertion,
concludes in good faith that the assertion is wrong, and edits it. A verification
that merely runs catches that completely.

Recipe `canopus-freeze-v1`, named in every manifest so a future algorithm change
breaks loudly instead of silently:

    file digest = sha256(LF-normalized bytes)
    dir digest  = sha256("".join(f"{relpath}\\n" for relpath in sorted members))
    root hash   = sha256(canonical JSON of {recipe, anchor, files, dirs})

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

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional, Tuple

from scripts.utils.atomic import atomic_write_text

RECIPE = "canopus-freeze-v1"
FREEZE_DIRNAME = ".canopus"
FREEZE_FILENAME = "freeze.json"
HISTORY_FILENAME = "history.jsonl"
ANCHOR_PREFIX = "canopus-anchor:"


class FreezeError(Exception):
    """A freeze operation was refused."""


# ============================================================
# Hashing
# ============================================================

def file_digest(path: Path) -> str:
    """sha256 over LF-normalized file bytes."""
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def _members(directory: Path, *, recursive: bool) -> list[Path]:
    """Regular files in *directory*, sorted by POSIX relative path.

    Symlinks are excluded (the workspace forbids them) and anything under the
    freeze state directory is excluded so the manifest never hashes itself.
    """
    candidates = directory.rglob("*") if recursive else directory.iterdir()
    files = [
        p for p in candidates
        if p.is_file()
        and not p.is_symlink()
        and FREEZE_DIRNAME not in p.relative_to(directory).parts
    ]
    return sorted(files, key=lambda p: p.relative_to(directory).as_posix())


def dir_members_digest(directory: Path, *, recursive: bool) -> str:
    """sha256 over the sorted POSIX relative paths of a directory's members.

    Composition only, not content: this is what detects a file appearing beside
    a frozen one (the conftest.py case), while per-file digests detect edits.
    """
    lines = "".join(
        f"{p.relative_to(directory).as_posix()}\n"
        for p in _members(directory, recursive=recursive)
    )
    return hashlib.sha256(lines.encode("utf-8")).hexdigest()


def dir_member_rels(directory: Path, root: Path, *, recursive: bool) -> list[str]:
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
        for p in _members(directory, recursive=recursive)
    )


def root_hash(manifest: dict) -> str:
    """sha256 over recipe, anchor path, sorted files, sorted dirs."""
    payload = {
        "recipe": manifest["recipe"],
        "anchor": manifest.get("anchor") or "",
        "files": dict(sorted(manifest["files"].items())),
        "dirs": dict(sorted(manifest["dirs"].items())),
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

def build_manifest(
    paths: Iterable[Path],
    root: Path,
    *,
    label: str,
    frozen_at: str,
    anchor: Optional[Path] = None,
) -> dict:
    """Build a freeze manifest over *paths*, all relative to *root*.

    A directory freezes recursively: every member file gets a content digest and
    the directory gets a recursive composition digest.

    A file freezes itself and additionally installs a NON-recursive composition
    guard on its parent directory, which is what catches a conftest.py dropped
    beside a frozen test. The guard is skipped when the parent is the working
    tree root, because guarding the root's composition would deny every new
    top-level file and make the tool something people route around.
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
                "hash": dir_members_digest(target, recursive=True),
                "members": dir_member_rels(target, resolved_root, recursive=True),
            }
            for member in _members(target, recursive=True):
                files[member.relative_to(resolved_root).as_posix()] = file_digest(member)
        else:
            files[rel] = file_digest(target)
            parent = target.parent
            if parent != resolved_root:
                parent_rel = parent.relative_to(resolved_root).as_posix()
                if parent_rel not in dirs:
                    dirs[parent_rel] = {
                        "mode": "members",
                        "hash": dir_members_digest(parent, recursive=False),
                        "members": dir_member_rels(parent, resolved_root, recursive=False),
                    }

    manifest = {
        "recipe": RECIPE,
        "label": label,
        "frozen_at": frozen_at,
        "anchor": str(validate_anchor_path(anchor, resolved_root)) if anchor else "",
        "git_sha": "",
        "files": dict(sorted(files.items())),
        "dirs": dict(sorted(dirs.items())),
    }
    manifest["root"] = root_hash(manifest)
    return manifest
