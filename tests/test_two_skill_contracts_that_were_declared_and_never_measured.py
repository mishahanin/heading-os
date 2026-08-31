"""Two skill-catalogue contracts that were written down but never measured.

Both are stated in `.claude/rules/development-standards.md`, and both had a gate
standing next to them that could not see the breach.

1. **`x-heading-orchestration.shared_state` must name what the skill writes.**
   `.claude/rules/skill-orchestrator.md` § Conflict Detection step 4 decides
   whether two skills may run in parallel by intersecting their `shared_state`
   lists. A skill that writes to a fixed path and declares `[]` therefore
   registers NO conflict against a concurrent run writing the same path.
   `scripts/skill-metadata-check.py` already reads this field, and validates
   only that it is a *list* (line ~354). An empty list is a list, so `/docparse`,
   `/marp` and `/notebooklm` shipped `[]` while writing to
   `outputs/intel/docparse/`, `outputs/deliverables/presentations/` and
   `outputs/content/notebooklm/` respectively. The type check passed on all three.

2. **Every skill reference file must carry a "Consumed by:" pointer.**
   `scripts/artifact-evaluator.py` line ~347 checks exactly this, and misses it
   twice over: the check is `warn=True`, so it never fails, and the evaluator is
   wired into neither CI nor pre-commit, so it runs only when a human points
   `/evaluate` at one path. Nothing ever swept the corpus, and nine files under
   `.claude/skills/*/references/` had no pointer at all.

A third contract joined them, because writing the second one surfaced it.
`.claude/skills/yt-pulse/references/configuration.md` told the reader to run
`python pw.py youtube ...`, and `pw.py` lives in the *playwright* skill. The same
file then did it five more times with `python pulse.py ...`, a name four
different files in this repo carry. Neither command runs from anywhere.
`test_a_skill_never_invokes_a_script_by_bare_filename` parses the fenced bash
blocks and resolves each bare `*.py` argument against the skill's own directory
and the repo root.

Every test here carries a corpus floor. A directory walk that finds nothing
passes every assertion in the loop it never entered, so the count is asserted
first.
"""

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
SKILLS_DIR = ROOT / ".claude" / "skills"

# Floors, well under the live counts (100 skills / 83 reference files as of
# 2026-08-30). They exist so a broken glob fails loudly instead of passing.
MIN_SKILLS = 80
MIN_REFERENCE_FILES = 70
MIN_BASH_BLOCKS = 200

# The fence markers accept leading whitespace, and that is not cosmetic. The
# first version of this pattern anchored at column 0, which silently skipped 77
# of the corpus's 252 bash blocks - every one nested inside a numbered list
# item. The mutation that proved this gate was planted in exactly such a block
# (`.claude/skills/yt-pulse/references/configuration.md`, step 2 of the proxy
# procedure) and the gate stayed green. 175 blocks is a lot of blocks to walk
# while missing the one you came for.
BASH_FENCE_RE = re.compile(
    r"^[ \t]*```(?:bash|sh|shell)[ \t]*\n(.*?)^[ \t]*```", re.S | re.M)
PY_INVOCATION_RE = re.compile(r"(?:python3?|uv run python)\s+([^\s\"'|;&]+\.py)")

WRITE_TOOL_RE = re.compile(r"\b(Write|Edit|MultiEdit|NotebookEdit)\b")

# A skill dispatched in parallel that holds a write tool must name the paths it
# writes. These are the exceptions, each with the reason it is one. SHRINK-ONLY:
# `test_the_empty_shared_state_allowance_is_not_stale` fails when an entry no
# longer declares `[]`, so a skill that gains a real declaration must be deleted
# from here rather than left behind as a claim nobody rechecks.
EMPTY_SHARED_STATE_ALLOWED = {
    "census": (
        "Isolated writes only, which is the documented meaning of "
        "parallel_safe: true. scripts/census.py runs the traversal in a "
        "tempfile.TemporaryDirectory unique to the run, and the traversal "
        "program is written to a path the caller names. No fixed path is "
        "shared between two concurrent runs."
    ),
}


def _frontmatter(skill_md: Path) -> dict:
    text = skill_md.read_text(encoding="utf-8")
    match = re.match(r"^---\n(.*?)\n---", text, re.S)
    if not match:
        return {}
    parsed = yaml.safe_load(match.group(1))
    return parsed if isinstance(parsed, dict) else {}


def _skill_files() -> list[Path]:
    return sorted(p / "SKILL.md" for p in SKILLS_DIR.iterdir()
                  if (p / "SKILL.md").is_file())


def _reference_files() -> list[Path]:
    return sorted(SKILLS_DIR.glob("*/references/*.md"))


def _holds_write_tool(frontmatter: dict) -> bool:
    tools = frontmatter.get("allowed-tools") or ""
    if isinstance(tools, list):
        tools = ", ".join(str(t) for t in tools)
    return bool(WRITE_TOOL_RE.search(str(tools)))


def _consumed_by_line(ref_file: Path) -> str | None:
    """The "Consumed by:" line, found by label position, not by substring.

    The requirement is a labelled pointer at the head of a line, so the line is
    split on its first colon and the label is compared. A mid-sentence mention
    of the phrase in body prose is not a pointer and must not satisfy the gate.
    """
    text = ref_file.read_text(encoding="utf-8")
    if text.startswith("---\n"):
        end = text.find("\n---", 4)
        if end != -1:
            text = text[end + 4:]
    for raw in text.splitlines():
        line = raw.strip().lstrip(">").strip().lstrip("-*").strip()
        if ":" not in line:
            continue
        label = line.split(":", 1)[0]
        label = label.replace("*", "").replace("_", "").replace("`", "").strip()
        if label.lower() == "consumed by":
            return line
    return None


def _skills_named_in(line: str) -> set[str]:
    """Skill names a pointer line refers to: explicit SKILL.md paths and /slugs."""
    named = set(re.findall(r"\.claude/skills/([a-z0-9-]+)/SKILL\.md", line))
    named |= set(re.findall(r"(?<![\w/])/([a-z0-9-]+)\b", line))
    return named


# ---------------------------------------------------------------------------
# Contract 1 - shared_state names what the skill writes
# ---------------------------------------------------------------------------

def test_the_skill_corpus_is_not_empty():
    found = _skill_files()
    assert len(found) >= MIN_SKILLS, (
        f"only {len(found)} SKILL.md found under {SKILLS_DIR}; the glob is "
        f"broken and every per-skill assertion below is vacuous"
    )


def test_a_parallel_dispatchable_writer_declares_the_paths_it_writes():
    offenders = []
    for skill_md in _skill_files():
        name = skill_md.parent.name
        frontmatter = _frontmatter(skill_md)
        orchestration = frontmatter.get("x-heading-orchestration") or {}
        if orchestration.get("shared_state"):
            continue
        if orchestration.get("parallel_safe") not in (True, "partial"):
            # Never dispatched concurrently, so an empty list cannot produce a
            # false "no conflict" in the orchestrator's step-4 intersection.
            continue
        if not _holds_write_tool(frontmatter):
            continue
        if name in EMPTY_SHARED_STATE_ALLOWED:
            continue
        offenders.append(name)
    assert not offenders, (
        "these skills hold a write tool, may be dispatched in parallel, and "
        "declare no shared_state, so the orchestrator sees no conflict between "
        "two concurrent runs writing the same path: " + ", ".join(sorted(offenders))
    )


def test_the_empty_shared_state_allowance_is_not_stale():
    for name, reason in EMPTY_SHARED_STATE_ALLOWED.items():
        skill_md = SKILLS_DIR / name / "SKILL.md"
        assert skill_md.is_file(), (
            f"{name} is allowed an empty shared_state but has no SKILL.md; "
            f"delete the entry. Reason on record: {reason}"
        )
        orchestration = _frontmatter(skill_md).get("x-heading-orchestration") or {}
        assert not orchestration.get("shared_state"), (
            f"{name} now declares shared_state="
            f"{orchestration.get('shared_state')!r}; delete its entry from "
            f"EMPTY_SHARED_STATE_ALLOWED. This allowance only shrinks."
        )


@pytest.mark.parametrize(
    "skill,expected",
    [
        ("docparse", "outputs/intel/docparse/"),
        ("marp", "outputs/deliverables/presentations/"),
        ("notebooklm", "outputs/content/notebooklm/"),
        ("design", "outputs/design/"),
        ("design", "outputs/content/images/"),
    ],
)
def test_a_repaired_skill_still_names_its_output_root(skill, expected):
    """Guards the specific paths, not just non-emptiness.

    Established by reading the writers: `get_outputs_dir() / "intel" /
    "docparse"` in scripts/docparse.py, `default_output_dir()` in
    scripts/marp_render.py, and the `nlm ... -o` targets in
    .claude/skills/notebooklm/references/mode-catalog.md.

    /design has two roots because it has two writers, and both resolve through
    `get_outputs_dir()`: `scripts/design-studio.py:54` returns
    `get_outputs_dir() / "design"` (HTML Studio renders, plus the `source/` and
    `.tmp/` subtrees beneath it), and `scripts/design-engine.py:189` returns
    `get_outputs_dir() / "content" / "images"` (Replicate imagery). Both match
    the SKILL.md "Output Locations" section. The second root is shared with
    /flux-image, which declares `[]` - the asymmetry the orchestrator's step-4
    intersection exists to catch, and which this repair only half closes.
    """
    orchestration = _frontmatter(SKILLS_DIR / skill / "SKILL.md").get(
        "x-heading-orchestration") or {}
    declared = orchestration.get("shared_state") or []
    assert expected in declared, (
        f"/{skill} writes to {expected} but declares shared_state={declared!r}"
    )


# ---------------------------------------------------------------------------
# Contract 2 - every skill reference file carries a true "Consumed by:" pointer
# ---------------------------------------------------------------------------

def test_the_reference_corpus_is_not_empty():
    found = _reference_files()
    assert len(found) >= MIN_REFERENCE_FILES, (
        f"only {len(found)} files found under {SKILLS_DIR}/*/references/; the "
        f"glob is broken and every per-file assertion below is vacuous"
    )


def test_every_skill_reference_file_carries_a_consumed_by_pointer():
    missing = [str(f.relative_to(ROOT)) for f in _reference_files()
               if _consumed_by_line(f) is None]
    assert not missing, (
        "reference files with no 'Consumed by:' pointer (required by "
        ".claude/rules/development-standards.md § Reference File Standards): "
        + ", ".join(missing)
    )


def test_every_consumed_by_pointer_names_a_skill_that_exists():
    dangling = []
    for ref_file in _reference_files():
        line = _consumed_by_line(ref_file)
        if line is None:
            continue
        for name in sorted(_skills_named_in(line)):
            if not (SKILLS_DIR / name / "SKILL.md").is_file():
                dangling.append(f"{ref_file.relative_to(ROOT)} -> /{name}")
    assert not dangling, "'Consumed by:' pointers at skills that do not exist: " + \
        ", ".join(dangling)


def test_every_consumed_by_pointer_names_its_own_owning_skill():
    """A pointer that names only OTHER skills is the wrong-pointer defect.

    Measured 2026-08-30 across the corpus: every pointer that names a skill at
    all names the skill whose directory holds it. A pointer that names no skill
    (a bare prose consumer) is left alone; there is nothing to resolve.
    """
    wrong = []
    for ref_file in _reference_files():
        line = _consumed_by_line(ref_file)
        if line is None:
            continue
        named = _skills_named_in(line)
        owner = ref_file.parents[1].name
        if named and owner not in named:
            wrong.append(
                f"{ref_file.relative_to(ROOT)} is owned by /{owner} but points "
                f"at {sorted(named)}"
            )
    assert not wrong, "'Consumed by:' pointers at the wrong skill: " + "; ".join(wrong)


# ---------------------------------------------------------------------------
# Contract 3 - a documented command must resolve from where it is run
# ---------------------------------------------------------------------------

def _bash_invocations() -> list[tuple[Path, Path, str, str]]:
    """(file, owning skill dir, script argument, whole command line) per hit."""
    found = []
    for md in sorted(list(SKILLS_DIR.glob("*/SKILL.md"))
                     + list(SKILLS_DIR.glob("*/references/*.md"))):
        skill_dir = md.parents[1] if md.parent.name == "references" else md.parent
        for block in BASH_FENCE_RE.finditer(md.read_text(encoding="utf-8")):
            for line in block.group(1).splitlines():
                for script in PY_INVOCATION_RE.findall(line):
                    found.append((md, skill_dir, script, line.strip()))
    return found


def test_the_bash_fence_corpus_is_not_empty():
    blocks = sum(len(BASH_FENCE_RE.findall(md.read_text(encoding="utf-8")))
                 for md in list(SKILLS_DIR.glob("*/SKILL.md"))
                 + list(SKILLS_DIR.glob("*/references/*.md")))
    assert blocks >= MIN_BASH_BLOCKS, (
        f"only {blocks} fenced bash blocks parsed out of the skill corpus; the "
        f"fence pattern is broken and the invocation check below is vacuous"
    )


def test_a_skill_never_invokes_a_script_by_bare_filename():
    """A bare `script.py` is only runnable when the reader is already in its dir.

    Path-bearing arguments are left alone - those state where the file is, and
    may legitimately point at a sibling skill, a placeholder, or a temp file.
    A BARE name states nothing, and the reader's working directory is the repo
    root or wherever the previous step left them.
    """
    unresolvable = []
    for md, skill_dir, script, line in _bash_invocations():
        if "/" in script:
            continue
        if (skill_dir / script).exists() or (ROOT / script).exists():
            continue
        unresolvable.append(f"{md.relative_to(ROOT)}: {line}")
    assert not unresolvable, (
        "documented commands that invoke a python script by bare filename, "
        "resolvable from neither the owning skill directory nor the repo root: "
        + "; ".join(unresolvable)
    )
