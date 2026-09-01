"""The suite's timezone pin, and the guard that it is a pin at all.

`tests/conftest.py` opens by declaring that the whole session runs on
Etc/GMT-4, "set deterministically", so that calendar, scheduling and daemon
heartbeat tests validate the per-instance logic rather than the engine's UTC
default. Until 2026-08-30 the line under that declaration was

    os.environ.setdefault("HEADING_OS_TZ", "Etc/GMT-4")

which is not a pin. Any ambient value won silently: `HEADING_OS_TZ=America/
New_York pytest tests/` moved every offset-sensitive assertion onto New York
and reported nothing, while the docstring above it still promised Etc/GMT-4.
The same file already codified the correct rule three constants later, for
WORKSPACE_LOG_DIR: "isolation that a stray shell variable can switch off is
not isolation."

Two tests, because either alone is weak. The first is a behaviour check: a
pytest CHILD is launched with a hostile ambient value and must still see
Etc/GMT-4 inside the run. That is the defect exactly, and it fails the moment
the assignment goes back to a setdefault. The second is an AST check on
conftest's own module body, which costs no subprocess and states the shape
the first test measures - deliberately not a source grep, which would pass on
this docstring's own quotation of the offending line.

Run: python3 -m pytest tests/test_a_timezone_pin_a_stray_variable_could_switch_off.py
"""
from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFTEST = ROOT / "tests" / "conftest.py"

# The value conftest pins. Spelled once here; a drift between the two is a
# failure of the AST test below, not a silent disagreement.
PINNED = "Etc/GMT-4"

# A zone that is neither the pin nor UTC, so a pass cannot come from a
# coincidence of offsets.
HOSTILE = "America/New_York"


def test_the_session_runs_on_the_pinned_zone():
    """Read inside the run: whatever the shell said, this is what tests see."""
    assert os.environ["HEADING_OS_TZ"] == PINNED


def test_the_pin_survives_a_hostile_ambient_value():
    """The negative case: a child launched with the wrong zone still sees the pin.

    This test IS the mutation detector. Restore the `setdefault` in
    `tests/conftest.py` and the child below reports America/New_York, because
    a name already present in the environment is exactly what setdefault
    declines to touch.
    """
    env = dict(os.environ)
    env["HEADING_OS_TZ"] = HOSTILE
    # `-p no:randomly` so the child's ordering plugin cannot reorder a
    # single-node selection into a different one, and `--no-header` to keep
    # the failure output short enough to read in a report.
    proc = subprocess.run(
        [sys.executable, "-m", "pytest",
         f"{Path(__file__).relative_to(ROOT).as_posix()}"
         "::test_the_session_runs_on_the_pinned_zone",
         "-q", "--no-header", "-p", "no:randomly"],
        cwd=str(ROOT), env=env, capture_output=True, text=True,
        timeout=300, check=False)
    # The child's TEST result, not its SESSION exit status, and the difference
    # is a measured false red rather than a nicety.
    #
    # The root conftest's `pytest_sessionfinish` sets `session.exitstatus = 1`
    # whenever the operator's overlay changed between the run's first and last
    # instant. The overlay is a LIVE tree: a daemon, the operator, or a
    # concurrent agent writes into it on its own schedule, and
    # `watch_complaints` says so in its own docstring. So a child that ran this
    # one test and printed `1 passed` still exits 1 when an unrelated file
    # appeared while it ran, and the old assertion reported "did not see the
    # pin" over a run that had seen it.
    #
    # REPRODUCED 2026-09-01 in a scratch tree, deterministically: a background
    # writer touching the scratch overlay every 150 ms while this test ran gave
    #   ERROR: 2 file(s) appeared in the operator's live operator overlay ...
    #   1 passed in 1.97s
    # and `assert 1 == 0`. That is load-sensitive by construction, because a
    # slower child holds a wider window for someone else to write, which is why
    # this test failed once inside a full `-n auto` run on 2026-09-01 and passed
    # three times out of three when re-run alone. It is NOT slowness, so marking
    # the file `slow` would hide it rather than fix it.
    #
    # A child that crashed, errored in collection, or collected nothing prints
    # no `1 passed` either, so nothing that the exit-status check caught is
    # lost; the status is reported in the message instead of asserted on.
    child_passed = "1 passed" in proc.stdout and " failed" not in proc.stdout
    assert child_passed, (
        f"a child run carrying HEADING_OS_TZ={HOSTILE} did not see the pin "
        f"(child exit status {proc.returncode}):\n{proc.stdout}\n{proc.stderr}")


def _tz_statements() -> tuple[list[ast.Assign], list[ast.Call]]:
    """Every module-level write to HEADING_OS_TZ in conftest, by shape.

    AST, not a substring scan: this file quotes the banned `setdefault` line
    verbatim in its own docstring, and a grep would either trip on that or be
    weakened to avoid it. Punishing a file for documenting its own trap is the
    failure mode this repository refuses.
    """
    tree = ast.parse(CONFTEST.read_text(encoding="utf-8"))
    assigns: list[ast.Assign] = []
    calls: list[ast.Call] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if (isinstance(target, ast.Subscript)
                        and isinstance(target.value, ast.Attribute)
                        and target.value.attr == "environ"
                        and isinstance(target.slice, ast.Constant)
                        and target.slice.value == "HEADING_OS_TZ"):
                    assigns.append(node)
        elif isinstance(node, ast.Call):
            func = node.func
            if (isinstance(func, ast.Attribute) and func.attr == "setdefault"
                    and node.args
                    and isinstance(node.args[0], ast.Constant)
                    and node.args[0].value == "HEADING_OS_TZ"):
                calls.append(node)
    return assigns, calls


def test_the_pin_is_an_assignment_and_not_a_setdefault():
    assigns, calls = _tz_statements()
    assert not calls, (
        "tests/conftest.py sets HEADING_OS_TZ with setdefault at line(s) "
        f"{[c.lineno for c in calls]}; an ambient value would win")
    assert len(assigns) == 1, (
        "expected exactly one module-level pin of HEADING_OS_TZ in "
        f"tests/conftest.py, found {len(assigns)}")
    value = assigns[0].value
    assert isinstance(value, ast.Constant) and value.value == PINNED, (
        f"the pin should be the literal {PINNED!r}")
