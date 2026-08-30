#!/usr/bin/env python3
"""Auto-retire memory files whose explicit ``expires:`` date has passed.

The safe, deterministic slice of /dream: it acts ONLY on records the author
pre-authorized by stamping an expiry date, so no judgement happens at
retire-time. Orphans, redundancy pairs, contradictions, and rewording remain a
human-gated /dream call - this script never touches them.

Retires both-store (canonical DATA + every native harness store) via
retire_memory, then strips the record's pointer line from MEMORY.md. A pointer
that names a PATH rather than a bare top-level filename is never matched. That
rule was written for the ``## Active Threads`` section, retired 2026-08-27, and
it still holds for any pointer into a subdirectory.

Usage:
    python scripts/memory-auto-retire.py            # act: retire past-due, update index
    python scripts/memory-auto-retire.py --dry-run  # show what would go, mutate nothing
"""
import argparse
import datetime
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils.colors import GRAY, GREEN, RED, RESET, YELLOW
from scripts.utils.memory_expiry import find_expired, index_link_targets, strip_index_pointers
from scripts.utils.memory_stores import retire_memory
from scripts.utils.workspace import get_auto_memory_dir, get_default_tz
from scripts.utils.paths import load_env, log_dir

INDEX_NAME = "MEMORY.md"
# `log_dir(*parts)` mkdirs the WHOLE joined path, so passing the filename made
# `.logs/memory-auto-retire.log` a DIRECTORY. Every append then raised
# IsADirectoryError into the `except OSError` below, and the retire audit
# trail recorded nothing from 2026-07-06 until this was found on 2026-08-29.
# Directory from the helper, filename here, as every other caller does.
LOG_PATH = log_dir() / "memory-auto-retire.log"


def _pointers_removed(before: str, after: str) -> int:
    """How many index pointers the rewrite actually took out.

    Measured from the two texts, never assumed from the retire list. This line
    used to print `len(names)`, the number of MEMORIES retired, under the words
    "removed N pointer(s)", and the two are different numbers by design: a
    pointer that names a PATH rather than a bare top-level filename is left
    alone (`memory_expiry.strip_index_pointers`), so a retired memory can
    perfectly well have no pointer taken out for it. MEASURED 2026-08-30,
    `strip_index_pointers("- Group: [x](threads/foo.md) · [y](bar.md)",
    ["foo.md", "bar.md"])` removes one pointer and the old line called it two.

    Overstating a deletion is the worst direction for this particular sentence
    to be wrong in. It is the audit trail of a destructive edit to an
    operator-curated index that nothing else records, and an operator reading
    "removed 2" has been told the second index entry is gone when it is still
    sitting there.

    Targets come from `index_link_targets`, the module's own reader for the
    grammar `strip_index_pointers` writes with, so this counts a link the same
    way the remover does rather than growing a second copy of that pattern.
    Occurrences, not distinct targets: a name pointed at from two groups is two
    pointers gone.
    """
    gone = index_link_targets(before) - index_link_targets(after)
    return sum(before.count(f"]({target})") for target in gone)


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

    # The index pointer is stripped only for a name that came off EVERY store.
    # A file left behind by a failed unlink and stripped from the index anyway is
    # an orphan, and the newest-wins reconcile copies it back into the stores it
    # was deleted from - so the memory returns with nothing pointing at it.
    names = []
    for name, exp in expired:
        removed, failed = retire_memory(name)
        if failed:
            print(f"{RED}NOT retired{RESET} {name} (expired {exp.isoformat()}): "
                  f"{len(removed)} store(s) cleared, {len(failed)} refused; the "
                  f"index pointer is left in place")
            for path, why in failed:
                print(f"    {path}: {why}")
            _log_line(f"retire FAILED {name} expired={exp.isoformat()} "
                      f"removed={len(removed)} failed={len(failed)}")
            continue
        names.append(name)
        print(f"{GREEN}retired{RESET} {name} (expired {exp.isoformat()}): {len(removed)} store(s)")
        _log_line(f"retired {name} expired={exp.isoformat()} stores={len(removed)}")

    # Strip pointers from MEMORY.md in one rewrite.
    index = memory_dir / INDEX_NAME
    if names and index.exists():
        before = index.read_text(encoding="utf-8")
        after = strip_index_pointers(before, names)
        if after != before:
            index.write_text(after, encoding="utf-8")
            print(f"{GREEN}updated{RESET} {INDEX_NAME} "
                  f"(removed {_pointers_removed(before, after)} pointer(s) "
                  f"for {len(names)} retired memor"
                  f"{'y' if len(names) == 1 else 'ies'})")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
