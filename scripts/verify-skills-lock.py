#!/usr/bin/env python3
"""Verify vendored-skill integrity against skills-lock.json (F-9.5).

skills-lock.json pins the vendored (upstream-derived) skills that ship inside
this repo. A tampered or drifted vendored tree must be caught, so this script
recomputes each pinned hash over the on-disk tree and fails on mismatch. It runs
in CI (guards) and at pre-push.

Recipe `sha256-tree-v1` (self-documented in the lock's top-level `recipe` field):

    tree_dir = the vendored skill directory (the parent of `skillPath`)
    files    = every regular file under tree_dir, sorted by POSIX relative path
    for each file:  line = "<relpath>\\n<sha256(LF-normalized bytes) hexdigest>\\n"
    computedHash = sha256("".join(lines) UTF-8)

Binding both the relative path and the per-file content digest makes the hash
sensitive to added, removed, renamed, or edited files, and independent of
filesystem iteration order. Per-file bytes are LF-normalized (\\r\\n -> \\n)
before hashing so the digest matches git's `* text=auto` storage and is stable
across checkouts regardless of a working tree's local line endings (otherwise a
CRLF working copy and a fresh CI checkout would disagree).

History: the original lock (2026-05-20) carried hashes computed over the pristine
upstream trees by a generator that is not part of this repo, and those hashes
never matched the in-repo (lightly adapted) copies. There was no verifier. F-9.5
defines the recipe above and re-locks against the in-repo vendored trees, so the
lock now protects the copies that actually ship here. `frontend-design` is
plugin-managed (installed via the Claude Code plugin system, not vendored in this
tree), so it is marked `vendored: false` and its integrity is the plugin system's
responsibility, not this verifier's.

Usage:
    python scripts/verify-skills-lock.py            # verify (default; --check alias)
    python scripts/verify-skills-lock.py --check    # verify only, never write
    python scripts/verify-skills-lock.py --relock    # recompute + rewrite the lock
    python scripts/verify-skills-lock.py --quiet     # suppress OK/skip lines
"""
import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.utils.colors import GREEN, YELLOW, RED, GRAY, BOLD, RESET
from scripts.utils.workspace import get_workspace_root

RECIPE = "sha256-tree-v1"
LOCK_PATH = get_workspace_root() / "skills-lock.json"


def _tree_hash(tree_dir: Path) -> str:
    """Compute the sha256-tree-v1 digest over a vendored skill directory."""
    files = sorted(
        (p for p in tree_dir.rglob("*") if p.is_file()),
        key=lambda p: p.relative_to(tree_dir).as_posix(),
    )
    lines = []
    for p in files:
        rel = p.relative_to(tree_dir).as_posix()
        # LF-normalize so the digest matches git's `* text=auto` storage and is
        # stable across checkouts (a CRLF working copy must hash the same as a
        # fresh LF CI checkout).
        content = p.read_bytes().replace(b"\r\n", b"\n")
        digest = hashlib.sha256(content).hexdigest()
        lines.append(f"{rel}\n{digest}\n")
    return hashlib.sha256("".join(lines).encode("utf-8")).hexdigest()


def _vendored_dir(root: Path, entry: dict) -> Path | None:
    """Resolve the on-disk vendored tree for a lock entry, or None."""
    skill_path = entry.get("skillPath")
    if not skill_path:
        return None
    return root / Path(skill_path).parent


def verify(relock: bool, quiet: bool) -> int:
    root = get_workspace_root()
    if not LOCK_PATH.exists():
        print(f"{RED}FAIL{RESET}  skills-lock.json not found at {LOCK_PATH}")
        return 1
    try:
        lock = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        print(f"{RED}FAIL{RESET}  skills-lock.json unreadable: {e}")
        return 1

    recipe = lock.get("recipe")
    if recipe != RECIPE:
        print(f"{RED}FAIL{RESET}  lock recipe {recipe!r} != expected {RECIPE!r}")
        return 1

    issues = 0
    changed = False
    for name, entry in sorted(lock.get("skills", {}).items()):
        if entry.get("vendored") is False:
            if not quiet:
                print(f"{GRAY}SKIP{RESET}  {name}: {entry.get('note', 'not vendored in-repo')}")
            continue
        tree_dir = _vendored_dir(root, entry)
        if tree_dir is None or not tree_dir.is_dir():
            print(f"{RED}FAIL{RESET}  {name}: vendored tree missing "
                  f"({tree_dir.relative_to(root) if tree_dir else 'no skillPath'})")
            issues += 1
            continue
        actual = _tree_hash(tree_dir)
        expected = entry.get("computedHash")
        if relock:
            if actual != expected:
                entry["computedHash"] = actual
                changed = True
                if not quiet:
                    print(f"{YELLOW}RELOCK{RESET}  {name}: {expected} -> {actual}")
            elif not quiet:
                print(f"{GREEN}OK{RESET}  {name}: {actual} (unchanged)")
        elif actual == expected:
            if not quiet:
                print(f"{GREEN}OK{RESET}  {name}: {actual}")
        else:
            print(f"{RED}FAIL{RESET}  {name}: hash mismatch\n"
                  f"        expected {expected}\n        actual   {actual}")
            issues += 1

    if relock and changed:
        LOCK_PATH.write_text(json.dumps(lock, indent=2) + "\n", encoding="utf-8")
        print(f"{BOLD}Re-locked{RESET} {LOCK_PATH.relative_to(root)}")
        return 0

    if issues:
        print(f"\n{RED}{BOLD}{issues} vendored-skill integrity issue(s).{RESET} "
              f"If the change is intentional, re-lock with --relock.")
        return 1
    if not quiet:
        print(f"\n{GREEN}{BOLD}Vendored skills verified.{RESET}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify vendored-skill hashes (F-9.5)")
    parser.add_argument("--check", action="store_true",
                        help="verify only, never write (default behaviour)")
    parser.add_argument("--relock", action="store_true",
                        help="recompute and rewrite skills-lock.json")
    parser.add_argument("--quiet", action="store_true", help="suppress OK/skip lines")
    args = parser.parse_args()
    if args.relock and args.check:
        parser.error("--relock and --check are mutually exclusive")
    return verify(relock=args.relock, quiet=args.quiet)


if __name__ == "__main__":
    sys.exit(main())
