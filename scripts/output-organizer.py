#!/usr/bin/env python3
"""
Outputs Directory Manager for 31C Workspace

Reports on, organizes, and archives files in the outputs/ directory.

Usage:
    python scripts/output-organizer.py report                  # show what's there
    python scripts/output-organizer.py organize                # dry-run: show what would move
    python scripts/output-organizer.py organize --execute      # actually move files
    python scripts/output-organizer.py archive --days 60       # dry-run: archive old files
    python scripts/output-organizer.py archive --days 60 --execute  # actually archive
"""

import argparse
import shutil
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.utils.colors import GREEN, YELLOW, RED, BOLD, RESET
from scripts.utils.workspace import get_workspace_root, get_outputs_dir

WORKSPACE = get_workspace_root()

OUTPUTS_DIR = get_outputs_dir()

# Extension to subdirectory mapping
EXT_MAP = {
    ".png": "images",
    ".jpg": "images",
    ".jpeg": "images",
    ".webp": "images",
    ".gif": "images",
    ".md": "documents",
    ".txt": "documents",
    ".pptx": "presentations",
    ".pdf": "presentations",
    ".svg": "diagrams",
    ".skill": "packages",
}


def report():
    """List files in outputs/ by type and size."""
    if not OUTPUTS_DIR.exists():
        print(f"{RED}outputs/ directory not found{RESET}")
        return

    files = [f for f in OUTPUTS_DIR.iterdir() if f.is_file()]
    if not files:
        print(f"{YELLOW}outputs/ is empty{RESET}")
        return

    # Categorize
    by_type = {}
    total_size = 0
    for f in files:
        ext = f.suffix.lower() or "(none)"
        category = EXT_MAP.get(ext, "other")
        by_type.setdefault(category, []).append(f)
        total_size += f.stat().st_size

    print(f"\n{BOLD}Outputs Directory Report{RESET}")
    print(f"Directory: {OUTPUTS_DIR}")
    print(f"Total files: {len(files)}")
    print(f"Total size: {total_size / (1024*1024):.1f} MB\n")

    for category in sorted(by_type.keys()):
        cat_files = sorted(by_type[category], key=lambda f: f.stat().st_mtime, reverse=True)
        cat_size = sum(f.stat().st_size for f in cat_files)
        print(f"  {BOLD}{category}/{RESET} ({len(cat_files)} files, {cat_size / 1024:.0f} KB)")
        for f in cat_files:
            mtime = datetime.fromtimestamp(f.stat().st_mtime)
            size = f.stat().st_size
            print(f"    {f.name:50s}  {size/1024:6.0f} KB  {mtime.strftime('%Y-%m-%d')}")
        print()


def organize(execute=False):
    """Move files into subdirectories by type."""
    if not OUTPUTS_DIR.exists():
        print(f"{RED}outputs/ directory not found{RESET}")
        return

    files = [f for f in OUTPUTS_DIR.iterdir() if f.is_file()]
    moves = []

    for f in files:
        ext = f.suffix.lower()
        category = EXT_MAP.get(ext)
        if category:
            target_dir = OUTPUTS_DIR / category
            target = target_dir / f.name
            moves.append((f, target, target_dir))

    if not moves:
        print(f"{GREEN}No files to organize - all files are in subdirectories or have unknown types{RESET}")
        return

    print(f"\n{BOLD}{'Organizing' if execute else 'Dry Run - Would organize'} {len(moves)} file(s):{RESET}\n")

    for src, dst, dst_dir in sorted(moves, key=lambda m: m[1]):
        category = dst_dir.name
        print(f"  {src.name} -> {category}/{src.name}")

        if execute:
            dst_dir.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dst))

    if execute:
        print(f"\n{GREEN}Moved {len(moves)} files.{RESET}")
    else:
        print(f"\n{YELLOW}Dry run - no files moved. Use --execute to apply.{RESET}")


def archive(days, execute=False):
    """Move files older than N days to outputs/archive/."""
    if not OUTPUTS_DIR.exists():
        print(f"{RED}outputs/ directory not found{RESET}")
        return

    cutoff = datetime.now() - timedelta(days=days)
    files = [f for f in OUTPUTS_DIR.rglob("*") if f.is_file() and "archive" not in f.parts]
    old_files = []

    for f in files:
        mtime = datetime.fromtimestamp(f.stat().st_mtime)
        if mtime < cutoff:
            old_files.append(f)

    if not old_files:
        print(f"{GREEN}No files older than {days} days found.{RESET}")
        return

    archive_dir = OUTPUTS_DIR / "archive"

    print(f"\n{BOLD}{'Archiving' if execute else 'Dry Run - Would archive'} {len(old_files)} file(s) older than {days} days:{RESET}\n")

    for f in sorted(old_files):
        mtime = datetime.fromtimestamp(f.stat().st_mtime)
        rel = f.relative_to(OUTPUTS_DIR)
        target = archive_dir / rel
        print(f"  {rel} (modified {mtime.strftime('%Y-%m-%d')})")

        if execute:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(f), str(target))

    if execute:
        print(f"\n{GREEN}Archived {len(old_files)} files to outputs/archive/{RESET}")
    else:
        print(f"\n{YELLOW}Dry run - no files moved. Use --execute to apply.{RESET}")


def main():
    parser = argparse.ArgumentParser(description="31C Outputs Directory Manager")
    parser.add_argument("action", choices=["report", "organize", "archive"],
                        help="Action to perform")
    parser.add_argument("--execute", action="store_true",
                        help="Actually move files (default is dry-run)")
    parser.add_argument("--days", type=int, default=60,
                        help="For archive: files older than N days (default: 60)")
    args = parser.parse_args()

    if args.action == "report":
        report()
    elif args.action == "organize":
        organize(execute=args.execute)
    elif args.action == "archive":
        archive(args.days, execute=args.execute)


if __name__ == "__main__":
    main()
