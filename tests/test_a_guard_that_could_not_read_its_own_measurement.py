"""A clock guard that a harness environment variable could stop from parsing.

`test_a_suite_that_only_passed_on_one_clock.py` runs the clock-sensitive files
in a child pytest and reads the count back out of its output:

    passed = int(out.split(" passed")[0].split()[-1])

The child inherited the parent's environment. MEASURED 2026-09-03: the agent
harness exports `FORCE_COLOR=3`, the child colourised into a pipe even though
nothing was a terminal, and both parametrised cases died with

    ValueError: invalid literal for int() with base 10: '\\x1b[32m\\x1b[32m\\x1b[1m234'

Nothing in this repository was wrong. A variable in the surrounding environment
decided whether the guard could read its own measurement, and the guard reported
that as a failure of the code under test. Confirmed by unsetting it:
`env -u FORCE_COLOR ... pytest <those two nodes>` passed.

Two layers now, and they fail differently. `--color=no` plus dropping
`FORCE_COLOR`/`CLICOLOR_FORCE` from the child env stops the decoration at
source; `_passed_count` strips ANSI anyway, so a colouriser nobody predicted
still parses. The second is what makes this robust rather than merely fixed for
today's harness.

Run: python3 -m pytest tests/test_a_guard_that_could_not_read_its_own_measurement.py
"""
from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.utils.repo_files import read_sources  # noqa: E402

TARGET = ROOT / "tests" / "test_a_suite_that_only_passed_on_one_clock.py"

clock = pytest.importorskip("tests.test_a_suite_that_only_passed_on_one_clock")


def _target_source() -> str:
    vanished: list[Path] = []
    texts = dict(read_sources([TARGET], vanished))
    assert not vanished, f"the target vanished mid-read: {vanished}"
    return texts[TARGET]


# ============================================================
# The parser, over decorated and undecorated input
# ============================================================

def test_a_plain_summary_parses():
    assert clock._passed_count("234 passed in 12.00s") == 234


def test_a_colourised_summary_parses():
    """The exact bytes from the reported failure."""
    coloured = "\x1b[32m\x1b[32m\x1b[1m234 passed\x1b[0m\x1b[32m in 12.00s\x1b[0m"
    assert clock._passed_count(coloured) == 234


def test_a_summary_with_other_counts_still_reads_the_passed_one():
    assert clock._passed_count("2 failed, 234 passed, 3 skipped in 9s") == 234


def test_a_multiline_tail_reads_the_summary_line():
    out = "some\nlines\nbefore\n\x1b[32m412 passed\x1b[0m in 30.00s\n"
    assert clock._passed_count(out) == 412


def test_the_naive_parser_would_still_fail_on_the_same_input():
    """The negative case: proves the strip is what does the work.

    Without this, `_passed_count` could be returning the right number for some
    other reason and the ANSI handling would be untested.
    """
    coloured = "\x1b[32m\x1b[1m234 passed\x1b[0m in 12.00s"
    with pytest.raises(ValueError):
        int(coloured.split(" passed")[0].split()[-1])


# ============================================================
# The child is told not to decorate in the first place
# ============================================================

def test_the_child_command_disables_colour():
    src = _target_source()
    assert '"--color=no"' in src, (
        "the child pytest may colourise; the parser is then the only defence")


def test_the_forced_colour_variables_are_dropped_from_the_child_env():
    src = _target_source()
    assert '"FORCE_COLOR", "CLICOLOR_FORCE"' in src
    assert "env.pop(forced, None)" in src


def test_the_helper_is_a_module_level_function_not_an_inline_expression():
    """It has to be reachable to be tested. The defect lived in an inline
    `int(...)` that no test could drive."""
    tree = ast.parse(_target_source())
    names = {n.name for n in ast.walk(tree)
             if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    assert "_passed_count" in names


# ============================================================
# End to end: the guard survives a hostile environment
# ============================================================

@pytest.mark.slow
def test_the_clock_guard_runs_under_forced_colour():
    """The reproduction, driven. FORCE_COLOR is set deliberately here.

    This is the environment that broke it. One parametrised case is enough to
    exercise the parse; running both doubles a multi-minute child for no new
    information.
    """
    env = dict(os.environ, FORCE_COLOR="3", CLICOLOR_FORCE="1")
    node = (f"{TARGET.relative_to(ROOT)}::"
            "test_the_clock_sensitive_files_pass_away_from_the_operator_zone"
            "[Etc/GMT+12]")
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", node, "-q", "--no-header",
         "-p", "no:cacheprovider", "--color=no"],
        cwd=str(ROOT), capture_output=True, text=True, timeout=900, env=env)
    assert "invalid literal for int()" not in proc.stdout, (
        "the guard still cannot read its own measurement under forced colour")
    assert proc.returncode == 0, proc.stdout[-3000:]
