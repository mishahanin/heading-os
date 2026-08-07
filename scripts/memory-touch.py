#!/usr/bin/env python3
"""memory-touch.py -- bump auto-memory access_count/last_accessed (Gap #2).

Usage:
    python scripts/memory-touch.py <path> [<path> ...]

Each <path> may be relative to the auto-memory directory (get_auto_memory_dir())
or absolute. Refuses any path that does not resolve inside that directory.

Does a minimal, targeted text edit scoped to the frontmatter `metadata:` block:
increments `access_count` (inserting it at 1 if absent) and sets
`last_accessed` to today's date (get_default_tz()). Every other line --
comments, key order, unrelated fields, the whole body -- is preserved
byte-for-byte. NOT a full YAML re-serialize.

Writes atomically (tempfile + os.replace(), scripts.utils.atomic).

Consumed by:
  - .claude/skills/recall/SKILL.md (Phase 1, one touch per cited memory-layer hit)
  - scripts/utils/memory_touch.py holds the logic; this file is the CLI surface.
"""
import argparse
import datetime
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils.colors import GRAY, GREEN, RED, RESET
from scripts.utils.memory_touch import TouchError, _bump_frontmatter, touch_file  # noqa: F401
from scripts.utils.workspace import get_auto_memory_dir, get_default_tz


def main() -> int:
    ap = argparse.ArgumentParser(description="Bump auto-memory access_count/last_accessed")
    ap.add_argument("paths", nargs="+", help="auto-memory file path(s), relative or absolute")
    args = ap.parse_args()

    auto_memory_dir = get_auto_memory_dir()
    today = datetime.datetime.now(get_default_tz()).date().isoformat()

    exit_code = 0
    for raw_path in args.paths:
        try:
            access_count, resolved = touch_file(raw_path, auto_memory_dir, today)
        except TouchError as exc:
            sys.stderr.write(f"{RED}refused:{RESET} {exc}\n")
            exit_code = 1
            continue
        print(
            f"{GREEN}touched{RESET} {resolved} "
            f"{GRAY}access_count={access_count} last_accessed={today}{RESET}"
        )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
