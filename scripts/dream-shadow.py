#!/usr/bin/env python3
"""dream-shadow.py -- nightly memory consolidation worklist (Gap #1).

Read-only detector over auto-memory/*.md content. Computes a dormancy list
(oldest first, informational only — nothing is ever proposed for removal) and
merge candidates (near-duplicate pairs, salience-ranked) and writes a dated
report.
NEVER mutates, merges, or deletes a memory file -- resolution stays with
/dream (a human reviews, then applies).

Usage:
  python scripts/dream-shadow.py            # full run, writes report, exit 0/2
  python scripts/dream-shadow.py --json     # structured result to stdout
  python scripts/dream-shadow.py --quiet    # one summary line only

Exit codes: 0 always on a clean run (advisory only, no gate); 2 script error.

Consumed by:
  - scripts/prime-health-parallel.py (dream_shadow check)
  - .claude/skills/dream/SKILL.md (Phase 1A additional signal)

Tests: tests/test_a_stall_after_the_headers_arrived.py
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils.clone_guard import require_main_clone  # noqa: E402
from scripts.utils.colors import BOLD, CYAN, GRAY, GREEN, RESET  # noqa: E402
from scripts.utils.markdown import parse_frontmatter  # noqa: E402
from scripts.utils.memory_health import STALE_DAYS, scan_redundancy  # noqa: E402
from scripts.utils.salience import composite_salience  # noqa: E402
from scripts.utils.paths import load_env  # noqa: E402
from scripts.utils.workspace import get_auto_memory_dir, get_default_tz, get_outputs_dir  # noqa: E402

DORMANT_DAYS = STALE_DAYS  # 45; a file younger than this has not had its chance

# The literal that lets a reader of the REPORT tell "the scan found nothing"
# from "the scan never ran". `scripts/prime-health-parallel.py` matches the same
# text with its own regex - it runs at session boot and importing a kebab-case
# module for one string is not worth the cost there - so the two are held in
# agreement by a test, not by a shared import.
MERGE_UNAVAILABLE_MARKER = "UNAVAILABLE:"


def _memory_meta(path: Path) -> tuple[str, int, str]:
    """(mem_type, access_count, last_accessed) from a memory file's frontmatter.

    Real auto-memory files nest these under `metadata:` — checked as a
    fallback when no top-level key exists, mirroring memory-index.py's
    parse_note() precedent.
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return "", 0, ""
    meta, _ = parse_frontmatter(text)
    nested = meta.get("metadata")
    nested = nested if isinstance(nested, dict) else {}
    mem_type = str(meta.get("type") or nested.get("type") or "")
    raw_access = meta.get("access_count", nested.get("access_count", 0))
    try:
        access_count = int(raw_access or 0)
    except (TypeError, ValueError):
        access_count = 0
    last_accessed = str(meta.get("last_accessed") or nested.get("last_accessed") or "")
    return mem_type, access_count, last_accessed


def _days_since(date_str: str, now: datetime) -> int | None:
    """Whole days since an ISO date, or None when it is absent or unparseable."""
    try:
        d = datetime.fromisoformat(date_str.strip()).date()
    except (AttributeError, ValueError):
        return None
    return (now.date() - d).days


def compute_dormant(memory_dir: Path, now: datetime) -> list[dict]:
    """Memories the retriever has not surfaced lately. INFORMATIONAL ONLY.

    Nothing in this list is a deletion candidate. Auto-memory is never pruned;
    low use is evidence about ranking position, never about worth. The list
    exists so the operator can SEE what has gone quiet, not so anything acts
    on it.

    A file qualifies when it is older than DORMANT_DAYS AND either was never
    surfaced (access_count == 0) or was last surfaced more than DORMANT_DAYS
    ago. Oldest first.

    The two clauses are independent only because the bump preserves the file's
    mtime (scripts/utils/memory_touch.py). If a bump restamped mtime, an aged
    file that was surfaced yesterday would necessarily look young, the age gate
    would exclude it first, and the access clause below would be unreachable —
    this function would silently reduce to the old age-only rule.

    Expect the first runs after reinforcement ships to list nearly every aged
    file: the counter starts at zero everywhere, and "not observed in use yet"
    is the honest reading of that. The list shrinks as counting accrues.
    """
    dormant = []
    if not memory_dir.is_dir():
        return dormant
    for p in sorted(memory_dir.glob("*.md")):
        if p.name == "MEMORY.md":
            continue
        try:
            mtime = datetime.fromtimestamp(p.stat().st_mtime, tz=get_default_tz())
        except OSError:
            continue
        age_days = (now - mtime).days
        if age_days <= DORMANT_DAYS:
            continue
        mem_type, access_count, last_accessed = _memory_meta(p)
        since_access = _days_since(last_accessed, now)
        surfaced_recently = (
            access_count > 0 and since_access is not None and since_access <= DORMANT_DAYS
        )
        if surfaced_recently:
            continue
        dormant.append({
            "name": p.name,
            "age_days": age_days,
            "type": mem_type or "unknown",
            "access_count": access_count,
            "last_accessed": last_accessed or "never",
            "salience": round(composite_salience(mem_type, access_count), 3),
        })
    dormant.sort(key=lambda c: c["age_days"], reverse=True)
    return dormant


MERGE_EMBED_TIMEOUT = 600  # seconds; a nightly cron path has no interactive
# latency pressure, unlike memory-hygiene.py's weekly call -- a 32-file batch
# against the real corpus has been observed taking ~137s on CPU-only ollama,
# over the embed() default of 120s, so this path needs a longer ceiling.


def compute_merge_candidates(memory_dir: Path) -> dict:
    """Near-duplicate pairs (scan_redundancy), salience-ranked by the higher-
    salience member descending. Degrades gracefully (ok=False) when the
    embedder is unavailable -- never raises."""
    redundancy = scan_redundancy(memory_dir, timeout=MERGE_EMBED_TIMEOUT)
    if not redundancy["ok"]:
        return {"ok": False, "note": redundancy["note"], "pairs": []}
    pairs = []
    for pair in redundancy["pairs"]:
        a_type, a_access, _ = _memory_meta(memory_dir / pair["a"])
        b_type, b_access, _ = _memory_meta(memory_dir / pair["b"])
        a_sal = composite_salience(a_type, a_access)
        b_sal = composite_salience(b_type, b_access)
        pairs.append({
            "a": pair["a"], "b": pair["b"], "score": pair["score"],
            "a_salience": round(a_sal, 3), "b_salience": round(b_sal, 3),
            "rank_salience": round(max(a_sal, b_sal), 3),
        })
    pairs.sort(key=lambda p: p["rank_salience"], reverse=True)
    return {"ok": True, "note": redundancy["note"], "pairs": pairs}


def gather() -> dict:
    memory_dir = get_auto_memory_dir()
    now = datetime.now(get_default_tz())
    dormant = compute_dormant(memory_dir, now)
    merge = compute_merge_candidates(memory_dir)
    return {"memory_dir": str(memory_dir), "dormant": dormant, "merge": merge}


def render_report(result: dict, generated_iso: str) -> str:
    dormant = result["dormant"]
    merge = result["merge"]
    lines: list[str] = []
    lines.append("# Dream-Shadow Report")
    lines.append("")
    lines.append(f"**Generated:** {generated_iso}")
    lines.append(f"**Auto-memory:** `{result['memory_dir']}`")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append(f"## Dormant (not surfaced in {DORMANT_DAYS}+ days): {len(dormant)}")
    lines.append("")
    lines.append(
        "_Informational. Nothing listed here is a candidate for removal — "
        "a quiet fact only sinks in ranking, never in existence. It stays on "
        "the shelf until the operator says otherwise._"
    )
    lines.append("")
    if dormant:
        for c in dormant:
            lines.append(
                f"- {c['name']} ({c['age_days']}d old, type={c['type']}, "
                f"access_count={c['access_count']}, last surfaced {c['last_accessed']})"
            )
    else:
        lines.append("None today.")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## Merge Candidates (near-duplicate, salience-ranked)")
    lines.append("")
    if not merge["ok"]:
        # `UNAVAILABLE:`, not a bare note. The note line renders as an ordinary
        # bullet, which is what a human skims past and what the /prime health
        # check (`run_dream_shadow`, matching `^- .+<->.+$`) counts as ZERO
        # candidates - so it returned `status: ok` with no output and the
        # embedder could be down every night for a month without one word at
        # session boot. A scan that could not run is not a scan that found
        # nothing, and the report is the only place that difference survives.
        lines.append(f"- {MERGE_UNAVAILABLE_MARKER} {merge['note']}")
    elif merge["pairs"]:
        for p in merge["pairs"]:
            lines.append(
                f"- {p['a']} <-> {p['b']} (score {p['score']}, "
                f"salience {p['a_salience']}/{p['b_salience']}) "
                "- consolidate via /dream"
            )
    else:
        lines.append("None today.")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append(
        "Advisory only. This tool never mutates memory and never proposes "
        "removing a fact. Near-duplicate pairs are consolidated through "
        "`/dream`, one pair at a time, with the operator's approval."
    )
    lines.append("")
    return "\n".join(lines)


def write_report(text: str, generated_dt: datetime) -> Path:
    report_dir = get_outputs_dir() / "operations" / "dream"
    report_dir.mkdir(parents=True, exist_ok=True)
    today = generated_dt.strftime("%Y-%m-%d")
    path = report_dir / f"{today}_dream-shadow_report.md"
    tmp = path.with_suffix(".md.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)
    return path


def main() -> int:
    require_main_clone(__file__)

    # First, before anything reads the clock. `get_default_tz()` reads os.environ
    # ONLY, and HEADING_OS_TZ lives in the gitignored .env, which nothing exports
    # -- so without this the 03:10 fire dates its report, and every memory mtime
    # it ages, in UTC. The report filename would land under the previous day.
    load_env()

    parser = argparse.ArgumentParser(description="Nightly memory consolidation worklist")
    parser.add_argument("--json", action="store_true", help="Emit the structured result as JSON")
    parser.add_argument("--quiet", action="store_true", help="Print only the one-line summary")
    parser.add_argument("--no-report", action="store_true", help="Do not write the report file (stdout only)")
    args = parser.parse_args()

    try:
        result = gather()
    except Exception as exc:  # noqa: BLE001 - degrade clearly, never silently swallow
        print(f"ERROR: dream-shadow failed: {exc}", file=sys.stderr)
        return 2

    # Inside a guard too. Only `gather()` was wrapped, and the docstring
    # promises "0 always on a clean run; 2 script error" - so an OSError from
    # writing into a read-only or full outputs volume, or any failure in
    # rendering, escaped as a traceback with exit 1: neither of the two codes
    # this script's consumers (`prime-health-parallel.py`, the /dream skill)
    # are told to expect, from a tool advertised as advisory with no gate.
    try:
        now = datetime.now(get_default_tz())
        generated_iso = now.isoformat(timespec="seconds")
        report_text = render_report(result, generated_iso)

        report_path = None
        if not args.no_report:
            report_path = write_report(report_text, now)
    except Exception as exc:  # noqa: BLE001 - degrade clearly, never silently swallow
        print(f"ERROR: dream-shadow could not write its report: {exc}",
              file=sys.stderr)
        return 2

    dormant_n = len(result["dormant"])
    merge_ok = result["merge"]["ok"]
    merge_n = len(result["merge"]["pairs"]) if merge_ok else 0
    # "0 merge candidate(s)" was printed for a scan that never ran, and `--quiet`
    # prints ONLY this line - so the one mode a health check would use was the
    # one that could not tell the two apart. The `clean` line forty lines below
    # already guards against exactly this; the summary above it did not.
    merge_part = (f"{merge_n} merge candidate(s)" if merge_ok
                  else "merge scan UNAVAILABLE")

    if args.json:
        out = dict(result)
        out["report_path"] = str(report_path) if report_path else None
        out["generated"] = generated_iso
        print(json.dumps(out, indent=2, default=str))
    else:
        summary = f"{BOLD}dream-shadow:{RESET} {dormant_n} dormant, {merge_part}"
        if report_path:
            summary += f" {GRAY}- report: {report_path}{RESET}"
        print(summary)
        if not args.quiet:
            for c in result["dormant"]:
                print(f"  {GRAY}-{RESET} dormant: {c['name']} ({c['age_days']}d, last surfaced {c['last_accessed']})")
            if result["merge"]["ok"]:
                for p in result["merge"]["pairs"]:
                    print(f"  {CYAN}-{RESET} merge: {p['a']} <-> {p['b']} (score {p['score']})")
            elif result["merge"]["note"]:
                print(f"  {GRAY}note{RESET}: {result['merge']['note']}")
            if merge_n:
                print(f"  {GRAY}consolidate via /dream (this tool never mutates memory){RESET}")
            elif not result["merge"]["ok"]:
                pass
            else:
                print(f"  {GREEN}clean -- nothing to review{RESET}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
