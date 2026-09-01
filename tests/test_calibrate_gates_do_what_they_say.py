"""/calibrate's three gates must do what the skill says they do.

Three defects, all of the same family: the skill's prose described a control that
its own instructions could not perform.

1. **The classification gate ran a command that did nothing.** Phase 3 decided
   auto-apply versus review queue by running `python scripts/utils/workspace.py
   get_classification <path>`. That module declares no `ArgumentParser` and no
   `__main__` block, so as a script it defines functions, reads no argument,
   prints nothing, and exits 0. The second half was worse than the first: the
   skill said "on any error from the resolver, treat as corporate
   (fail-closed)", and there is no error. Exit 0 with empty stdout is not an
   error, so the fail-closed branch could never fire on the actual failure mode.
   The gate could not tell "not classified" from "the resolver said nothing".

2. **The memory patches went to the wrong store.** Phase 5.1 wrote to the native
   harness store `~/.claude/projects/<slug>/memory/`, a per-launch runtime cache
   keyed to the session's launch directory. The canonical store every other
   memory tool reads is `<data-root>/auto-memory/`.

3. **The commit rode in on the patch approval, and staged a wildcard.** Phase 4
   approves the numbered patches. Step 5.4 then staged `.claude/`,
   `outputs/operations/calibrate/` "and any other modified workspace files" and
   committed, with no separate ask. A directory argument stages whatever else is
   sitting in the tree, including another agent's unfinished work.

None of these tests is a source grep. A skill is free to name a wrong form in
prose in order to explain why it is wrong -- SKILL.md now does exactly that for
both the dead CLI and the harness store -- and a grep would punish it for
documenting its own trap. The command assertions parse the fenced code blocks
and reason over the parsed commands; the classification assertions run the real
resolver.

The fence parser strips each line before testing for the fence marker, so a
fence indented inside a list item is seen. A parser anchored at column 0 misses
those, and the blocks that matter are usually the indented ones.
"""
import ast
import re
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.utils.workspace import get_classification  # noqa: E402
from tests.repo_files import read_sources  # noqa: E402

SKILL = ROOT / ".claude" / "skills" / "calibrate" / "SKILL.md"
REFS = ROOT / ".claude" / "skills" / "calibrate" / "references"
PROTOCOL = REFS / "patch-application-protocol.md"
WORKSPACE_MODULE = ROOT / "scripts" / "utils" / "workspace.py"

_BASH_FENCES = ("bash", "sh", "shell")


def _blocks(path: Path) -> list[tuple[str, list[str]]]:
    """Every fenced block as (language, lines). Indented fences included."""
    out: list[tuple[str, list[str]]] = []
    lang: str | None = None
    body: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("```"):
            if lang is None:
                lang = stripped.strip("`").lower()
                body = []
            else:
                out.append((lang, body))
                lang = None
            continue
        if lang is not None:
            body.append(line.strip())
    return out


def _commands(path: Path) -> list[str]:
    """Shell commands from bash-labelled fences, continuations joined."""
    cmds: list[str] = []
    for lang, body in _blocks(path):
        if lang not in _BASH_FENCES:
            continue
        pending: str | None = None
        for line in body:
            if not line:
                continue
            joined = line if pending is None else f"{pending} {line}"
            if line.endswith("\\"):
                pending = joined.rstrip("\\").rstrip()
                continue
            pending = None
            cmds.append(joined)
    return cmds


def _skill_commands() -> list[str]:
    return _commands(SKILL)


# --------------------------------------------------------------------------
# Non-vacuity. Every assertion below filters the parsed corpus, so an empty or
# truncated parse would pass having examined nothing.
# --------------------------------------------------------------------------

def test_the_fence_parser_reads_a_fence_indented_inside_a_list_item(tmp_path):
    """A peer's gate anchored its fence pattern at column 0 and silently missed
    77 of 252 blocks, because the ones that mattered were indented inside list
    items. This parser strips first. Proven directly, not assumed."""
    doc = tmp_path / "SKILL.md"
    doc.write_text(
        "1. Resolve it:\n"
        "   ```bash\n"
        "   python3 -c \"print(1)\"\n"
        "   ```\n"
        "\n"
        "```bash\n"
        "echo top-level\n"
        "```\n",
        encoding="utf-8",
    )
    assert len(_blocks(doc)) == 2, "an indented fence was not seen"
    assert _commands(doc) == ['python3 -c "print(1)"', "echo top-level"]


def test_the_fence_parser_sees_the_indented_blocks_too():
    all_blocks = _blocks(SKILL)
    assert len(all_blocks) >= 5, f"only {len(all_blocks)} fenced blocks parsed"
    raw = SKILL.read_text(encoding="utf-8")
    marker_lines = [ln for ln in raw.splitlines() if ln.strip().startswith("```")]
    assert len(marker_lines) == len(all_blocks) * 2, (
        f"{len(marker_lines)} fence markers but {len(all_blocks)} blocks parsed: "
        "an unbalanced fence would silently swallow the rest of the file"
    )
    assert len(_skill_commands()) >= 3, "no shell commands parsed out of SKILL.md"


# --------------------------------------------------------------------------
# Finding 1 - the classification gate
# --------------------------------------------------------------------------

def test_workspace_module_still_has_no_cli():
    """The premise of the fix. If someone adds a CLI, this test says so loudly
    rather than letting the skill's prose quietly become wrong again."""
    tree = ast.parse(WORKSPACE_MODULE.read_text(encoding="utf-8"))
    mains = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.If) and "__main__" in ast.dump(n.test)
    ]
    parsers = [
        n for n in ast.walk(tree)
        if (isinstance(n, ast.Attribute) and n.attr == "ArgumentParser")
        or (isinstance(n, ast.Name) and n.id == "ArgumentParser")
    ]
    assert not mains and not parsers, (
        "scripts/utils/workspace.py gained a CLI. /calibrate Phase 3 explains "
        "that it has none; update the skill deliberately."
    )


def test_the_gate_does_not_invoke_the_module_as_a_script():
    offenders = [
        c for c in _skill_commands()
        if re.search(r"python3?\s+scripts/utils/workspace\.py", c)
    ]
    assert not offenders, (
        "Phase 3 runs workspace.py as a script. It has no argument parser: it "
        f"prints nothing and exits 0.\n  " + "\n  ".join(offenders)
    )


def _classification_command() -> str:
    cmds = [c for c in _skill_commands() if "get_classification" in c]
    assert len(cmds) == 1, f"expected one get_classification command, got {cmds}"
    return cmds[0]


def test_the_documented_classification_command_actually_prints_one():
    """Behavioural. Run the exact command the skill documents, on a real path,
    and require a real classification on stdout."""
    template = _classification_command()
    assert template.startswith("python3 -c"), template
    target = ".claude/rules/voice.md"
    command = template.replace("<target-path>", target)
    # `shlex.split` rather than `shell=True`. The documented command is
    # `python3 -c "<program>"`, which a shell only ever tokenises - it expands
    # nothing, pipes nothing and globs nothing - so splitting it and running the
    # argv list executes the identical program. Fidelity to the documented string
    # is what this test measures, and it is unchanged; the shell was doing no
    # work, and `.claude/rules/security.md` forbids `shell=True` outright.
    proc = subprocess.run(
        shlex.split(command), cwd=ROOT, capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, f"exit {proc.returncode}: {proc.stderr}"
    assert proc.stdout.strip() in {"ceo-only", "corporate"}, (
        f"the documented command printed {proc.stdout.strip()!r}, not a "
        f"classification. stderr: {proc.stderr}"
    )
    assert proc.stdout.strip() == get_classification(target)


@pytest.mark.parametrize(
    "target,expected",
    [
        ("auto-memory/feedback_example.md", "ceo-only"),
        ("auto-memory/MEMORY.md", "ceo-only"),
        ("outputs/operations/calibrate/2026-01-01_corporate-review.md", "ceo-only"),
        (".claude/settings.local.json", "ceo-only"),
        # Engine code is the most-shared thing, so a skill or rule patch is
        # corporate and belongs in the review queue. The dead gate auto-applied
        # to these.
        (".claude/skills/calibrate/SKILL.md", "corporate"),
        (".claude/rules/voice.md", "corporate"),
    ],
)
def test_the_targets_the_skill_routes_resolve_as_the_skill_says(target, expected):
    assert get_classification(target) == expected


def test_an_empty_or_unresolvable_result_is_never_read_as_ceo_only():
    """Fail-closed, measured rather than asserted. The empty path is what the
    dead command effectively supplied: nothing."""
    assert get_classification("") == "corporate"
    # An absolute path matches no routing rule, which is why Phase 3 now tells
    # the caller to pass a repo-relative path.
    assert get_classification("/home/example/.claude/projects/x/memory/a.md") == "corporate"


def test_phase_3_defines_all_three_outcomes_as_a_table():
    """Empty stdout, other stdout, and a non-zero exit each need a stated
    behaviour, and two of the three must land on corporate."""
    text = SKILL.read_text(encoding="utf-8")
    rows = [ln for ln in text.splitlines()
            if ln.startswith("| exit") and "|" in ln.rstrip("|")]
    assert len(rows) == 4, f"expected 4 outcome rows in the Phase 3 table, got {rows}"
    empty = [r for r in rows if "stdout empty" in r]
    nonzero = [r for r in rows if "exit != 0" in r]
    assert empty and nonzero, f"missing the empty / non-zero rows: {rows}"
    for row in empty + nonzero:
        assert "corporate" in row and "fail-closed" in row, (
            f"an unresolved outcome does not fail closed to corporate: {row}"
        )


# --------------------------------------------------------------------------
# Finding 2 - the memory store
# --------------------------------------------------------------------------

def test_the_memory_store_is_resolved_from_the_canonical_seam():
    cmds = [c for c in _skill_commands() if "get_auto_memory_dir" in c]
    assert cmds, (
        "SKILL.md documents no command resolving the canonical auto-memory "
        "store. /dream resolves it with get_auto_memory_dir(); so must this."
    )


def test_no_reference_file_still_targets_the_harness_store():
    """The examples and the detection prompts are what a run copies. A prose
    warning in SKILL.md does not help if the worked example names the cache."""
    offenders = []
    # SCAN: a reference file that vanished between the glob and the read names
    # no store at all, so skipping it is the right answer; `read_sources` warns
    # naming it and the count goes into the message below.
    vanished: list[Path] = []
    for path, text in read_sources(sorted(REFS.glob("*.md")), vanished):
        for i, line in enumerate(text.splitlines(), 1):
            if ".claude/projects/" in line and "memory" in line:
                offenders.append(f"{path.name}:{i}: {line.strip()}")
    assert not offenders, (
        "A /calibrate reference file still targets the native harness memory "
        f"store instead of the canonical auto-memory store "
        f"({len(vanished)} file(s) vanished mid-walk):\n  "
        + "\n  ".join(offenders)
    )


# --------------------------------------------------------------------------
# Finding 3 - the commit
# --------------------------------------------------------------------------

def _git_add_commands() -> list[str]:
    cmds = [c for c in _skill_commands() + _commands(PROTOCOL)
            if re.match(r"git\s+add\b", c)]
    assert cmds, "no `git add` command found in the calibrate corpus"
    return cmds


def test_staging_names_files_and_never_a_directory_or_wildcard():
    offenders = []
    for command in _git_add_commands():
        # Arguments after `git add`, minus flags and the `--` separator.
        args = [a for a in command.split()[2:] if a != "--" and not a.startswith("-")]
        flags = [a for a in command.split()[2:] if a.startswith("-") and a != "--"]
        if "-A" in flags or "--all" in flags:
            offenders.append(f"stages everything: {command}")
        for arg in args:
            if arg.endswith("/") or arg in {".", "*"} or "*" in arg:
                offenders.append(f"directory or wildcard argument {arg!r}: {command}")
    assert not offenders, (
        "/calibrate stages more than it wrote. Seven agents can hold "
        "uncommitted edits across this tree; a directory argument commits all "
        "of them unreviewed:\n  " + "\n  ".join(offenders)
    )


def test_the_commit_has_its_own_approval_gate_separate_from_phase_4():
    """Approving the work is never approving the commit. Assert on the parsed
    Step 5.4 section, not on the whole file, so an approval word anywhere else
    cannot satisfy this."""
    text = SKILL.read_text(encoding="utf-8")
    start = text.index("### Step 5.4")
    end = text.index("### Step 5.5")
    section = text[start:end]
    assert "HARD STOP" in section, "Step 5.4 has no hard stop before the commit"
    assert "Phase 4" in section, (
        "Step 5.4 does not say that the Phase 4 approval is not a commit approval"
    )
    lowered = section.lower()
    assert "wait for" in lowered, "Step 5.4 does not wait for an answer"
    assert "never pushes" in lowered or "never push" in lowered, (
        "Step 5.4 does not state that the skill does not push"
    )


def test_the_never_list_carries_the_commit_and_store_prohibitions():
    text = SKILL.read_text(encoding="utf-8")
    never = text[text.index("## NEVER"):text.index("## Error handling")]
    lowered = never.lower()
    for phrase, why in (
        ("phase 4 approval", "the commit must not ride in on the patch approval"),
        ("harness memory store", "memory patches must not go to the per-launch cache"),
        ("empty resolver", "an empty resolver result must not read as a classification"),
    ):
        assert phrase in lowered, f"NEVER list is missing: {why}"
