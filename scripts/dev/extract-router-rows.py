#!/usr/bin/env python3
"""One-shot migration (F-5.1): move each skill's router row into its SKILL.md frontmatter.

Parses the seven four-column detail tables under ``reference/skill-router/`` and, for
every ``/slash`` row, writes an ``x-heading-routing`` block into the matching
``.claude/skills/<name>/SKILL.md`` frontmatter (category, triggers[], exclusions[],
compound, router, and label only when the Skill cell is not the plain ``/name``). This is
the inverse of ``scripts/generate-skill-router.py``: split on the exact separators the
generator joins on, so the round-trip reproduces each cell (modulo separator whitespace,
which folds into the one consciously-approved normalization diff).

It read ``.claude/rules/skill-router.md`` until 2026-08-27, which was correct until
F-5.2 split the generator's output in two: a TWO-column core index there, and the
four-column tables in the detail files. Every row then failed the "expected 4 cells"
check, so on the live tree it parsed 0 of 94 rows, warn-skipped all of them, and both
exit paths still printed a green success line and returned 0. The category now comes
from each detail file's NAME rather than from a ``### Heading`` scan, so the parser
cannot silently read a file whose tables are not the ones it wants.

Kept in-repo for provenance. Idempotent: an
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
# The generator's own destination for the four-column tables, so the split here
# and the join there stay in lockstep the way the imported constants above do.
CATEGORY_FILE_DIR = _gen.CATEGORY_FILE_DIR


_UNESCAPED_PIPE = re.compile(r"(?<!\\)\|")
_NAME_RE = re.compile(r"^/([a-z0-9][a-z0-9-]*)")


class _IndentDumper(yaml.SafeDumper):
    """Indent block sequences under their key (4-space items under 2-space keys), matching
    the house frontmatter style used by x-heading-orchestration."""

    def increase_indent(self, flow=False, indentless=False):
        return super().increase_indent(flow, False)


def _split_cells(row: str) -> list[str]:
    """Split a markdown table row on unescaped pipes into stripped cell strings.

    Each cell is UNESCAPED afterwards, because the generator escaped it on the
    way out. Without that step the round trip did not reproduce the cell: the
    `/canopus` trigger came back carrying `\\|`, and writing that block would
    have put a backslash into a SKILL.md that never had one.
    """
    parts = _UNESCAPED_PIPE.split(row)
    # A well-formed row is `| a | b | c | d |` -> ['', ' a ', ' b ', ' c ', ' d ', ''].
    cells = [_gen.unescape_pipes(p.strip()) for p in parts]
    # Drop the empty leading/trailing cells produced by the outer pipes.
    if cells and cells[0] == "":
        cells = cells[1:]
    if cells and cells[-1] == "":
        cells = cells[:-1]
    return cells


def parse_registry() -> tuple[dict[str, dict], list[str]]:
    """Return ({skill_name: routing_dict}, warnings) parsed from the detail tables.

    One file per category, named by ``_gen.category_slug``, so the category comes
    from the filename and a missing file is REPORTED rather than read as a
    category with no skills.
    """
    rows: dict[str, dict] = {}
    warnings: list[str] = []
    for category in CATEGORY_ORDER:
        path = CATEGORY_FILE_DIR / f"{_gen.category_slug(category)}.md"
        if not path.exists():
            warnings.append(f"{category}: no detail file at {path}")
            continue
        _parse_category_file(path, category, rows, warnings)
    return rows, warnings


def _warn_on_separator_ambiguity(name: str, routing: dict, warnings: list) -> None:
    """Report a cell whose split cannot be trusted against its own SKILL.md.

    Compared against the frontmatter the generator reads, so this asks the
    authoritative file rather than guessing from the rendered text. A skill with
    no block on disk is skipped: there is nothing to disagree with.
    """
    skill_md = SKILLS_DIR / name / "SKILL.md"
    if not skill_md.exists():
        return
    fm, err = _gen.parse_frontmatter(skill_md)
    if err:
        return
    source = (fm.get(ROUTING_KEY) or {}) if isinstance(fm.get(ROUTING_KEY), dict) else {}
    for field, sep in (("triggers", TRIGGER_SEP), ("exclusions", EXCL_SEP)):
        original = source.get(field)
        if not isinstance(original, list):
            continue
        if len(routing[field]) != len(original):
            warnings.append(
                f"{name}: {field} split into {len(routing[field])} item(s) but "
                f"the SKILL.md has {len(original)}. An item containing the "
                f"separator {sep!r} is indistinguishable from two items once "
                f"rendered; writing this block back would change the file.")


def _parse_category_file(path: Path, category: str, rows: dict, warnings: list) -> None:
    """Parse one reference/skill-router/<slug>.md into `rows`, in place."""
    for raw in path.read_text(encoding="utf-8").splitlines():
        if not raw.lstrip().startswith("|"):
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
        # A rendered cell cannot say whether `a, b` was one item or two, and
        # this tool WRITES its answer back into the authoritative SKILL.md. When
        # the split disagrees with the frontmatter it came from, the file would
        # gain an item its author never wrote. The parser cannot resolve it, so
        # it reports it rather than choosing silently.
        _warn_on_separator_ambiguity(name, routing, warnings)
        routing["compound"] = compound_cell
        routing["router"] = "manual" if "NEVER auto-trigger" in triggers_cell else "auto"
        if name in rows:
            # Every other anomaly in this parser appends a warning; a duplicate
            # row silently replaced the first, so one category's triggers were
            # lost with no signal at all.
            warnings.append(
                f"duplicate router row for {name}: the later one wins and the "
                f"earlier row's triggers are dropped")
        rows[name] = routing


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
    # Split on FENCE LINES, not on the substring `---` anywhere in the file.
    # `text.split("---", 2)` had two failure modes, and both CORRUPTED the file
    # while reporting success: a frontmatter value containing `---`
    # (`description: "alpha --- beta"`) was split mid-frontmatter, so the block
    # landed inside the YAML and the closing fence inside a value; and a file
    # with NO frontmatter but two horizontal rules in its body passed the
    # `len(parts) < 3` check and had the block spliced into its prose.
    if not text.startswith("---\n"):
        raise ValueError(f"{skill_md}: does not open with a frontmatter fence")
    close = re.search(r"^---[ \t]*$", text[4:], re.MULTILINE)
    if close is None:
        raise ValueError(f"{skill_md}: frontmatter is never closed")
    parts = ["", text[4:4 + close.start()], text[4 + close.end():]]
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
    # A parser whose whole job is to re-derive every skill's block must not
    # report success after deriving none. Between F-5.2 and 2026-08-27 it read a
    # file that no longer held its tables, warn-skipped all 94 rows, and printed
    # a green line and exit 0 down BOTH paths. The exit code is what a script or
    # a CI step reads, and a green one said the round trip had been checked.
    if not rows:
        # The path is printed WHOLE. `relative_to(ROOT)` raises ValueError when
        # the directory sits outside the repo, which is precisely the situation
        # this message is being printed about.
        print(f"{RED}FAIL{RESET}: parsed no router rows at all. The four-column "
              f"tables live under {CATEGORY_FILE_DIR}; check that they exist "
              f"and that the row shape has not changed.",
              file=sys.stderr)
        return 1
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
