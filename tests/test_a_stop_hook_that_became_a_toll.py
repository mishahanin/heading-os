#!/usr/bin/env python3
"""The turn check ran serially on sixteen cores and refused five turns running.

`scripts/turn-check.py` is the Stop-hook lane that measures the tests matching
this turn's edits. It ran pytest with no `-n`, on a 16-core machine, while its
own sibling `scripts/run-tests.py:78` already passed `-n auto` for the whole
suite. `.claude/hooks/turn-check.py` allowed it 80 seconds (`BUDGET_SECONDS`
minus 10).

MEASURED 2026-08-31, on the real changed set, on a machine loaded by five
parallel fix agents:

    40 test files                serial 25.95s    parallel 18.90s   (1.37x)
    78 test files, 1932 tests                     parallel 45.21s

Serial did not fit the budget at campaign size. The hook refused five turns in
a row, and every single re-run with a longer cap came back CLEAN:

    the matched tests did not finish in 80s (74 file(s))
    the matched tests did not finish in 80s (75 file(s))
    the matched tests did not finish in 80s (83 file(s))
    the matched tests did not finish in 80s (89 file(s))
    the matched tests did not finish in 80s (109 file(s))

The set grew while the operator waited, because a fleet of agents kept editing,
so the check could never converge. That is not caution. A budget too small to
ever finish converts a check into a toll: the operator pays the wait and learns
nothing, which is precisely what the "short enough that nobody learns to dread
the end of a turn" comment beside the number was written to prevent.

Two changes, and BOTH are needed, which is why both are pinned here. `-n auto`
alone is only a 1.37x speed-up, because worker start-up is a fixed cost. The
wider budget alone leaves the lane serial and merely moves the cliff.

What this file does NOT claim: that `-n auto` is safe for every test. That rests
on precedent rather than on anything asserted here, and the precedent is real:
`scripts/run-tests.py` has run the entire suite that way for a long time, and
the root conftest arms its isolation per test rather than per session.
`test_the_whole_suite_runner_also_parallelises` keeps the two in step, so the
day someone removes the flag from the runner because parallel turned out to be
unsafe, this lane is not left as the last parallel caller in the tree.
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

LANE = ROOT / "scripts" / "turn-check.py"
HOOK = ROOT / ".claude" / "hooks" / "turn-check.py"
RUNNER = ROOT / "scripts" / "run-tests.py"


def _pytest_arg_lists(source: str, path: Path) -> list[list[str]]:
    """Every list literal in the file that builds a pytest command line.

    Read from the AST, not by grepping the source text. A grep for `"-n"` is
    satisfied by the string appearing in a comment or a docstring, and this
    whole file exists because a control that can be satisfied by prose is not a
    control. Only literal string elements are collected; a computed element
    becomes `None` so a caller can see the list was not fully readable.
    """
    tree = ast.parse(source, filename=str(path))
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.List, ast.Tuple)):
            continue
        parts = []
        for element in node.elts:
            if isinstance(element, ast.Constant) and isinstance(element.value, str):
                parts.append(element.value)
            elif isinstance(element, ast.Starred):
                parts.append("*")
            else:
                parts.append(None)
        # The RUN lane only. `-x` is what separates it from the `--collect-only`
        # list a few lines below in the same module, which legitimately has no
        # `-n` and no marker expression. Keying on "pytest in parts" alone
        # matched both and made this file fail against correct code.
        if "pytest" in parts and "-x" in parts and "--collect-only" not in parts:
            found.append(parts)
    return found


def test_the_turn_check_lane_runs_pytest_in_parallel():
    """The fix. Without `-n auto` the lane cannot finish inside the budget.

    The flag is CONDITIONAL on the matched-file count, so in the AST it is a
    starred expression rather than two plain strings, and reading it out of the
    argument list would report `None`. The claim is therefore split: the source
    must contain the flag and the threshold, and the argument list must not be a
    flat serial command with the flag removed altogether.
    """
    source = LANE.read_text(encoding="utf-8")
    assert '"-n", "auto"' in source, (
        "the turn-check pytest lane never passes `-n auto`. On this machine "
        "that measured 25.95s for 40 files against 18.90s in parallel, and at "
        "campaign size it cannot finish inside the hook's budget, so the Stop "
        "hook refuses the turn and the operator re-runs it by hand for the "
        "same clean answer.")
    assert "PARALLEL_FILE_THRESHOLD" in source, (
        "the lane has no parallelism threshold. Parallelising unconditionally "
        "costs about 7s of xdist worker start-up on every turn, measured: a "
        "2-test file is 0.04s serial and 7.41s parallel.")

    lists = _pytest_arg_lists(source, LANE)
    assert lists, (
        "no pytest RUN command line found in scripts/turn-check.py. Either the "
        "lane was restructured or this test is looking at the wrong shape; "
        "either way it is measuring nothing until that is resolved.")


def test_the_threshold_sits_between_the_two_measurements():
    """A threshold below the crossover makes the common turn slower, not faster.

    Read from the module rather than restated, so this measures the shipped
    value. The bounds are the two runs that produced it: 7.41s of start-up on a
    2-test file says do not go near the bottom, and parallel already winning at
    40 files says do not sit above it.
    """
    import importlib.util
    spec = importlib.util.spec_from_file_location("turn_check_threshold", LANE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    threshold = module.PARALLEL_FILE_THRESHOLD
    assert 8 <= threshold <= 40, (
        f"PARALLEL_FILE_THRESHOLD is {threshold}. Below ~8 the 7s of xdist "
        f"start-up costs more than it saves on an ordinary turn; above 40 the "
        f"campaign case stays serial and the Stop hook goes back to timing out.")


def test_an_unreadable_deselect_count_is_not_reported_as_zero():
    """The regression `-n auto` would otherwise have introduced, silently.

    MEASURED on a fixture holding one fast and one slow test: the serial run
    prints `1 passed, 1 deselected` and the parallel run prints `1 passed`. The
    parser matched on that literal, so under the parallel lane it returned 0,
    the renderer printed nothing, and an exclusion that really happened read as
    full coverage. `.claude/rules/scope-claims.md` forbids exactly that.

    The existing `tests/test_turn_check.py::test_the_test_lane_deselects_slow_
    marked_tests` is what caught it, which is the system working.
    """
    import importlib.util
    spec = importlib.util.spec_from_file_location("turn_check_deselect", LANE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    serial_body = "1 passed, 1 deselected, 1 warning in 0.04s"
    parallel_body = "1 passed, 16 warnings in 7.41s"

    assert module._deselected(serial_body) == 1
    assert module._deselected(serial_body, parallel=True) == 1
    assert module._deselected(parallel_body, parallel=False) == 0, (
        "a serial run with no deselection really did deselect nothing")
    assert module._deselected(parallel_body, parallel=True) == module.DESELECTED_UNKNOWN, (
        "the parallel lane reported 0 deselected over output that cannot say. "
        "Zero is a claim; this one is unknowable and must say so.")
    assert module.DESELECTED_UNKNOWN != 0, (
        "the unknown sentinel is 0, so it is indistinguishable from a real zero "
        "and the renderer cannot tell them apart")


def test_the_unknown_count_still_names_the_exclusion():
    """Knowing less must not mean saying less.

    The renderer prints nothing for a real zero, which is right. For the
    unknown case it must still tell the operator that slow tests were dropped
    and where to run them, or the parallel lane silently under-claims.
    """
    import importlib.util
    spec = importlib.util.spec_from_file_location("turn_check_render", LANE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module._slow_note({"deselected_slow": 0}) == ""
    unknown = module._slow_note({"deselected_slow": module.DESELECTED_UNKNOWN})
    assert unknown, "the unknown count printed nothing at all"
    assert "run-tests.py" in unknown, (
        "the note must still point at where those tests DO run")
    assert str(module.DESELECTED_UNKNOWN) not in unknown, (
        f"the note printed the raw sentinel {module.DESELECTED_UNKNOWN} at the "
        f"operator, e.g. '-1 slow test(s)'")
    counted = module._slow_note({"deselected_slow": 3})
    assert "3 slow test(s)" in counted


def test_the_whole_suite_runner_also_parallelises():
    """The precedent this lane's safety rests on, pinned so it cannot vanish.

    `-n auto` in the lane is justified by `run-tests.py` having run the entire
    suite that way. If that stops being true, the justification is gone and this
    fails rather than leaving the lane as the tree's last parallel caller.
    """
    lists = _pytest_arg_lists(RUNNER.read_text(encoding="utf-8"), RUNNER)
    flat = [p for parts in lists for p in parts if p]
    source = RUNNER.read_text(encoding="utf-8")
    assert "-n" in flat or '"-n", "auto"' in source or "'-n', 'auto'" in source, (
        "scripts/run-tests.py no longer runs the suite in parallel. That was "
        "the whole precedent for parallelising the turn-check lane; re-derive "
        "it before leaving `-n auto` in scripts/turn-check.py.")


def test_the_hook_budget_fits_a_campaign_sized_run():
    """A budget the lane can never meet is a toll, not a guard.

    The number is read from the hook rather than restated, so this measures the
    shipped value. 120 is the floor because the largest MEASURED run was 45.2s
    on a loaded machine and a guard with no headroom fails on the bad day.
    """
    source = HOOK.read_text(encoding="utf-8")
    match = re.search(r"^BUDGET_SECONDS\s*=\s*(\d+)", source, re.MULTILINE)
    assert match, "BUDGET_SECONDS is no longer a module-level integer literal"
    budget = int(match.group(1))
    assert budget >= 120, (
        f"BUDGET_SECONDS is {budget}. The largest measured matched run was "
        f"45.2s for 78 files and 1932 tests, in parallel, on a loaded machine. "
        f"At 90 this hook refused five consecutive turns and every re-run came "
        f"back clean.")
    assert budget <= 600, (
        f"BUDGET_SECONDS is {budget}, which is long enough that the operator "
        f"waits out a hung lane at the end of every turn. The point of the "
        f"parallel flag was to keep this bounded, not to buy an unbounded wait.")


def test_the_inner_cap_leaves_the_hook_room_to_report():
    """The lane must be killed by its OWN timeout, not by the hook's.

    The hook passes `BUDGET_SECONDS - 10` inward and then waits
    `BUDGET_SECONDS`. If those were equal, the outer `subprocess.run` would
    raise first and the hook would report "the checker could not run" instead of
    the lane's own "did not finish in Ns (N files)" line, which is the one that
    tells the operator what to do next.
    """
    source = HOOK.read_text(encoding="utf-8")
    assert "BUDGET_SECONDS - 10" in source, (
        "the hook no longer reserves headroom between the inner cap and its own "
        "wait, so a slow lane is reported as a broken checker")
    assert "timeout=BUDGET_SECONDS" in source


def test_the_lane_still_drops_the_slow_marker():
    """An anchor, so 'make it fit the budget' never becomes 'run less'.

    The honest way to fit a budget is to go faster. Quietly widening `-m` to
    include the sleeping timing tests, or narrowing the matched set, would also
    make this file's other tests pass while measuring less.
    """
    lists = _pytest_arg_lists(LANE.read_text(encoding="utf-8"), LANE)
    for parts in lists:
        assert "not slow" in parts, (
            f"the lane's marker expression changed: {parts}. Dropping `-m 'not "
            f"slow'` makes the lane slower, and widening it to run the sleeping "
            f"timing tests is what the marker was added to prevent.")
        assert "-x" in parts, (
            "the lane stopped exiting on first failure, which is what keeps a "
            "broken turn cheap to report")
