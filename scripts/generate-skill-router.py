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
workflow section, plugin notes, ...) is preserved unchanged, LINE ENDINGS ASIDE:
`read_text`/`write_text` apply universal-newline translation, so a CRLF router file
is rewritten LF on POSIX. "byte-for-byte" was the old wording and it was false for
any non-LF file. The repository is LF, `--check` compares translated text, and the
claim is narrowed rather than the I/O rewritten. ``--check`` regenerates
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

from scripts.utils.colors import RED, GREEN, CYAN, YELLOW, RESET  # noqa: E402
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

    NOT MIGRATED to ``scripts.utils.markdown.parse_frontmatter``, for the same
    reason that audit keeps its own copy: the shared util collapses every failure
    mode into ``({}, text)``, and the error string is this gate's whole output -
    ``load_routing_rows`` prints ``{rel}: {err}`` and CI fails on it. Measured
    2026-08-20 over the 96 SKILL.md corpus: the rendered rows and the audit
    results are identical under the shared util today, but the parsed dict
    already differs on 2 of 96 (canopus, census - the shared util's regex drops
    the newline before the closing fence, so the last folded scalar of
    ``x-heading-capability`` loses its trailing "\\n"). Deduplicating the two
    gates needs a diagnostic parser in scripts/utils/markdown.py that returns the
    taxonomy; until that exists, the mirrored copy is deliberate.
    """
    try:
        text = skill_md.read_text(encoding="utf-8")
    except OSError as exc:
        return {}, f"unreadable: {exc}"
    # Split on FENCE LINES, not on the three characters wherever they land.
    # `text.split("---", 2)` matched `---` inside a scalar, so a description
    # like `handles drift --- state check` either failed the gate with a
    # misleading "invalid YAML" message or, worse, parsed a TRUNCATED mapping:
    # every key after the embedded `---` silently dropped, the routing row
    # generated from partial data, and `--check` ratifying it. The same defect
    # was fixed in `scripts/dev/extract-router-rows.py` on 2026-08-24; this was
    # the second copy.
    if not re.match(r"^---[ \t]*$", text.split("\n", 1)[0]):
        return {}, "no frontmatter (missing opening ---)"
    closing = re.search(r"^---[ \t]*$", text[4:], re.MULTILINE)
    if closing is None:
        return {}, "malformed frontmatter (missing closing ---)"
    body = text[4:4 + closing.start()]
    try:
        data = yaml.safe_load(body)
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

def _as_list(value, *, field: str) -> list[str]:
    """Coerce a triggers/exclusions frontmatter value to a list of strings.

    Raises ``ValueError`` on an entry that is not a string. That case is not
    hypothetical and the coercion is what made it dangerous: an unquoted
    ``colon-space`` inside a YAML list item makes the whole item parse as a
    MAPPING, and the old ``str(v)`` rendered the mapping's Python ``repr`` -
    braces, quoted key, quoted value - straight into the generated router row.

    Measured 2026-08-20 on `.claude/skills/checkpoint/SKILL.md`: one such
    sentence put a Python dict literal into `.claude/rules/skill-router.md`, an
    ALWAYS-ON rule injected into every session, and grew that row from 804 to
    867 characters. Both gates stayed green through it - `--check` compared a
    corrupt generation against a corrupt file and found them equal, and
    `skill-metadata-check.py` never inspects the item type. A coercion that can
    turn a structural mistake into valid-looking output is a gate that reports
    on nothing.
    """
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if not isinstance(value, list):
        # A MAPPING iterates its keys, and every key is a string, so
        # `triggers: {alpha: 1, beta: 2}` was coerced into the plausible-looking
        # list `[alpha, beta]` with the values silently dropped, and both gates
        # stayed green. The docstring above says this function exists to stop a
        # structural mistake becoming valid-looking output; the non-str ITEM
        # case was guarded and the non-list CONTAINER case was not.
        raise ValueError(
            f"{field}: value is a {type(value).__name__}, not a list: "
            f"{str(value)[:80]!r}. Write it as a YAML list of strings."
        )
    out: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise ValueError(
                f"{field}: entry is a {type(item).__name__}, not a string: "
                f"{str(item)[:80]!r}. A YAML list item containing an unquoted "
                f"'colon space' parses as a mapping - quote the whole item, or "
                f"replace the colon with a dash."
            )
        out.append(item)
    return out


def load_routing_rows() -> tuple[list[dict], list[str]]:
    """Read every skill's x-heading-routing block.

    Returns (rows, errors). Each row is a dict with keys name, category, label,
    triggers, exclusions, compound, router. errors is a list of human-readable
    strings (missing block, bad category, ...); a non-empty errors list means the
    registry must not be generated.

    Advisory warnings go straight to stderr and never block. They exist so a
    disagreement between a field and the prose it describes is VISIBLE without
    a night-shift decision to make it fatal.
    """
    rows: list[dict] = []
    errors: list[str] = []
    warnings: list[str] = []
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
        # Validated like every other field. `name` was taken raw, so a YAML
        # `name: 7` produced an int that reached `sorted(key=lambda r: r["name"])`
        # and raised `TypeError: '<' not supported between 'str' and 'int'` --
        # an uncaught traceback instead of the curated `{rel}: {err}` line this
        # gate exists to print. `name: 0` and `name: false` were quieter still:
        # both are falsy, so `or child.name` swallowed them and the directory
        # name silently stood in for a value the author had set on purpose.
        raw_name = fm.get("name")
        if raw_name is not None and not isinstance(raw_name, str):
            errors.append(
                f"{rel}: 'name' is {raw_name!r} ({type(raw_name).__name__}); "
                f"it must be a string. An unquoted YAML scalar like `name: 7` "
                f"or `name: no` is not.")
            continue
        name = raw_name or child.name
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
        try:
            triggers = _as_list(routing.get("triggers"), field=f"{ROUTING_KEY}.triggers")
            exclusions = _as_list(routing.get("exclusions"), field=f"{ROUTING_KEY}.exclusions")
        except ValueError as exc:
            errors.append(f"{rel}: {exc}")
            continue
        router = routing.get("router", "auto")
        if router not in ("auto", "manual"):
            errors.append(f"{rel}: {ROUTING_KEY}.router must be 'auto' or 'manual', "
                          f"got {router!r}")
            continue
        # The `router` field was LOADED and never read again: not validated, not
        # rendered, not used to filter. `FIX_IT_SNIPPET` documents it as
        # `manual (NEVER auto-trigger skills)`, so an author who set it believed
        # they had switched a safety control that did nothing. It is read now,
        # in two steps of very different strength.
        #
        # HARD: the value must be one of the two documented ones. A typo used to
        # pass silently.
        #
        # SOFT: a warning when the field disagrees with the trigger cell, which
        # is what the always-on rule actually shows the model. Measured across
        # all 94 skills on 2026-08-24: 23 manual, all 23 already saying "NEVER
        # auto-trigger", and no auto skill saying it. So the convention holds
        # everywhere today -- but it is a convention about ENGLISH PROSE in a
        # free-form list, and a future author writing "explicit invocation only"
        # would be correct and still fail a hard gate. Turning this into an
        # error is a change to how skills are authored, which is the operator's
        # call, not a night-shift one.
        #
        # NOT tied to `disable-model-invocation`, the harness-enforced flag that
        # is the real control: `brain-audit` is `router: manual` and
        # deliberately does NOT set it, because composing skills invoke it
        # through the Skill tool and the flag would block them. One measured
        # exception is enough to say that link is not an invariant either.
        # `str()` on an unvalidated value, and PyYAML is YAML 1.1: an unquoted
        # `compound: No` parses to the BOOLEAN False, and `str(False)` is
        # "False". The always-on router rule then showed `| False |` in the
        # Compound column -- and because the corruption is deterministic,
        # `--check` regenerated the same wrong cell and passed. Every other
        # field here is type-checked for exactly this reason; this one was not.
        compound_raw = routing.get("compound", "No")
        if isinstance(compound_raw, bool):
            errors.append(
                f"{rel}: {ROUTING_KEY}.compound is the YAML boolean "
                f"{compound_raw!r}. Unquoted No/Yes/On/Off are booleans in "
                f"YAML 1.1 -- write compound: \"No\" (quoted) or a real "
                f"description like 'Yes: Meeting Prep'.")
            continue
        if not isinstance(compound_raw, str):
            errors.append(
                f"{rel}: {ROUTING_KEY}.compound is {compound_raw!r} "
                f"({type(compound_raw).__name__}); it must be a string.")
            continue
        compound = compound_raw

        says_never = "never auto-trigger" in " ".join(triggers).lower()
        if (router == "manual") != says_never:
            warnings.append(
                f"{rel}: {ROUTING_KEY}.router is {router!r} but the triggers "
                f"{'do not say' if router == 'manual' else 'say'} "
                f"'NEVER auto-trigger'. The trigger cell is what the always-on "
                f"router rule shows the model; the two should agree.")
        rows.append(
            {
                "name": name,
                "category": category,
                "label": routing.get("label") or f"/{name}",
                "triggers": triggers,
                "exclusions": exclusions,
                "compound": compound,
                "router": router,
            }
        )
    for warning in warnings:
        print(f"{CYAN}note{RESET}: {warning}", file=sys.stderr)
    return rows, errors


# ============================================================
# Rendering
# ============================================================

def escape_pipes(text: str) -> str:
    """Escape a raw ``|`` as ``\\|`` for markdown-table safety, leaving an already
    escaped ``\\|`` untouched.

    Parity-aware. A plain negative lookbehind treated ANY preceding backslash as
    an escape, so a literal backslash that is DATA (`C:\\|foo`) left its pipe
    unescaped and split the table cell into a spurious column. A pipe is already
    escaped only when preceded by an ODD run of backslashes.
    """
    def _fix(match: "re.Match") -> str:
        slashes = match.group(1)
        return match.group(0) if len(slashes) % 2 else slashes + "\\|"

    return re.sub(r"(\\*)\|", _fix, text)


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
    # `\n.*?\n` demanded at least one line BETWEEN the markers, so a file whose
    # markers sit on adjacent lines -- which is what you get after clearing the
    # region for the generator to refill, or after adding the markers exactly as
    # the error above instructs -- matched zero times and raised "expected
    # exactly one marker region, found 0". One pair of markers was present; the
    # message sent the reader hunting for duplicates that did not exist, and
    # both --write and --check exited 2 with no way back.
    pattern = re.compile(
        re.escape(MARKER_BEGIN) + r"\n?.*?\n?" + re.escape(MARKER_END), re.DOTALL
    )
    replacement = MARKER_BEGIN + "\n" + region + "\n" + MARKER_END
    new_text, n = pattern.subn(lambda _m: replacement, router_text)
    if n == 0:
        raise ValueError(
            "both markers are present but no region could be matched between "
            "them; this should not happen -- report the router file's contents")
    if n > 1:
        raise ValueError(
            f"found {n} marker regions in "
            f"{ROUTER_FILE.relative_to(ROOT)}; there must be exactly one. "
            f"Remove the extra {MARKER_BEGIN} / {MARKER_END} pair(s).")
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

    # DETAIL FILES FIRST, the core index last. Neither order is atomic, but the
    # order decides what a reader sees if a write fails mid-way: the router
    # index is the always-on layer the model reads every session, so it is the
    # one that must never point at detail files that have not caught up. This
    # ran the other way round, so a permissions or disk failure inside the loop
    # left the always-on index describing frontmatter the detail files did not.
    wrote_any = False
    CATEGORY_FILE_DIR.mkdir(parents=True, exist_ok=True)

    # Orphans are REMOVED, not merely reported. `cmd_split_check` calls any
    # *.md here that no current category backs an ORPHAN and counts it as
    # drift, then tells the operator to run this command -- which only ever
    # wrote the seven expected files and deleted nothing. So one stray file (a
    # renamed category committed earlier, a leftover from a reverted branch, a
    # hand-written note) made `--check` fail forever, with CI and pre-commit
    # unresolvable by the documented path and an undocumented `rm` as the only
    # way out. Reproduced 2026-08-25.
    #
    # Every removal is named. This directory is generated in full by this
    # function, so deleting what does not belong is within its remit -- but a
    # tool that deletes a file the operator wrote must say which one.
    for stray in sorted(CATEGORY_FILE_DIR.glob("*.md")):
        if stray.name in {f"{category_slug(c)}.md" for c in CATEGORY_ORDER}:
            continue
        print(f"{YELLOW}removed orphan{RESET}: "
              f"{stray.relative_to(ROOT)} (no category backs it)")
        stray.unlink()
        wrote_any = True

    for category in CATEGORY_ORDER:
        path = CATEGORY_FILE_DIR / f"{category_slug(category)}.md"
        content = render_category_file(category, rows)
        existing = path.read_text(encoding="utf-8") if path.exists() else None
        if existing != content:
            path.write_text(content, encoding="utf-8")
            wrote_any = True

    if new_text != router_text:
        ROUTER_FILE.write_text(new_text, encoding="utf-8")
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
    # INSIDE the group. `--check --flat` used to be accepted, print the flat
    # monolith, and exit 0 WITHOUT running the drift check -- a CI invocation
    # with a typo'd flag combination going green while checking nothing.
    # `--write --flat` silently discarded the write the same way.
    mode.add_argument("--flat", action="store_true",
                      help="Print the legacy flat monolith to stdout (debug + semantics proof); no write.")
    mode.add_argument(
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
