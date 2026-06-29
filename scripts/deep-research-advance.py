#!/usr/bin/env python3
"""
deep-research-advance.py — headless Phase 0-2 of /deep-research-advance.

Runs the token-heavy work OFF the Claude session:
  Phase 0  decompose the question into angles (Kimi)
  Phase 1  fan-out web acquisition, one call per angle (Perplexity)
  Phase 2  synthesize + per-claim verify the corpus (Kimi)
Writes a compact intermediate.json the SKILL (Claude) consumes for Phase 3-5.

GUARDRAIL: only the public research question + web corpus flow to Kimi/Perplexity.
No CRM/Odin/private context is ever passed in.

Usage:
  python scripts/deep-research-advance.py "<question>" [--depth N] [--critical]
      [--domains a.com,b.com] [--exclude-domains x.com]

Exit codes:
  0  success — intermediate.json written (may be degraded=true; e.g. Kimi
     unavailable degrades to corpus-without-analysis for the skill to handle)
  2  bad arguments (argparse usage error: bad --depth, or --domains together
     with --exclude-domains). The RuntimeError catch in main() is a safety net.
  3  unrecoverable acquisition failure: no corpus at all (every Perplexity call
     failed, including the case of a missing PERPLEXITY_API_KEY).
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.utils.workspace import get_outputs_dir  # noqa: E402
from scripts.utils.colors import GREEN, YELLOW, RED, RESET  # noqa: E402
from scripts.utils.kimi_transport import reason as kimi_reason  # noqa: E402
from scripts.utils.perplexity_client import research as pplx_research  # noqa: E402
from scripts.utils.deep_research_prompts import (  # noqa: E402
    build_decompose_prompt,
    build_reason_prompt,
)

DEFAULT_DEPTH = 4
MAX_DEPTH = 8


def slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s[:40] or "research"


def run_slug(question: str) -> str:
    """Run-directory slug: a readable 40-char prefix plus a short hash of the
    FULL question. Two questions sharing a 40-char prefix (e.g. both starting
    "what is publicly known on the open web...") would otherwise collide on the
    same directory and overwrite each other's intermediate.json. The hash keeps
    re-runs of the SAME question idempotent (same question -> same directory)
    while separating distinct ones.
    """
    # usedforsecurity=False: this hash only disambiguates a directory name, it is
    # not a security primitive (silences ruff/bandit S324 on sha1).
    h = hashlib.sha1(question.encode("utf-8"), usedforsecurity=False).hexdigest()[:8]
    return f"{slugify(question)}-{h}"


def extract_json(raw: str):
    """Parse a JSON object/array from model output, tolerating a ```json fence or prose."""
    fence = re.search(r"```(?:json)?\s*(.*?)```", raw, re.DOTALL)
    candidate = fence.group(1) if fence else raw
    candidate = candidate.strip()
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        m = re.search(r"(\{.*\}|\[.*\])", candidate, re.DOTALL)
        if not m:
            raise
        return json.loads(m.group(1))


def run(question: str, depth: int = DEFAULT_DEPTH, critical: bool = False,
        domains: Optional[str] = None, exclude_domains: Optional[str] = None,
        recency: Optional[str] = None) -> Path:
    """Execute Phases 0-2 and write intermediate.json. Returns the run directory."""
    now = datetime.datetime.now().astimezone()
    date = now.strftime("%Y-%m-%d")
    run_dir = get_outputs_dir() / "research" / f"{date}_deep-research_{run_slug(question)}"
    run_dir.mkdir(parents=True, exist_ok=True)

    result = {
        "question": question,
        "generated_at": now.isoformat(),
        "depth": depth,
        "critical": critical,
        "angles": [],
        "sources": [],
        "corpus": [],
        "kimi_analysis": {},
        "degraded": False,
        "degraded_reason": "",
    }

    # Phase 0 — decompose (Kimi). Fall back to the bare question on failure.
    try:
        raw = kimi_reason(build_decompose_prompt(question, depth), max_tokens=2048)
        angles = extract_json(raw)
        if not isinstance(angles, list) or not angles:
            raise ValueError("decompose did not return a non-empty list")
        result["angles"] = [str(a) for a in angles][:depth]
    except Exception as e:
        print(f"{YELLOW}Phase 0 decompose failed ({e}); using the bare question.{RESET}", file=sys.stderr)
        result["angles"] = [question]
        result["degraded"] = True
        result["degraded_reason"] = f"decompose: {e}"

    # Phase 1 — acquire (Perplexity fan-out). Each angle -> one search.
    next_source_id = 1
    for angle in result["angles"]:
        try:
            content, citations = pplx_research(
                angle, domains=domains, exclude_domains=exclude_domains,
                recency=recency)
        except RuntimeError as e:
            print(f"{YELLOW}Perplexity failed for angle '{angle}': {e}{RESET}", file=sys.stderr)
            continue
        sids = []
        for url in citations:
            result["sources"].append({"id": next_source_id, "url": url, "angle": angle})
            sids.append(next_source_id)
            next_source_id += 1
        result["corpus"].append({"angle": angle, "content": content, "source_ids": sids})

    if not result["corpus"]:
        _write(run_dir, {**result, "degraded": True,
                         "degraded_reason": "no corpus: all Perplexity calls failed"})
        print(f"{RED}No corpus acquired — aborting.{RESET}", file=sys.stderr)
        sys.exit(3)

    # Phase 2 — reason + verify (Kimi). Retry once on transient failure (cloud
    # latency on a large reasoning prompt is the common cause) with a longer
    # timeout, then degrade gracefully if it still fails.
    reason_prompt = build_reason_prompt(question, result["corpus"])
    last_err = None
    for attempt in range(2):
        try:
            raw = kimi_reason(reason_prompt, max_tokens=8192, timeout=180.0)
            result["kimi_analysis"] = extract_json(raw)
            last_err = None
            break
        except Exception as e:
            last_err = e
            if attempt == 0:
                print(f"{YELLOW}Phase 2 reasoning failed ({e}); retrying once.{RESET}", file=sys.stderr)
    if last_err is not None:
        print(f"{YELLOW}Phase 2 reasoning failed after retry ({last_err}); corpus saved without analysis.{RESET}",
              file=sys.stderr)
        result["kimi_analysis"] = {"summary": "", "claims": [], "contradictions": []}
        result["degraded"] = True
        result["degraded_reason"] = (result["degraded_reason"] + f"; kimi reason: {last_err}").strip("; ")

    _write(run_dir, result)
    print(f"{GREEN}intermediate.json written:{RESET} {run_dir / 'intermediate.json'}")
    return run_dir


def _write(run_dir: Path, result: dict) -> None:
    """Atomic write of intermediate.json."""
    path = run_dir / "intermediate.json"
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="deep-research-advance.py",
                                description="Headless Phase 0-2 of /deep-research-advance.")
    p.add_argument("question", help="The research question (public-web topic).")
    p.add_argument("--depth", type=int, default=DEFAULT_DEPTH,
                   help=f"Number of angles (1-{MAX_DEPTH}). Default {DEFAULT_DEPTH}.")
    p.add_argument("--critical", action="store_true",
                   help="Mark this run critical (forces Claude's audit governor in the skill).")
    p.add_argument("--domains", default=None, help="Comma-separated include domains (max 20).")
    p.add_argument("--exclude-domains", default=None, help="Comma-separated exclude domains (max 20).")
    p.add_argument("--recency", default=None,
                   choices=["hour", "day", "week", "month", "year"],
                   help="Perplexity time window. Default: none (full index) — correct "
                        "for evergreen/footprint research. Set e.g. 'week' for recent-events research.")
    args = p.parse_args(argv)

    if args.domains and args.exclude_domains:
        p.error("Cannot use --domains and --exclude-domains together")
    depth = max(1, min(args.depth, MAX_DEPTH))

    try:
        run(args.question, depth=depth, critical=args.critical,
            domains=args.domains, exclude_domains=args.exclude_domains,
            recency=args.recency)
    except RuntimeError as e:
        print(f"{RED}Error:{RESET} {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
