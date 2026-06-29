#!/usr/bin/env python3
"""Weekly LLM-fit report - downgrade-audit aggregator.

Track B of the LLM-fit logging project (plans/2026-05-24-llm-fit-logging-
three-tracks.md). Queries the last N days of Langfuse traces, buckets by
trace name (skill / function), and reports objective signals that a
cheaper model tier could have sufficed.

Surface-level only. ALL judgments are mechanical from the response shape
(output_tokens, has_tool_use, stop_reason, vendor). Never Claude judging
Claude.

Usage:
  python scripts/llm-fit-report.py                # last 7 days
  python scripts/llm-fit-report.py --days 30      # 30-day window
  python scripts/llm-fit-report.py --json         # JSON output for downstream
  python scripts/llm-fit-report.py --no-write     # stdout only (no file)

Output: outputs/operations/llm-fit/{YYYY-MM-DD}_llm-fit-report.md (Markdown).
The bridge daemon runs this weekly on Sunday 03:00 local via APScheduler.

Cross-platform: pure Python, no shell. Runs anywhere the bridge daemon runs.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WORKSPACE_ROOT))

from scripts.utils.colors import BOLD, CYAN, GRAY, GREEN, RESET, YELLOW  # noqa: E402
from scripts.utils.workspace import get_default_tz, get_default_tz_name, get_outputs_dir, load_env  # noqa: E402

REPORT_DIR = get_outputs_dir() / "operations" / "llm-fit"


def _local_today_iso() -> str:
    from zoneinfo import ZoneInfo
    return datetime.now(ZoneInfo(get_default_tz_name())).strftime("%Y-%m-%d")


def fetch_traces(days: int, page_size: int = 100) -> list:
    """Page through Langfuse traces in the window. Returns a flat list."""
    from langfuse import get_client
    client = get_client()
    api = client.api
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    out: list = []
    page = 1
    while True:
        try:
            resp = api.trace.list(from_timestamp=cutoff, page=page, limit=page_size)
        except Exception as e:
            print(f"{YELLOW}WARN langfuse query failed at page {page}: {e}{RESET}", file=sys.stderr)
            break
        batch = list(resp.data or [])
        if not batch:
            break
        out.extend(batch)
        if len(batch) < page_size:
            break
        page += 1
        # Safety cap so a runaway query doesn't burn API budget.
        if page > 50:
            print(f"{YELLOW}WARN fetched 50 pages ({len(out)} traces); stopping{RESET}", file=sys.stderr)
            break
    return out


def _extract_signals(trace) -> dict | None:
    """Pull downgrade signals + vendor info from a trace's metadata.

    Returns None when the trace was not produced by llm_fallback (no
    metadata, or pre-Track-B trace).
    """
    md = getattr(trace, "metadata", None) or {}
    if not isinstance(md, dict):
        return None
    signals = md.get("downgrade_signals")
    fallback_triggered = bool(md.get("fallback_triggered"))
    tags = list(getattr(trace, "tags", None) or [])
    vendor = next(
        (t.split(":", 1)[1] for t in tags if isinstance(t, str) and t.startswith("vendor:")),
        None,
    )
    if signals is None and vendor is None and not fallback_triggered:
        return None
    return {
        "vendor": vendor or "unknown",
        "fallback_triggered": fallback_triggered,
        "signals": signals,  # may be None for Gemini/Grok-served traces
    }


def aggregate(traces: list) -> dict:
    """Group by trace name; per bucket compute downgrade flag rate + sizes."""
    buckets: dict = defaultdict(lambda: {
        "total": 0,
        "by_vendor": defaultdict(int),
        "fallback_count": 0,
        "downgrade_candidates": 0,
        "with_tool_use": 0,
        "output_tokens": [],
    })

    for tr in traces:
        info = _extract_signals(tr)
        if info is None:
            continue
        name = getattr(tr, "name", "unknown") or "unknown"
        b = buckets[name]
        b["total"] += 1
        b["by_vendor"][info["vendor"]] += 1
        if info["fallback_triggered"]:
            b["fallback_count"] += 1
        sig = info["signals"]
        if isinstance(sig, dict):
            if sig.get("downgrade_candidate"):
                b["downgrade_candidates"] += 1
            if sig.get("has_tool_use"):
                b["with_tool_use"] += 1
            tok = sig.get("output_tokens")
            if isinstance(tok, int):
                b["output_tokens"].append(tok)

    # Materialize defaultdicts so JSON serialisation works
    return {
        name: {
            "total": b["total"],
            "by_vendor": dict(b["by_vendor"]),
            "fallback_count": b["fallback_count"],
            "downgrade_candidates": b["downgrade_candidates"],
            "downgrade_pct": (b["downgrade_candidates"] / b["total"] * 100) if b["total"] else 0.0,
            "with_tool_use": b["with_tool_use"],
            "median_output_tokens": (
                int(statistics.median(b["output_tokens"])) if b["output_tokens"] else None
            ),
            "p90_output_tokens": (
                int(statistics.quantiles(b["output_tokens"], n=10)[-1])
                if len(b["output_tokens"]) >= 10 else None
            ),
        }
        for name, b in buckets.items()
    }


def render_markdown(agg: dict, window_days: int, run_iso: str, total_traces: int) -> str:
    today = _local_today_iso()
    lines: list[str] = []
    lines.append(f"# LLM-fit report - {today}")
    lines.append("")
    lines.append(f"Window: last {window_days} days. Run: {run_iso}. "
                 f"Traces with llm_fallback metadata: {sum(b['total'] for b in agg.values())} "
                 f"(of {total_traces} fetched).")
    lines.append("")
    lines.append("**What this is.** Objective downgrade-audit per Track B "
                 "(plans/2026-05-24-llm-fit-logging-three-tracks.md). No model judges "
                 "another model here - the `downgrade_candidate` flag is mechanical "
                 "from the Anthropic response shape (output_tokens < 500, no tool_use, "
                 "stop_reason == end_turn, and tier is not already Haiku).")
    lines.append("")
    lines.append("**What this is NOT.** A routing recommendation. The flags surface "
                 "patterns; acting on them is a separate later decision, not part of Phase 2.")
    lines.append("")

    if not agg:
        lines.append("_No traces with llm_fallback metadata yet. Either nothing was "
                     "wired through the fallback wrapper in this window, or Langfuse was "
                     "disabled. Re-check in a few days once normal traffic accumulates._")
        lines.append("")
        return "\n".join(lines)

    lines.append("## Per-skill summary")
    lines.append("")
    lines.append("| Skill / trace | Total | Anthropic | Fallback | Downgrade flag % | Median out tok | P90 out tok | Tool use |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|")

    sorted_buckets = sorted(agg.items(), key=lambda kv: kv[1]["total"], reverse=True)
    for name, b in sorted_buckets:
        anthropic_n = b["by_vendor"].get("anthropic", 0)
        med = b["median_output_tokens"]
        p90 = b["p90_output_tokens"]
        lines.append(
            f"| {name} | {b['total']} | {anthropic_n} | {b['fallback_count']} | "
            f"{b['downgrade_pct']:.1f}% | "
            f"{med if med is not None else '-'} | "
            f"{p90 if p90 is not None else '-'} | "
            f"{b['with_tool_use']} |"
        )
    lines.append("")

    # Highlight the top downgrade candidates
    candidates = [(n, b) for n, b in agg.items() if b["downgrade_candidates"] >= 3]
    if candidates:
        lines.append("## Top downgrade candidates (>=3 flagged in window)")
        lines.append("")
        candidates.sort(key=lambda kv: kv[1]["downgrade_pct"], reverse=True)
        for name, b in candidates:
            lines.append(
                f"- **{name}**: {b['downgrade_candidates']}/{b['total']} flagged "
                f"({b['downgrade_pct']:.1f}%). Median {b['median_output_tokens']} output "
                f"tokens. If this skill is on Sonnet/Opus, a Haiku swap is worth "
                f"manual A/B-testing before formalising the route."
            )
        lines.append("")

    fallback_rows = [(n, b) for n, b in agg.items() if b["fallback_count"] > 0]
    if fallback_rows:
        lines.append("## Fallback events (Anthropic -> cross-vendor in this window)")
        lines.append("")
        for name, b in sorted(fallback_rows, key=lambda kv: kv[1]["fallback_count"], reverse=True):
            lines.append(
                f"- **{name}**: {b['fallback_count']} fallback(s) of {b['total']} call(s). "
                f"Vendor mix: {dict(b['by_vendor'])}."
            )
        lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("_Generated by `scripts/llm-fit-report.py`. Re-run any time; "
                 "the bridge daemon also runs it weekly on Sunday 03:00 local._")
    return "\n".join(lines)


def write_report(content: str) -> Path:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORT_DIR / f"{_local_today_iso()}_llm-fit-report.md"
    path.write_text(content, encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Weekly LLM-fit downgrade-audit report.")
    parser.add_argument("--days", type=int, default=7, help="Lookback window (default: 7).")
    parser.add_argument("--json", action="store_true", help="Print JSON aggregate to stdout.")
    parser.add_argument("--no-write", action="store_true", help="Skip writing the .md file.")
    args = parser.parse_args(argv)

    load_env()
    run_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")

    print(f"{CYAN}Fetching Langfuse traces (last {args.days}d)...{RESET}", file=sys.stderr)
    traces = fetch_traces(args.days)
    print(f"{GRAY}  {len(traces)} traces fetched{RESET}", file=sys.stderr)

    agg = aggregate(traces)

    if args.json:
        print(json.dumps({
            "run_iso": run_iso,
            "window_days": args.days,
            "total_traces_fetched": len(traces),
            "buckets": agg,
        }, indent=2))
        return 0

    md = render_markdown(agg, args.days, run_iso, len(traces))
    if args.no_write:
        print(md)
    else:
        path = write_report(md)
        print(f"{GREEN}Report written: {path}{RESET}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
