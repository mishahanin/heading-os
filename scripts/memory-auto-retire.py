#!/usr/bin/env python3
"""Auto-retire memory files whose explicit ``expires:`` date has passed.

The safe, deterministic slice of /dream: it acts ONLY on records the author
pre-authorized by stamping an expiry date, so no judgement happens at
retire-time. Orphans, redundancy pairs, contradictions, and rewording remain a
human-gated /dream call - this script never touches them.

Retires both-store (canonical DATA + every native harness store) via
retire_memory, then strips the record's pointer line from MEMORY.md. The managed
``## Active Threads`` section is safe: pointers there reference thread PATHS, not
bare top-level filenames.

Usage:
    python scripts/memory-auto-retire.py            # act: retire past-due, update index
    python scripts/memory-auto-retire.py --dry-run  # show what would go, mutate nothing
"""
import argparse
import datetime
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils.colors import GRAY, GREEN, RESET, YELLOW
from scripts.utils.memory_expiry import find_expired, strip_index_pointers
from scripts.utils.memory_stores import retire_memory
from scripts.utils.workspace import get_auto_memory_dir, get_default_tz
from scripts.utils.paths import load_env, log_dir

INDEX_NAME = "MEMORY.md"
LOG_PATH = log_dir("memory-auto-retire.log")


def _log_line(msg: str) -> None:
    """Append a timestamped audit line locally (console-first: also printed)."""
    ts = datetime.datetime.now(get_default_tz()).isoformat(timespec="seconds")
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open("a", encoding="utf-8") as fh:
            fh.write(f"{ts} {msg}\n")
    except OSError:
        pass  # a failed audit write must not block the retire itself


def main() -> int:
    # First, before anything reads the clock. `get_default_tz()` reads os.environ
    # ONLY, and HEADING_OS_TZ lives in the gitignored .env, which nothing exports
    # -- so without this the run compares an `expires:` date against a UTC
    # "today" while the timer fires on local time. Expiry is date-granular, so a
    # single day's error retires a memory early or keeps a dead one alive.
    load_env()

    ap = argparse.ArgumentParser(description="Auto-retire memories past their expires: date")
    ap.add_argument("--dry-run", action="store_true", help="show candidates, mutate nothing")
    args = ap.parse_args()

    memory_dir = get_auto_memory_dir()
    today = datetime.datetime.now(get_default_tz()).date()
    expired = find_expired(memory_dir, today)

    if not expired:
        print(f"{GRAY}no expired memories (checked {today.isoformat()}){RESET}")
        return 0

    if args.dry_run:
        print(f"{YELLOW}would retire {len(expired)} expired memor{'y' if len(expired)==1 else 'ies'}:{RESET}")
        for name, exp in expired:
            print(f"  {name} (expired {exp.isoformat()})")
        return 0

    names = [name for name, _ in expired]
    for name, exp in expired:
        removed = retire_memory(name)
        print(f"{GREEN}retired{RESET} {name} (expired {exp.isoformat()}): {len(removed)} store(s)")
        _log_line(f"retired {name} expired={exp.isoformat()} stores={len(removed)}")

    # Strip pointers from MEMORY.md in one rewrite.
    index = memory_dir / INDEX_NAME
    if index.exists():
        before = index.read_text(encoding="utf-8")
        after = strip_index_pointers(before, names)
        if after != before:
            index.write_text(after, encoding="utf-8")
            print(f"{GREEN}updated{RESET} {INDEX_NAME} (removed {before.count(chr(10)) - after.count(chr(10))} pointer line(s))")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
