#!/usr/bin/env python3
"""memory_health.py - objective auto-memory defect computation (importable).

Pure, directory-parameterized computation of the mechanically-verifiable defects
in an auto-memory directory (a folder of `*.md` fact files plus a `MEMORY.md`
index). Extracted from the inlined logic in `scripts/prime-health-parallel.py`
so both that health panel and `scripts/memory-hygiene.py` share one
implementation.

This module READS ONLY. It never writes, merges, or deletes a memory file.
"Objective" here means deterministically checkable without judgement:
  - orphans       : a `*.md` fact file whose name is not referenced from MEMORY.md
  - over_budget   : MEMORY.md exceeds the line budget (default 200)
  - stale         : a fact file older than STALE_DAYS by mtime (advisory signal)

Consumed by:
  - scripts/prime-health-parallel.py (run_memory_health)
  - scripts/memory-hygiene.py
"""
from __future__ import annotations

import datetime
from pathlib import Path

# Budget + staleness thresholds (kept identical to the prior inlined values).
MEMORY_BUDGET_LINES = 200
STALE_DAYS = 45


def compute_memory_defects(memory_dir: Path) -> dict:
    """Compute objective auto-memory defects for a single memory directory.

    Returns a pure data dict (no human-facing string, no exit code). Callers
    decide how to present it and which subset gates. Shape:

        {
          "status": "ok" | "missing",
          "memory_dir": str,
          "file_count": int,            # *.md files excluding nothing (incl. MEMORY.md)
          "memory_md_lines": int,       # line count of MEMORY.md (0 if absent)
          "over_budget": bool,          # memory_md_lines > MEMORY_BUDGET_LINES
          "stale": list[tuple[str,int]],# [(filename, days_old), ...] for >STALE_DAYS
          "orphans": list[str],         # filenames not referenced from MEMORY.md
        }
    """
    if not memory_dir.is_dir():
        return {
            "status": "missing",
            "memory_dir": str(memory_dir),
            "file_count": 0,
            "memory_md_lines": 0,
            "over_budget": False,
            "stale": [],
            "orphans": [],
        }

    files = sorted(p for p in memory_dir.glob("*.md") if p.is_file())
    memory_file = memory_dir / "MEMORY.md"

    if memory_file.exists():
        try:
            lines = sum(1 for _ in memory_file.open("r", encoding="utf-8"))
        except OSError:
            lines = 0
    else:
        lines = 0

    # Stale: mtime older than STALE_DAYS (naive local time, matching prior behavior).
    now = datetime.datetime.now()
    stale: list[tuple[str, int]] = []
    for p in files:
        if p.name == "MEMORY.md":
            continue
        try:
            mtime = datetime.datetime.fromtimestamp(p.stat().st_mtime)
        except OSError:
            continue
        age = (now - mtime).days
        if age > STALE_DAYS:
            stale.append((p.name, age))

    # Orphans: fact files whose name is not referenced anywhere in MEMORY.md.
    orphans: list[str] = []
    if memory_file.exists():
        try:
            content = memory_file.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            content = ""
        for p in files:
            if p.name == "MEMORY.md":
                continue
            if p.name not in content:
                orphans.append(p.name)

    return {
        "status": "ok",
        "memory_dir": str(memory_dir),
        "file_count": len(files),
        "memory_md_lines": lines,
        "over_budget": lines > MEMORY_BUDGET_LINES,
        "stale": stale,
        "orphans": orphans,
    }
