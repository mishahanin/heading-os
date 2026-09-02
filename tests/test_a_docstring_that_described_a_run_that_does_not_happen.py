"""`checkpoint-inject.py`'s docstring described output manual mode never emits.

The module docstring's `compact` bullet said, until 2026-09-02:

    The text printed on that path says what this hook DID and never what is on
    disk

There are two runs on that path, not one. `main()` prints `AUTO_AFTER_COMPACT`
only when the resolved mode is auto; in manual mode it prints nothing and
returns 0, so "the text printed on that path" names a run that does not happen
and a reader is told to look for a sentence that is not there.

MEASURED 2026-09-02, driving the real hook with `source=compact` against a
scratch project and data root: CLAUDE_HANDOFF_AUTO unset gave 0 bytes on stdout
and exit 0; CLAUDE_HANDOFF_AUTO=1 gave 645 bytes beginning "# Checkpoint".

The CODE is the correct half. Manual mode is precisely the mode in which
`checkpoint-offer.py` asks the operator what to do rather than compacting for
him, so an "AUTO MODE: continue without asking for confirmation" instruction
printed into a manual session would take back the decision the offer just handed
over. The docstring was corrected to the code.

A prose fix with no guard is how this drifted in the first place, so the three
tests below pin the claim mechanically rather than by eye:

  - the two modes are MEASURED by running the hook, which is what fails if the
    code ever starts printing in manual mode;
  - the AST is asked whether the print sits under the `auto` guard, which fails
    if the guard is moved or widened even where stdout happens to stay empty;
  - the docstring is required to carry the mode split the measurement found,
    which fails if the corrected sentence is reverted or deleted.

Run: python3 -m pytest tests/test_a_docstring_that_described_a_run_that_does_not_happen.py
"""
from __future__ import annotations

import ast
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

HOOK = ROOT / ".claude" / "hooks" / "checkpoint-inject.py"

SESSION = "dddddddd-0000-0000-0000-000000000000"


# ============================================================
# Measuring the two runs
# ============================================================

def _run_compact(tmp_path: Path, auto: bool) -> subprocess.CompletedProcess:
    """Drive the shipped hook on `source=compact`, in one mode, off the live tree."""
    project = tmp_path / "project"
    data = tmp_path / "data"
    project.mkdir(exist_ok=True)
    data.mkdir(exist_ok=True)

    env = dict(os.environ)
    env["HEADING_OS_DATA"] = str(data)
    env.pop("CLAUDE_HANDOFF_AUTO", None)
    if auto:
        env["CLAUDE_HANDOFF_AUTO"] = "1"

    return subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps({
            "session_id": SESSION,
            "cwd": str(project),
            "workspace": {"project_dir": str(project)},
            "source": "compact",
        }),
        capture_output=True, text=True, env=env,
    )


def test_the_compact_path_prints_nothing_in_manual_mode(tmp_path):
    """The claim the corrected docstring makes, asked of the hook itself."""
    result = _run_compact(tmp_path, auto=False)

    assert result.returncode == 0, f"the hook failed: {result.stderr}"
    assert result.stdout == "", (
        "manual mode printed on the compact path; the docstring says it prints "
        f"nothing there:\n{result.stdout}"
    )


def test_the_compact_path_prints_the_auto_continuation_in_auto_mode(tmp_path):
    """The anchor. A hook that printed on NO path would pass the test above.

    Without this case the pair could be satisfied by deleting the print
    outright, which is the opposite defect and would strand every unattended
    stretch at its compaction boundary.
    """
    result = _run_compact(tmp_path, auto=True)

    assert result.returncode == 0, f"the hook failed: {result.stderr}"
    assert "AUTO MODE" in result.stdout, (
        f"auto mode printed no continuation instruction:\n{result.stdout!r}")


def test_the_compact_path_still_injects_no_handoff_body(tmp_path):
    """The second anchor, on the half of the bullet that did NOT change.

    "No body on `compact`" is the older claim and it is still true; the auto
    text is a continuation instruction, not a pointer. A fix that satisfied the
    mode split by re-injecting the archive on the auto path would regress it.
    """
    result = _run_compact(tmp_path, auto=True)

    assert "## Latest summary" not in result.stdout
    assert "## Continuation prompt" not in result.stdout


# ============================================================
# Asking the AST which run the print belongs to
# ============================================================

def _module() -> ast.Module:
    return ast.parse(HOOK.read_text(encoding="utf-8"))


def _compact_branch(module: ast.Module) -> ast.If:
    """The `if source... == "compact":` statement, located by its own constant."""
    branches = [
        node for node in ast.walk(module)
        if isinstance(node, ast.If)
        and any(isinstance(c, ast.Constant) and c.value == "compact"
                for c in ast.walk(node.test))
    ]
    assert len(branches) == 1, (
        f"expected one compact branch in {HOOK.name}, found {len(branches)}")
    return branches[0]


def _prints_outside_the_auto_guard(branch: ast.If) -> list[int]:
    """Line numbers of `print(...)` on the compact branch not under `if auto:`."""
    parent: dict[int, ast.AST] = {}
    for node in ast.walk(branch):
        for child in ast.iter_child_nodes(node):
            parent[id(child)] = node

    unguarded = []
    for node in ast.walk(branch):
        if not (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "print"):
            continue
        cursor: ast.AST | None = node
        guarded = False
        while cursor is not None and cursor is not branch:
            holder = parent.get(id(cursor))
            if (isinstance(holder, ast.If)
                    and isinstance(holder.test, ast.Name)
                    and holder.test.id == "auto"
                    and any(cursor is s or cursor in ast.walk(s)
                            for s in holder.body)):
                guarded = True
                break
            cursor = holder
        if not guarded:
            unguarded.append(node.lineno)
    return unguarded


def test_the_compact_print_sits_under_the_auto_guard(tmp_path):
    """Structure, not stdout. Moving the print out of the guard must fail HERE.

    The measurement above catches a print that reaches stdout in manual mode.
    This catches the same edit one step earlier, and it catches a version whose
    manual silence comes from somewhere else entirely - an empty constant, an
    early return - while the docstring still credits the `auto` guard for it.
    """
    branch = _compact_branch(_module())
    unguarded = _prints_outside_the_auto_guard(branch)

    assert unguarded == [], (
        f"{HOOK.name} prints on the compact branch at line(s) {unguarded} "
        "without an `if auto:` above it; the docstring says manual mode prints "
        "nothing there")


def test_the_ast_probe_can_actually_see_an_unguarded_print():
    """The probe's own anchor: a walker that found nothing would pass above.

    Built against a synthetic branch of the same shape, because the point is
    whether `_prints_outside_the_auto_guard` REPORTS - a version returning `[]`
    unconditionally is indistinguishable from a clean hook otherwise.
    """
    module = ast.parse(
        'def main():\n'
        '    if source.strip() == "compact":\n'
        '        print(BANNER)\n'
        '        if auto:\n'
        '            print(AUTO_AFTER_COMPACT)\n'
        '        return 0\n'
    )
    branch = _compact_branch(module)

    assert _prints_outside_the_auto_guard(branch) == [3], (
        "the AST probe cannot see an unguarded print, so its verdict on the "
        "real hook establishes nothing")


# ============================================================
# Requiring the docstring to carry what was measured
# ============================================================

def test_the_docstring_records_the_mode_split_the_hook_actually_has(tmp_path):
    """The pin. The required claim is DERIVED from the run, never hard-coded.

    When the measurement says auto prints and manual does not, the compact
    bullet has to name both modes and say manual produces nothing; the sentence
    it carried until 2026-09-02 named neither, which is how it stayed wrong
    through review. If the code is ever changed so manual prints too, the
    measurement changes and the two tests at the top of this file fail rather
    than this one silently relaxing: the disagreement is caught either way.
    """
    manual = _run_compact(tmp_path, auto=False).stdout
    auto = _run_compact(tmp_path, auto=True).stdout
    doc = ast.get_docstring(_module()) or ""

    assert doc, f"{HOOK.name} lost its module docstring"

    if auto and not manual:
        assert re.search(r"\bauto\b", doc, re.I), (
            "the hook prints on the compact path only in auto mode and the "
            "docstring never names that mode")
        assert re.search(r"\bmanual\b", doc, re.I), (
            "the hook prints nothing on the compact path in manual mode and "
            "the docstring never names that mode, so it describes one of the "
            "two runs as if it were the only one")
        assert re.search(
            r"manual mode[^.]{0,80}?(prints? nothing|no output|nothing at all)"
            r"|(prints? nothing|no output|nothing at all)[^.]{0,80}?manual mode",
            doc, re.I | re.S), (
            "the docstring names manual mode but never says it produces no "
            "output there; that is the sentence retired on 2026-09-02 and it "
            "must not come back")
    else:  # pragma: no cover - reached only once the code's behaviour changes
        pytest.fail(
            "the compact path's measured behaviour changed: manual stdout is "
            f"{manual!r} and auto stdout is {auto[:60]!r}. Re-decide which of "
            "the code and the docstring is right before relaxing this test.")
