#!/usr/bin/env python3
"""One-shot, local import of an exec's legacy records into the new data overlay.

Recovers records from a previous on-disk workspace after a clean HEADING OS
deploy. It is the non-destructive replacement for the retired
`workspace-sync.py` corporate-pull: it copies four subtrees off disk into the
data overlay and never deletes, never touches the network, and never runs git.

Subtrees imported (source -> destination, destinations resolved ONLY through the
data-root helpers so imports land in the data overlay, never the engine tree):

    <from>/crm/contacts/                 -> get_crm_contacts_dir()
    <from>/threads/                      -> get_threads_dir()
    <from>/knowledge/                    -> get_knowledge_dir()
    <from>/personal/context/ | context/ -> get_personal_context_dir()

Collision policy is fail-safe: a destination file that already exists is NEVER
overwritten -- it is counted as "skipped" and reported. Re-running is therefore
idempotent (a second run imports 0 files). Copies are atomic (temp file in the
destination directory, then os.replace).

Usage:
    # dry-run first -- shows exactly what WOULD be copied, writes nothing
    python scripts/import-legacy-records.py --from /path/to/old-workspace --dry-run

    # live import (all four subtrees)
    python scripts/import-legacy-records.py --from /path/to/old-workspace

    # restrict to one or more subtrees
    python scripts/import-legacy-records.py --from /old --only crm --only threads

    # best-effort: scan sibling dirs for a plausible old root, then re-run with --from
    python scripts/import-legacy-records.py --auto-detect
"""

import argparse
import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils.colors import BOLD, CYAN, GRAY, GREEN, RED, RESET, YELLOW
from scripts.utils.workspace import (
    get_crm_contacts_dir,
    get_knowledge_dir,
    get_personal_context_dir,
    get_threads_dir,
    get_workspace_root,
)

# ============================================================
# Subtree definitions
# ============================================================

# Each subtree: a key, the source-relative path(s) to try (first existing wins),
# and a callable that resolves the destination through the data-root helpers.
SUBTREES = {
    "crm": {
        "label": "CRM contacts",
        # The sources below are subpaths UNDER the user's --from root (the old
        # records location), not data-root paths; destinations resolve via the
        # get_*_dir() helpers. The crm one trips the leak-guard token, suppressed
        # inline:
        "sources": ["crm/contacts"],  # leak-guard: ok (source-relative subpath under --from)
        "dest": get_crm_contacts_dir,
    },
    "threads": {
        "label": "threads",
        "sources": ["threads"],
        "dest": get_threads_dir,
    },
    "knowledge": {
        "label": "knowledge",
        "sources": ["knowledge"],
        "dest": get_knowledge_dir,
    },
    # Personal context: exec two-layer keeps it under personal/context/; the
    # legacy flat layout keeps it under context/. First existing wins.
    "context": {
        "label": "personal context",
        "sources": ["personal/context", "context"],
        "dest": get_personal_context_dir,
    },
}


# ============================================================
# Helpers
# ============================================================


def _resolve_source(from_root: Path, rel_candidates: list) -> Path | None:
    """Return the first existing source subtree directory, or None."""
    for rel in rel_candidates:
        candidate = from_root / rel
        if candidate.is_dir():
            return candidate
    return None


def _atomic_copy(src_file: Path, dest_file: Path) -> None:
    """Copy src_file to dest_file atomically (temp in dest dir, then os.replace).

    Preconditions: dest_file does not exist; caller verified path safety.
    """
    dest_file.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest_file.parent / (dest_file.name + ".tmp-import")
    shutil.copy2(src_file, tmp)
    os.replace(tmp, dest_file)


def _import_subtree(
    src_dir: Path, dest_dir: Path, *, dry_run: bool
) -> tuple[int, int, int]:
    """Walk src_dir; copy each file to dest_dir preserving structure.

    Returns (imported, skipped_existing, skipped_unsafe).
    - imported: file copied (or, in dry-run, would be copied)
    - skipped_existing: destination already exists -> never overwritten
    - skipped_unsafe: destination escaped dest_dir (traversal) -> refused
    """
    imported = skipped_existing = skipped_unsafe = 0
    dest_root_resolved = dest_dir.resolve()

    for src_file in sorted(src_dir.rglob("*")):
        if not src_file.is_file():
            continue
        rel = src_file.relative_to(src_dir)
        dest_file = dest_dir / rel

        # Path-safety: the destination must stay under dest_dir even if a source
        # name contains '..' or other traversal. Resolve and prefix-check.
        try:
            resolved = (dest_root_resolved / rel).resolve()
        except (OSError, RuntimeError):
            skipped_unsafe += 1
            print(f"    {RED}unsafe{RESET} {rel} (cannot resolve)")
            continue
        if not resolved.is_relative_to(dest_root_resolved):
            skipped_unsafe += 1
            print(f"    {RED}unsafe{RESET} {rel} (escapes destination)")
            continue

        if dest_file.exists():
            skipped_existing += 1
            continue

        if not dry_run:
            _atomic_copy(src_file, dest_file)
        imported += 1

    return imported, skipped_existing, skipped_unsafe


def _auto_detect(workspace_root: Path) -> list:
    """Best-effort: scan sibling dirs for a plausible old workspace.

    A candidate is any sibling directory that contains a crm/contacts/ subtree.
    Named CEO-machine patterns are ALSO listed, but they are CEO-biased and will
    usually be absent on an exec deploy -- the generic crm/contacts/ scan is what
    gives auto-detect value on a real deploy target.
    """
    parent = workspace_root.parent
    named_globs = ["31c-workspace-*", "ceo-main", "ms-steward", "heading-os-data*"]
    candidates: list = []
    seen: set = set()

    def _consider(path: Path) -> None:
        rp = path.resolve()
        if rp in seen or rp == workspace_root.resolve():
            return
        seen.add(rp)
        if path.is_dir() and (path / "crm" / "contacts").is_dir():
            candidates.append(path)

    # Generic scan: any sibling with crm/contacts/.
    if parent.is_dir():
        for child in sorted(parent.iterdir()):
            _consider(child)
    # Named patterns (CEO-biased; may overlap the generic scan).
    for pattern in named_globs:
        for path in sorted(parent.glob(pattern)):
            _consider(path)

    return candidates


# ============================================================
# Main
# ============================================================


def main() -> int:
    parser = argparse.ArgumentParser(
        description="One-shot, local, non-destructive import of legacy records "
        "into the new data overlay.",
    )
    parser.add_argument(
        "--from",
        dest="from_path",
        help="Root of the old records on disk (the previous workspace).",
    )
    parser.add_argument(
        "--auto-detect",
        action="store_true",
        help="Best-effort scan of sibling dirs for a plausible old root, then exit.",
    )
    parser.add_argument(
        "--only",
        action="append",
        choices=sorted(SUBTREES.keys()),
        help="Restrict to one or more subtrees (repeatable). Default: all four.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would be imported; write nothing.",
    )
    args = parser.parse_args()

    workspace_root = get_workspace_root()

    if args.auto_detect:
        cands = _auto_detect(workspace_root)
        if not cands:
            print(
                f"{YELLOW}auto-detect: no sibling directory with a crm/contacts/ "
                f"subtree found.{RESET}"
            )
            print("Supply the old records path explicitly with --from <path>.")
            return 1
        print(f"{BOLD}auto-detect candidates (confirm one, then re-run with --from):{RESET}")
        for c in cands:
            print(f"  {CYAN}{c}{RESET}")
        return 0

    if not args.from_path:
        parser.error("--from is required (or use --auto-detect to find candidates).")

    from_root = Path(args.from_path).expanduser()
    if not from_root.is_dir():
        print(f"{RED}ERROR: --from path is not a directory: {from_root}{RESET}")
        return 2

    selected = args.only or sorted(SUBTREES.keys())
    mode = f"{YELLOW}DRY-RUN{RESET} — " if args.dry_run else ""
    print(f"{BOLD}{mode}Importing legacy records from {CYAN}{from_root}{RESET}\n")

    tot_imported = tot_skipped = tot_unsafe = 0

    for key in selected:
        spec = SUBTREES[key]
        src_dir = _resolve_source(from_root, spec["sources"])
        if src_dir is None:
            tried = " | ".join(spec["sources"])
            print(f"  {GRAY}{spec['label']}: source absent ({tried}) — skipped{RESET}")
            continue

        dest_dir = spec["dest"]()
        imported, skipped, unsafe = _import_subtree(
            src_dir, dest_dir, dry_run=args.dry_run
        )
        tot_imported += imported
        tot_skipped += skipped
        tot_unsafe += unsafe

        verb = "would import" if args.dry_run else "imported"
        line = (
            f"  {GREEN}{spec['label']}{RESET}: {verb} {BOLD}{imported}{RESET}, "
            f"skipped {BOLD}{skipped}{RESET} (already exist)"
        )
        if unsafe:
            line += f", {RED}{unsafe} refused (unsafe path){RESET}"
        print(line)
        print(f"    {GRAY}{src_dir}  ->  {dest_dir}{RESET}")

    verb = "would import" if args.dry_run else "imported"
    print(
        f"\n{BOLD}Total:{RESET} {verb} {GREEN}{tot_imported}{RESET}, "
        f"skipped {tot_skipped} (already exist)"
        + (f", {RED}{tot_unsafe} refused{RESET}" if tot_unsafe else "")
    )
    if args.dry_run:
        print(f"{YELLOW}Dry-run: nothing was written.{RESET}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
