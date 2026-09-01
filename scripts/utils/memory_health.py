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
import logging
import math
import re
from pathlib import Path

from scripts.utils.memory_expiry import index_link_targets
from scripts.utils.workspace import get_default_tz

logger = logging.getLogger(__name__)

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
# One MEMORY.md index hook: "[Title](file.md) — note", up to the next ` · `
# separator or the end of the line. Pointers under threads/ are skipped (generated
# pointers to live records, not memory hooks); a bare filename OR a future
# memory-subdir hook is still scanned (do NOT skip on any '/').
#
# This matched from the BULLET until 2026-08-27: `^\s*-\s*\[[^\]]+\]\((...)\)`,
# which demands the link immediately after the dash. The index is grouped by
# subject, so a line reads `- Memory: [a](a.md) · [b](b.md)`, and the label
# between the bullet and the bracket made the whole line fail. Measured against
# the live index that day: 10 lines matched, out of 216 pointers present. The
# guard reported zero findings and was believed, because a scan of 5% of a corpus
# prints the same words as a scan of all of it.
#
# Pointer-at-a-time also fixes the misattribution: signals were read from the
# whole line while the reported target was the FIRST pointer on it, so a price in
# the fifth hook sent the operator to the first hook's file.
# `scripts/utils/memory_expiry.py` already solved this shape; this mirrors it.
# The tail stops at the next pointer as well as at ` · `. It was `[^·\n]*`,
# greedy, and `[`, `]`, `(` and `)` are all inside that class: on a line whose
# pointers are NOT separated by a middle dot, the first match swallowed the rest
# of the line, `finditer` resumed past the end, and every later pointer went
# unscanned and unreported. That is the misattribution the paragraph above says
# was fixed - it was fixed for the separated case only.
_VH_POINTER_RE = re.compile(
    r"\[[^\]]*?\]\((?P<target>[^)]+)\)(?:(?!\[[^\]]*?\]\()[^·\n])*")
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
            if not raw.lstrip().startswith("-"):
                continue  # not an index bullet, so it names no memory to open
            # A hook runs from the end of the previous one to the end of its own
            # pointer, so it carries BOTH its leading label and its trailing
            # note: "- Mortgage EUR 412,000: [bank](x.md)" and
            # "- Mortgage: [bank](x.md) - EUR 412,000" are the same claim.
            #
            # This used to give the leading label to the FIRST pointer only
            # (`raw[:match.end()] if index == 0 else match.group(0)`). Every
            # later hook on a grouped line lost the words between the separator
            # and its own bracket, which is where the label lives, so a value
            # written "· costing EUR 412,000, see [rate](b.md)" was read by
            # nobody. Sliding the window makes every hook the same shape.
            prev_end = 0
            for match in _VH_POINTER_RE.finditer(raw):
                target = match.group("target")
                segment = raw[prev_end:match.end()]
                prev_end = match.end()
                if not target.endswith(".md"):
                    continue
                if target.startswith("threads/"):  # leak-guard: ok (relative prefix match on a MEMORY.md link target, not a path join)
                    continue
                signals = _volatile_signals(segment)
                if signals:
                    flagged.append({
                        "target": target,
                        "line": segment.strip(),
                        "signals": signals,
                    })

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


_WIKILINK_RE = re.compile(r"\[\[([^\]]+)\]\]")


def scan_dangling_links(memory_dir) -> dict:
    """Advisory: find `[[wikilinks]]` in auto-memory that resolve to no memory file.

    A dangling link is cheap on its own -- the convention allows one as a marker
    for a memory worth writing later. It is expensive because the SENTENCE around
    it goes stale with it and nothing notices. On 2026-08-19 five files cited
    `no-exec-sync-until-ceo-cutover` as the reason work was "still deferred", two
    months after the deferral was lifted; the pointer being dead was the only
    outward sign that the premise was too.

    Two namespaces are deliberately not memory links and are skipped: `thread:*`
    addresses the thread registry, and an all-digit target is a bare record id.

    READS ONLY; never mutates. Returns:

        {
          "ok": bool,                 # False when the directory is unreadable
          "flagged": [{"target": str, "cited_by": [filename, ...]}, ...],
          "note": str,
        }
    """
    memory_dir = Path(memory_dir)
    if not memory_dir.is_dir():
        return {"ok": False, "flagged": [], "note": f"no such directory: {memory_dir}"}

    files = sorted(memory_dir.glob("*.md"))
    known = {p.stem for p in files}
    citers: dict[str, list[str]] = {}
    for p in files:
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            # A file we cannot read cannot be cleared, so say so by skipping it
            # rather than counting it as link-free.
            continue
        for target in _WIKILINK_RE.findall(text):
            if target in known or target.startswith("thread:") or target.isdigit():
                continue
            citers.setdefault(target, [])
            if p.name not in citers[target]:
                citers[target].append(p.name)

    flagged = [{"target": t, "cited_by": c} for t, c in sorted(citers.items())]
    return {"ok": True, "flagged": flagged, "note": f"{len(flagged)} dangling link(s)"}


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
          "index_readable": bool,       # MEMORY.md exists and could be read
          "index_problem": str,         # why not, "" when it could
        }

    BOTH branches return all nine keys. The `"missing"` branch used to return
    only the first seven, so a caller reading `result["index_readable"]` - the
    key added precisely because an unreadable index was a blind spot - got a
    KeyError on exactly the path where the index is most certainly unreadable.
    MEASURED 2026-08-30: `compute_memory_defects(Path("/nonexistent"))` returned
    seven keys and `["index_readable"]` raised. The docstring listed seven too,
    so nothing named the divergence.
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
            "index_readable": False,
            "index_problem": f"{memory_dir} is not a directory",
        }

    files = sorted(p for p in memory_dir.glob("*.md") if p.is_file())
    memory_file = memory_dir / "MEMORY.md"

    if memory_file.exists():
        try:
            # `errors="ignore"`, matching the read of this SAME file forty
            # lines down. Strict here and lenient there is not a style
            # difference, it is two answers to one question: MEASURED
            # 2026-09-01 on a `MEMORY.md` carrying a lone 0xe9, this line raised
            # `UnicodeDecodeError` straight out of `compute_memory_defects`,
            # while the orphan read below would have carried on and reported
            # `index_readable: True`. `UnicodeDecodeError` is a `ValueError` and
            # not an `OSError`, so the handler could not catch it and the whole
            # health computation died - defeating `index_readable`, the key this
            # function grew precisely to answer "was the index actually read?".
            # Dropping an invalid byte cannot change a LINE count: 0x0A is never
            # part of an invalid UTF-8 sequence.
            lines = sum(1 for _ in memory_file.open(
                "r", encoding="utf-8", errors="ignore"))
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

    # Orphans: fact files the index carries no pointer to.
    #
    # "Referenced" is the exact `](<name>)` link target, read through
    # `memory_expiry.index_link_targets` - the same grammar the retirement path
    # rewrites with, so what counts as a pointer here and what counts as a
    # pointer there cannot drift apart. It was `p.name not in content`, a
    # substring of the whole index, which reported a file as referenced whenever
    # its name sat inside a LONGER name on the same index, or inside a pointer to
    # a different record under a subdirectory. See that function for the two
    # reproductions.
    #
    # An ABSENT or unreadable index is the state where EVERY fact file is
    # unreferenced, and the check simply skipped it: the caller received
    # `orphans: []` under `status: "ok"` and `/memory-hygiene` printed
    # "0 orphans / none" over an index it had never read. `status` answers a
    # different question (the DIRECTORY exists), so it could not carry this, and
    # nothing else in the returned dict did either. `index_readable` is that
    # missing fact, and the orphan list now names the real state.
    orphans: list[str] = []
    index_readable = True
    index_problem = ""
    content = ""
    if not memory_file.exists():
        index_readable = False
        index_problem = f"{memory_file} does not exist"
    else:
        try:
            content = memory_file.read_text(encoding="utf-8", errors="ignore")
        except OSError as exc:
            index_readable = False
            index_problem = f"{memory_file} could not be read: {exc}"

    linked = index_link_targets(content)
    for p in files:
        if p.name == "MEMORY.md":
            continue
        if p.name not in linked:
            orphans.append(p.name)

    return {
        "status": "ok",
        "memory_dir": str(memory_dir),
        "file_count": len(files),
        "memory_md_lines": lines,
        "over_budget": lines > MEMORY_BUDGET_LINES,
        "stale": stale,
        "orphans": orphans,
        "index_readable": index_readable,
        "index_problem": index_problem,
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
            from scripts.utils.embeddings import embed, index_embed_target

            host, model = index_embed_target()

            def embedder(ts):
                return embed(ts, model=model, host=host, timeout=timeout)
        except Exception as e:
            return {"ok": False, "pairs": [], "note": f"embedder unavailable: {e}"}
    # Every other reader in this module guards `OSError`; this one did not, and
    # it runs over a directory the auto-retire sweep mutates. MEASURED
    # 2026-08-30: one memory file at mode 000 raised `PermissionError` straight
    # out of an advisory check whose contract is to degrade to `ok=False`. A
    # file that cannot be read is dropped from the pair scan (with `files` kept
    # in step with `texts`, or every later index would name the wrong file) and
    # the note says how many went.
    readable, texts, unreadable = [], [], 0
    for f in files:
        try:
            texts.append(f.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError):
            # `UnicodeDecodeError` is a `ValueError`, NOT an `OSError`. Until
            # 2026-09-01 this handler could not catch it, so the "degrade to
            # ok=False" contract described three lines above did not hold for
            # the one input most likely to break a read: a file that is not
            # valid UTF-8. MEASURED that day on a corpus of one clean note and
            # one carrying a lone 0xe9, `scan_redundancy` raised out of the
            # walk instead of returning a note.
            unreadable += 1
            logger.warning("dropping unreadable memory file %s from the "
                           "redundancy scan", f, exc_info=True)
            continue
        readable.append(f)
    files = readable
    if len(files) < 2:
        return {"ok": False, "pairs": [],
                "note": f"fewer than 2 readable memory files ({unreadable} unreadable)"}
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
    skipped = f"; {unreadable} unreadable file(s) skipped" if unreadable else ""
    return {"ok": True, "pairs": pairs,
            "note": f"{len(pairs)} near-duplicate pair(s){skipped}"}
