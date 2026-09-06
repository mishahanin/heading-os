#!/usr/bin/env python3
"""Price a recall confidence threshold on both sides, on this corpus, today.

The salience threshold decides whether `memory-index.py query` reports a
CONFIDENT answer or a flagged near-miss, and `/recall` is forbidden by its own
Phase 1 from opening files on a near-miss. It shipped at 0.55, calibrated on
keyword queries against a 2,175-file corpus. The corpus is now over 5,000 files
and the cut never moved.

This is not a tuner. It is the measurement the choice has to be made from, and
it is deliberately TWO-SIDED, because a one-sided one always says "lower it":

* BENEFIT - of the gold questions, how many come back CONFIDENT with the known
  answer inside the top k.
* COST - of the nonsense questions, how many come back confident at all. A
  confident answer to a question memory cannot answer is worse than a gap: the
  gap is honest and the confident wrong answer gets quoted back later as fact.

The method is not invented here. It is the one this workspace already used once,
for the commit layers, recorded verbatim at `config/memory-index.yaml:186-191`:
a fixed query set, a bar agreed before the numbers were seen, and the cost priced
on six nonsense queries. That calibration accepted 0.45 only because the
false-confident count was zero.

Usage:
    python scripts/recall-calibration.py measure
    python scripts/recall-calibration.py measure --grid 0.40,0.45,0.50,0.55
    python scripts/recall-calibration.py measure --dry-run --json

Both question sets live in the DATA overlay and are read through the data-root
helpers, never from a path built against the checkout: they name real documents,
and this engine repository is public.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils.colors import BOLD, CYAN, GRAY, GREEN, RED, RESET, YELLOW  # noqa: E402
from scripts.utils.workspace import (  # noqa: E402
    get_data_root,
    get_datastore_dir,
    get_outputs_dir,
    get_workspace_root,
)

# Resolved through the data-root seam, never spelled as a literal against the
# checkout: `scripts/leak-guard.py` refuses the literal form, and rightly, since
# a path built against the engine tree reads as missing and writes a second copy.
EVAL_SUBDIR = "operations/memory-eval"
GOLD_NAME = "2026-09-06_recall-gold-set.json"
NEGATIVE_NAME = "2026-09-06_recall-negative-controls.json"
RECORD_SUBDIR = "operations/memory-calibration"

DEFAULT_GRID = (0.40, 0.45, 0.50, 0.55)
DEFAULT_TOP_K = 5
QUERY_TIMEOUT = 120


# ============================================================
# Inputs
# ============================================================

def _load(path: Path) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def check_answer_paths(gold: dict, data_root: Path) -> list[str]:
    """Answer paths that no longer exist. A rotted set scores like a worse corpus.

    Returned rather than raised so the caller decides; `measure` refuses on a
    non-empty list, because a calibration recorded against a rotted set is a
    number nobody can interpret afterwards.
    """
    missing = []
    for q in gold["questions"]:
        for rel in [q["answer_path"], *q.get("alternates", [])]:
            if not (data_root / rel).exists():
                missing.append(f'{q["id"]}: {rel}')
    return missing


# ============================================================
# One query
# ============================================================

def run_query(root: Path, text: str, threshold: float, top_k: int,
              collection: str) -> dict:
    """Drive the real CLI, not an internal function.

    The internal path would skip `resolve_threshold`, the layer scoping and the
    near-miss branch, which is most of what is being measured. `--touch` is
    never passed: it would write access counts back into the corpus and the
    measurement would change the thing it measures.
    """
    proc = subprocess.run(
        [sys.executable, str(root / "scripts" / "memory-index.py"), "query", text,
         "--json", "--threshold", str(threshold), "--top-k", str(top_k),
         "--collection", collection],
        capture_output=True, text=True, cwd=str(root), timeout=QUERY_TIMEOUT,
    )
    try:
        payload = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        return {"error": (proc.stderr or proc.stdout or "").strip()[:200]}
    if "embed_unavailable" in payload:
        return {"error": "embed_unavailable"}
    return payload


def is_confident(payload: dict) -> bool:
    """A confident answer is one `/recall` is allowed to read files from.

    `cmd_query` writes `confident: false` ONLY beside `near_miss`; a confident
    result carries neither key. So the question is asked of `near_miss` and of
    there being anything at all, exactly as `.claude/hooks/recall-inject.py`
    asks it.
    """
    return bool(payload.get("hits")) and not payload.get("near_miss")


def _paths(payload: dict, top_k: int) -> list[str]:
    return [h.get("path", "") for h in payload.get("hits", [])[:top_k]]


# ============================================================
# The grid
# ============================================================

def measure_threshold(root: Path, gold: dict, negatives: dict, threshold: float,
                      top_k: int, collection: str) -> dict:
    hit_confident = 0
    hit_any = 0
    errors: list[str] = []
    per_question = []

    for q in gold["questions"]:
        payload = run_query(root, q["question"], threshold, top_k, collection)
        if "error" in payload:
            errors.append(f'{q["id"]}: {payload["error"]}')
            continue
        wanted = {q["answer_path"], *q.get("alternates", [])}
        found = [p for p in _paths(payload, top_k) if p in wanted]
        confident = is_confident(payload)
        if found:
            hit_any += 1
            if confident:
                hit_confident += 1
        per_question.append({
            "id": q["id"], "difficulty": q.get("difficulty"),
            "confident": confident, "found": bool(found),
            "top_score": payload.get("hits", [{}])[0].get("score"),
        })

    # The cost column is SPLIT, because two different mechanisms produce a
    # confident answer to an unanswerable question and only one of them is the
    # threshold's fault.
    #
    # MEASURED 2026-09-06 at the incumbent 0.55: six of eight nonsense queries
    # came back confident, and five of those six had a top cosine BELOW the cut
    # (0.30 to 0.41). They arrive through `_path_match_ids`, which is not
    # cosine-gated at all; a non-empty `combined_sparse` skips the gap/near-miss
    # branch entirely (`scripts/memory-index.py:1777`). One token shared with a
    # FILENAME - "runs" - was enough. Folding those into one number would price
    # the threshold for a cost it does not control and cannot fix.
    false_confident = []
    lexical_confident = []
    for n in negatives["questions"]:
        payload = run_query(root, n["question"], threshold, top_k, collection)
        if "error" in payload:
            errors.append(f'{n["id"]}: {payload["error"]}')
            continue
        if not is_confident(payload):
            continue
        top = payload.get("hits", [{}])[0]
        entry = {"id": n["id"], "top": _paths(payload, 1), "score": top.get("score")}
        above = (top.get("score") or 0.0) >= threshold
        (false_confident if above else lexical_confident).append(entry)

    total = len(gold["questions"])
    return {
        "threshold": threshold,
        "questions": total,
        "confident_correct": hit_confident,
        "ranked_correct": hit_any,
        "false_confident": len(false_confident),
        "false_confident_detail": false_confident,
        "lexical_confident": len(lexical_confident),
        "lexical_confident_detail": lexical_confident,
        "errors": errors,
        "per_question": per_question,
    }


# ============================================================
# Output
# ============================================================

def print_table(rows: list[dict], top_k: int) -> None:
    print()
    print(f"{BOLD}threshold   confident+correct   ranked-correct   "
          f"false-confident   lexical{RESET}")
    for r in rows:
        n = r["questions"]
        cost = r["false_confident"]
        colour = GREEN if cost == 0 else RED
        print(f"  {r['threshold']:.2f}      "
              f"{r['confident_correct']:>2}/{n} "
              f"({r['confident_correct'] / n * 100:5.1f}%)      "
              f"{r['ranked_correct']:>2}/{n}          "
              f"{colour}{cost:>2}{RESET}            "
              f"{YELLOW}{r['lexical_confident']:>2}{RESET}")
    print(f"{GRAY}  confident+correct: the answer is in the top {top_k} AND the "
          f"result is confident, so /recall may open it.{RESET}")
    print(f"{GRAY}  false-confident: a confident answer above the cut to a question "
          f"the corpus cannot answer. The bar is 0.{RESET}")
    print(f"{GRAY}  lexical: the same, but BELOW the cut - through the ungated path "
          f"channel. The threshold does not control this column.{RESET}")


def recommend(rows: list[dict]) -> tuple[float | None, str]:
    """Best benefit among the cuts whose cost is zero. Ties go to the HIGHER cut."""
    clean = [r for r in rows if r["false_confident"] == 0 and not r["errors"]]
    if not clean:
        return None, "every threshold produced a false-confident answer"
    best = max(clean, key=lambda r: (r["confident_correct"], r["threshold"]))
    incumbent = next((r for r in rows if abs(r["threshold"] - 0.55) < 1e-9), None)
    if incumbent and best["confident_correct"] <= incumbent["confident_correct"]:
        return incumbent["threshold"], "no cut beats the incumbent; leave it alone"
    return best["threshold"], (
        f"{best['confident_correct']}/{best['questions']} confident and correct, "
        f"zero false-confident")


# ============================================================
# CLI
# ============================================================

def cmd_measure(args) -> int:
    root = get_workspace_root()
    data_root = Path(get_data_root())
    eval_dir = get_outputs_dir() / EVAL_SUBDIR
    gold_path = Path(args.questions) if args.questions else eval_dir / GOLD_NAME
    neg_path = Path(args.negatives) if args.negatives else eval_dir / NEGATIVE_NAME

    for p in (gold_path, neg_path):
        if not p.is_file():
            sys.stderr.write(f"{RED}missing question set:{RESET} {p}\n")
            return 2

    gold, negatives = _load(gold_path), _load(neg_path)

    missing = check_answer_paths(gold, data_root)
    if missing:
        sys.stderr.write(
            f"{RED}refusing to measure:{RESET} {len(missing)} gold answer path(s) "
            f"no longer exist, so a lower score would read as corpus decay "
            f"rather than as set rot:\n  " + "\n  ".join(missing) + "\n")
        return 3

    grid = [float(x) for x in args.grid.split(",")]
    rows = []
    for t in grid:
        sys.stderr.write(f"{CYAN}measuring{RESET} threshold {t:.2f} over "
                         f"{len(gold['questions'])} gold + "
                         f"{len(negatives['questions'])} control queries...\n")
        rows.append(measure_threshold(root, gold, negatives, t, args.top_k,
                                      args.collection))

    chosen, why = recommend(rows)
    record = {
        "measured": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "gold_set": str(gold_path.relative_to(data_root)) if gold_path.is_relative_to(data_root) else str(gold_path),
        "negative_set": str(neg_path.relative_to(data_root)) if neg_path.is_relative_to(data_root) else str(neg_path),
        "collection": args.collection,
        "top_k": args.top_k,
        "grid": grid,
        "rows": rows,
        "recommended": chosen,
        "reason": why,
    }

    if args.json:
        print(json.dumps(record, ensure_ascii=False, indent=2))
    else:
        print_table(rows, args.top_k)
        errs = [e for r in rows for e in r["errors"]]
        if errs:
            print(f"\n{YELLOW}{len(errs)} query error(s):{RESET} " + "; ".join(errs[:5]))
        print(f"\n{BOLD}recommended:{RESET} "
              f"{chosen if chosen is not None else 'none'}  {GRAY}({why}){RESET}")

    if not args.dry_run:
        out_dir = get_datastore_dir() / RECORD_SUBDIR
        out_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        target = out_dir / f"{stamp}_threshold-grid.json"
        tmp = target.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(record, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        tmp.replace(target)
        with open(out_dir / "trend.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps({
                "measured": record["measured"],
                "recommended": chosen,
                "rows": [{k: r[k] for k in
                          ("threshold", "confident_correct", "ranked_correct",
                           "false_confident")} for r in rows],
            }, ensure_ascii=False) + "\n")
        sys.stderr.write(f"{GREEN}recorded{RESET} {target}\n")

    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="command", required=True)

    m = sub.add_parser("measure", help="price the threshold grid on both sides")
    m.add_argument("--questions", help="gold set JSON (default: the overlay's)")
    m.add_argument("--negatives", help="nonsense-query JSON (default: the overlay's)")
    m.add_argument("--grid", default=",".join(f"{t:.2f}" for t in DEFAULT_GRID))
    m.add_argument("--top-k", type=int, default=DEFAULT_TOP_K)
    m.add_argument("--collection", default="content")
    m.add_argument("--dry-run", action="store_true", help="measure, record nothing")
    m.add_argument("--json", action="store_true")
    m.set_defaults(func=cmd_measure)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
