#!/usr/bin/env python3
"""One-shot migration (F-5.1): move each skill's router row into its SKILL.md frontmatter.

Parses the seven registry tables in ``.claude/rules/skill-router.md`` and, for every
``/slash`` row, writes an ``x-heading-routing`` block into the matching
``.claude/skills/<name>/SKILL.md`` frontmatter (category, triggers[], exclusions[],
compound, router, and label only when the Skill cell is not the plain ``/name``). This is
the inverse of ``scripts/generate-skill-router.py``: split on the exact separators the
generator joins on, so the round-trip reproduces each cell (modulo separator whitespace,
which folds into the one consciously-approved normalization diff).

Kept in-repo for provenance, like ``scripts/dev/rename-x31c-namespace.py``. Idempotent: an
existing ``x-heading-routing`` block is replaced, not duplicated. The block is appended to
the end of the frontmatter as text (yaml.dump for correct quoting), so the rest of each
SKILL.md - folded scalars, key order, body - is never reflowed.

Usage:
    python scripts/dev/extract-router-rows.py --dry-run   # print per-skill blocks, write nothing
    python scripts/dev/extract-router-rows.py             # write the blocks
"""

import argparse
import importlib.util
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from scripts.utils.colors import RED, GREEN, YELLOW, CYAN, GRAY, RESET  # noqa: E402

# Reuse the generator's constants so the split (here) and the join (there) stay in lockstep.
_GEN_PATH = ROOT / "scripts" / "generate-skill-router.py"
_spec = importlib.util.spec_from_file_location("generate_skill_router", _GEN_PATH)
_gen = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_gen)

CATEGORY_ORDER = _gen.CATEGORY_ORDER
TRIGGER_SEP = _gen.TRIGGER_SEP
EXCL_SEP = _gen.EXCL_SEP
ROUTING_KEY = _gen.ROUTING_KEY

SKILLS_DIR = ROOT / ".claude" / "skills"
ROUTER_FILE = ROOT / ".claude" / "rules" / "skill-router.md"

_CATEGORY_RE = re.compile(r"^### (" + "|".join(re.escape(c) for c in CATEGORY_ORDER) + r")\s*$")
_UNESCAPED_PIPE = re.compile(r"(?<!\\)\|")
_NAME_RE = re.compile(r"^/([a-z0-9][a-z0-9-]*)")


class _IndentDumper(yaml.SafeDumper):
    """Indent block sequences under their key (4-space items under 2-space keys), matching
    the house frontmatter style used by x-heading-orchestration."""

    def increase_indent(self, flow=False, indentless=False):
        return super().increase_indent(flow, False)


def _split_cells(row: str) -> list[str]:
    """Split a markdown table row on unescaped pipes into stripped cell strings."""
    parts = _UNESCAPED_PIPE.split(row)
    # A well-formed row is `| a | b | c | d |` -> ['', ' a ', ' b ', ' c ', ' d ', ''].
    cells = [p.strip() for p in parts]
    # Drop the empty leading/trailing cells produced by the outer pipes.
    if cells and cells[0] == "":
        cells = cells[1:]
    if cells and cells[-1] == "":
        cells = cells[:-1]
    return cells


def parse_registry() -> tuple[dict[str, dict], list[str]]:
    """Return ({skill_name: routing_dict}, warnings) parsed from the router tables."""
    rows: dict[str, dict] = {}
    warnings: list[str] = []
    category = None
    for raw in ROUTER_FILE.read_text(encoding="utf-8").splitlines():
        m = _CATEGORY_RE.match(raw)
        if m:
            category = m.group(1)
            continue
        if category is None or not raw.lstrip().startswith("|"):
            continue
        stripped = raw.strip()
        if stripped.startswith("| Skill ") or set(stripped) <= set("|- "):
            continue  # header or separator row
        cells = _split_cells(raw)
        if len(cells) != 4:
            warnings.append(f"{category}: row with {len(cells)} cells (expected 4): {stripped[:60]}...")
            continue
        label_cell, triggers_cell, exclusions_cell, compound_cell = cells
        # The Skill cell is backtick-wrapped code: `/name` or `/name [args]`.
        inner = label_cell
        if inner.startswith("`") and inner.endswith("`") and len(inner) >= 2:
            inner = inner[1:-1]
        else:
            warnings.append(f"{category}: Skill cell not backtick-wrapped: {label_cell[:60]}")
        name_m = _NAME_RE.match(inner)
        if not name_m:
            warnings.append(f"{category}: cannot parse skill name from label cell: {label_cell[:60]}")
            continue
        name = name_m.group(1)
        routing: dict = {"category": category}
        if inner != f"/{name}":
            routing["label"] = inner
        routing["triggers"] = [t.strip() for t in triggers_cell.split(TRIGGER_SEP)]
        routing["exclusions"] = [e.strip() for e in exclusions_cell.split(EXCL_SEP)]
        routing["compound"] = compound_cell
        routing["router"] = "manual" if "NEVER auto-trigger" in triggers_cell else "auto"
        rows[name] = routing
    return rows, warnings


def _render_block(routing: dict) -> str:
    """yaml.dump just the x-heading-routing block (correct quoting, house indentation)."""
    return yaml.dump(
        {ROUTING_KEY: routing},
        Dumper=_IndentDumper,
        sort_keys=False,
        allow_unicode=True,
        width=10 ** 9,
        default_flow_style=False,
    )


def _strip_existing_block(fm_lines: list[str]) -> list[str]:
    """Remove an existing top-level x-heading-routing block (idempotency)."""
    out: list[str] = []
    skipping = False
    for line in fm_lines:
        if line.startswith(f"{ROUTING_KEY}:"):
            skipping = True
            continue
        if skipping:
            if line.strip() == "" or line.startswith((" ", "\t")):
                continue  # still inside the block
            skipping = False
        out.append(line)
    return out


def apply_block(skill_md: Path, routing: dict) -> str:
    """Insert/replace the x-heading-routing block at the end of the frontmatter. Returns the
    rendered block text (for dry-run display)."""
    text = skill_md.read_text(encoding="utf-8")
    parts = text.split("---", 2)
    if len(parts) < 3:
        raise ValueError(f"{skill_md}: malformed frontmatter")
    block = _render_block(routing)  # ends with a trailing newline
    fm_yaml = parts[1].strip("\n")  # pure frontmatter body, no fence-adjacent newlines
    fm_lines = _strip_existing_block(fm_yaml.split("\n"))
    new_fm_yaml = "\n".join(fm_lines).rstrip("\n")
    # ---\n{existing keys}\n{block}---{body}  reproduces the original fences exactly.
    new_text = "---\n" + new_fm_yaml + "\n" + block + "---" + parts[2]
    skill_md.write_text(new_text, encoding="utf-8")
    return block


def _dry_render(routing: dict) -> str:
    return _render_block(routing)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    parser.add_argument("--dry-run", action="store_true", help="Print per-skill blocks; write nothing.")
    args = parser.parse_args()

    rows, warnings = parse_registry()
    disk_skills = {
        d.name for d in SKILLS_DIR.iterdir()
        if d.is_dir() and d.name not in _gen.SKIP_SUBDIRS and (d / "SKILL.md").exists()
    }

    unmatched_rows = sorted(set(rows) - disk_skills)
    missing_rows = sorted(disk_skills - set(rows))

    print(f"{CYAN}Parsed {len(rows)} router rows across {len(CATEGORY_ORDER)} categories.{RESET}")
    for w in warnings:
        print(f"  {YELLOW}warn{RESET}: {w}")
    if unmatched_rows:
        print(f"  {YELLOW}rows with no matching skill dir{RESET}: {', '.join(unmatched_rows)}")
    if missing_rows:
        print(f"  {YELLOW}skill dirs with no router row{RESET}: {', '.join(missing_rows)}")

    written = 0
    for name in sorted(rows):
        if name not in disk_skills:
            continue
        skill_md = SKILLS_DIR / name / "SKILL.md"
        if args.dry_run:
            print(f"\n{GRAY}--- {name} ---{RESET}")
            print(_dry_render(rows[name]).rstrip())
        else:
            apply_block(skill_md, rows[name])
            written += 1

    matched = sum(1 for n in rows if n in disk_skills)
    if args.dry_run:
        print(f"\n{GREEN}DRY-RUN{RESET}: {matched} block(s) would be written; nothing changed.")
    else:
        print(f"{GREEN}WROTE{RESET}: x-heading-routing into {written} SKILL.md file(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
