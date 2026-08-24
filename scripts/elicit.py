#!/usr/bin/env python3
"""Serve the elicitation/critique technique catalog without dumping it all into context.

The catalog is a CSV (num, category, method_name, description, output_pattern). `description`
is a self-contained gist — enough to propose and run a method inline. `output_pattern` is the
arrow-notation shape of the method's flow (e.g. `assumptions -> truths -> new approach`). There
is no detail-file layer: every method is fully described by its row, so no command ever reaches
out to extra files or loads the whole catalog implicitly.

Commands:
  categories                    list category names + counts (the cheap entry point)
  list --category C [...]        the index (category/name/gist) for those categories
  list --all                     the whole index at once — deliberate; large, avoid interactively
  show NAME [NAME ...]           full gist + output pattern for each named method
  random [--category C] [-n N]   pick N at random (optionally within categories)

`list` refuses to run with neither --category nor --all: reaching the whole catalog at once
must always be an explicit, deliberate choice, never an accident.

Default output is lean text for an LLM to read; pass --json for structured output.

Usage:
  python scripts/elicit.py categories
  python scripts/elicit.py list --category risk --category framing
  python scripts/elicit.py show "First Principles Analysis" "Pre-mortem Analysis"
  python scripts/elicit.py random --category creative -n 3
  python scripts/elicit.py categories --json

Consumed by: /deep-think, /devil, /scrutinize, /council (optional method-selection sub-phase).
"""
import argparse
import csv
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils.colors import BOLD, CYAN, GRAY, GREEN, RESET  # noqa: E402
from scripts.utils.workspace import get_workspace_root  # noqa: E402

FIELDS = ("num", "category", "method_name", "description", "output_pattern")


def default_file() -> Path:
    return get_workspace_root() / "reference" / "elicitation-methods.csv"


REQUIRED_FIELDS = ("category", "method_name", "description")


def load(file: Path) -> list[dict]:
    """Rows from the catalog CSV. Raises ValueError when it is not the catalog.

    `setdefault` below fills a MISSING column with "", which is right for the
    optional `num` / `output_pattern` and catastrophic for the rest: any CSV at
    all was accepted and served as a catalog of nameless methods. Measured on a
    two-row `name,email` file, `categories` reported one category - the empty
    string - with 2 methods in it, and `list` printed two rows of nothing at
    exit 0. `--file` makes that reachable by a typo'd path to a real CSV.
    """
    with open(file, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        header = reader.fieldnames or []
        missing = [k for k in REQUIRED_FIELDS if k not in header]
        if missing:
            raise ValueError(
                f"{file} is not the elicitation catalog: no {', '.join(missing)} "
                f"column(s). Header was: {', '.join(header) or '(empty file)'}"
            )
        rows = list(reader)
    for r in rows:
        for k in FIELDS:
            r.setdefault(k, "")
            r[k] = (r.get(k) or "").strip()
    return rows


def categories(rows: list[dict]) -> list[tuple[str, int]]:
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["category"]] = counts.get(r["category"], 0) + 1
    return sorted(counts.items())


def filter_cats(rows: list[dict], cats: list[str] | None) -> list[dict]:
    if not cats:
        return rows
    wanted = {c.lower() for c in cats}
    return [r for r in rows if r["category"].lower() in wanted]


def find(rows: list[dict], names: list[str]) -> tuple[list[dict], list[str]]:
    """(found rows, unmatched names). A duplicate name is reported, not resolved.

    The dict comprehension keeps the LAST row for a repeated `method_name`, so
    `show "X"` would hand back one of two different methods with no signal that
    a choice was made. The catalog is hand-maintained (53 rows, no duplicates
    today), which is exactly the kind of file where a second row named the same
    thing arrives without anyone noticing.
    """
    by_name: dict[str, dict] = {}
    for r in rows:
        key = r["method_name"].lower()
        if key and key in by_name:
            print(f"warning: two catalog rows are named {r['method_name']!r} "
                  f"(rows {by_name[key].get('num') or '?'} and "
                  f"{r.get('num') or '?'}); `show` returns the later one.",
                  file=sys.stderr)
        by_name[key] = r
    found, missing = [], []
    for n in names:
        r = by_name.get(n.strip().lower())
        (found if r else missing).append(r if r else n)
    return found, missing


def fmt_categories(cats: list[tuple[str, int]], as_json: bool) -> str:
    if as_json:
        return json.dumps([{"category": c, "count": n} for c, n in cats])
    return "\n".join(f"{CYAN}{c}{RESET}\t{n}" for c, n in cats)


def fmt_list(rows: list[dict], as_json: bool) -> str:
    if as_json:
        return json.dumps([{k: r[k] for k in ("category", "method_name", "description")} for r in rows])
    return "\n".join(f"{GRAY}{r['category']}{RESET}\t{BOLD}{r['method_name']}{RESET}\t{r['description']}" for r in rows)


def fmt_show(rows: list[dict], as_json: bool) -> str:
    if as_json:
        return json.dumps([
            {k: r[k] for k in ("category", "method_name", "description", "output_pattern")} for r in rows
        ])
    blocks = []
    for r in rows:
        block = f"{BOLD}{r['method_name']}{RESET}  {GRAY}[{r['category']}]{RESET}\n{r['description']}"
        if r.get("output_pattern"):
            block += f"\n{GREEN}pattern:{RESET} {r['output_pattern']}"
        blocks.append(block)
    return "\n\n".join(blocks)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument(
        "--file", type=Path, default=None,
        help="technique CSV (default: reference/elicitation-methods.csv)",
    )
    # Shared so each subcommand accepts --json after it (e.g. `show NAME --json`).
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", help="emit structured JSON instead of lean text")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("categories", help="list category names + counts", parents=[common])
    pl = sub.add_parser("list", help="the index: category/name/gist (needs --category or --all)", parents=[common])
    pl.add_argument("--category", action="append", help="filter to a category (repeatable)")
    pl.add_argument("--all", action="store_true", help="dump the entire catalog (deliberate; large)")
    ps = sub.add_parser("show", help="full gist + output pattern for named methods", parents=[common])
    ps.add_argument("names", nargs="+")
    pr = sub.add_parser("random", help="pick methods at random", parents=[common])
    pr.add_argument("--category", action="append", help="restrict to a category (repeatable)")
    pr.add_argument("-n", type=int, default=1, help="how many (default 1)")
    args = p.parse_args(argv)

    file = args.file or default_file()
    if not file.is_file():
        print(f"error: technique file not found: {file}", file=sys.stderr)
        return 2
    try:
        rows = load(file)
    except (OSError, ValueError, csv.Error) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.cmd == "categories":
        print(fmt_categories(categories(rows), args.json))
    elif args.cmd == "list":
        if not args.category and not args.all:
            print(
                "error: `list` needs --category (one or more) — or --all to dump the whole "
                "catalog on purpose. Use `categories` for the cheap map, or `random` to draw blind.",
                file=sys.stderr,
            )
            return 2
        # A category that matches nothing is named, not answered with silence.
        # `list --category framming` printed an empty string and exited 0, which
        # reads as "that category is empty" - while `random --category framming`
        # said "# no methods match" and exited 1 for the same typo. One tool,
        # two answers to the same mistake.
        selected = filter_cats(rows, args.category)
        if args.category and not selected:
            known = ", ".join(c for c, _ in categories(rows))
            print(f"error: no methods in category/categories "
                  f"{', '.join(args.category)}. Known: {known}", file=sys.stderr)
            return 1
        print(fmt_list(selected, args.json))
    elif args.cmd == "show":
        found, missing = find(rows, args.names)
        for m in missing:
            print(f"# not found: {m}", file=sys.stderr)
        if not found:
            return 1
        print(fmt_show(found, args.json))
    elif args.cmd == "random":
        pool = filter_cats(rows, args.category)
        if not pool:
            print("# no methods match", file=sys.stderr)
            return 1
        n = max(0, min(args.n, len(pool)))  # clamp: never crash on a negative or oversized -n
        print(fmt_list(random.sample(pool, n), args.json))
    return 0


if __name__ == "__main__":
    sys.exit(main())
