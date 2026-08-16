#!/usr/bin/env python3
"""Generate the partner scorecard in partners.md from the pipeline.

`context/partners.md` opens with a Partner Scorecard Summary table. It was
hand-written, and on 2026-08-17 it held six partners against twenty-three
partnership rows in `context/pipeline.md`. It also carried an executed
worldwide OEM agreement as "In Discussion", eighty days after signature,
which is what `/deal-strategy`, `/proposal`, `/investor-pitch` and
`/data-room` were reading as fact.

A second hand-maintained copy of a list drifts. So the summary table is
generated from the pipeline, exactly the way `pipeline-summary.py` already
generates the Pipeline Summary block, and everything a human wrote outside
the two markers survives untouched. The detailed per-partner profiles below
the table stay hand-written on purpose: that is judgement, not a list.

Usage:
    python scripts/partner-scorecard.py             # print the table
    python scripts/partner-scorecard.py --update    # splice it into partners.md
    python scripts/partner-scorecard.py --check     # exit 1 if partners.md has drifted
"""
from __future__ import annotations
import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.utils.colors import GREEN, RED, YELLOW, BOLD, RESET  # noqa: E402
from scripts.utils.workspace import get_context_dir  # noqa: E402

BEGIN = "<!-- BEGIN GENERATED SCORECARD -->"
END = "<!-- END GENERATED SCORECARD -->"

# Health is derived from the pipeline Stage cell, never invented here. An
# unmapped stage stays blank rather than guessing a colour: a wrong GREEN on a
# partner nobody has spoken to since March is worse than an empty cell.
STAGE_HEALTH = {
    "active": "GREEN",
    "demo/poc": "GREEN",
    "strategic asset": "GREEN",
    "discussions initiated": "YELLOW",
    "post-mwc": "YELLOW",
    "parked": "--",
}


def parse_partnerships(content: str) -> list[dict]:
    """Read the Partnership Discussions table out of pipeline.md."""
    rows, in_table, seen_sep = [], False, False
    for line in content.split("\n"):
        if line.strip().startswith("#") and "partnership discussions" in line.lower():
            in_table, seen_sep = True, False
            continue
        if in_table and line.strip().startswith("|"):
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if not seen_sep:
                if cells and cells[0].startswith("---"):
                    seen_sep = True
                elif cells and cells[0].lower() == "partner":
                    pass
                continue
            if len(cells) < 5:
                continue
            # "Globex (Jane Roe)" is one partner. The parenthetical is the
            # contact, and carrying it into the name makes the same partner
            # unmatchable the moment the contact changes.
            name = re.sub(r"\s*\([^)]*\)\s*$", "", cells[0]).strip()
            rows.append({
                "partner": name,
                "topic": cells[1],
                "stage": cells[2],
                "priority": cells[3],
                "stage_date": cells[4],
            })
        elif in_table and seen_sep and line.strip() and not line.strip().startswith("|"):
            break
    return rows


def _health(stage: str) -> str:
    s = stage.lower()
    for key, colour in STAGE_HEALTH.items():
        if key in s:
            return colour
    return "--"


def render_scorecard(rows: list[dict]) -> str:
    out = [
        "| Partner | Topic | Stage | Priority | Stage date | Health |",
        "|---------|-------|-------|----------|------------|--------|",
    ]
    for r in sorted(rows, key=lambda x: x["partner"].lower()):
        stage = r["stage"].replace("|", "/")
        out.append(
            f"| {r['partner']} | {r['topic']} | {stage} | "
            f"{r['priority']} | {r['stage_date']} | {_health(r['stage'])} |"
        )
    out.append("")
    out.append(f"_{len(rows)} partnerships. Generated from `context/pipeline.md` by "
               f"`scripts/partner-scorecard.py --update`. Do not hand-edit this block; "
               f"edit the pipeline row instead._")
    return "\n".join(out)


def splice(partners_md: str, table: str) -> str:
    """Replace the marked block, leaving every human-written line alone."""
    if BEGIN not in partners_md or END not in partners_md:
        raise ValueError(
            f"partners.md is missing the {BEGIN} / {END} marker pair; a generator "
            f"that silently writes nothing looks exactly like one that found no "
            f"changes, so this is an error rather than a no-op"
        )
    pattern = re.compile(
        re.escape(BEGIN) + r"[\s\S]*?" + re.escape(END), re.MULTILINE)
    return pattern.sub(f"{BEGIN}\n\n{table}\n\n{END}", partners_md, count=1)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--update", action="store_true", help="Write into partners.md")
    ap.add_argument("--check", action="store_true",
                    help="Exit 1 if partners.md differs from the generated table")
    args = ap.parse_args(argv)

    ctx = get_context_dir()
    pipeline, partners = ctx / "pipeline.md", ctx / "partners.md"
    for f in (pipeline, partners):
        if not f.exists():
            print(f"{RED}missing: {f}{RESET}", file=sys.stderr)
            return 2

    rows = parse_partnerships(pipeline.read_text(encoding="utf-8"))
    if not rows:
        print(f"{RED}no Partnership Discussions rows found in {pipeline.name}{RESET}",
              file=sys.stderr)
        return 2
    table = render_scorecard(rows)
    current = partners.read_text(encoding="utf-8")

    if args.check:
        try:
            expected = splice(current, table)
        except ValueError as exc:
            print(f"{RED}{exc}{RESET}", file=sys.stderr)
            return 1
        if expected != current:
            print(f"{RED}{BOLD}partners.md scorecard has drifted.{RESET} "
                  f"Run `python scripts/partner-scorecard.py --update`.")
            return 1
        print(f"{GREEN}partner scorecard in sync{RESET} ({len(rows)} partnerships)")
        return 0

    if args.update:
        try:
            partners.write_text(splice(current, table), encoding="utf-8")
        except ValueError as exc:
            print(f"{RED}{exc}{RESET}", file=sys.stderr)
            return 1
        print(f"{GREEN}wrote{RESET} {len(rows)} partnerships into {partners.name}")
        return 0

    print(table)
    print(f"\n{YELLOW}(preview only - pass --update to write){RESET}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
