#!/usr/bin/env python3
"""Score the semantic index against a frozen query set, and print the bar.

The design spec asks for a differential metric, not an absolute one: this
workspace already has grep, FTS5 and `codegraph explore`, so an index earns its
place only on the questions those cannot answer. The frozen set is split
accordingly -- Set A is grep-blind by measurement, Set B is what grep already
answers -- and this script reports both, because a number for Set A alone would
hide an index that buries known-good exact answers.

Spec and bar: `docs/superpowers/specs/2026-08-21-semantic-index-commits-and-symbols-design.md`
Frozen set: `docs/superpowers/specs/2026-08-21-semantic-index-query-set-phase-1.md`
Both live in the private DATA overlay and are read through `get_data_root()`.

The set file is Markdown on purpose. Misha edits it, and a bar he cannot read is
a bar he did not agree to.

Usage:
  python scripts/eval-query-set.py --layer commit-engine
  python scripts/eval-query-set.py --layer commit-engine --top-k 5 --json
"""
import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.utils.workspace import get_data_root, get_workspace_root  # noqa: E402
from scripts.utils.colors import BOLD, CYAN, GRAY, GREEN, RED, RESET, YELLOW  # noqa: E402

SET_REL = "docs/superpowers/specs/2026-08-21-semantic-index-query-set-phase-1.md"

# The bar, from the spec. Named here so a run prints pass/fail rather than a
# number the reader has to go and compare by hand.
BAR = {"A": 0.80, "B": None}   # B is "no regression", judged against a baseline run

_ROW = re.compile(r"^\|\s*\d+\s*\|\s*(?P<q>.+?)\s*\|\s*`(?P<sha>[0-9a-f]{7,40})`\s*\|")


def load_set(path: Path) -> list[dict]:
    """Parse the frozen Markdown tables into cases, tagging each with its set.

    Section headings drive the tagging, so a query physically moved between
    tables changes set -- which is the point: set membership is a property of the
    file, never of this script.
    """
    cases, current = [], None
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("## Set A"):
            current = "A"
        elif line.startswith("## Set B"):
            current = "B"
        elif line.startswith("## "):
            current = None
        m = _ROW.match(line)
        if m and current:
            cases.append({"set": current, "q": m.group("q"), "sha": m.group("sha")})
    return cases


def query(text: str, layer: str, top_k: int, threshold: float | None = None) -> list[dict]:
    """Run one query the way the operator would.

    No threshold is passed by default ON PURPOSE. An early version forced
    `--threshold 0` and scored 85% while the real CLI answered 77%, because the
    prose-calibrated cut was suppressing correct commit hits. A harness that
    measures a path the operator never takes flatters the tool. Pass
    `--threshold` explicitly only to measure raw ranking, and say so in the report.
    """
    cmd = [sys.executable, "scripts/memory-index.py", "query", text,
           "--layer", layer, "--top-k", str(top_k), "--json"]
    if threshold is not None:
        cmd += ["--threshold", str(threshold)]
    proc = subprocess.run(
        cmd, cwd=get_workspace_root(), capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"query failed: {proc.stderr.strip()[:300]}")
    payload = json.loads(proc.stdout)
    return payload.get("hits", [])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--layer", default="commit-engine", help="index layer to score")
    ap.add_argument("--top-k", type=int, default=5, help="a hit counts if the target is in the top N")
    ap.add_argument("--threshold", type=float, default=None,
                    help="override the index threshold; omit to measure the operator's real path")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    set_path = get_data_root() / SET_REL
    if not set_path.exists():
        sys.stderr.write(f"{RED}query set not found:{RESET} {set_path}\n")
        sys.stderr.write(f"{YELLOW}It lives in the private overlay; a public clone has no bar to run.{RESET}\n")
        return 2

    cases = load_set(set_path)
    if not cases:
        sys.stderr.write(f"{RED}no cases parsed from {set_path}{RESET}\n")
        return 2

    results = []
    for c in cases:
        hits = query(c["q"], args.layer, args.top_k, args.threshold)
        # A row's path is `<label>@<full sha>`; the set carries short shas.
        rank = next(
            (i + 1 for i, h in enumerate(hits)
             if (h.get("path") or "").split("@")[-1].startswith(c["sha"])),
            None,
        )
        results.append({**c, "rank": rank, "n_hits": len(hits)})

    out = {}
    for s in ("A", "B"):
        rows = [r for r in results if r["set"] == s]
        hit = [r for r in rows if r["rank"]]
        out[s] = {"n": len(rows), "hits": len(hit),
                  "rate": (len(hit) / len(rows)) if rows else 0.0,
                  "mean_rank": (sum(r["rank"] for r in hit) / len(hit)) if hit else None}

    if args.json:
        print(json.dumps({"layer": args.layer, "top_k": args.top_k,
                          "summary": out, "cases": results}, ensure_ascii=False, indent=2))
        return 0

    thr = "index default" if args.threshold is None else f"forced {args.threshold}"
    print(f"{BOLD}{CYAN}Query set — layer {args.layer}, top-{args.top_k}, threshold {thr}{RESET}")
    print(f"{GRAY}{set_path}{RESET}\n")
    for s, label in (("A", "Set A (grep-blind — the justification)"),
                     ("B", "Set B (grep already answers — the guard)")):
        d = out[s]
        bar = BAR[s]
        verdict = ""
        if bar is not None:
            verdict = f"  {GREEN}PASS{RESET}" if d["rate"] >= bar else f"  {RED}FAIL{RESET} (bar {bar:.0%})"
        mr = f", mean rank {d['mean_rank']:.1f}" if d["mean_rank"] else ""
        print(f"{BOLD}{label}{RESET}\n  {d['hits']}/{d['n']} = {d['rate']:.0%}{mr}{verdict}")
        for r in [x for x in results if x["set"] == s]:
            mark = f"{GREEN}{r['rank']}{RESET}" if r["rank"] else f"{RED}miss{RESET}"
            print(f"    {mark:>14}  {GRAY}{r['sha']}{RESET}  {r['q'][:58]}")
        print()

    a = out["A"]
    return 0 if a["rate"] >= BAR["A"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
