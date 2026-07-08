#!/usr/bin/env python3
"""Generate the skill-router registry from each SKILL.md's x-heading-routing frontmatter.

The registry is a build artifact. Each skill owns its router row in its own SKILL.md
frontmatter under ``x-heading-routing`` (category, triggers[], exclusions[], compound,
router, optional label). This script renders those rows into a two-layer split (F-5.2):

  1. A compact core index (Skill + Triggers only) between the sentinel markers in the
     always-active ``.claude/rules/skill-router.md`` -- enough for first-pass routing.
  2. Per-category detail files ``reference/skill-router/<category>.md`` carrying the full
     Skill | Triggers | Exclusions | Compound table -- read on demand for disambiguation.

Everything outside the markers (protocol header, corporate-docs guardrail, compound-
workflow section, plugin notes, ...) is preserved byte-for-byte. ``--check`` regenerates
BOTH layers in memory and diffs against disk, failing on any *content* drift, so a router
row can no longer disagree with its skill.

Usage:
    python scripts/generate-skill-router.py            # default: write the split layout (core + per-category files)
    python scripts/generate-skill-router.py --check    # regen both layers -> diff; exit 1 on drift (CI / pre-commit)
    python scripts/generate-skill-router.py --split-by-category   # explicit synonym of the default write
    python scripts/generate-skill-router.py --flat     # print the legacy flat monolith to stdout (debug / semantics proof); no write
"""

import argparse
import difflib
import re
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils.colors import RED, GREEN, CYAN, RESET  # noqa: E402
from scripts.utils.workspace import get_workspace_root  # noqa: E402

# ============================================================
# Configuration
# ============================================================

ROOT = get_workspace_root()
SKILLS_DIR = ROOT / ".claude" / "skills"
ROUTER_FILE = ROOT / ".claude" / "rules" / "skill-router.md"
# F-5.2: the verbose per-category detail tables live here (engine-routed, shareable),
# read on demand; only the compact index stays in the always-on router rule.
CATEGORY_FILE_DIR = ROOT / "reference" / "skill-router"

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

# F-5.2 compact core index: only Skill + Triggers stay always-on; Exclusions and
# Compound move to the per-category detail files.
CORE_TABLE_HEADER = "| Skill | Triggers |"
CORE_TABLE_SEP = "|---|---|"

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


def category_slug(category: str) -> str:
    """Filename slug for a category's detail file. 'CRM' -> 'crm', 'Intel' -> 'intel'."""
    return category.lower()


def render_core_row(row: dict) -> str:
    """Compact 2-column core-index row: backtick label + full triggers only."""
    label = escape_pipes(row["label"])
    triggers = escape_pipes(TRIGGER_SEP.join(row["triggers"]))
    return f"| `{label}` | {triggers} |"


def render_core_index(rows: list[dict]) -> str:
    """Render the compact always-on core index (the content between the markers).

    Per category: a heading, a pointer to the detail file, and a 2-column
    Skill|Triggers table (full triggers, for first-pass matching). Exclusions and
    compound patterns live in the detail file, read on demand. Deterministic order:
    fixed category order, then skill name ascending. Blocks separated by a blank
    line; no trailing newline (matches splice_region's contract).
    """
    blocks: list[str] = []
    for category in CATEGORY_ORDER:
        members = sorted(
            (r for r in rows if r["category"] == category), key=lambda r: r["name"]
        )
        slug = category_slug(category)
        lines = [
            f"### {category}",
            "",
            f"Full triggers, exclusions, and compound patterns: "
            f"`reference/skill-router/{slug}.md`",
            "",
            CORE_TABLE_HEADER,
            CORE_TABLE_SEP,
        ]
        lines.extend(render_core_row(r) for r in members)
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def render_category_file(category: str, rows: list[dict]) -> str:
    """Render one whole reference/skill-router/<slug>.md detail file.

    H1 + one-line description + a generated-by note (no volatile date, so the file
    is byte-identical every run and --check stays deterministic), then the full
    4-column table via the shared render_row. Trailing newline (whole-file).
    """
    members = sorted(
        (r for r in rows if r["category"] == category), key=lambda r: r["name"]
    )
    lines = [
        f"# Skill Router — {category}",
        "",
        f"Full routing detail (triggers, exclusions, compound patterns) for the "
        f"{category} skill category.",
        "",
        "Generated by scripts/generate-skill-router.py from each SKILL.md "
        "x-heading-routing block; do not edit by hand. "
        "Consumed by .claude/rules/skill-router.md.",
        "",
        TABLE_HEADER,
        TABLE_SEP,
    ]
    lines.extend(render_row(r) for r in members)
    return "\n".join(lines) + "\n"


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


def cmd_split_write(rows: list[dict]) -> int:
    """Write the compact core index into the markers AND the 7 category detail files."""
    router_text = ROUTER_FILE.read_text(encoding="utf-8")
    core = render_core_index(rows)
    try:
        new_text = splice_region(router_text, core)
    except ValueError as exc:
        print(f"{RED}ERROR{RESET}: {exc}", file=sys.stderr)
        return 2

    wrote_any = False
    if new_text != router_text:
        ROUTER_FILE.write_text(new_text, encoding="utf-8")
        wrote_any = True

    CATEGORY_FILE_DIR.mkdir(parents=True, exist_ok=True)
    for category in CATEGORY_ORDER:
        path = CATEGORY_FILE_DIR / f"{category_slug(category)}.md"
        content = render_category_file(category, rows)
        existing = path.read_text(encoding="utf-8") if path.exists() else None
        if existing != content:
            path.write_text(content, encoding="utf-8")
            wrote_any = True

    if wrote_any:
        print(f"{GREEN}WROTE{RESET}: regenerated compact core index + "
              f"{len(CATEGORY_ORDER)} category files ({len(rows)} skills).")
    else:
        print(f"{GREEN}OK{RESET}: core index + category files already current "
              f"({len(rows)} skills).")
    return 0


def cmd_split_check(rows: list[dict]) -> int:
    """Verify both layers (core region + every category file) for content idempotency."""
    router_text = ROUTER_FILE.read_text(encoding="utf-8")
    core = render_core_index(rows)
    try:
        new_text = splice_region(router_text, core)
    except ValueError as exc:
        print(f"{RED}ERROR{RESET}: {exc}", file=sys.stderr)
        return 2

    # (name, on_disk_text, regenerated_text) for each drifted/missing/orphan artifact.
    drift: list[tuple[str, str, str]] = []
    if new_text != router_text:
        drift.append((".claude/rules/skill-router.md (core region)", router_text, new_text))

    for category in CATEGORY_ORDER:
        slug = category_slug(category)
        path = CATEGORY_FILE_DIR / f"{slug}.md"
        content = render_category_file(category, rows)
        if not path.exists():
            drift.append((f"reference/skill-router/{slug}.md (MISSING)", "", content))
        else:
            existing = path.read_text(encoding="utf-8")
            if existing != content:
                drift.append((f"reference/skill-router/{slug}.md", existing, content))

    # Orphan detail files (a *.md not backed by a current category) are drift too.
    if CATEGORY_FILE_DIR.exists():
        expected = {f"{category_slug(c)}.md" for c in CATEGORY_ORDER}
        for f in sorted(CATEGORY_FILE_DIR.glob("*.md")):
            if f.name not in expected:
                drift.append(
                    (f"reference/skill-router/{f.name} (ORPHAN)",
                     f.read_text(encoding="utf-8"), "")
                )

    if not drift:
        print(f"{GREEN}OK{RESET}: core index + category files in sync with "
              f"SKILL.md frontmatter ({len(rows)} skills).")
        return 0

    print(
        f"{RED}DRIFT{RESET}: {len(drift)} artifact(s) differ from the SKILL.md "
        f"frontmatter. Run {CYAN}python scripts/generate-skill-router.py{RESET} and commit.",
        file=sys.stderr,
    )
    for name, old, new in drift:
        print(f"  - {name}", file=sys.stderr)
        diff = difflib.unified_diff(
            old.splitlines(keepends=True), new.splitlines(keepends=True),
            fromfile=f"{name} (on disk)", tofile=f"{name} (regenerated)", n=2,
        )
        sys.stderr.write("".join(diff))
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--write", action="store_true",
                     help="Write the split layout in place: compact core index + per-category files (default).")
    mode.add_argument("--check", action="store_true",
                     help="Regenerate both layers and diff; exit 1 on drift (CI / pre-commit).")
    parser.add_argument("--flat", action="store_true",
                        help="Print the legacy flat monolith to stdout (debug + semantics proof); no write.")
    parser.add_argument(
        "--split-by-category", action="store_true",
        help="Explicit synonym of the default split write (compact core + per-category files).",
    )
    args = parser.parse_args()

    if not ROUTER_FILE.exists():
        print(f"{RED}ERROR{RESET}: {ROUTER_FILE} not found", file=sys.stderr)
        return 2

    rows, errors = load_routing_rows()
    if errors:
        _report_errors(errors)
        return 1

    # --flat is a stdout-only debug/proof action: print the legacy flat monolith,
    # never write. (Used by the semantics-preservation test.)
    if args.flat:
        print(render_registry(rows))
        return 0

    # Split is the canonical layout. --split-by-category is an explicit synonym of
    # the default write; --check verifies both layers.
    if args.check:
        return cmd_split_check(rows)
    return cmd_split_write(rows)


if __name__ == "__main__":
    sys.exit(main())
