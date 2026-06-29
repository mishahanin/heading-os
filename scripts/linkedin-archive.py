#!/usr/bin/env python3
"""linkedin-archive.py - move a published LinkedIn post into the typed archive.

Default is dry-run: prints what would move, no changes. Pass --execute to apply.

Usage:
    python scripts/linkedin-archive.py                            # dry-run, latest .md
    python scripts/linkedin-archive.py --slug <slug>              # dry-run, explicit slug
    python scripts/linkedin-archive.py --type post                # disambiguate type
    python scripts/linkedin-archive.py --image path/to/img.png    # attach image (repeatable)
    python scripts/linkedin-archive.py --execute --commit         # apply moves + auto-commit

The staged .md is relocated within the repo with `git mv` (history preserved); it
must therefore be tracked first (exit 7 otherwise). Images are arbitrary external
attachments (a /mnt/c screenshot, a clipboard save, a Downloads file), so they are
COPIED into the archive folder and `git add`-ed - never `git mv`-ed, which cannot
move a source from outside the repo. The original image file is left untouched.

Exit codes:
    0 success
    2 no .md found in source dir (or slug not found)
    3 destination folder already exists
    4 git mv (.md) / copy or git add (image) failed mid-sequence
    5 type ambiguous (no frontmatter, no _linkedin-(type)_ in filename, no --type)
    6 --image path does not exist
    7 .md source file is untracked (git mv would partial-fail; images need not be tracked)
    8 auto-commit failed
    9 git command timed out (30s)
   10 archive root directory missing
"""
from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.utils.workspace import get_data_root, get_datastore_dir, get_outputs_dir

FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
TYPE_FRONTMATTER_RE = re.compile(r"^type:\s*linkedin-(article|post|comment)\s*$", re.MULTILINE)
TYPE_FILENAME_RE = re.compile(r"_linkedin-(article|post|comment)_")
GIT_TIMEOUT_SECONDS = 30


def _run_git(cmd: list[str], ws: Path) -> subprocess.CompletedProcess:
    """Run a git command with a hard 30s timeout. Exit with code 9 on hang."""
    try:
        return subprocess.run(
            cmd, cwd=str(ws), capture_output=True, text=True, timeout=GIT_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        print(f"ABORT: git command timed out after {GIT_TIMEOUT_SECONDS}s: {' '.join(cmd)}",
              file=sys.stderr)
        raise SystemExit(9)


def detect_type(md: Path) -> str | None:
    """Return 'article'/'post'/'comment' or None if ambiguous."""
    text = md.read_text(encoding="utf-8", errors="replace")
    fm = FRONTMATTER_RE.match(text)
    if fm:
        m = TYPE_FRONTMATTER_RE.search(fm.group(1))
        if m:
            return m.group(1)
    m = TYPE_FILENAME_RE.search(md.name)
    if m:
        return m.group(1)
    return None


def find_latest(source_dir: Path, slug: str | None = None) -> Path | None:
    """Latest .md in source_dir top level (analytics/ subdir is excluded via is_file filter)."""
    if not source_dir.exists():
        return None
    # iterdir() is intentional - top-level only excludes the analytics/ subdir per skill contract.
    candidates = [p for p in source_dir.iterdir() if p.is_file() and p.suffix == ".md"]
    if slug is not None:
        for p in candidates:
            if p.stem == slug:
                return p
        return None
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def find_untracked(paths: list[Path], ws: Path) -> list[Path]:
    """Return paths that are NOT tracked by git. Single batched git invocation."""
    if not paths:
        return []
    result = _run_git(
        ["git", "ls-files", "--error-unmatch", "--", *[str(p) for p in paths]],
        ws,
    )
    if result.returncode == 0:
        return []
    # Stderr lines look like: "error: pathspec 'foo.md' did not match any file(s) known to git"
    flagged = set()
    for line in result.stderr.splitlines():
        m = re.search(r"pathspec '([^']+)'", line)
        if m:
            flagged.add(m.group(1))
    untracked = []
    for p in paths:
        s = str(p)
        if s in flagged or s.replace("\\", "/") in flagged:
            untracked.append(p)
    return untracked


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Move published LinkedIn content to archive.")
    parser.add_argument("--slug", default=None, help="Explicit slug (default: latest .md by mtime)")
    parser.add_argument("--type", choices=["article", "post", "comment"], default=None,
                        help="Force content type when filename / frontmatter is ambiguous")
    parser.add_argument("--image", action="append", default=[],
                        help="Path to image to move with .md (repeatable)")
    parser.add_argument("--execute", action="store_true", help="Apply moves (default: dry-run)")
    parser.add_argument("--commit", action="store_true", help="git commit after successful execute")
    args = parser.parse_args(argv)

    data_root = get_data_root()
    source_dir = get_outputs_dir() / "content" / "linkedin"
    archive_root = get_datastore_dir() / "content" / "linkedin-archive"

    if not archive_root.is_dir():
        print(f"ABORT: archive root missing: {archive_root}", file=sys.stderr)
        return 10

    md = find_latest(source_dir, slug=args.slug)
    if md is None:
        suffix = f" for slug '{args.slug}'" if args.slug else ""
        print(f"No content found in {source_dir}{suffix}", file=sys.stderr)
        return 2

    content_type = args.type or detect_type(md)
    if content_type is None:
        print(
            f"ABORT: cannot determine type for {md.name}. "
            "Pass --type {article|post|comment} or add 'type: linkedin-<type>' to frontmatter.",
            file=sys.stderr,
        )
        return 5

    type_dir = {"article": "articles", "post": "posts", "comment": "comments"}[content_type]
    slug = md.stem
    dest_folder = archive_root / type_dir / slug

    images = [Path(p).resolve() for p in args.image]
    for img in images:
        if not img.exists():
            print(f"ABORT: image not found: {img}", file=sys.stderr)
            return 6

    mode = "EXECUTE" if args.execute else "DRY RUN"
    print(f"=== LinkedIn Archive [{mode}] ===")
    print(f"Type:  {content_type}")
    print(f"Slug:  {slug}")
    print(f"Dest:  {dest_folder}")
    print()
    print(f"  {md}")
    print(f"    -> {dest_folder / md.name}")
    if images:
        print()
        print(f"Images ({len(images)}):")
        for img in images:
            print(f"  {img}")
            print(f"    -> {dest_folder / img.name}")

    if dest_folder.exists():
        print(f"\nABORT: destination already exists: {dest_folder}", file=sys.stderr)
        return 3

    # Only the .md must be tracked - it is git-mv'd. Images are copied in (see below),
    # so they need not be tracked and must NOT be passed here: an out-of-repo image path
    # makes `git ls-files` fail with "outside repository", which the parser cannot read
    # and which would mask a genuinely untracked .md (turning exit 7 into a confusing 4).
    untracked = find_untracked([md], data_root)
    if untracked:
        print(
            f"\nABORT: {len(untracked)} source file(s) untracked - run `git add` first:",
            file=sys.stderr,
        )
        for p in untracked:
            print(f"  {p}", file=sys.stderr)
        return 7

    if not args.execute:
        return 0

    dest_folder.mkdir(parents=True)

    # 1. The .md is a repo-internal relocation: git mv preserves its history.
    md_dest = dest_folder / md.name
    result = _run_git(["git", "mv", "--", str(md), str(md_dest)], data_root)
    if result.returncode != 0:
        print(f"ERROR: git mv failed for {md.name}: {result.stderr.strip()}", file=sys.stderr)
        try:
            dest_folder.rmdir()  # nothing moved yet - clean up the empty dest
        except OSError:
            pass
        return 4

    # 2. Images are arbitrary external attachments. Copy them in (the original is left
    #    in place) and git add the copy - git mv cannot move a source from outside the repo.
    for img in images:
        img_dest = dest_folder / img.name
        try:
            shutil.copy2(str(img), str(img_dest))
        except OSError as exc:
            print(f"ERROR: failed to copy image {img.name}: {exc}", file=sys.stderr)
            return 4
        result = _run_git(["git", "add", "--", str(img_dest)], data_root)
        if result.returncode != 0:
            print(f"ERROR: git add failed for {img.name}: {result.stderr.strip()}", file=sys.stderr)
            return 4

    print(f"\nMOVED -> {dest_folder}")

    if args.commit:
        msg = f"chore(linkedin-archive): {slug} -> {type_dir}/"
        result = _run_git(["git", "commit", "-m", msg], data_root)
        if result.returncode != 0:
            print(f"WARN: auto-commit failed: {result.stderr.strip()}", file=sys.stderr)
            return 8
        print(f"COMMITTED: {msg}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
