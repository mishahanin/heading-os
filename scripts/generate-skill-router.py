#!/usr/bin/env python3
"""Generate the skill-router registry from each SKILL.md's x-heading-routing frontmatter.

The registry is a build artifact. Each skill owns its router row in its own SKILL.md
frontmatter under ``x-heading-routing`` (category, triggers[], exclusions[], compound,
router, optional label). This script renders those rows into a two-layer split (F-5.2):

  1. A compact core index between the sentinel markers in the always-active
     ``.claude/rules/skill-router.md`` -- enough for first-pass routing and no more.
     It carries Skill + Triggers for the ``router: auto`` skills ONLY, minus any
     trigger that merely repeats the skill's own name. The ``router: manual``
     skills keep a ROW (several gates parse the registry through it) but their
     trigger cell is reduced to ``MANUAL_TRIGGER_CELL``, because no message can
     route to them and they are owed no matching vocabulary in the layer that is
     paid for on every session.
  2. Per-category detail files ``reference/skill-router/<category>.md`` carrying the full
     Skill | Triggers | Exclusions | Compound table for EVERY skill, auto and manual
     alike -- read on demand for disambiguation.

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

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils.colors import RED, GREEN, CYAN, YELLOW, RESET  # noqa: E402
from scripts.utils.workspace import get_workspace_root  # noqa: E402
from scripts.utils import markdown as md  # noqa: E402
from scripts.utils.markdown import parse_frontmatter_strict  # noqa: E402
from scripts.utils.repo_files import read_sources  # noqa: E402

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

# The Triggers cell of a `router: manual` row in the ALWAYS-ON core index.
#
# A manual skill cannot be reached by matching a natural-language message, so
# every byte of trigger text on its row is spent telling the model not to do
# something it cannot do. Measured 2026-09-04: the 23 manual skills spent 3868
# bytes of the core index on exactly that, `/checkpoint` alone spending 1072 to
# describe three session switches -- inside a rule whose entire job is matching.
#
# What actually stops them being matched is not that prose. 22 of the 23 carry
# `disable-model-invocation: true`, which the HARNESS enforces: the model cannot
# invoke them from natural language at all. The 23rd is `brain-audit`, which
# omits the flag deliberately so composing skills can reach it through the Skill
# tool, and which is invoked by a skill rather than by a user message either way.
# So the prose is redundant with a mechanical control, and the full row -- with
# the reason, the flags and the argument grammar -- survives in
# `reference/skill-router/<category>.md`, read on demand.
#
# The cell was "Explicit invocation only; never auto-routed." until 2026-09-04,
# which is a 44-character sentence repeated verbatim on 23 rows: 1012 bytes of an
# always-on file spent saying one thing 23 times. It says it once now, in the
# rule's prose above the registry, and the cell carries the one word that lets a
# human reading the table tell "cannot be matched" from "nobody wrote triggers
# yet". Emptying it entirely would save 138 bytes more and lose that distinction.
#
# Nothing parses this value. VERIFIED 2026-09-04 across tests/, scripts/,
# .claude/hooks/, .claude/skills/, .github/ and docs/: the `NEVER auto-trigger`
# string that IS load-bearing (`scripts/dev/extract-router-rows.py`, and the
# advisory warning below) is read from SKILL.md frontmatter and from the
# per-category detail files, never from this core cell. The gates that read the
# core index -- `test_skill_graph_covers_the_router`,
# `test_three_flag_lists_that_described_one_skill`,
# `workspace-health.py::check_skill_router_coverage` -- all parse the LABEL.
MANUAL_TRIGGER_CELL = "manual"

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

def _existing_text(path: Path) -> str | None:
    """The file's text, or None when it is not there.

    Replaces `path.read_text(...) if path.exists() else None` and the
    `if not path.exists(): ... else: read_text(...)` pair below it. Both spelled
    the same TOCTOU: a file present at the `exists()` call and gone by the read
    raised FileNotFoundError out of a gate that already HAS a meaning for
    "missing" -- write it at one site, report it as drift at the other.

    Asking once and letting the answer be the absence removes the window
    entirely, rather than narrowing it. Every other read error still raises: a
    file that is there and cannot be read is a real fault, and this gate must
    not quietly regenerate over it.
    """
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None


def parse_frontmatter(skill_md: Path) -> tuple[dict, str]:
    """Return (frontmatter_dict, error_message); error_message is empty on success.

    A thin wrapper over ``scripts.utils.markdown.parse_frontmatter_strict``,
    which is the diagnostic parser the previous version of this docstring asked
    for. The error string is this gate's whole output - ``load_routing_rows``
    prints ``{rel}: {err}`` and CI fails on it - so the classification comes from
    the shared parser and the WORDING stays here, unchanged.

    Its own copy was already fence-line anchored, and carried one defect of its
    own: it computed the block as ``text[4:...]``, assuming the opening fence is
    exactly four characters. MEASURED 2026-08-28, an opening fence written
    ``---\\t\\t`` left a tab at the start of the block and PyYAML refused it with
    "found character '\\t' that cannot start any token", on a file whose YAML was
    perfectly good. The shared splitter computes the offset from the first line.

    The docstring this replaces claimed to mirror
    ``skill-metadata-check.py::parse_frontmatter`` "for consistency". It had not
    mirrored it since 2026-08-20, when the fence-line fix landed HERE and not
    there, and the two gates then disagreed about any SKILL.md whose frontmatter
    contained ` --- ` inside a scalar. Both are wrappers now, so the claim is
    true again by construction.
    """
    try:
        text = skill_md.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        # `UnicodeDecodeError` is a ValueError, so `except OSError` never caught
        # it, and a SKILL.md carrying one stray Latin-1 byte (a paste, an editor
        # that saved cp1252) took the whole generator down with a traceback -
        # in CI and in pre-commit, where `--check` runs. This function's entire
        # contract is to hand `load_routing_rows` a `(data, error)` pair so the
        # gate can print the curated `{rel}: {err}` line naming the file, which
        # is the one thing a traceback does not do. MEASURED 2026-09-01 on a
        # scratch skill tree: `UnicodeDecodeError: 'utf-8' codec can't decode
        # byte 0xe9` out of `load_routing_rows`, with no file named.
        return {}, f"unreadable: {exc}"
    data, kind, detail = parse_frontmatter_strict(text)
    if kind == md.FM_OK:
        return data, ""
    return {}, {
        md.FM_NO_OPENING: "no frontmatter (missing opening ---)",
        md.FM_NO_CLOSING: "malformed frontmatter (missing closing ---)",
        md.FM_INVALID_YAML: f"invalid YAML frontmatter: {detail}",
        md.FM_EMPTY: "empty frontmatter",
        md.FM_NOT_MAPPING: f"frontmatter must be a mapping, got {detail}",
    }[kind]


# ============================================================
# Row loading
# ============================================================

# A markdown table row is ONE line. These two characters end it, so a cell
# holding either produces a half row plus an orphan fragment on the next line -
# in an always-on rule, and in a detail file where the remaining columns then
# describe the wrong skill. Deterministic, so `--check` regenerates the same
# corruption and passes.
#
# A tab is deliberately NOT here. It renders as whitespace inside a cell, so
# refusing it would fail a working SKILL.md over a symptom no reader can see.
FORBIDDEN_IN_CELL = {"\n": "newline", "\r": "carriage return"}

# `label` alone is rendered INSIDE a backtick code span, by both `render_row` and
# `render_core_row`: `| `{label}` | ...`. So a backtick in it closes the span
# early and the Skill cell of an always-on rule renders mangled - the same
# deterministic corruption `--check` regenerates and passes that the two
# characters above are refused for. Triggers, exclusions and compound are NOT in
# a code span and backticks there are ordinary house style: measured on the live
# corpus 2026-09-02, 30 of the 94 skills carry one, so refusing the character
# everywhere would fail a third of the tree over nothing.
FORBIDDEN_IN_CODE_SPAN = {"`": "backtick"}


def _as_cell(value, *, field: str, code_span: bool = False) -> str:
    """One string, safe to place in a markdown table cell.

    `_as_list` below guards the item TYPE and the container TYPE, and `compound`
    is guarded against the YAML 1.1 boolean. Cell CONTENT was the hole all three
    left: a trigger written as a folded scalar, which is the house style for
    `x-heading-capability` in this same frontmatter, arrives with a trailing
    newline and splits the row.

    `label` reaches this function for a second reason. It was read raw three
    lines below the `name` check and its explaining comment, so `label: 7`
    raised an uncaught `TypeError` out of `escape_pipes` instead of the curated
    `{rel}: {err}` line this gate exists to print, and `label: no` - the YAML
    boolean False, which is falsy - vanished into the `or f"/{name}"` default
    with no word to the author who set it.

    `code_span=True` adds the backtick refusal, and only `label` passes it,
    because only `label` is rendered inside a code span. See
    `FORBIDDEN_IN_CODE_SPAN`.
    """
    if not isinstance(value, str):
        raise ValueError(
            f"{field} is {value!r} ({type(value).__name__}); it must be a "
            f"string. An unquoted YAML scalar like `7` or `no` is not.")
    for char, name in FORBIDDEN_IN_CELL.items():
        if char in value:
            raise ValueError(
                f"{field}: contains a {name}, which ends the markdown table "
                f"row: {value[:60]!r}. Write the value on one line - a folded "
                f"scalar (`- >`) adds a trailing newline.")
    if code_span:
        for char, name in FORBIDDEN_IN_CODE_SPAN.items():
            if char in value:
                raise ValueError(
                    f"{field}: contains a {name}, and this cell is rendered "
                    f"inside a code span: {value[:60]!r}. The span closes on the "
                    f"first one and the Skill column renders mangled. Remove it.")
    # An empty cell is refused rather than defaulted. `label: ""` renders an
    # empty code span in the Skill column, which is a broken row; and the
    # alternative - falling back to `/{name}` - is the same silent-ignore this
    # function was written to end for `label: no`. The author wrote something,
    # so they hear about it. Measured on the live corpus 2026-08-27: no
    # trigger, exclusion, compound or label is empty, so nothing regresses.
    if not value.strip():
        raise ValueError(
            f"{field} is empty (or only whitespace). An empty markdown cell "
            f"renders as a blank column; write a value or remove the key.")
    return value


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
        out.append(_as_cell(item, field=field))
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
        # `label` joins this block rather than staying a raw `.get()` three
        # lines below: it is a cell value like the others, and the `name` fix
        # above documents exactly what a raw read of one costs.
        raw_label = routing.get("label")
        try:
            triggers = _as_list(routing.get("triggers"), field=f"{ROUTING_KEY}.triggers")
            exclusions = _as_list(routing.get("exclusions"), field=f"{ROUTING_KEY}.exclusions")
            # The DEFAULT is checked too, not only an authored `label`. It is
            # built from `name`, which falls back to the directory name, and both
            # reach the same code span.
            label = _as_cell(
                raw_label if raw_label is not None else f"/{name}",
                field=f"{ROUTING_KEY}.label", code_span=True)
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
        try:
            compound = _as_cell(compound_raw, field=f"{ROUTING_KEY}.compound")
        except ValueError as exc:
            errors.append(f"{rel}: {exc}")
            continue

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
                "label": label,
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

    Parity-aware: a pipe counts as already escaped only when the backslash run
    before it is ODD. A plain negative lookbehind treated ANY preceding backslash
    as an escape, so an EVEN run - a literal backslash that is DATA, written
    ``C:\\\\|foo`` - left its pipe unescaped and split the table cell into a
    spurious column. Parity escapes that one: ``C:\\\\|foo`` renders
    ``C:\\\\\\|foo``.

    ``C:\\|foo`` was named as the motivating example here until 2026-09-02, and
    it is the case parity does NOT repair: one backslash is an odd run, so both
    the old lookbehind and the parity rule read it as an existing escape and
    leave the pipe bare. MEASURED that day, the two spellings agree on that
    input and differ only on the even one. See ``unescape_pipes`` for what the
    odd run costs on the way back.
    """
    def _fix(match: "re.Match") -> str:
        slashes = match.group(1)
        return match.group(0) if len(slashes) % 2 else slashes + "\\|"

    return re.sub(r"(\\*)\|", _fix, text)


def unescape_pipes(text: str) -> str:
    """Drop the ONE backslash ``escape_pipes`` added. NOT its exact inverse.

    It was called the exact inverse until 2026-09-02 and that claim is false on
    one shape: a pipe preceded by an ODD run of backslashes. ``escape_pipes``
    reads such a run as an existing escape and passes the text through, so the
    forward function is not injective and no backward function can undo it.
    MEASURED 2026-09-02: ``unescape_pipes(escape_pipes("C:\\|foo"))`` is
    ``"C:|foo"`` - the data backslash is gone. Every other input round-trips,
    including the even runs ``escape_pipes`` was made parity-aware for.

    Kept as a documented limit rather than repaired, because the repair is to
    make ``escape_pipes`` escape unconditionally, and that would double the
    backslash in every cell an author already escaped by hand. Those exist:
    ``.claude/skills/workspace-deep-audit/SKILL.md`` writes
    ``--mode={full\\|quick\\|focus}`` and ``.claude/skills/modem-tune/SKILL.md``
    writes ``[status \\| revert]``, both hand-escaping the pipe for markdown, and
    both correctly passed through untouched. The lossy case is the OTHER reading
    of the same bytes - a backslash meant as data - which no live SKILL.md has,
    and which this function cannot tell apart from those two.

    The parser in ``scripts/dev/extract-router-rows.py`` splits a row on
    unescaped pipes and, until 2026-08-27, handed the cell on with the escape
    still in it. So `canopus`, whose trigger reads
    ``/canopus [note | check | probe]``, round-tripped to a trigger containing
    ``\\|`` - and that string would then have been written back into the
    authoritative SKILL.md, putting a backslash in a file that never had one.
    The docstring promising that "the round-trip reproduces each cell" was the
    only thing saying otherwise.

    Parity-aware for the same reason the forward function is: a pipe is escaped
    only when the backslash run before it is ODD, so a literal backslash that is
    DATA keeps its own escape.
    """
    def _unfix(match: "re.Match") -> str:
        slashes = match.group(1)
        return slashes[:-1] + "|" if len(slashes) % 2 else match.group(0)

    return re.sub(r"(\\*)\|", _unfix, text)


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


def _normalise_token(text: str) -> str:
    """Lowercase and strip every non-alphanumeric, for trigger-vs-label comparison."""
    return re.sub(r"[^a-z0-9]", "", text.lower())


def core_triggers(row: dict) -> list[str]:
    """The triggers worth spending always-on bytes on, for one auto-routable skill.

    Drops a trigger that is EXACTLY the skill's own name or its rendered label,
    modulo case and punctuation -- `/viraid` listing "viraid", `/mullvad` listing
    "/mullvad", `/telegram` listing "telegram". The Skill cell of the same row
    already spells it, so the duplicate buys no match and costs bytes in the one
    layer that is paid for on every session.

    EXACT match only, and the narrowness is the point. Dropping every trigger that
    merely CONTAINS the name was measured on 2026-09-04 and saved 1587 bytes by
    deleting real matching surface: `/thread` fell from 8 triggers to 1, losing
    "open a thread", "close thread", "thread list" and "thread find" -- distinct
    phrases a user actually types -- and `/design` kept only "design social" while
    "design infographic", "design mockup" and "design logo" went. A containment
    rule cannot tell a redundant echo from a compound phrase built on the name.
    The exact rule saves less and loses nothing.

    Never returns empty: a skill whose only trigger is its own name keeps it, so
    the row still shows what the model is matching against.
    """
    label_key = _normalise_token(row["label"])
    name_key = _normalise_token(row["name"])
    kept = [
        t for t in row["triggers"]
        if _normalise_token(t) not in (label_key, name_key)
    ]
    return kept or row["triggers"][:1]


def render_core_row(row: dict) -> str:
    """Compact 2-column core-index row: backtick label + first-pass triggers.

    A `router: manual` row gets MANUAL_TRIGGER_CELL instead of its triggers. The
    row itself stays -- gates read it, and an operator should find the command
    listed -- but the cell does not, because a skill that cannot be reached by
    matching a message is owed no matching vocabulary in the layer that is paid
    for on every session.
    """
    label = escape_pipes(row["label"])
    if row["router"] == "manual":
        return f"| `{label}` | {MANUAL_TRIGGER_CELL} |"
    triggers = escape_pipes(TRIGGER_SEP.join(core_triggers(row)))
    return f"| `{label}` | {triggers} |"


def render_core_index(rows: list[dict]) -> str:
    """Render the compact always-on core index (the content between the markers).

    Per category: a heading, a pointer to the detail file, and a 2-column
    Skill|Triggers table. Exclusions and compound patterns live in the category
    file, read on demand. Deterministic order: fixed category order, then skill
    name ascending. Blocks separated by a blank line; no trailing newline
    (matches splice_region's contract).

    Every skill keeps a ROW, including the `router: manual` ones. Their rows are
    what several gates read the registry FOR -- `test_skill_graph_covers_the_router`
    asserts two-way set equality between the ``| `/name`` rows here and
    `reference/skill-graph.csv`, `test_three_flag_lists_that_described_one_skill`
    reads `/scrutinize`'s flag-bearing label out of this table, and
    `workspace-health.py::check_skill_router_coverage` requires every skill
    directory to be named here. What a manual skill does NOT keep is its trigger
    PROSE: see MANUAL_TRIGGER_CELL.
    """
    blocks: list[str] = []
    for category in CATEGORY_ORDER:
        members = sorted(
            (r for r in rows if r["category"] == category), key=lambda r: r["name"]
        )
        slug = category_slug(category)
        # The pointer says only where the detail is; WHAT is in it, and that
        # reading it is mandatory before selecting, is stated once in the rule's
        # prose above the registry. Spelling that out per category cost 108 bytes
        # seven times over in an always-on file to repeat one sentence.
        lines = [
            f"### {category}",
            "",
            f"Detail: `reference/skill-router/{slug}.md`",
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

    Everything outside the markers is preserved unchanged, LINE ENDINGS ASIDE, and
    the qualifier is the module docstring's, not a hedge added here: ``read_text``
    and ``write_text`` apply universal-newline translation, so a CRLF router file
    is rewritten LF and the bytes outside the markers DO change. "byte-for-byte"
    was the old wording in both places; the module docstring was narrowed and this
    one, describing the same I/O, was left carrying the claim its own file had
    already recorded as false.

    Raises ValueError if a marker is missing, or if either marker appears more
    than once.
    """
    if MARKER_BEGIN not in router_text or MARKER_END not in router_text:
        raise ValueError(
            f"sentinel markers not found in {ROUTER_FILE.relative_to(ROOT)}; "
            f"add\n  {MARKER_BEGIN}\n  {MARKER_END}\naround the '### Intel' ... last registry row."
        )
    # Duplicates are settled BEFORE the splice, because the pattern below cannot
    # report them afterwards. Under DOTALL the non-greedy body happily spans a
    # SECOND `BEGIN`, so `subn` returned n == 1 and the n > 1 guard at the foot
    # of this function never fired. Measured 2026-08-31 on
    # `BEGIN / row A / BEGIN / row B / END`: the result held ONE `BEGIN`, the
    # second marker and every line between the two were gone, exit 0, nothing
    # printed, and a following `--check` regenerated the same result and PASSED.
    # A doubled `END` is the mirror image: the body stops at the first one, so
    # `row B` and a stray `END` survive outside the region and `--check` blesses
    # that file too. This runs in pre-commit and in CI, which is exactly where a
    # gate that certifies its own corruption does the most damage.
    begins = router_text.count(MARKER_BEGIN)
    ends = router_text.count(MARKER_END)
    if begins > 1 or ends > 1:
        detail = (f"found {begins} marker regions" if begins == ends
                  else f"found {begins} BEGIN and {ends} END markers")
        raise ValueError(
            f"{detail} in {ROUTER_FILE.relative_to(ROOT)}; there must be "
            f"exactly one. Remove the extra {MARKER_BEGIN} / {MARKER_END} "
            f"pair(s). Nothing was written: the splice would otherwise destroy "
            f"the content between them.")
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
        existing = _existing_text(path)
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
        existing = _existing_text(path)
        if existing is None:
            drift.append((f"reference/skill-router/{slug}.md (MISSING)", "", content))
        elif existing != content:
            drift.append((f"reference/skill-router/{slug}.md", existing, content))

    # Orphan detail files (a *.md not backed by a current category) are drift too.
    if CATEGORY_FILE_DIR.exists():
        expected = {f"{category_slug(c)}.md" for c in CATEGORY_ORDER}
        orphans = [f for f in sorted(CATEGORY_FILE_DIR.glob("*.md"))
                   if f.name not in expected]
        # Through `read_sources`, and SKIPPING is right here. An orphan that
        # disappeared between the glob and the read is an orphan that no longer
        # exists, so declining to report it is the correct answer rather than a
        # narrowed one -- and the alternative was a FileNotFoundError out of a
        # pre-commit gate whose only finding had just deleted itself. The
        # regenerated side is the empty string for every orphan (the file should
        # not be there at all), so nothing here is a checksum a skip could bend.
        for f, text in read_sources(orphans):
            drift.append((f"reference/skill-router/{f.name} (ORPHAN)", text, ""))

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
