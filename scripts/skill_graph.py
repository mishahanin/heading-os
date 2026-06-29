#!/usr/bin/env python3
"""Serve the skill-relationship catalog without dumping it all into context.

The catalog is a CSV (skill, phase, preceded_by, followed_by, produces_in, consumes_from).
`followed_by` / `preceded_by` are `|`-delimited skill lists encoding the soft sequence edges.
`produces_in` is the outputs/ subdirectory a skill lands its artifact in -- the join key that
maps a recently-touched file back to its producing skill. The `/next` recommender reasons over
these edges plus a "what just happened" signal (scripts/next-signal.py).

Commands:
  followers SKILL          skills that typically run AFTER this one (the recommendation)
  predecessors SKILL       skills that typically run BEFORE this one
  by-output-dir PATH       producing skill(s) for an output path, most-specific first
  show SKILL [SKILL ...]   the full catalog row for each named skill

Default output is lean text for an LLM to read; pass --json for structured output.

Usage:
  python scripts/skill_graph.py followers osint
  python scripts/skill_graph.py by-output-dir outputs/intel/osint/2026-06-04_osint_exampletelco.md
  python scripts/skill_graph.py show proposal --json

Consumed by: /next (and scripts/next-signal.py, which imports by_output_dir).
"""
import argparse
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils.colors import BOLD, CYAN, GRAY, GREEN, RESET  # noqa: E402
from scripts.utils.workspace import get_workspace_root  # noqa: E402

FIELDS = ("skill", "phase", "preceded_by", "followed_by", "produces_in", "consumes_from")
SEP = "|"


def default_file() -> Path:
    return get_workspace_root() / "reference" / "skill-graph.csv"


def load(file: Path) -> list[dict]:
    with open(file, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        for k in FIELDS:
            r.setdefault(k, "")
            r[k] = (r.get(k) or "").strip()
    return rows


def _split(cell: str) -> list[str]:
    return [s.strip() for s in cell.split(SEP) if s.strip()]


def _row(rows: list[dict], skill: str) -> dict | None:
    key = skill.strip().lower()
    for r in rows:
        if r["skill"].lower() == key:
            return r
    return None


def followers(rows: list[dict], skill: str) -> list[str]:
    r = _row(rows, skill)
    return _split(r["followed_by"]) if r else []


def predecessors(rows: list[dict], skill: str) -> list[str]:
    r = _row(rows, skill)
    return _split(r["preceded_by"]) if r else []


def by_output_dir(rows: list[dict], path: str) -> list[str]:
    """Producing skill(s) whose produces_in is a prefix of `path`, most-specific first.

    A shared subdir (e.g. outputs/intel) legitimately maps to several skills; the recency
    signal disambiguates. Returns [] when no produces_in matches.
    """
    p = path.strip().strip("/")
    matches = []
    for r in rows:
        produces = r["produces_in"].strip().strip("/")
        if produces and (p == produces or p.startswith(produces + "/")):
            matches.append((len(produces), r["skill"]))
    matches.sort(key=lambda t: (-t[0], t[1]))
    return [skill for _, skill in matches]


def fmt_names(names: list[str], as_json: bool) -> str:
    if as_json:
        return json.dumps(names)
    return "\n".join(f"{CYAN}{n}{RESET}" for n in names) if names else f"{GRAY}(none){RESET}"


def fmt_show(rows: list[dict], as_json: bool) -> str:
    if as_json:
        return json.dumps([{k: r[k] for k in FIELDS} for r in rows])
    blocks = []
    for r in rows:
        block = f"{BOLD}{r['skill']}{RESET}  {GRAY}[{r['phase']}]{RESET}"
        if r["followed_by"]:
            block += f"\n{GREEN}followed_by:{RESET} {r['followed_by']}"
        if r["preceded_by"]:
            block += f"\n{GRAY}preceded_by:{RESET} {r['preceded_by']}"
        if r["produces_in"]:
            block += f"\n{GRAY}produces_in:{RESET} {r['produces_in']}"
        blocks.append(block)
    return "\n\n".join(blocks)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument(
        "--file", type=Path, default=None,
        help="catalog CSV (default: reference/skill-graph.csv)",
    )
    # Shared so each subcommand accepts --json after it (e.g. `followers osint --json`).
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", help="emit structured JSON instead of lean text")
    sub = p.add_subparsers(dest="cmd", required=True)
    pf = sub.add_parser("followers", help="skills that run after SKILL", parents=[common])
    pf.add_argument("skill")
    pp = sub.add_parser("predecessors", help="skills that run before SKILL", parents=[common])
    pp.add_argument("skill")
    pb = sub.add_parser("by-output-dir", help="producing skill(s) for an output path", parents=[common])
    pb.add_argument("path")
    psh = sub.add_parser("show", help="full catalog row for named skills", parents=[common])
    psh.add_argument("skills", nargs="+")
    args = p.parse_args(argv)

    file = args.file or default_file()
    if not file.is_file():
        print(f"error: skill-graph catalog not found: {file}", file=sys.stderr)
        return 2
    rows = load(file)

    if args.cmd == "followers":
        print(fmt_names(followers(rows, args.skill), args.json))
    elif args.cmd == "predecessors":
        print(fmt_names(predecessors(rows, args.skill), args.json))
    elif args.cmd == "by-output-dir":
        print(fmt_names(by_output_dir(rows, args.path), args.json))
    elif args.cmd == "show":
        found = [r for s in args.skills if (r := _row(rows, s))]
        missing = [s for s in args.skills if not _row(rows, s)]
        for m in missing:
            print(f"# not found: {m}", file=sys.stderr)
        if not found:
            return 1
        print(fmt_show(found, args.json))
    return 0


if __name__ == "__main__":
    sys.exit(main())
