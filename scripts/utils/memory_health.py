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
import re
from pathlib import Path

from scripts.utils.workspace import get_default_tz

# Budget + staleness thresholds (kept identical to the prior inlined values).
MEMORY_BUDGET_LINES = 200
STALE_DAYS = 45

# ---------------------------------------------------------------------------
# Volatile-hook guard (advisory) — enforces the memory-discipline convention:
# a MEMORY.md index hook names the TOPIC and points to the file; it must NOT
# quote a live/volatile value (a price, ceiling, offer, live count, live
# deadline, current status). Volatile values belong in the record body, read on
# demand — a hook that never quotes a live number cannot go stale into a wrong
# number (see .claude/rules/memory-discipline.md).
#
# The heuristic is deliberately HIGH-PRECISION, not high-recall: it targets the
# money/quantitative-state class that caused the stale-money-hook failure, and accepts that
# some volatile prose (bare live counts/dates with no money signal) is not
# caught. It is ADVISORY (never gates), so a rare false positive is a review
# nudge, not a build break.
#
# Volatile signals (flag) — precision-first money detection:
#   - currency:        €/$/£ or an ISO code (USD/EUR/GBP/AED/CHF) adjacent to a digit
#   - money magnitude: a 'k'/'K'/'m'/'M' thousands/millions suffix, ONLY when a
#                      money-context word is present in the same text (price, offer,
#                      seller, loan, mortgage, budget, deal, LTV, pipeline, ...).
# The money-context co-factor is what keeps SPEC magnitudes from false-flagging:
# "128k context", "1M-context", "5K display", "10k RPM", "i9-13900K", "~7-8B" carry
# a k/M/B token but NO money word, so they do not flag. A bare money-VOCABULARY
# signal was rejected earlier (it flagged "local ceiling ~7-8B"); here vocabulary is
# only a REQUIRED co-factor for a magnitude token, never a standalone trigger, so
# that false-positive class stays closed. "ceiling" is intentionally NOT a money
# word here.
#
# Recall is deliberately PARTIAL (see .claude/rules/memory-discipline.md): this
# guard closes the MONEY-hook class that caused the stale-money-hook failure. Non-money
# volatile prose (live dates like "due 2026-09-05", live counts, "70%", status) is
# NOT mechanically caught — that breadth is the always-on principle's job, not a
# reason to widen this heuristic into a false-positive machine. The guard is
# ADVISORY and never gates. It scans BOTH the MEMORY.md index hooks and each memory
# file's frontmatter `description:` (both are pointer-layer summaries that go stale).
_VH_CURRENCY_RE = re.compile(r"(?:[€$£]|\b(?:USD|EUR|GBP|AED|CHF)\b)\s?\d")
_VH_MAGNITUDE_RE = re.compile(r"\b\d+(?:\.\d+)?[kKmM]\b")
_VH_MONEY_CTX_RE = re.compile(
    r"\b(price|offer|seller|buyer|loan|mortgage|budget|deal|asking|valuation|"
    r"salary|revenue|ARR|cash|equity|fee|fees|deposit|rent|proceeds|pipeline|LTV)\b",
    re.IGNORECASE,
)
# A MEMORY.md index hook: "- [Title](file.md) — hook". Pointers under threads/ are
# skipped (generated pointers to live records, not memory hooks); a bare filename
# OR a future memory-subdir hook is still scanned (do NOT skip on any '/').
_VH_HOOK_LINE_RE = re.compile(r"^\s*-\s*\[[^\]]+\]\(([^)]+\.md)\)")
_VH_DESC_RE = re.compile(r"^description:\s*(.*)$")


def _volatile_signals(text: str) -> list:
    """Return the volatile-money signals present in a single string (see module
    comment). Currency is standalone; a magnitude token needs a money-context word."""
    signals: list[str] = []
    if _VH_CURRENCY_RE.search(text):
        signals.append("currency")
    if _VH_MAGNITUDE_RE.search(text) and _VH_MONEY_CTX_RE.search(text):
        signals.append("money-magnitude")
    return signals


def _extract_description(path) -> str:
    """Pull the frontmatter `description:` value from a memory file (single line).
    Returns '' when absent/unreadable. READS ONLY."""
    try:
        with open(path, encoding="utf-8", errors="ignore") as fh:
            head = [next(fh, "") for _ in range(20)]
    except OSError:
        return ""
    if not head or not head[0].startswith("---"):
        return ""
    for line in head[1:]:
        if line.strip() == "---":
            break
        m = _VH_DESC_RE.match(line)
        if m:
            return m.group(1).strip().strip('"').strip("'")
    return ""


def scan_volatile_hooks(memory_dir) -> dict:
    """Advisory: flag volatile-state values in MEMORY.md index hooks AND in each
    memory file's frontmatter `description:` (both are pointer-layer summaries that
    can go stale). READS ONLY; never mutates. High-precision money heuristic.

    Returns:
        {
          "ok": bool,
          "flagged": [{"target": str, "line": str, "signals": [...]}, ...],
          "flagged_descriptions": [{"file": str, "description": str, "signals": [...]}, ...],
          "note": str,
        }
    """
    memory_dir = Path(memory_dir)
    memory_file = memory_dir / "MEMORY.md"
    flagged: list[dict] = []
    if memory_file.exists():
        try:
            text = memory_file.read_text(encoding="utf-8", errors="ignore")
        except OSError as exc:
            return {
                "ok": False,
                "flagged": [],
                "flagged_descriptions": [],
                "note": f"unreadable MEMORY.md: {exc}",
            }
        for raw in text.splitlines():
            m = _VH_HOOK_LINE_RE.match(raw)
            if not m or m.group(1).startswith("threads/"):  # leak-guard: ok (relative prefix match on a MEMORY.md link target, not a path join)
                continue
            signals = _volatile_signals(raw)
            if signals:
                flagged.append({"target": m.group(1), "line": raw.strip(), "signals": signals})

    flagged_desc: list[dict] = []
    for p in sorted(memory_dir.glob("*.md")):
        if p.name == "MEMORY.md":
            continue
        desc = _extract_description(p)
        if desc and (signals := _volatile_signals(desc)):
            flagged_desc.append({"file": p.name, "description": desc, "signals": signals})

    return {
        "ok": True,
        "flagged": flagged,
        "flagged_descriptions": flagged_desc,
        "note": f"{len(flagged)} volatile hook(s), {len(flagged_desc)} volatile description(s)",
    }


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


def scan_redundancy(memory_dir, *, threshold=0.86, embedder=None, timeout=120) -> dict:
    """Advisory near-duplicate detector over auto-memory/*.md. Proposes only; never
    mutates. Returns {"ok": bool, "pairs": [{a,b,score}], "note": str}. Degrades to
    ok=False (never raises) when the embedder is unavailable.

    `timeout` (seconds, default 120) is the per-request socket timeout passed
    to the default embedder. A single request can batch up to 32 full memory
    files -- on CPU-only ollama that can exceed 120s as the corpus grows, so a
    background/cron caller with no interactive latency pressure (e.g.
    dream-shadow.py) should pass a longer value. Ignored when a custom
    `embedder` callable is supplied (the caller owns its own timeout then)."""
    files = sorted(p for p in Path(memory_dir).glob("*.md") if p.name != "MEMORY.md")
    if len(files) < 2:
        return {"ok": True, "pairs": [], "note": "fewer than 2 memory files"}
    if embedder is None:
        try:
            from scripts.utils.embeddings import embed

            def embedder(ts):
                return embed(ts, model="bge-m3", host="http://localhost:11434", timeout=timeout)
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
