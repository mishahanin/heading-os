#!/usr/bin/env python3
"""Generate the skill-router.md registry tables from each SKILL.md's x-heading-routing frontmatter.

The seven registry tables in ``.claude/rules/skill-router.md`` are a build artifact.
Each skill owns its router row in its own SKILL.md frontmatter under
``x-heading-routing`` (category, triggers[], exclusions[], compound, router, optional
label); this script renders those rows between the sentinel markers. Everything outside
the markers (protocol header, corporate-docs guardrail, compound-workflow section,
plugin notes, ...) is preserved byte-for-byte.

This replaces the presence-only ``check-skill-router-sync.py``: ``--check`` regenerates
in memory and diffs against the on-disk marked region, failing on any *content* drift, so
a router row can no longer disagree with its skill.

Usage:
    python scripts/generate-skill-router.py            # --write (default): rewrite the marked region in place
    python scripts/generate-skill-router.py --check    # regen -> diff; exit 1 on drift (CI / pre-commit)
    python scripts/generate-skill-router.py --flat     # explicit flat monolith (the current default shape)
    python scripts/generate-skill-router.py --split-by-category   # F-5.2 shape; not yet implemented (exit 2)
"""

import argparse
import difflib
import re
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils.colors import RED, GREEN, YELLOW, CYAN, RESET  # noqa: E402
from scripts.utils.workspace import get_workspace_root  # noqa: E402

# ============================================================
# Configuration
# ============================================================

ROOT = get_workspace_root()
SKILLS_DIR = ROOT / ".claude" / "skills"
ROUTER_FILE = ROOT / ".claude" / "rules" / "skill-router.md"

# Skill subdirs that are not actual skills (archived, internal).
SKIP_SUBDIRS = {"archive", "_archive", ".cache"}

# Fixed category order in the rendered registry (matches the hand-written order today).
CATEGORY_ORDER = ["Intel", "Communication", "Content", "CRM", "Design", "Strategy", "Operations"]

# Cell separators. The migration splits on exactly these; the generator joins on exactly
# these, so join(sep, split(sep, cell)) reproduces the cell modulo separator whitespace.
TRIGGER_SEP = ", "
EXCL_SEP = "; "

ROUTING_KEY = "x-heading-routing"

MARKER_BEGIN = "<!-- BEGIN GENERATED REGISTRY (generate-skill-router.py; do not edit) -->"
MARKER_END = "<!-- END GENERATED REGISTRY -->"

TABLE_HEADER = "| Skill | Triggers | Exclusions | Compound |"
TABLE_SEP = "|---|---|---|---|"

FIX_IT_SNIPPET = """\
x-heading-routing:
  category: <Intel|Communication|Content|CRM|Design|Strategy|Operations>
  triggers: ["<trigger phrase>", "<another>"]
  exclusions: ["<signal> -> /<other-skill>"]   # or ["N/A"]
  compound: "No"                                 # or "Yes: <pattern>"
  router: auto                                   # or manual (NEVER auto-trigger skills)
  # label: "/name [args]"                        # only when the Skill cell is not the plain /name"""


# ============================================================
# Frontmatter parsing
# ============================================================

def parse_frontmatter(skill_md: Path) -> tuple[dict, str]:
    """Return (frontmatter_dict, error_message); error_message is empty on success.

    Mirrors scripts/skill-metadata-check.py::parse_frontmatter for consistency.
    """
    try:
        text = skill_md.read_text(encoding="utf-8")
    except OSError as exc:
        return {}, f"unreadable: {exc}"
    if not text.startswith("---"):
        return {}, "no frontmatter (missing opening ---)"
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, "malformed frontmatter (missing closing ---)"
    try:
        data = yaml.safe_load(parts[1])
    except yaml.YAMLError as exc:
        return {}, f"invalid YAML frontmatter: {exc}"
    if data is None:
        return {}, "empty frontmatter"
    if not isinstance(data, dict):
        return {}, f"frontmatter must be a mapping, got {type(data).__name__}"
    return data, ""


# ============================================================
# Row loading
# ============================================================

def _as_list(value) -> list[str]:
    """Coerce a triggers/exclusions frontmatter value to a list of strings."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    return [str(v) for v in value]


def load_routing_rows() -> tuple[list[dict], list[str]]:
    """Read every skill's x-heading-routing block.

    Returns (rows, errors). Each row is a dict with keys name, category, label,
    triggers, exclusions, compound, router. errors is a list of human-readable
    strings (missing block, bad category, ...); a non-empty errors list means the
    registry must not be generated.
    """
    rows: list[dict] = []
    errors: list[str] = []
    if not SKILLS_DIR.exists():
        return rows, [f"skills dir not found: {SKILLS_DIR}"]

    for child in sorted(SKILLS_DIR.iterdir(), key=lambda p: p.name):
        if not child.is_dir() or child.name in SKIP_SUBDIRS:
            continue
        skill_md = child / "SKILL.md"
        if not skill_md.exists():
            continue
        fm, err = parse_frontmatter(skill_md)
        rel = skill_md.relative_to(ROOT)
        if err:
            errors.append(f"{rel}: {err}")
            continue
        name = fm.get("name") or child.name
        routing = fm.get(ROUTING_KEY)
        if not isinstance(routing, dict):
            errors.append(
                f"{rel}: missing '{ROUTING_KEY}' block. Add it under the frontmatter:\n"
                + "\n".join("      " + ln for ln in FIX_IT_SNIPPET.splitlines())
            )
            continue
        category = routing.get("category")
        if category not in CATEGORY_ORDER:
            errors.append(
                f"{rel}: '{ROUTING_KEY}.category' is {category!r}; must be one of {CATEGORY_ORDER}"
            )
            continue
        rows.append(
            {
                "name": name,
                "category": category,
                "label": routing.get("label") or f"/{name}",
                "triggers": _as_list(routing.get("triggers")),
                "exclusions": _as_list(routing.get("exclusions")),
                "compound": str(routing.get("compound", "No")),
                "router": routing.get("router", "auto"),
            }
        )
    return rows, errors


# ============================================================
# Rendering
# ============================================================

def escape_pipes(text: str) -> str:
    """Escape a raw ``|`` as ``\\|`` for markdown-table safety, leaving an already
    escaped ``\\|`` untouched (negative lookbehind on the backslash)."""
    return re.sub(r"(?<!\\)\|", r"\\|", text)


def render_row(row: dict) -> str:
    # The Skill column is backtick-wrapped code: `/name` or `/name [args]`.
    label = escape_pipes(row["label"])
    triggers = escape_pipes(TRIGGER_SEP.join(row["triggers"]))
    exclusions = escape_pipes(EXCL_SEP.join(row["exclusions"]))
    compound = escape_pipes(row["compound"])
    return f"| `{label}` | {triggers} | {exclusions} | {compound} |"


def render_registry(rows: list[dict]) -> str:
    """Render the seven category tables as the content that lives between the markers.

    Deterministic ordering: fixed category order, then skill name ascending within a
    category. Blocks are separated by a blank line; no trailing newline.
    """
    blocks: list[str] = []
    for category in CATEGORY_ORDER:
        members = sorted(
            (r for r in rows if r["category"] == category), key=lambda r: r["name"]
        )
        lines = [f"### {category}", "", TABLE_HEADER, TABLE_SEP]
        lines.extend(render_row(r) for r in members)
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def splice_region(router_text: str, region: str) -> str:
    """Replace the text strictly between the two markers with ``region``.

    Everything outside the markers is preserved byte-for-byte. Raises ValueError if a
    marker is missing.
    """
    if MARKER_BEGIN not in router_text or MARKER_END not in router_text:
        raise ValueError(
            f"sentinel markers not found in {ROUTER_FILE.relative_to(ROOT)}; "
            f"add\n  {MARKER_BEGIN}\n  {MARKER_END}\naround the '### Intel' ... last registry row."
        )
    pattern = re.compile(
        re.escape(MARKER_BEGIN) + r"\n.*?\n" + re.escape(MARKER_END), re.DOTALL
    )
    replacement = MARKER_BEGIN + "\n" + region + "\n" + MARKER_END
    new_text, n = pattern.subn(lambda _m: replacement, router_text)
    if n != 1:
        raise ValueError(f"expected exactly one marker region, found {n}")
    return new_text


# ============================================================
# Commands
# ============================================================

def _report_errors(errors: list[str]) -> None:
    print(f"{RED}FAIL{RESET}: {len(errors)} skill(s) cannot be rendered:", file=sys.stderr)
    for e in errors:
        print(f"  - {e}", file=sys.stderr)


def cmd_write(rows: list[dict]) -> int:
    router_text = ROUTER_FILE.read_text(encoding="utf-8")
    region = render_registry(rows)
    try:
        new_text = splice_region(router_text, region)
    except ValueError as exc:
        print(f"{RED}ERROR{RESET}: {exc}", file=sys.stderr)
        return 2
    if new_text == router_text:
        print(f"{GREEN}OK{RESET}: registry already current ({len(rows)} skills).")
        return 0
    ROUTER_FILE.write_text(new_text, encoding="utf-8")
    print(f"{GREEN}WROTE{RESET}: regenerated registry region ({len(rows)} skills) in {ROUTER_FILE.relative_to(ROOT)}.")
    return 0


def cmd_check(rows: list[dict]) -> int:
    router_text = ROUTER_FILE.read_text(encoding="utf-8")
    region = render_registry(rows)
    try:
        new_text = splice_region(router_text, region)
    except ValueError as exc:
        print(f"{RED}ERROR{RESET}: {exc}", file=sys.stderr)
        return 2
    if new_text == router_text:
        print(f"{GREEN}OK{RESET}: registry in sync with SKILL.md frontmatter ({len(rows)} skills).")
        return 0
    diff = difflib.unified_diff(
        router_text.splitlines(keepends=True),
        new_text.splitlines(keepends=True),
        fromfile="skill-router.md (on disk)",
        tofile="skill-router.md (regenerated)",
        n=2,
    )
    excerpt = "".join(diff)
    print(
        f"{RED}DRIFT{RESET}: the generated registry differs from the on-disk region. "
        f"Run {CYAN}python scripts/generate-skill-router.py{RESET} and commit.",
        file=sys.stderr,
    )
    print(excerpt, file=sys.stderr)
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--write", action="store_true", help="Rewrite the marked region in place (default).")
    mode.add_argument("--check", action="store_true", help="Regenerate and diff; exit 1 on drift (CI / pre-commit).")
    parser.add_argument("--flat", action="store_true", help="Explicit flat monolith (the current default shape).")
    parser.add_argument(
        "--split-by-category", action="store_true",
        help="F-5.2 per-category output shape; not yet implemented.",
    )
    args = parser.parse_args()

    if args.split_by_category:
        print(
            f"{YELLOW}--split-by-category is the F-5.2 output shape and is not yet implemented.{RESET}\n"
            "This pass ships the flat monolith only (--flat / default).",
            file=sys.stderr,
        )
        return 2

    if not ROUTER_FILE.exists():
        print(f"{RED}ERROR{RESET}: {ROUTER_FILE} not found", file=sys.stderr)
        return 2

    rows, errors = load_routing_rows()
    if errors:
        _report_errors(errors)
        return 1

    if args.check:
        return cmd_check(rows)
    return cmd_write(rows)


if __name__ == "__main__":
    sys.exit(main())
