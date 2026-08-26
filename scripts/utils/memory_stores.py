#!/usr/bin/env python3
"""Canonical + native memory store resolution and both-store retirement.

memory-reconcile.py syncs the canonical DATA auto-memory with per-launch native
harness stores newest-wins and NEVER propagates deletions (memory-reconcile.py:23-25).
So a memory removed on one store alone is resurrected from the other at the next
SessionStart. retire_memory removes a file from ALL stores at once, the only delete
that sticks.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from scripts.utils.workspace import get_auto_memory_dir


def iter_native_memory_stores():
    """Every per-launch native harness memory dir on this machine."""
    base = Path.home() / ".claude" / "projects"
    if not base.exists():
        return []
    return [p for p in base.glob("*/memory") if p.is_dir()]


def all_memory_stores():
    """Canonical DATA auto-memory + every native store."""
    return [get_auto_memory_dir(), *iter_native_memory_stores()]


def retire_memory(name: str, *, stores=None) -> tuple[list, list]:
    """Remove a top-level memory file from ALL stores. Idempotent; missing-safe.

    Returns ``(removed, failed)``: the paths actually deleted, and the ones that
    are still there with the reason.

    The failures used to be swallowed by a bare `continue`. A read-only store,
    a permission change, a file held open - any of them left the memory ON DISK
    while the caller printed "retired <name>", wrote that word to its audit log,
    and stripped the pointer from MEMORY.md. The result was the opposite of the
    intent: an orphan file that this module's own docstring says the newest-wins
    reconcile will copy back into every other store. "The only delete that
    sticks" is only true if a delete that did not stick is reported.
    """
    removed, failed = [], []
    for store in (stores if stores is not None else all_memory_stores()):
        f = Path(store) / name
        try:
            if f.exists():
                f.unlink()
                removed.append(str(f))
        except OSError as exc:
            print(f"[memory_stores] could not retire {f}: {exc}", file=sys.stderr)
            failed.append((str(f), str(exc)))
    return removed, failed
