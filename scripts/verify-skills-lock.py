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
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.utils.colors import GREEN, YELLOW, RED, GRAY, BOLD, RESET
from scripts.utils.workspace import get_workspace_root

RECIPE = "sha256-tree-v1"
LOCK_PATH = get_workspace_root() / "skills-lock.json"


class TreeChangedUnderVerification(Exception):
    """A file listed by the walk was gone by the time the hash reached it."""


def _locked_read(p: Path, read):
    """``read(p)``, retried once, refusing rather than skipping when it is gone.

    WHY THIS DOES NOT USE `scripts.utils.repo_files.read_sources`, which is the
    shared answer for a walk-then-read race everywhere else in this tree.
    `read_sources` SKIPS a vanished path and warns. That is right for a scanner
    whose verdict is per-file; it is wrong here, because this walk feeds a
    CHECKSUM. Dropping one file's line from `lines` silently redefines the tree
    the digest covers. The immediate consequence is a mismatch against a lock
    that is in fact correct, and the dangerous one is the mirror of it: under
    `--relock` a skipped file would be written into `skills-lock.json` as the
    new pin, so the next verification would accept a tree missing that file.
    A lock over a tree that changed underneath the hash is not a lock, so this
    fails loudly and names the file instead of hashing what is left.

    The retry is not decoration and it is not a fix for deletion. It recovers
    exactly one shape: a writer that unlinks and rewrites, leaving the path
    briefly absent. A file that is genuinely gone is still gone on the second
    look, and that is the case this refuses.
    """
    try:
        return read(p)
    except FileNotFoundError:
        pass
    try:
        return read(p)
    except FileNotFoundError as e:
        raise TreeChangedUnderVerification(
            f"{p} vanished between the tree walk and the hash. The vendored "
            f"tree changed while it was being verified, so no digest computed "
            f"over it can be trusted. Re-run once the tree is quiet."
        ) from e


def _tree_hash(tree_dir: Path) -> str:
    """Compute the sha256-tree-v1 digest over a vendored skill directory."""
    # `is_file()` FOLLOWS symlinks, so a vendored file replaced by a link to an
    # identical-content file elsewhere hashed the same and the substitution --
    # the one thing this verifier exists to catch -- went undetected. A link
    # OUTSIDE the tree also made the digest depend on content the lock does not
    # cover, and a broken link vanished silently. Links are now recorded as
    # links, by target, and never followed.
    entries = sorted(
        (p for p in tree_dir.rglob("*") if p.is_symlink() or p.is_file()),
        key=lambda p: p.relative_to(tree_dir).as_posix(),
    )
    lines = []
    for p in entries:
        rel = p.relative_to(tree_dir).as_posix()
        if p.is_symlink():
            target = _locked_read(p, os.readlink)
            lines.append(f"{rel}\nsymlink:{target}\n")
            continue
        # LF-normalize so the digest matches git's `* text=auto` storage and is
        # stable across checkouts (a CRLF working copy must hash the same as a
        # fresh LF CI checkout).
        content = _locked_read(p, Path.read_bytes).replace(b"\r\n", b"\n")
        digest = hashlib.sha256(content).hexdigest()
        lines.append(f"{rel}\n{digest}\n")
    return hashlib.sha256("".join(lines).encode("utf-8")).hexdigest()


def _vendored_dir(root: Path, entry: dict) -> Path | None:
    """Resolve the on-disk vendored tree for a lock entry, or None."""
    skill_path = entry.get("skillPath")
    if not isinstance(skill_path, str) or not skill_path:
        # `isinstance`, not truthiness. Every other wrong shape in this file is
        # converted into a clean FAIL line; a number, list or object here still
        # reached `Path()` and raised TypeError, killing the verifier with an
        # uncontrolled exit code.
        return None
    rel_parent = Path(skill_path).parent
    if Path(skill_path).is_absolute() or rel_parent in (Path("."), Path("")):
        # A parent-less `skillPath` ("SKILL.md") made tree_dir the WORKSPACE
        # ROOT, and `--relock` would then pin a hash of the entire repository --
        # changing on every commit and failing verification forever after. No
        # adversary needed; the footgun is the entry itself.
        return None
    tree = (root / rel_parent).resolve()
    root_r = root.resolve()
    # One condition, and it must stay one. The old form was
    # `tree != root_r and root_r not in tree.parents`, which made tree == root
    # an ACCEPTED result: the first conjunct went False and short-circuited the
    # whole test. Any `skillPath` with a `..` that resolves back up -- say
    # `x/../SKILL.md` -- therefore returned the workspace root, which is exactly
    # the footgun the comment above says was closed; the parent-less guard only
    # catches the literal spelling of it.
    #
    # `root_r not in tree.parents` alone covers both cases, because a path is
    # never a member of its own `.parents`. Re-adding an explicit `tree ==
    # root_r` test would be dead weight, not defence.
    if root_r not in tree.parents:
        return None
    return tree


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

    if not isinstance(lock, dict):
        # Valid JSON, wrong shape. A list here reached `lock.get` as an
        # AttributeError traceback instead of the clean FAIL path an unreadable
        # file already gets.
        print(f"{RED}FAIL{RESET}  skills-lock.json is a "
              f"{type(lock).__name__}, expected an object")
        return 1

    recipe = lock.get("recipe")
    if recipe != RECIPE:
        print(f"{RED}FAIL{RESET}  lock recipe {recipe!r} != expected {RECIPE!r}")
        return 1

    issues = 0
    changed = False
    hashed = 0
    skills = lock.get("skills", {})
    if not isinstance(skills, dict):
        print(f"{RED}FAIL{RESET}  lock 'skills' is a "
              f"{type(skills).__name__}, expected an object")
        return 1

    for name, entry in sorted(skills.items()):
        if not isinstance(entry, dict):
            print(f"{RED}FAIL{RESET}  {name}: entry is a "
                  f"{type(entry).__name__}, expected an object")
            issues += 1
            continue
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
        try:
            actual = _tree_hash(tree_dir)
        except TreeChangedUnderVerification as e:
            # A clean FAIL line, like every other wrong shape in this file, and
            # NOT a pass. `hashed` is deliberately not incremented: this entry
            # was not verified, and the `not hashed` guard below exists so a
            # gate cannot report success over trees it never hashed.
            print(f"{RED}FAIL{RESET}  {name}: {e}")
            issues += 1
            continue
        hashed += 1
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
        if issues:
            # `return 0` here DISCARDED `issues`: a missing vendored tree
            # alongside any other change gave CI a green run, a rewritten lock,
            # and a stale hash still pinned for the tree nobody could verify.
            print(f"\n{RED}{BOLD}{issues} entr(ies) could not be verified and were "
                  f"NOT re-locked.{RESET}")
            return 1
        return 0

    if issues:
        print(f"\n{RED}{BOLD}{issues} vendored-skill integrity issue(s).{RESET} "
              f"If the change is intentional, re-lock with --relock.")
        return 1
    if not hashed:
        # Nothing counted the trees actually hashed, so a lock whose `skills`
        # map was absent, empty, or every entry `vendored: false` produced zero
        # comparisons, zero issues, the green line and exit 0 - in CI and at
        # pre-push, the two places this gate is trusted. Deleting the one real
        # entry disarmed the gate while it kept reporting a pass. The sibling
        # `scripts/validate-crm-schema.py` closed this same class explicitly.
        print(f"\n{RED}{BOLD}No vendored skill was hashed.{RESET} The lock lists "
              f"nothing to verify, so a pass here would assert an integrity "
              f"check that never ran.")
        return 1
    if not quiet:
        print(f"\n{GREEN}{BOLD}Vendored skills verified.{RESET} "
              f"({hashed} tree(s) hashed)")
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
