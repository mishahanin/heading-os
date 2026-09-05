#!/usr/bin/env python3
""""The push path" was one category, and it held two different risks.

`CLAUDE.md` sent the whole push path to a YARD, on the ground that a
half-finished edit in HELM is live in the tree that executes now. That is right
for some of it and wrong for the rest, and the difference is WHO CAN RUN THE
FILE: a timer runs on its own schedule, your own `git push` cannot run while you
are mid-edit, and push and `/backup` happen only in HELM, which is one session.

THIS FILE WENT RED ON ITS OWN AUTHOR, and that is why it exists rather than
being a sentence in the rule. The first draft asserted that `run-tests.py` was
HELM's, on a claim built from a grep whose hits were read as prose. MEASURED
2026-09-05, after the red:

    scripts/nightly-refresh.py:393
        gate = [sys.executable, str(root / "scripts" / "run-tests.py")]
    nightly-refresh.timer   fires 01:30 daily, Persistent=true

So a timer executes the test gate every night, and a half-saved copy of it
breaks the nightly at 01:30 with nobody watching. `run-tests.py` belongs in a
worktree. `install-git-hooks.py` does not: nothing scheduled names it.

An example in prose is a hand-kept list, which is the defect one level up that
`tests/test_a_prohibition_written_as_a_list_of_verbs.py` was written for, where
a prohibition spelled as three verbs was implemented as those three verbs and a
fourth ran a second mail daemon for twelve hours. So the reachable set here is
DERIVED from `scripts/templates/systemd/*.service`, the units that actually get
installed, and a timer added tomorrow is followed without anyone remembering to
come back to this file.

WHAT THIS DOES NOT ESTABLISH, stated rather than left to be found. Reachability
is followed two ways: static `import` statements under `scripts/`, and a path
spelled as a literal in CODE. A module reached only through a name assembled at
run time, or through a shell string, is invisible to it. It is a floor under the
rule, never a proof of unreachability.

Run: .venv/bin/python -m pytest \\
     tests/test_the_push_path_splits_on_who_runs_it.py -q
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

import pytest

from tests.repo_files import read_sources

ROOT = Path(__file__).resolve().parent.parent
UNITS = ROOT / "scripts/templates/systemd"

#: Measured 2026-09-05: 20 `ExecStart=` lines across the installed unit
#: templates. A floor, not the count, so adding a unit does not fail this.
MIN_UNITS = 15

#: What the rule names as HELM's: nothing on a schedule reaches it.
HELM_SIDE = ("scripts/install-git-hooks.py",)

#: What the rule names as a YARD's. These are the FLOOR: were the walk to find
#: nothing, every absence asserted on the HELM side would pass vacuously.
YARD_SIDE = ("scripts/utils/day_mode.py", "scripts/run-tests.py")

_EXEC = re.compile(r"ExecStart=.*?(scripts/[A-Za-z0-9_./-]+\.py)")


def _unit_entry_points() -> set[Path]:
    """Every `scripts/*.py` an installed systemd unit executes."""
    found: set[Path] = set()
    # `read_sources`, not `read_text`: this is a walk-then-read, and a unit
    # template written and removed between the two by a parallel agent would
    # raise FileNotFoundError out of a test that is not about that file.
    # `tests/test_a_guard_that_crashed_on_a_file_that_vanished_mid_walk.py`
    # holds every sweep in this tree to it, and caught this one.
    for _unit, text in read_sources(sorted(UNITS.glob("*.service"))):
        for rel in _EXEC.findall(text):
            path = ROOT / rel
            if path.is_file():
                found.add(path)
    return found


def _imports_of(path: Path) -> set[str]:
    """Dotted `scripts.*` module names this file imports, statically."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError:
        return set()
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            if node.module.startswith("scripts"):
                names.add(node.module)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("scripts"):
                    names.add(alias.name)
    return names


def _code_literals(path: Path) -> set[str]:
    """String constants the file's CODE holds, docstrings excluded.

    The exclusion is the difference between a check and a nuisance. Every file
    on the push path discusses the others by name in its prose, and a scan that
    reads an explanation as the thing it describes teaches people to stop
    explaining. `.claude/rules/scope-claims.md` draws the same line for the same
    reason. Comments never reach the AST at all, so they need no handling.
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError:
        return set()

    docstrings: set[int] = set()
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if not isinstance(node, (ast.Module, ast.FunctionDef,
                                 ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        if (body and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)):
            docstrings.add(id(body[0].value))

    return {node.value for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
            and id(node) not in docstrings}


def _module_file(dotted: str) -> Path | None:
    candidate = ROOT / (dotted.replace(".", "/") + ".py")
    return candidate if candidate.is_file() else None


def _reachable_from(seeds: set[Path]) -> set[Path]:
    """Transitive static-import closure of `seeds`, within `scripts/`."""
    seen: set[Path] = set()
    queue = list(seeds)
    while queue:
        current = queue.pop()
        if current in seen:
            continue
        seen.add(current)
        for dotted in _imports_of(current):
            target = _module_file(dotted)
            if target is not None and target not in seen:
                queue.append(target)
    return seen


@pytest.fixture(scope="module")
def scheduled_closure() -> set[Path]:
    return _reachable_from(_unit_entry_points())


def _spawners_of(rel: str, closure: set[Path]) -> list[str]:
    """Scheduled files whose CODE spells `rel` as a PATH, so they can run it.

    Equality, never containment, and the difference was measured rather than
    reasoned. `scripts/nightly-refresh.py` spells the gate as a path TWICE, at
    lines 243 and 393, and ALSO carries three operator messages reading
    "Reproduce: python scripts/run-tests.py". Under a containment test those
    three messages alone satisfied this function, so the anchor was a sentence
    rather than a call. Verifying that took two attempts and the first one lied:
    breaking only line 393 left every case green, which reads as a surviving
    mutation and is really an incomplete one. BOTH path literals have to go
    before the absence is real — and with equality, breaking both is caught
    while the three messages stay untouched.

    A path is spelled as a whole segment or not at all, so a literal that
    EQUALS the basename or the repo-relative path is a path, and one that merely
    contains it inside a sentence is prose.
    """
    wanted = {rel, Path(rel).name}
    return sorted(p.relative_to(ROOT).as_posix() for p in closure
                  if wanted & _code_literals(p))


# ============================================================
# The floor, before anything is concluded from an absence
# ============================================================

def test_the_units_are_found_and_they_lead_somewhere(scheduled_closure):
    entries = _unit_entry_points()

    assert len(entries) >= MIN_UNITS, (
        f"only {len(entries)} unit entry point(s) found under {UNITS}; every "
        f"absence asserted below would be vacuous")
    assert len(scheduled_closure) > len(entries), (
        "the import walk added nothing to the entry points, so it is not "
        "following imports and cannot establish reachability either way")


@pytest.mark.parametrize("rel", YARD_SIDE)
def test_the_walk_finds_what_the_rule_puts_in_a_yard(rel, scheduled_closure):
    """THE OTHER DIRECTION, and the half that caught this file's own error.

    Without it the walk could return the seeds alone and every HELM-side
    assertion would pass while measuring nothing. It is also what says, on the
    day the nightly stops running the gate, that the rule's example is stale.
    """
    target = ROOT / rel
    assert target.is_file(), f"{rel} has moved; CLAUDE.md names it by this path"

    reachable = target in scheduled_closure or bool(
        _spawners_of(rel, scheduled_closure))

    assert reachable, (
        f"{rel} is no longer reachable from any installed unit, by import or by "
        f"a path spelled in code. Either a scheduled job stopped using it — in "
        f"which case CLAUDE.md's example is stale and the file may move to the "
        f"HELM side — or this walk has stopped working.")


# ============================================================
# THE GUARD: what the rule calls HELM's stays out of reach
# ============================================================

@pytest.mark.parametrize("rel", HELM_SIDE)
def test_a_scheduled_job_cannot_reach_the_files_helm_may_fix(rel,
                                                             scheduled_closure):
    """Asked of the two mechanisms that can actually reach these files.

    NOT of the import graph alone, and the difference is most of this test's
    value. `install-git-hooks.py` carries a hyphen, so no `import` statement can
    name it under any circumstances: "not in the import closure" is true of it
    forever, whatever anyone does, and a guard that cannot fail is not a guard.
    A kebab-named script is executed rather than imported, so the reachable
    shapes are a unit naming it in `ExecStart=` and a scheduled script spelling
    its path in code. Those two are what is checked.
    """
    target = ROOT / rel
    assert target.is_file(), f"{rel} has moved; CLAUDE.md names it by this path"

    named_by_units = [u.name for u, text
                      in read_sources(sorted(UNITS.glob("*.service")))
                      if rel in text]
    assert not named_by_units, (
        f"{rel} is named by installed unit(s) {named_by_units}, so a timer runs "
        f"it on a schedule. CLAUDE.md calls it HELM-fixable on the ground that "
        f"nothing but a typed command runs it, and that ground is gone.")

    spawners = _spawners_of(rel, scheduled_closure)
    assert not spawners, (
        f"{rel} is spelled in the code of {spawners}, which a systemd unit "
        f"reaches. A scheduled job can therefore execute a half-saved copy of "
        f"it. Move it to the YARD side of the rule, or drop the call.")


# ============================================================
# The paragraph and the tree must not drift apart
# ============================================================

def test_claude_md_still_carries_the_rule_this_file_defends():
    """A test defending a sentence has to fail when the sentence is rewritten.

    Not a prose assertion about wording: it checks that the files this test
    measures are the ones the rule points at. Rewording the paragraph is free;
    dropping a file from it while this test still guards it is what goes red.
    """
    text = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")

    assert "The push path splits on WHO RUNS IT" in text, (
        "the rule this file guards is gone from CLAUDE.md; delete this test or "
        "restore the rule, but do not leave a guard over a sentence nobody "
        "wrote")
    for rel in YARD_SIDE + HELM_SIDE:
        assert Path(rel).name in text, (
            f"CLAUDE.md no longer names {rel}, which this file classifies")


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-q"]))
