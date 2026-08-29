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
idempotent (a second run imports 0 files). Copies go through a UNIQUE temp file
in the destination directory and land via `os.link`, which the filesystem refuses
if the destination appeared in the meantime.

"Already exists" means the NAME is taken, which is what a copy actually collides
with: a dangling symlink counts, even though `Path.exists()` follows the link and
reports False for one. And the refusal `os.link` raises when a destination appears
between the check and the link is the same skip, not a reason to abandon the run.
Both were aborts with a traceback until 2026-08-25 -- the second reachable without
any race, since a dangling symlink made `os.link` fail every single time -- so a
policy this file documents as skip-and-continue killed the import and left every
remaining file and subtree unprocessed.

Where the filesystem has no hard links (FAT32, exFAT, some network mounts), the
copy falls back to `os.replace`, which CAN clobber. That branch re-checks the
destination immediately before the replace and prints that the guarantee is
degraded for that file; until 2026-08-30 it did neither and silently overwrote.

Two source-side rules, both added 2026-08-30 after the same audit:
a source SYMLINK is never followed -- it is counted and named, so a link pointing
outside the four subtrees cannot pull its target's content into the overlay --
and a source file that cannot be READ is counted, named, and skipped, with the
walk continuing and the run exiting 1. One unreadable file used to end the whole
import with a traceback, no totals, and every later subtree unprocessed.

Usage:
    # dry-run first -- shows exactly what WOULD be copied, writes nothing
    python scripts/import-legacy-records.py --from /path/to/old-workspace --dry-run

    # live import (all four subtrees)
    python scripts/import-legacy-records.py --from /path/to/old-workspace

    # restrict to one or more subtrees
    python scripts/import-legacy-records.py --from /old --only crm --only threads

    # best-effort: scan sibling dirs for a plausible old root, then re-run with --from
    python scripts/import-legacy-records.py --auto-detect

Tests: tests/test_import_legacy_records.py,
       tests/test_an_import_that_died_on_the_skip_it_promised.py
"""

import argparse
import errno
import os
import shutil
import sys
import tempfile
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
    """Copy src_file to dest_file atomically, never overwriting anything.

    Preconditions: caller verified path safety.

    Two ways this destroyed data before 2026-08-24, both from the fixed scratch
    name `<dest>.tmp-import`:

      - a file ALREADY at that name in the destination directory was
        overwritten by `copy2` and then moved away by `os.replace`, so its
        contents were gone. This importer's whole documented invariant is that
        an existing destination file is never overwritten, and the scratch path
        is a destination file like any other.
      - two concurrent imports of the same relative path wrote the one scratch
        path and raced.

    `mkstemp` gives each writer its own scratch name, and `os.link` + unlink
    replaces the check-then-`os.replace` with a create-if-absent that the
    filesystem enforces: `link` fails with EEXIST if the destination appeared
    between the caller's check and this call. Where hard links are unavailable
    it falls back to `os.replace`, re-checking the destination immediately
    beforehand and printing that the guarantee is degraded for that file - until
    2026-08-30 that branch did neither, and simply overwrote.
    """
    dest_file.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(dest_file.parent),
                                    prefix=dest_file.name + ".", suffix=".tmp-import")
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        shutil.copy2(src_file, tmp)
        try:
            os.link(tmp, dest_file)
        except OSError as exc:
            if exc.errno == errno.EEXIST:
                raise FileExistsError(
                    f"{dest_file} appeared after the existence check; not overwritten"
                ) from exc
            # No hard-link support on this filesystem (FAT32, exFAT, some
            # network mounts - all reachable recovery targets). `os.replace` is
            # the only option left and it CLOBBERS: measured 2026-08-30 with
            # `os.link` raising EPERM and a destination already holding content,
            # this line overwrote that content and printed nothing, against the
            # one invariant the module docstring leads with.
            #
            # `os.replace` cannot be made create-if-absent, so the check is
            # moved as close to it as it can get and the degradation is SAID
            # rather than left for a reader of the source. A name taken in the
            # microseconds that remain is the same FileExistsError the link path
            # raises, which the caller already counts as a skip.
            if os.path.lexists(dest_file):
                raise FileExistsError(
                    f"{dest_file} appeared after the existence check; not overwritten"
                ) from exc
            print(f"    {YELLOW}warning{RESET} {dest_file.name}: no hard-link "
                  f"support here, so the create-if-absent guarantee is degraded "
                  f"to a check-then-replace for this file")
            os.replace(tmp, dest_file)
            return
        tmp.unlink()
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


def _import_subtree(
    src_dir: Path, dest_dir: Path, *, dry_run: bool
) -> tuple[int, int, int, int, int]:
    """Walk src_dir; copy each file to dest_dir preserving structure.

    Returns (imported, skipped_existing, skipped_unsafe, skipped_link, failed).
    - imported: file copied (or, in dry-run, would be copied)
    - skipped_existing: the destination NAME is taken -> never overwritten.
      Counted whether the name was taken before the walk reached it or by
      another writer during the copy itself.
    - skipped_unsafe: destination escaped dest_dir (traversal) -> refused
    - skipped_link: the SOURCE entry is a symlink -> not followed
    - failed: the source file could not be read -> reported, walk continues
    """
    imported = skipped_existing = skipped_unsafe = 0
    skipped_link = failed = 0
    dest_root_resolved = dest_dir.resolve()

    for src_file in sorted(src_dir.rglob("*")):
        # A SOURCE symlink, checked before `is_file()`, which follows one.
        # Every path-safety guard in this file watches the destination; nothing
        # watched the source, so `ln -s /etc/passwd old/knowledge/link.md` had
        # that file's CONTENT imported into the data overlay under a .md name.
        # Measured 2026-08-30. The tool's docstring scopes it to four subtrees
        # off disk, this workspace keeps no symlinks by standing decision, and a
        # recovery run must not quietly widen its own collection surface.
        # (`rglob` does not descend into symlinked DIRECTORIES on any supported
        # Python, checked on 3.11.15, so only the entry itself is at issue.)
        if src_file.is_symlink():
            skipped_link += 1
            print(f"    {YELLOW}symlink{RESET} "
                  f"{src_file.relative_to(src_dir)} (not followed)")
            continue
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

        # `lexists`, not `exists`: a DANGLING symlink is a directory entry whose
        # name is taken, but `exists()` follows the link and answers False for
        # it. Such a name then passed this check and `os.link` refused it with
        # EEXIST on every single attempt -- no race required.
        if os.path.lexists(dest_file):
            skipped_existing += 1
            continue

        # Can this source actually be read? One unreadable file (`chmod 000`, a
        # bad sector, a permission the old workspace carried and this user does
        # not) raised PermissionError out of `shutil.copy2`, out of this walk and
        # out of `main` as a traceback: every remaining file in the subtree and
        # every later subtree went unprocessed, no totals were printed, and the
        # per-subtree lines already on screen read as success. Measured
        # 2026-08-30 on a two-subtree tree. The check is deliberately narrow -
        # it asks only about THIS file's readability, so a full disk or any
        # other whole-run failure still propagates out of `_atomic_copy` as
        # before, because that is not a per-file skip and must not read as one.
        if not dry_run:
            try:
                with src_file.open("rb"):
                    pass
            except OSError as exc:
                failed += 1
                print(f"    {RED}unreadable{RESET} {rel}: "
                      f"{exc.strerror or exc}")
                continue
            try:
                _atomic_copy(src_file, dest_file)
            except FileExistsError:
                # The check-then-link race the copy exists to catch: another
                # writer took the name in between. That is this importer's
                # documented skip, so count it and keep walking.
                skipped_existing += 1
                continue
        imported += 1

    return imported, skipped_existing, skipped_unsafe, skipped_link, failed


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
    tot_link = tot_failed = 0

    for key in selected:
        spec = SUBTREES[key]
        src_dir = _resolve_source(from_root, spec["sources"])
        if src_dir is None:
            tried = " | ".join(spec["sources"])
            print(f"  {GRAY}{spec['label']}: source absent ({tried}) — skipped{RESET}")
            continue

        dest_dir = spec["dest"]()
        imported, skipped, unsafe, links, failed = _import_subtree(
            src_dir, dest_dir, dry_run=args.dry_run
        )
        tot_imported += imported
        tot_skipped += skipped
        tot_unsafe += unsafe
        tot_link += links
        tot_failed += failed

        verb = "would import" if args.dry_run else "imported"
        line = (
            f"  {GREEN}{spec['label']}{RESET}: {verb} {BOLD}{imported}{RESET}, "
            f"skipped {BOLD}{skipped}{RESET} (already exist)"
        )
        if unsafe:
            line += f", {RED}{unsafe} refused (unsafe path){RESET}"
        if links:
            line += f", {YELLOW}{links} symlink(s) not followed{RESET}"
        if failed:
            line += f", {RED}{failed} unreadable{RESET}"
        print(line)
        print(f"    {GRAY}{src_dir}  ->  {dest_dir}{RESET}")

    verb = "would import" if args.dry_run else "imported"
    print(
        f"\n{BOLD}Total:{RESET} {verb} {GREEN}{tot_imported}{RESET}, "
        f"skipped {tot_skipped} (already exist)"
        + (f", {RED}{tot_unsafe} refused{RESET}" if tot_unsafe else "")
        + (f", {YELLOW}{tot_link} symlink(s) not followed{RESET}" if tot_link else "")
        + (f", {RED}{tot_failed} unreadable{RESET}" if tot_failed else "")
    )
    if args.dry_run:
        print(f"{YELLOW}Dry-run: nothing was written.{RESET}")
    # A recovery run that left files behind did not succeed, and an operator who
    # scripts this needs the exit code to say so. Symlinks are a POLICY skip and
    # do not fail the run; a file this tool could not read is a hole in the
    # recovery and does.
    if tot_failed:
        print(f"{RED}{tot_failed} source file(s) could not be read and were not "
              f"imported.{RESET}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
