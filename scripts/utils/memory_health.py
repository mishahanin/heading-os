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
import math
from pathlib import Path

from scripts.utils.workspace import get_default_tz

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

    # Stale: mtime older than STALE_DAYS (tz-aware local time via get_default_tz).
    now = datetime.datetime.now(get_default_tz())
    stale: list[tuple[str, int]] = []
    for p in files:
        if p.name == "MEMORY.md":
            continue
        try:
            mtime = datetime.datetime.fromtimestamp(p.stat().st_mtime, tz=get_default_tz())
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


def _cosine(u, v):
    dot = sum(a * b for a, b in zip(u, v))
    nu = math.sqrt(sum(a * a for a in u))
    nv = math.sqrt(sum(b * b for b in v))
    return 0.0 if nu == 0 or nv == 0 else dot / (nu * nv)


def scan_redundancy(memory_dir, *, threshold=0.86, embedder=None) -> dict:
    """Advisory near-duplicate detector over auto-memory/*.md. Proposes only; never
    mutates. Returns {"ok": bool, "pairs": [{a,b,score}], "note": str}. Degrades to
    ok=False (never raises) when the embedder is unavailable."""
    files = sorted(p for p in Path(memory_dir).glob("*.md") if p.name != "MEMORY.md")
    if len(files) < 2:
        return {"ok": True, "pairs": [], "note": "fewer than 2 memory files"}
    if embedder is None:
        try:
            from scripts.utils.embeddings import embed

            def embedder(ts):
                return embed(ts, model="bge-m3", host="http://localhost:11434")
        except Exception as e:
            return {"ok": False, "pairs": [], "note": f"embedder unavailable: {e}"}
    texts = [f.read_text(encoding="utf-8") for f in files]
    try:
        vecs = embedder(texts)
    except Exception as e:
        return {"ok": False, "pairs": [], "note": f"embedder unavailable: {e}"}
    pairs = []
    for i in range(len(files)):
        for j in range(i + 1, len(files)):
            cos = _cosine(vecs[i], vecs[j])
            if cos >= threshold:
                pairs.append({"a": files[i].name, "b": files[j].name, "score": round(cos, 4)})
    pairs.sort(key=lambda p: p["score"], reverse=True)
    return {"ok": True, "pairs": pairs, "note": f"{len(pairs)} near-duplicate pair(s)"}
