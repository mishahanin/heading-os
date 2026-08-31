"""An orchestrator agent prompt must name a path the code actually uses.

Found by the 2026-08-23 engine audit. Pattern 2 (Morning Comms) dispatched its
Sentinel-queue scout to `outputs/operations/sentinel/`. `scripts/sentinel.py`
has never written there:

    RUNTIME_DIR = WORKSPACE_ROOT / ".sentinel"          # line 89
    STATE_FILE  = RUNTIME_DIR / "state.json"
    LOG_FILE    = RUNTIME_DIR / "sentinel.log"

and no `outputs/operations/sentinel/` exists in the engine repo or the data
overlay. The scout therefore read an absent directory, found nothing, and
Morning Comms reported an empty urgent queue no matter how full the real one
was. Nothing errored. That is the worst failure shape available to a briefing
pattern: a confident all-clear.

`.claude/rules/skill-orchestrator.md` makes reading this file mandatory before
dispatching, on the grounds that "dispatching without that Read means
dispatching without the DO-NOT list". That elevates every path in it to a live
instruction, so a wrong one is executed rather than merely read.

This test derives the path from the daemon instead of restating it. It is
narrow on purpose: only paths that a named module defines as a constant can be
checked this way, and the Sentinel state directory is the one the audit caught.
Extend it per-path when another prompt names a code-owned location; do not turn
it into a generic "every path in the file exists" scan, which would fail on the
many paths that are legitimately created on first use.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PATTERNS = ROOT / "reference" / "orchestrator-patterns.md"
SENTINEL = ROOT / "scripts" / "sentinel.py"


def _sentinel_runtime_dirname() -> str:
    """Read `RUNTIME_DIR = WORKSPACE_ROOT / "<name>"` out of the daemon."""
    tree = ast.parse(SENTINEL.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if (isinstance(node, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == "RUNTIME_DIR"
                        for t in node.targets)):
            value = node.value
            assert isinstance(value, ast.BinOp) and isinstance(value.op, ast.Div), (
                "RUNTIME_DIR is no longer a `<root> / \"<name>\"` expression; "
                "update this parser rather than the assertion below"
            )
            assert isinstance(value.right, ast.Constant)
            return value.right.value
    raise AssertionError("no RUNTIME_DIR assignment found in scripts/sentinel.py")


def _pattern_2_prompt() -> str:
    text = PATTERNS.read_text(encoding="utf-8")
    start = text.index("Sentinel-queue scout.")
    return text[start:text.index("\n\n", start)]


def test_the_parser_reads_the_real_constant():
    name = _sentinel_runtime_dirname()
    assert name == ".sentinel", (
        f"the daemon's runtime directory is now {name!r}; the orchestrator "
        "prompt and this test both need updating"
    )


def test_the_scout_prompt_names_the_directory_the_daemon_writes():
    name = _sentinel_runtime_dirname()
    prompt = _pattern_2_prompt()
    assert name in prompt, (
        f"Pattern 2's Sentinel scout does not name {name!r}, the directory "
        f"scripts/sentinel.py actually uses. Prompt: {prompt!r}"
    )


def test_the_scout_prompt_names_no_path_the_daemon_never_used():
    prompt = _pattern_2_prompt()
    assert "outputs/operations/sentinel" not in prompt, (
        "Pattern 2 points the scout at outputs/operations/sentinel/, which "
        "exists in neither repository and which the daemon has never written"
    )


def test_the_dead_path_is_still_dead():
    """If someone later makes the daemon write there, the correction above
    becomes the wrong one, and this test says so instead of going quiet."""
    for base in (ROOT, ROOT.parent / ".heading-os-data"):
        assert not (base / "outputs" / "operations" / "sentinel").exists(), (
            f"{base}/outputs/operations/sentinel now exists; re-check which "
            "path the Sentinel scout should read"
        )


def test_the_prompt_forbids_reporting_absence_as_all_clear():
    """The defect was not the wrong path alone. It was that a wrong path
    produced a clean report."""
    prompt = _pattern_2_prompt()
    assert "do not report an empty queue as a clear queue" in prompt.lower(), (
        "nothing tells the scout to distinguish 'no urgent items' from 'no "
        "state file', which is how the wrong path stayed invisible"
    )


def test_the_do_not_list_survived_the_edit():
    """The safety half of the prompt is the reason the file is a mandatory
    read. An edit that fixes the path and drops it is a worse outcome."""
    prompt = _pattern_2_prompt()
    assert "Do NOT modify Sentinel state" in prompt
    assert "do NOT acknowledge or dismiss items" in prompt


# --- Pattern 7: the push tail must not target the retired workspace ----------
#
# Same shape as the Sentinel path above, one repository further out. Pattern 7
# dispatched a background agent with: "Stage any CEO-only changes in ceo-main
# ... and push to the ceo-main `origin/main` remote." `ceo-main` is the legacy
# single workspace, retired at the 2026-06-15 cutover to the two-part topology;
# writing to it is forbidden. The sanctioned path is `scripts/push-all.py`,
# which pushes the engine clone and the data overlay and verifies each branch
# is level with its remote.
#
# The guard is a keyword check rather than a derivation, and that is a real
# limit: no engine-side constant names the retired repo, so there is nothing to
# derive from. It is scoped to the two orchestrator surfaces, and it allows a
# line that explains the retirement -- otherwise the fix could not document
# itself.

ORCHESTRATOR_RULE = ROOT / ".claude" / "rules" / "skill-orchestrator.md"
_RETIRED = "ceo-main"


def test_no_orchestrator_surface_dispatches_a_write_to_the_retired_workspace():
    bad = []
    inspected = 0
    for path in (PATTERNS, ORCHESTRATOR_RULE):
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if _RETIRED not in line:
                continue
            inspected += 1
            if re.search(r"retired|legacy|do not write|until 2026-08-23", line, re.I):
                continue                       # naming it to forbid it is the point
            bad.append(f"{path.relative_to(ROOT)}:{n}: {line.strip()[:110]}")
    # Measured 1 inspected line on 2026-08-26 (the single surviving mention of
    # the retired workspace, which is the retirement note itself), so the floor
    # sits at 1. If `_RETIRED not in line` drifts true for every line, because
    # the constant is renamed or both surfaces stop naming the retired
    # workspace at all, nothing is classified and the offender list is empty
    # for the wrong reason.
    assert inspected >= 1, (
        f"only {inspected} line(s) in the orchestrator surfaces were checked "
        f"against the {_RETIRED!r} guard; the corpus or the constant drifted "
        "and this test is no longer reading anything"
    )
    assert not bad, (
        f"an orchestrator surface still names {_RETIRED!r} as a live target. That "
        "workspace was retired at the 2026-06-15 cutover; pushes go through "
        "scripts/push-all.py, which covers the engine clone and the data overlay:\n"
        + "\n".join(bad)
    )


# --- Pattern 7: the CRM aggregate tail must not target a retired repository --
#
# Third instance of the same shape, found 2026-08-30. Pattern 7's Agent 2 was
# told to "refresh ../31c-crm-central/ from the per-exec CRM repos" and then
# "commit and push the result to the 31c-crm-central remote". Neither exists.
# `31c-crm-central` and the per-exec `31c-crm-{slug}` repos are both retired and
# absent from disk; `scripts/aggregate-crm.py` reads each exec's own data
# overlay and writes `<data-root>/crm/aggregated/`, which has no remote at all.
#
# So the tail agent pushed nothing to a repository that was not there, and the
# isolation guarantee above it named a directory no tail could write. Same
# failure shape as the Sentinel scout: a dispatched instruction pointing at an
# absent location, erroring nowhere.
#
# Keyword guard again, and for the same reason: no engine-side constant names a
# retired repo, so there is nothing to derive. Lines that name it in order to
# forbid it are allowed, otherwise the fix could not document itself.

_RETIRED_CRM = ("crm-central", "31c-crm-")
_RETIREMENT_NOTE = re.compile(
    r"retired|legacy|is absent|does not exist|no crm-central remote|do not write",
    re.I,
)


def test_no_orchestrator_surface_dispatches_a_write_to_a_retired_crm_repo():
    bad = []
    inspected = 0
    for path in (PATTERNS, ORCHESTRATOR_RULE):
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not any(token in line for token in _RETIRED_CRM):
                continue
            inspected += 1
            if _RETIREMENT_NOTE.search(line):
                continue                       # naming it to forbid it is the point
            bad.append(f"{path.relative_to(ROOT)}:{n}: {line.strip()[:110]}")
    # Measured 2 inspected lines on 2026-08-30: the isolation-guarantee bullet
    # and the Agent 2 prompt, both of which name the retired repo only to say it
    # is gone. The floor guards the same decay mode as the sibling above: if the
    # tokens stop appearing entirely, nothing is classified and `bad` is empty
    # for the wrong reason.
    assert inspected >= 2, (
        f"only {inspected} line(s) in the orchestrator surfaces were checked "
        f"against the {_RETIRED_CRM!r} guard; the corpus or the tokens drifted "
        "and this test is no longer reading anything"
    )
    assert not bad, (
        "an orchestrator surface still names a retired CRM repository as a live "
        "target. Both 31c-crm-central and the per-exec 31c-crm-{slug} repos are "
        "gone; aggregate-crm.py reads each exec's own data overlay and writes "
        "<data-root>/crm/aggregated/, which has no remote:\n" + "\n".join(bad)
    )


def test_pattern_7_aggregate_tail_names_the_directory_the_script_writes():
    """The aggregate tail's target is derivable, unlike the retired repo name:
    aggregate-crm.py documents where it writes, so read it rather than restate it."""
    aggregate = ROOT / "scripts" / "aggregate-crm.py"
    assert aggregate.is_file(), "scripts/aggregate-crm.py is gone; Pattern 7 names a script that does not exist"
    doc = ast.get_docstring(ast.parse(aggregate.read_text(encoding="utf-8"))) or ""
    assert "crm/aggregated/" in doc, (
        "aggregate-crm.py no longer documents crm/aggregated/ as its output; "
        "re-derive what Pattern 7's tail agent should be told to refresh"
    )
    text = PATTERNS.read_text(encoding="utf-8")
    block = text[text.index("## Pattern 7"):]
    assert "crm/aggregated/" in block, (
        "Pattern 7 no longer names crm/aggregated/, the only location "
        "aggregate-crm.py writes. A tail agent told to refresh anything else is "
        "pointed at a directory that does not exist."
    )


def test_pattern_7_does_not_tell_the_aggregate_tail_to_push():
    """The aggregated view has no remote. An agent told to push it will
    improvise one, which is how the retired-repo instruction survived."""
    text = PATTERNS.read_text(encoding="utf-8")
    block = text[text.index("## Pattern 7"):]
    start = block.index("Agent 2 (Haiku) prompt:")
    prompt = block[start:block.index("\n\n", start)]
    assert "Do NOT commit and do NOT push" in prompt, (
        "Pattern 7's Agent 2 prompt no longer forbids committing and pushing. "
        f"The aggregated view is local-only and has no remote. Prompt: {prompt!r}"
    )


def test_pattern_7_names_the_sanctioned_push_primitive():
    text = PATTERNS.read_text(encoding="utf-8")
    start = text.index("## Pattern 7")
    block = text[start:]
    assert "scripts/push-all.py" in block, (
        "Pattern 7 no longer names scripts/push-all.py. A tail agent told only "
        "to 'commit and push' will improvise a bare `git push`, which can report "
        "success while leaving the ref behind -- the ahead/behind check in "
        "push-all.py is the real gate."
    )
    assert (ROOT / "scripts" / "push-all.py").is_file(), (
        "scripts/push-all.py is gone; Pattern 7 now names a script that does not exist"
    )


# --- /push-updates: the same three failures, one skill further out -----------
#
# Found 2026-08-30, in the same family as everything above: an instruction that
# names a location or a command the code does not support, erroring nowhere.
#
#   1. Phase 4 step 1 told the operator to run a bare `git push origin main` on
#      the engine clone. The engine repo is PUBLIC and the layer-6 row of
#      docs/engine-data-segregation-contract.md names exactly that command as
#      THE bypass of `engine_clean_scan`, the unbypassable leak wall. The skill
#      already used push-all.py for the DATA half and hand-rolled only the half
#      the wall protects.
#   2. Phase 3 step 8 said "In the corporate repo:" with no `cd` and no `git -C`,
#      and no step changed back, so the Phase 4 push above ran with the corporate
#      repo as its plausible working directory.
#   3. Ten lines named `ceo-main`, retired at the 2026-06-15 cutover and absent
#      from disk (measured 2026-08-30: 6 in workflow.md, 4 in SKILL.md). The
#      occurrences did not all mean the same tree, which is why the fix was not
#      a rename.
#
# SCOPE, and it is deliberately narrow. These guards read the INSTRUCTION
# REGIONS of the two files only: fenced command blocks, `###` phase headings,
# and numbered step openers. A whole-file scan is refused here on principle -- it
# punishes a document for explaining the trap it just removed, and the fix for
# (3) is a prose section that names the retired workspace on purpose so the next
# reader knows which tree each occurrence used to mean. Prose explains; a fenced
# block is executed. Only the second is a live instruction.

PUSH_UPDATES = ROOT / ".claude" / "skills" / "push-updates" / "SKILL.md"
PUSH_WORKFLOW = PUSH_UPDATES.parent / "references" / "workflow.md"
_PUSH_UPDATES_FILES = (PUSH_UPDATES, PUSH_WORKFLOW)

_FENCE = re.compile(r"^\s*```")
_STEP_OPENER = re.compile(r"^\s*\d+\.\s")


def _fenced_lines(path: Path) -> list[tuple[int, str]]:
    """Every line inside a ``` fence: the command region, and nothing else."""
    out, inside = [], False
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if _FENCE.match(line):
            inside = not inside
            continue
        if inside:
            out.append((n, line))
    return out


def _instruction_lines(path: Path) -> list[tuple[int, str]]:
    """Fenced command lines, phase headings, and numbered step openers.

    The three shapes a reader executes. Explanatory paragraphs and blockquote
    notes are excluded on purpose -- see the SCOPE note above.
    """
    out = list(_fenced_lines(path))
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if line.startswith("#") or _STEP_OPENER.match(line):
            out.append((n, line))
    return out


def _phase_region(path: Path, start: str, end: str) -> str:
    text = path.read_text(encoding="utf-8")
    return text[text.index(start):text.index(end)]


def test_push_updates_routes_the_engine_push_through_the_wall_that_carries_it():
    """The choice between the two supervised paths is derived, not asserted.

    `safe-push.py` is the supervised-push primitive: a watchdog plus an
    ahead/behind [0 0] postcondition. `push-all.py` is that PLUS the leak wall.
    Only one of them defines `engine_clean_scan`, so read which, rather than
    trusting a sentence in a skill that says so.
    """
    def _defines(script: str, func: str) -> bool:
        tree = ast.parse((ROOT / "scripts" / script).read_text(encoding="utf-8"))
        return any(isinstance(n, ast.FunctionDef) and n.name == func
                   for n in ast.walk(tree))

    assert _defines("push-all.py", "engine_clean_scan"), (
        "scripts/push-all.py no longer defines engine_clean_scan. The engine leak "
        "wall moved; re-derive which script /push-updates Phase 4 must name."
    )
    assert not _defines("safe-push.py", "engine_clean_scan"), (
        "scripts/safe-push.py now defines engine_clean_scan too. If it grew the "
        "wall, this test's premise changed and Phase 4 may name either."
    )
    for path in _PUSH_UPDATES_FILES:
        region = _phase_region(path, "### Phase 4", "### Phase 5")
        assert "scripts/push-all.py" in region, (
            f"{path.relative_to(ROOT)} Phase 4 no longer names scripts/push-all.py, "
            "the only push path carrying engine_clean_scan. Anything else ships the "
            "engine clone past the wall."
        )


def test_no_push_updates_command_block_hand_runs_git_push():
    """A bare `git push` inside a fenced block is the documented layer-6 bypass.

    Scoped to fenced blocks because that is what an operator copies and runs.
    `git -C <path> push` is fine and is how the corporate repo is pushed: that
    repo is private, outside the engine wall, and push-all.py does not cover it.
    """
    bad, inspected = [], 0
    for path in _PUSH_UPDATES_FILES:
        for n, line in _fenced_lines(path):
            if not re.match(r"\s*git\b", line):
                continue
            inspected += 1
            if re.match(r"\s*git\s+push\b", line):
                bad.append(f"{path.relative_to(ROOT)}:{n}: {line.strip()}")
    # Measured 10 git command lines across the two files on 2026-08-30. The floor
    # guards the decay mode where the fences are reshaped and the matcher stops
    # seeing anything, leaving `bad` empty for the wrong reason.
    assert inspected >= 8, (
        f"only {inspected} git command line(s) were inspected in the /push-updates "
        "files; the fenced-block reader has drifted and this guard reads nothing"
    )
    assert not bad, (
        "a /push-updates command block hand-runs `git push`. The engine repo is "
        "public and scripts/push-all.py is the only path carrying engine_clean_scan, "
        "the secret content scan and the [0 0] verification. "
        "docs/engine-data-segregation-contract.md names a hand-run push as THE "
        "bypass of that wall:\n" + "\n".join(bad)
    )


def test_no_push_updates_command_block_leaves_its_working_directory_implicit():
    """No step may change directory, and a command on another repo must name it.

    The compounding half of the same defect: Phase 3 step 8 said "In the
    corporate repo:" in prose only, so Phase 4's next command inherited a
    directory nothing had set. Every command runs from the engine clone; a
    command acting elsewhere carries `git -C <path>`.
    """
    bad, inspected = [], 0
    other_repos = ("heading-os-corporate", ".heading-os-data")
    for path in _PUSH_UPDATES_FILES:
        for n, line in _fenced_lines(path):
            stripped = line.strip()
            if re.match(r"cd\b", stripped):
                bad.append(f"{path.relative_to(ROOT)}:{n}: changes directory: {stripped}")
                continue
            if not re.match(r"git\b", stripped):
                continue
            inspected += 1
            if any(repo in stripped for repo in other_repos) and " -C " not in stripped:
                bad.append(
                    f"{path.relative_to(ROOT)}:{n}: names another repo without -C: {stripped}")
    assert inspected >= 8, (
        f"only {inspected} git command line(s) were inspected; the fenced-block "
        "reader has drifted and this guard reads nothing"
    )
    assert not bad, (
        "a /push-updates command block leaves its working directory implicit. Run "
        "every command from the engine clone and name any other repository with "
        "`git -C <path>`:\n" + "\n".join(bad)
    )


_RETIRED_WORKSPACE = "ceo-main"


def test_push_updates_instruction_regions_name_no_retired_workspace():
    """The retired single workspace may be explained, never instructed against.

    Scoped to fenced blocks, `###` headings and numbered step openers, and NOT to
    the file: the fix for this defect is a prose section that names `ceo-main`
    deliberately, to record that the nine old occurrences meant three different
    trees. A whole-file grep would fail that section and push the next author
    into deleting the explanation instead of the instruction.
    """
    bad, inspected = [], 0
    for path in _PUSH_UPDATES_FILES:
        for n, line in _instruction_lines(path):
            inspected += 1
            if _RETIRED_WORKSPACE in line:
                bad.append(f"{path.relative_to(ROOT)}:{n}: {line.strip()[:110]}")
    # Measured 125 instruction lines across the two files on 2026-08-30. The floor
    # is set well below that and guards the same decay mode as its siblings: if the
    # markdown shape changes and nothing is classified, `bad` is empty for the
    # wrong reason.
    assert inspected >= 60, (
        f"only {inspected} instruction line(s) were classified in the /push-updates "
        "files; the region reader has drifted and this guard reads nothing"
    )
    assert not bad, (
        f"a /push-updates instruction still names {_RETIRED_WORKSPACE!r} as a live "
        "target. That workspace was retired at the 2026-06-15 cutover and is absent "
        "from disk. Name the tree the step actually means: the engine clone, the "
        "data overlay (which is the publish source), or both:\n" + "\n".join(bad)
    )


def _push_updates_frontmatter() -> dict:
    import yaml
    text = PUSH_UPDATES.read_text(encoding="utf-8")
    assert text.startswith("---\n"), "push-updates SKILL.md has no YAML frontmatter"
    return yaml.safe_load(text.split("---\n", 2)[1])


_EXEC_SYNC_CLAIM = re.compile(r"sync(s|ing)?\s+(the\s+)?exec", re.I)


def test_push_updates_advertises_no_exec_sync_its_own_body_says_is_retired():
    """A description promising a capability the body retires is a false claim.

    The contradiction is read from the two halves of the same skill rather than
    hardcoded, so the guard follows the body if central exec sync ever returns.
    """
    body = PUSH_UPDATES.read_text(encoding="utf-8")
    workflow = PUSH_WORKFLOW.read_text(encoding="utf-8")
    assert "NO central CEO-driven driver" in body and "retired" in workflow, (
        "the /push-updates body no longer states that central exec sync is "
        "retired. If a central driver came back, this guard's premise is gone and "
        "the description may advertise it again."
    )
    fm = _push_updates_frontmatter()
    claims = {"description": fm.get("description", "")}
    claims.update({f"x-heading-capability.{k}": v
                   for k, v in (fm.get("x-heading-capability") or {}).items()})
    bad = [f"{field}: {text.strip()[:140]}"
           for field, text in claims.items()
           if isinstance(text, str) and _EXEC_SYNC_CLAIM.search(text)]
    assert not bad, (
        "/push-updates frontmatter advertises syncing exec workspaces, which its "
        "own body says was retired on 2026-06-26. Each exec pulls; there is no "
        "central driver:\n" + "\n".join(bad)
    )


_TREES = ("engine clone", "data overlay")


def test_push_updates_commit_claim_names_the_trees_phase_1_commits():
    """The capability blurb and the Phase 1 heading must agree on the trees.

    The blurb claimed both trees while Phase 1 committed one unnamed tree and the
    data overlay was pushed only when CRM contacts happened to change. Derive the
    set from each side and compare, so neither can drift alone.
    """
    what = (_push_updates_frontmatter().get("x-heading-capability") or {}).get("what", "")
    claimed = {t for t in _TREES if t in what}
    assert claimed, (
        "x-heading-capability.what names neither the engine clone nor the data "
        "overlay, so there is nothing to hold Phase 1 to. State which trees it commits."
    )
    for path in _PUSH_UPDATES_FILES:
        heading = next(line for line in path.read_text(encoding="utf-8").splitlines()
                       if line.startswith("### Phase 1"))
        committed = {t for t in _TREES if t in heading}
        assert committed == claimed, (
            f"{path.relative_to(ROOT)} Phase 1 commits {sorted(committed)} but "
            f"x-heading-capability.what claims {sorted(claimed)}. Heading: {heading!r}"
        )


# ---------------------------------------------------------------------------
# /publish-corporate: the skill must drive the script it calls canonical, and
# must name a tree that exists.
#
# THREE DEFECTS, measured on 2026-08-30 and fixed the same day:
#   1. The blockquote at the top of SKILL.md named
#      `python scripts/publish-corporate.py --preview|--copy|--verify` as the
#      canonical mechanism, and then steps 3-4 described a hand-rolled copy loop
#      plus `git add -A`, a commit and a push, calling that script NOWHERE. The
#      neighbouring /push-updates makes the opposite a hard rule for the same
#      copy step, because a hand-typed list once shipped the functionally broken
#      build 77.
#   2. Step 3 said "`cd` to corporate repo directory" and no step changed back,
#      so every later command inherited the corporate repo as its directory. The
#      `cd` sat in a NUMBERED STEP rather than a fenced block, which is why the
#      guard below reads instruction lines and not fences alone.
#   3. Two lines named `ceo-main` as the live copy source. That workspace was
#      retired at the 2026-06-15 cutover and is absent from disk. Both meant the
#      DATA overlay.
#
# SCOPE. These guards read the INSTRUCTION REGIONS only -- fenced command lines,
# `#` headings, and numbered step openers -- reusing the readers defined above.
# A whole-file scan is refused: the fix for (3) is a prose section that names
# `ceo-main` on purpose, so the next reader knows which tree the old wording
# meant. A grep over the file would fail that section and teach the next author
# to delete the explanation rather than the instruction.

PUBLISH_CORPORATE = ROOT / ".claude" / "skills" / "publish-corporate" / "SKILL.md"
PUBLISH_SCRIPT = ROOT / "scripts" / "publish-corporate.py"


def _publish_script_flags() -> set[str]:
    """Every `--flag` the publish script's argparse actually defines."""
    tree = ast.parse(PUBLISH_SCRIPT.read_text(encoding="utf-8"))
    flags: set[str] = set()
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        if node.func.attr != "add_argument":
            continue
        for arg in node.args:
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str) \
                    and arg.value.startswith("--"):
                flags.add(arg.value)
    return flags


def _publish_script_git_verbs() -> set[str]:
    """The git subcommands the publish script itself issues via subprocess."""
    tree = ast.parse(PUBLISH_SCRIPT.read_text(encoding="utf-8"))
    verbs: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not node.args or not isinstance(node.args[0], ast.List):
            continue
        parts = [e.value for e in node.args[0].elts
                 if isinstance(e, ast.Constant) and isinstance(e.value, str)]
        if parts[:1] == ["git"] and len(parts) > 1:
            verbs.add(parts[1])
    return verbs


def test_publish_corporate_names_only_flags_the_script_defines():
    """Every flag the skill tells an operator to type must be a real flag.

    Derived from the script's own `add_argument` calls rather than a list
    written here, so renaming a flag reddens this instead of going unnoticed.
    The failure mode is not hypothetical: `ops_signals` shelled out to
    `--dry-run --json` for four months, two flags this parser has never defined.
    """
    defined = _publish_script_flags()
    assert {"--preview", "--copy", "--verify"} <= defined, (
        f"scripts/publish-corporate.py no longer defines the three canonical "
        f"modes; it defines {sorted(defined)}. Re-derive what /publish-corporate "
        "should tell an operator to run."
    )
    used = set(re.findall(r"publish-corporate\.py\s+(--[a-z-]+)",
                          PUBLISH_CORPORATE.read_text(encoding="utf-8")))
    assert used, (
        "the /publish-corporate skill names no flag of scripts/publish-corporate.py "
        "at all. It used to describe a hand-rolled copy loop instead; that is the "
        "defect this guard exists for."
    )
    assert used <= defined, (
        f"/publish-corporate tells the operator to run flag(s) the script does not "
        f"define: {sorted(used - defined)}. Defined: {sorted(defined)}"
    )


def test_publish_corporate_drives_the_script_for_the_copy_and_the_verify():
    """The copy and the pre-commit verify both go through the script.

    Scoped to the fenced command lines, because that is what an operator copies
    and runs. A prose mention of the script while the steps hand-roll the copy is
    exactly the state this skill was in.
    """
    fenced = " \n".join(line for _, line in _fenced_lines(PUBLISH_CORPORATE))
    for flag in ("--preview", "--copy", "--verify"):
        assert f"scripts/publish-corporate.py {flag}" in fenced, (
            f"no /publish-corporate command block runs "
            f"`scripts/publish-corporate.py {flag}`. The script derives the file "
            "set from config/routing-map.yaml; a hand-rolled copy loop does not, "
            "and one shipped the broken build 77."
        )


def test_publish_corporate_carries_the_git_steps_the_script_omits():
    """The skill must do exactly the part the script does not.

    `--copy` copies and verifies. It stages nothing, records nothing in git
    history and sends nothing to a remote, so the skill has to. Both halves are
    derived: the script's git verbs come from its own subprocess argument lists,
    and the skill's from its fences. An instruction that silently drops the
    history step is worse than the hand-rolled loop it replaced, because the
    files would sit in the corporate working tree and reach no executive.
    """
    script_verbs = _publish_script_git_verbs()
    assert script_verbs, (
        "no `git` subprocess call was found in scripts/publish-corporate.py; the "
        "reader has drifted and this guard's premise is unmeasured"
    )
    mutating = {"add", "commit", "push"}
    assert not (mutating & script_verbs), (
        f"scripts/publish-corporate.py now issues {sorted(mutating & script_verbs)} "
        "itself. If it grew one of those, /publish-corporate Step 4 duplicates it "
        "and the prose saying the script does neither is now false."
    )
    fenced = [line.strip() for _, line in _fenced_lines(PUBLISH_CORPORATE)]
    for verb in sorted(mutating):
        assert any(re.match(rf"git\s+-C\s+\S+\s+{verb}\b", line) for line in fenced), (
            f"/publish-corporate has no `git -C <repo> {verb}` command block. The "
            f"script never runs {verb}, so dropping this step strands the publish "
            "in the corporate working tree."
        )


def test_no_publish_corporate_instruction_leaves_its_working_directory_implicit():
    """No step may change directory, and a command on another repo must name it.

    Instruction lines, not fences alone: the original `cd` was step 3 item 2,
    plain numbered prose with the command in backticks, which a fenced-block
    reader never sees.

    Backticks are blanked before matching, and that is not cosmetic. The first
    version of this guard required whitespace after `cd` and so read straight
    past ``2. `cd` to corporate repo directory`` -- the literal line this test
    exists to catch. It survived its own mutation until the normalisation landed.
    """
    bad, inspected = [], 0
    other_repos = ("heading-os-corporate", ".heading-os-data")
    for n, line in _instruction_lines(PUBLISH_CORPORATE):
        inspected += 1
        stripped = line.strip()
        probe = stripped.replace("`", " ")
        if re.search(r"(?:^|[\s(])cd\s+\S", probe):
            bad.append(f"{PUBLISH_CORPORATE.relative_to(ROOT)}:{n}: "
                       f"changes directory: {stripped[:110]}")
            continue
        if not re.match(r"git\b", probe.strip()):
            continue
        if any(repo in probe for repo in other_repos) and " -C " not in probe:
            bad.append(f"{PUBLISH_CORPORATE.relative_to(ROOT)}:{n}: "
                       f"names another repo without -C: {stripped[:110]}")
    # Measured 29 instruction lines on 2026-08-30. The floor guards the decay mode
    # where the markdown is reshaped, nothing is classified, and `bad` is empty for
    # the wrong reason.
    assert inspected >= 18, (
        f"only {inspected} instruction line(s) were classified in "
        "/publish-corporate; the region reader has drifted and this guard reads nothing"
    )
    assert not bad, (
        "a /publish-corporate instruction leaves its working directory implicit. "
        "Run every command from the engine clone and name any other repository "
        "with `git -C <path>`:\n" + "\n".join(bad)
    )


def test_no_publish_corporate_command_block_hand_runs_an_unqualified_push():
    """An unqualified `git push` is the documented layer-6 bypass of the wall.

    `git -C ../heading-os-corporate push` is correct and is how this skill
    publishes: that repo is private, sits outside the engine leak wall, and
    neither push-all.py nor safe-push.py covers it. A push with no `-C` inherits
    whatever directory is current, which on this skill's own instructions is the
    public engine clone.
    """
    bad, inspected = [], 0
    for n, line in _fenced_lines(PUBLISH_CORPORATE):
        stripped = line.strip()
        if not re.match(r"git\b", stripped):
            continue
        inspected += 1
        if re.match(r"git\s+push\b", stripped):
            bad.append(f"{PUBLISH_CORPORATE.relative_to(ROOT)}:{n}: {stripped}")
    # Measured 4 git command lines on 2026-08-30.
    assert inspected >= 3, (
        f"only {inspected} git command line(s) were inspected in /publish-corporate; "
        "the fenced-block reader has drifted and this guard reads nothing"
    )
    assert not bad, (
        "a /publish-corporate command block runs an unqualified `git push`. Name "
        "the repository: the corporate repo takes "
        "`git -C ../heading-os-corporate push`, and the engine clone goes through "
        "scripts/push-all.py, never by hand:\n" + "\n".join(bad)
    )


def test_publish_corporate_instruction_regions_name_no_retired_workspace():
    """The retired single workspace may be explained, never instructed against.

    Deliberately NOT a whole-file grep. The fix for this defect added a prose
    section that names `ceo-main` on purpose, to record that both old occurrences
    meant the data overlay. A file-wide assertion would fail that section and
    push the next author into deleting the explanation instead of the instruction.
    """
    bad, inspected = [], 0
    for n, line in _instruction_lines(PUBLISH_CORPORATE):
        inspected += 1
        if _RETIRED_WORKSPACE in line:
            bad.append(f"{PUBLISH_CORPORATE.relative_to(ROOT)}:{n}: {line.strip()[:110]}")
    assert inspected >= 18, (
        f"only {inspected} instruction line(s) were classified in "
        "/publish-corporate; the region reader has drifted and this guard reads nothing"
    )
    assert not bad, (
        f"a /publish-corporate instruction still names {_RETIRED_WORKSPACE!r} as a "
        "live target. That workspace was retired at the 2026-06-15 cutover and is "
        "absent from disk. Both occurrences meant the DATA overlay "
        "(.heading-os-data), which is the publish source:\n" + "\n".join(bad)
    )
