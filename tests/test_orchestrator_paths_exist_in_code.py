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
