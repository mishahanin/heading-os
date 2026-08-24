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
  python scripts/eval-query-set.py --phase 1
  python scripts/eval-query-set.py --phase 2 --top-k 5 --json
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

# Ceiling for one index query. Generous for a warm store plus a cold-model
# load, short enough that a stuck query fails the run instead of hanging it.
QUERY_TIMEOUT_S = 300

# Each phase has its own frozen set and its own bar, because the corpora differ.
# Commits are 1,090 items of deliberate human prose; symbols are 9,562 items of
# code, only half of which carry a docstring. The same number would not mean the
# same thing, so it is not the same number.
PHASES = {
    "1": {"rel": "docs/superpowers/specs/2026-08-21-semantic-index-query-set-phase-1.md",
          "layer": "commit-engine", "bar_a": 0.80},
    "2": {"rel": "docs/superpowers/specs/2026-08-21-semantic-index-query-set-phase-2.md",
          "layer": "symbol", "bar_a": 0.70},
}

# Phase 1 targets a commit sha; phase 2 targets `path:symbol`. One pattern for
# both, because a second parser is a second thing to keep in step with the files.
_ROW = re.compile(r"^\|\s*\d+\s*\|\s*(?P<q>.+?)\s*\|\s*`(?P<target>[^`]+)`\s*\|")


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
            cases.append({"set": current, "q": m.group("q"), "target": m.group("target")})
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
    # A bounded wait. Without one, a single hung index query stalled the whole
    # frozen-set run with no result and no error — a hang reads as "still
    # measuring", never as the failure it is.
    try:
        proc = subprocess.run(
            cmd, cwd=get_workspace_root(), capture_output=True, text=True,
            timeout=QUERY_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"query did not answer within {QUERY_TIMEOUT_S}s and was killed: {text[:80]!r}"
        ) from exc
    if proc.returncode != 0:
        raise RuntimeError(f"query failed: {proc.stderr.strip()[:300]}")
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise SystemExit(
            f"the index did not answer in JSON for --layer {layer}.\n"
            f"  stdout: {proc.stdout.strip()[:200]}\n"
            f"Most likely the layer is not configured in config/memory-index.yaml. "
            f"The `symbol` layer, for one, was withdrawn on 2026-08-21 after it "
            f"measured 46% against a 70% bar; restore its four commented lines and "
            f"its `code` collection entry to measure it again."
        ) from exc
    if payload.get("empty_index"):
        raise SystemExit(
            f"no rows for --layer {layer}: the store is empty or the layer is not "
            f"configured. Run `python scripts/memory-index.py build` first."
        )
    return payload.get("hits", [])


def _matches(hit: dict, target: str) -> bool:
    """Does this hit name the frozen target?

    Phase 1 targets a short sha and rows carry `<label>@<full sha>`. Phase 2
    targets `path:symbol` and rows carry `path:start-end` plus the qualified name
    in the title, so the file must match AND the symbol must be the row's own
    name -- matching on the path alone would credit any symbol in the right file.
    """
    path = hit.get("path") or ""
    if "@" in path:
        return path.split("@")[-1].startswith(target)
    file_part, _, symbol = target.rpartition(":")
    title = hit.get("title") or ""
    return path.startswith(file_part + ":") and title.rsplit(".", 1)[-1] == symbol


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--phase", choices=sorted(PHASES), default="1",
                    help="which frozen set and bar to run")
    ap.add_argument("--layer", default=None, help="override the phase's layer")
    ap.add_argument("--top-k", type=int, default=5, help="a hit counts if the target is in the top N")
    ap.add_argument("--threshold", type=float, default=None,
                    help="override the index threshold; omit to measure the operator's real path")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    phase = PHASES[args.phase]
    layer = args.layer or phase["layer"]
    bar_a = phase["bar_a"]
    set_path = get_data_root() / phase["rel"]
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
        hits = query(c["q"], layer, args.top_k, args.threshold)
        rank = next((i + 1 for i, h in enumerate(hits) if _matches(h, c["target"])), None)
        results.append({**c, "rank": rank, "n_hits": len(hits)})

    out = {}
    for s in ("A", "B"):
        rows = [r for r in results if r["set"] == s]
        hit = [r for r in rows if r["rank"]]
        out[s] = {"n": len(rows), "hits": len(hit),
                  "rate": (len(hit) / len(rows)) if rows else 0.0,
                  "mean_rank": (sum(r["rank"] for r in hit) / len(hit)) if hit else None}

    if args.json:
        print(json.dumps({"phase": args.phase, "layer": layer, "top_k": args.top_k,
                          "summary": out, "cases": results}, ensure_ascii=False, indent=2))
        # The SAME below-bar measurement exited 1 in terminal mode and 0 here,
        # so the machine-readable mode — the one CI reads — could not see a
        # failing index at all. One measurement, one verdict.
        return 0 if out["A"]["rate"] >= bar_a else 1

    thr = "index default" if args.threshold is None else f"forced {args.threshold}"
    print(f"{BOLD}{CYAN}Phase {args.phase} — layer {layer}, top-{args.top_k}, threshold {thr}{RESET}")
    print(f"{GRAY}{set_path}{RESET}\n")
    for s, label in (("A", "Set A (grep-blind — the justification)"),
                     ("B", "Set B (grep already answers — the guard)")):
        d = out[s]
        bar = bar_a if s == "A" else None
        verdict = ""
        if bar is not None:
            verdict = f"  {GREEN}PASS{RESET}" if d["rate"] >= bar else f"  {RED}FAIL{RESET} (bar {bar:.0%})"
        mr = f", mean rank {d['mean_rank']:.1f}" if d["mean_rank"] else ""
        print(f"{BOLD}{label}{RESET}\n  {d['hits']}/{d['n']} = {d['rate']:.0%}{mr}{verdict}")
        for r in [x for x in results if x["set"] == s]:
            mark = f"{GREEN}{r['rank']}{RESET}" if r["rank"] else f"{RED}miss{RESET}"
            print(f"    {mark:>14}  {GRAY}{r['target'][-40:]}{RESET}  {r['q'][:52]}")
        print()

    return 0 if out["A"]["rate"] >= bar_a else 1


if __name__ == "__main__":
    raise SystemExit(main())
