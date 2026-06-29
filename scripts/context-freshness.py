#!/usr/bin/env python3
"""
Context Freshness Marker Manager for 31C Workspace

Manages '> Last verified: YYYY-MM-DD' headers on context files.

Usage:
    python scripts/context-freshness.py check                       # show freshness of all context files
    python scripts/context-freshness.py stamp context/pipeline.md    # update timestamp to today
    python scripts/context-freshness.py touch context/people.md      # mark as reviewed (update date only)
    python scripts/context-freshness.py stamp-all                    # stamp all context files with today's date
"""

import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.utils.colors import GREEN, YELLOW, RED, BOLD, RESET
from scripts.utils.workspace import get_workspace_root, get_context_dir

WORKSPACE = get_workspace_root()

CONTEXT_DIR = get_context_dir()


def get_freshness(filepath):
    """Read the freshness marker from a file. Returns (date_str, age_days) or (None, None)."""
    content = filepath.read_text(encoding="utf-8")
    first_line = content.split("\n")[0] if content else ""
    match = re.match(r">\s*Last verified:\s*(\d{4}-\d{2}-\d{2})", first_line)
    if match:
        date_str = match.group(1)
        verified = datetime.strptime(date_str, "%Y-%m-%d")
        age_days = (datetime.now() - verified).days
        return date_str, age_days
    return None, None


def stamp_file(filepath, date_str=None):
    """Add or update the freshness marker on a file."""
    if date_str is None:
        date_str = datetime.now().strftime("%Y-%m-%d")

    content = filepath.read_text(encoding="utf-8")
    marker = f"> Last verified: {date_str}"

    # Check if first line is already a freshness marker
    lines = content.split("\n")
    if lines and re.match(r">\s*Last verified:", lines[0]):
        lines[0] = marker
    else:
        lines.insert(0, marker)

    filepath.write_text("\n".join(lines), encoding="utf-8")
    return date_str


def check_all():
    """Show freshness status of all context files."""
    context_files = sorted(CONTEXT_DIR.glob("*.md"))
    if not context_files:
        print(f"{RED}No context files found in {CONTEXT_DIR}{RESET}")
        return

    print(f"\n{BOLD}Context File Freshness{RESET}")
    print(f"Directory: {CONTEXT_DIR}\n")

    for f in context_files:
        date_str, age_days = get_freshness(f)
        if date_str:
            if age_days <= 7:
                color = GREEN
                status = "Fresh"
            elif age_days <= 30:
                color = YELLOW
                status = "Aging"
            else:
                color = RED
                status = "Stale"
            print(f"  {color}{status:6s}{RESET}  {f.name:30s}  Verified: {date_str} ({age_days}d ago)")
        else:
            # Fall back to modification time
            mtime = datetime.fromtimestamp(f.stat().st_mtime)
            age_days = (datetime.now() - mtime).days
            print(f"  {YELLOW}{'No marker':6s}{RESET}  {f.name:30s}  Modified: {mtime.strftime('%Y-%m-%d')} ({age_days}d ago)")


def main():
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python scripts/context-freshness.py check")
        print("  python scripts/context-freshness.py stamp <file>")
        print("  python scripts/context-freshness.py touch <file>")
        print("  python scripts/context-freshness.py stamp-all")
        sys.exit(1)

    command = sys.argv[1]

    if command == "check":
        check_all()

    elif command in ("stamp", "touch"):
        if len(sys.argv) < 3:
            print(f"{RED}Error: specify a file path{RESET}")
            sys.exit(1)
        filepath = Path(sys.argv[2])
        if not filepath.is_absolute():
            filepath = WORKSPACE / filepath
        if not filepath.exists():
            print(f"{RED}Error: {filepath} not found{RESET}")
            sys.exit(1)
        date_str = stamp_file(filepath)
        print(f"{GREEN}Stamped{RESET} {filepath.name} with: > Last verified: {date_str}")

    elif command == "stamp-all":
        context_files = sorted(CONTEXT_DIR.glob("*.md"))
        for f in context_files:
            date_str = stamp_file(f)
            print(f"{GREEN}Stamped{RESET} {f.name} with: > Last verified: {date_str}")
        print(f"\n{GREEN}All {len(context_files)} context files stamped.{RESET}")

    else:
        print(f"{RED}Unknown command: {command}{RESET}")
        print("Use: check, stamp, touch, or stamp-all")
        sys.exit(1)


if __name__ == "__main__":
    main()
