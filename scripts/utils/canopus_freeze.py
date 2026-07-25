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
        alive = candidate.is_dir()
        dirs[rel] = {
            "mode": entry["mode"],
            "hash": dir_members_digest(candidate, recursive=recursive) if alive else "",
            "members": (
                dir_member_rels(candidate, resolved_root, recursive=recursive) if alive else []
            ),
        }
    return {
        "recipe": manifest["recipe"],
        "anchor": manifest.get("anchor") or "",
        "files": dict(sorted(files.items())),
        "dirs": dict(sorted(dirs.items())),
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

def read_anchor(anchor_path: Path) -> Tuple[str, Optional[str]]:
    """Read the expected root hash out of a committed anchor artifact.

    Returns ("missing", None) when the artifact is gone, ("unrecorded", None)
    when it exists but carries no `canopus-anchor:` line, and ("recorded", hash)
    otherwise.

    The distinction matters. Unrecorded is the expected state between freezing
    and writing the hash down, so it is amber. Missing means a recorded anchor
    disappeared, which is a stronger signal than one that was never written, so
    it is red.
    """
    path = Path(anchor_path)
    if not path.is_file():
        return ("missing", None)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        # Unreadable is not "absent": treat it like a vanished anchor.
        return ("missing", None)
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(ANCHOR_PREFIX):
            value = stripped[len(ANCHOR_PREFIX):].strip().lower()
            if value:
                return ("recorded", value)
    return ("unrecorded", None)


def lock_state(report: dict, anchor_status: str, anchor_value: Optional[str]) -> str:
    """Resolve the three-state indicator from a verify report plus the anchor.

    No prefix comparison anywhere: a truncated digest that looks rigorous and is
    not is worse than a full one, because a builder with a shell can brute-force
    a short prefix by appending whitespace to a frozen file.
    """
    if not report["held"]:
        return LOSS_OF_LOCK
    if anchor_status == "missing":
        return LOSS_OF_LOCK
    if anchor_status in ("none", "unrecorded"):
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
    parent = rel_posix.rsplit("/", 1)[0] if "/" in rel_posix else ""
    for dir_rel, entry in manifest["dirs"].items():
        if entry["mode"] == "recursive":
            if rel_posix == dir_rel or rel_posix.startswith(dir_rel + "/"):
                return f"{rel_posix} is inside the frozen directory {dir_rel}/"
        elif parent == dir_rel:
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
    except (OSError, json.JSONDecodeError) as exc:
        raise FreezeCorrupt(f"freeze manifest at {path} is unreadable: {exc}") from exc
    if not isinstance(manifest, dict):
        raise FreezeCorrupt(f"freeze manifest at {path} is not a JSON object")
    if manifest.get("recipe") != RECIPE:
        raise FreezeCorrupt(
            f"freeze manifest at {path} carries recipe {manifest.get('recipe')!r}, "
            f"expected {RECIPE!r}"
        )
    for key in ("files", "dirs", "root", "label"):
        if key not in manifest:
            raise FreezeCorrupt(f"freeze manifest at {path} is missing {key!r}")
    # Presence alone is not enough: recompute()/verify_manifest()/frozen_reason()
    # all dereference these keys assuming a specific shape (dict.items(), string
    # concatenation, entry["mode"]/["hash"]/["members"]). A syntactically valid
    # manifest with the wrong shape must fail here, not as an uncaught
    # AttributeError deep in a caller that only expects FreezeCorrupt/OSError.
    if not isinstance(manifest["files"], dict):
        raise FreezeCorrupt(
            f"freeze manifest at {path} has a non-dict 'files' value "
            f"({type(manifest['files']).__name__}), expected a dict"
        )
    if not isinstance(manifest["dirs"], dict):
        raise FreezeCorrupt(
            f"freeze manifest at {path} has a non-dict 'dirs' value "
            f"({type(manifest['dirs']).__name__}), expected a dict"
        )
    for rel, entry in manifest["dirs"].items():
        # A dir entry without its recorded member list would make every existing
        # member read as newly added. Refuse it rather than report a false alarm.
        if not isinstance(entry, dict) or "members" not in entry or "mode" not in entry:
            raise FreezeCorrupt(
                f"freeze manifest at {path} has an incomplete entry for directory {rel!r}"
            )
        if "hash" not in entry:
            raise FreezeCorrupt(
                f"freeze manifest at {path} has a directory entry for {rel!r} missing 'hash'"
            )
        if not isinstance(entry["members"], list):
            raise FreezeCorrupt(
                f"freeze manifest at {path} has a non-list 'members' for directory {rel!r}"
            )
    if not isinstance(manifest["root"], str):
        raise FreezeCorrupt(
            f"freeze manifest at {path} has a non-string 'root' value "
            f"({type(manifest['root']).__name__}), expected a string"
        )
    if not isinstance(manifest["label"], str):
        raise FreezeCorrupt(
            f"freeze manifest at {path} has a non-string 'label' value "
            f"({type(manifest['label']).__name__}), expected a string"
        )
    if "anchor" in manifest and not isinstance(manifest["anchor"], str):
        raise FreezeCorrupt(
            f"freeze manifest at {path} has a non-string 'anchor' value "
            f"({type(manifest['anchor']).__name__}), expected a string"
        )
    return manifest


def write_freeze(root: Path, manifest: dict) -> None:
    """Write the manifest atomically (tmp file plus os.replace)."""
    atomic_write_text(freeze_state_path(root), json.dumps(manifest, indent=2) + "\n")


def clear_freeze(root: Path) -> None:
    """Remove the active manifest. Idempotent, and never parses it, so it works
    on a damaged file."""
    freeze_state_path(root).unlink(missing_ok=True)


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
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, sort_keys=True) + "\n")
