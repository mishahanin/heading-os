#!/usr/bin/env python3
"""dream-shadow.py -- nightly salience-ranked consolidation worklist (Gap #1).

Read-only detector over auto-memory/*.md content. Computes a dormancy list
(informational only — nothing is ever proposed for removal) and merge
candidates (near-duplicate pairs, salience-ranked) and writes a dated report.
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
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils.colors import BOLD, CYAN, GRAY, GREEN, RESET  # noqa: E402
from scripts.utils.markdown import parse_frontmatter  # noqa: E402
from scripts.utils.memory_health import STALE_DAYS, scan_redundancy  # noqa: E402
from scripts.utils.salience import composite_salience  # noqa: E402
from scripts.utils.paths import load_env  # noqa: E402
from scripts.utils.workspace import get_auto_memory_dir, get_default_tz, get_outputs_dir  # noqa: E402

DORMANT_DAYS = STALE_DAYS  # 45; a file younger than this has not had its chance


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
        lines.append(f"- {merge['note']}")
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
    # First, before anything reads the clock. `get_default_tz()` reads os.environ
    # ONLY, and HEADING_OS_TZ lives in the gitignored .env, which nothing exports
    # -- so without this the 03:10 fire dates its report, and every memory mtime
    # it ages, in UTC. The report filename would land under the previous day.
    load_env()

    parser = argparse.ArgumentParser(description="Nightly salience-ranked memory consolidation worklist")
    parser.add_argument("--json", action="store_true", help="Emit the structured result as JSON")
    parser.add_argument("--quiet", action="store_true", help="Print only the one-line summary")
    parser.add_argument("--no-report", action="store_true", help="Do not write the report file (stdout only)")
    args = parser.parse_args()

    try:
        result = gather()
    except Exception as exc:  # noqa: BLE001 - degrade clearly, never silently swallow
        print(f"ERROR: dream-shadow failed: {exc}", file=sys.stderr)
        return 2

    now = datetime.now(get_default_tz())
    generated_iso = now.isoformat(timespec="seconds")
    report_text = render_report(result, generated_iso)

    report_path = None
    if not args.no_report:
        report_path = write_report(report_text, now)

    dormant_n = len(result["dormant"])
    merge_n = len(result["merge"]["pairs"]) if result["merge"]["ok"] else 0

    if args.json:
        out = dict(result)
        out["report_path"] = str(report_path) if report_path else None
        out["generated"] = generated_iso
        print(json.dumps(out, indent=2, default=str))
    else:
        summary = f"{BOLD}dream-shadow:{RESET} {dormant_n} dormant, {merge_n} merge candidate(s)"
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
