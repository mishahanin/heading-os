#!/usr/bin/env python3
"""The push gate printed which mode it chose one line above the verdict.

MEASURED 2026-09-05, the first end-to-end run of the reinstalled pre-push hook,
driven with real ref lines and its whole output captured to a file:

    line 1    bringing up nodes...
    ...
    line 567  24952 passed, 2 skipped, 19 warnings in 307.16s (0:05:07)
    line 568  pre-push gate: FULL SUITE, because day mode could not decide ...
    line 569  test gate: PASS

`pre_push_selection` prints that line BEFORE it returns, and `main()` spawns
pytest after it. During a push stdout is a PIPE, so the parent's `print` is
block-buffered and flushes at exit, while pytest is a CHILD writing to the same
pipe and flushing as it goes. Five minutes of the child's output therefore land
ahead of the parent's first line.

Nothing is lost, and the ordering is the entire value. `run-tests.py` says the
reason is printed on both branches so a narrowed gate cannot be mistaken for one
that quietly stopped narrowing; a line that arrives after the run cannot serve
that. It also produced a false symptom worth recording: piping that run into
`head -20` showed no gate line at all, which reads as "the hook still does not
call decide()".

WHY THIS TEST DRIVES THE REAL `main()`. An assertion that the line merely EXISTS
passes today, before the fix, which is what let this survive. So the pytest child
is replaced by a fake `pytest` module on PYTHONPATH: `child_env()` scrubs only
names beginning with `PYTEST_`, so PYTHONPATH survives into the child and
`python -m pytest` loads the fake. The gate then runs end to end, through a real
pipe, in milliseconds.

Run: .venv/bin/python -m pytest \\
     tests/test_a_gate_that_announced_its_mode_after_the_wait.py -q
"""
from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
GATE = ROOT / "scripts/run-tests.py"

#: What the fake child prints. Must not be a substring of any gate line.
CHILD_MARK = "FAKE-PYTEST-CHILD-OUTPUT"

_FAKE_PYTEST = f'''\
import sys
print("{CHILD_MARK}", flush=True)
sys.exit(0)
'''


@pytest.fixture(scope="module")
def gate_output(tmp_path_factory) -> str:
    """`run-tests.py --pre-push` once, with stdout on a PIPE as git gives it.

    Module-scoped so both cases read one run's bytes rather than two runs'.

    `input=""` is deliberate and it is what keeps this cheap: with no ref lines
    `decide()` short-circuits to the full suite without walking the tree, and
    MEASURED 2026-09-05 the whole invocation takes 0.09 s. That is the FULL-SUITE
    branch, which is enough for the ordering property — every gate line goes
    through the same flushed path, and `main()`'s flush covers all of them
    whichever branch printed.
    """
    tmp = tmp_path_factory.mktemp("fake-pytest")
    (tmp / "pytest.py").write_text(_FAKE_PYTEST, encoding="utf-8")
    env = dict(os.environ)
    env["PYTHONPATH"] = str(tmp) + os.pathsep + env.get("PYTHONPATH", "")
    # The gate re-execs under .venv when it is not already there; running it with
    # the interpreter that is running this test avoids the re-exec entirely.
    proc = subprocess.run(
        [sys.executable, str(GATE), "--pre-push"],
        input="", capture_output=True, text=True, cwd=str(ROOT),
        env=env, timeout=180)
    assert proc.returncode == 0, (
        f"the gate exited {proc.returncode}\nstdout:\n{proc.stdout}\n"
        f"stderr:\n{proc.stderr}")
    return proc.stdout


# ============================================================
# THE GUARD: the mode arrives before the wait it explains
# ============================================================

def test_the_gate_line_precedes_the_child_output(gate_output):
    out = gate_output

    assert "pre-push gate:" in out, (
        f"the gate printed no mode line at all:\n{out}")
    assert CHILD_MARK in out, (
        f"the fake child never ran, so this proves nothing about ordering:\n{out}")

    gate_at = out.index("pre-push gate:")
    child_at = out.index(CHILD_MARK)

    assert gate_at < child_at, (
        "the gate announced its mode AFTER the run it describes. On a real push "
        "that is five minutes of pytest output ahead of the one line telling the "
        f"operator which mode they are waiting on:\n{out}")


def test_the_verdict_still_comes_last(gate_output):
    """The other direction. Flushing early must not reorder the verdict."""
    out = gate_output

    assert "test gate: PASS" in out, out
    assert out.index(CHILD_MARK) < out.index("test gate: PASS"), (
        f"the verdict was printed before the run it reports:\n{out}")


# ============================================================
# The root fix, asked of the AST rather than of the text
# ============================================================

def _main_body() -> list[ast.stmt]:
    tree = ast.parse(GATE.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "main":
            return node.body
    raise AssertionError("run-tests.py has no main()")


def test_main_flushes_stdout_before_it_spawns_the_child():
    """Per-print flags cover only the prints that carry them.

    A `print` added to `pre_push_selection` tomorrow without the flag would
    reintroduce this exactly, and the behavioural cases above would not see it
    because they read the lines that exist today. The flush in `main()` is what
    makes the property hold for lines nobody has written yet, so it is asserted
    separately, and on the AST: a substring scan goes green on the sentence in
    the docstring that explains it.
    """
    flush_lines: list[int] = []
    spawn_lines: list[int] = []
    for stmt in _main_body():
        for node in ast.walk(stmt):
            if not (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)):
                continue
            target = node.func.value
            if (node.func.attr == "flush"
                    and isinstance(target, ast.Attribute)
                    and target.attr == "stdout"):
                flush_lines.append(node.lineno)
            elif (node.func.attr in {"run", "Popen"}
                    and isinstance(target, ast.Name)
                    and target.id == "subprocess"):
                spawn_lines.append(node.lineno)

    # The floor, OUTSIDE the loop. A walk that found nothing would satisfy every
    # assertion below by never reaching one, which is the shape
    # `scripts/check-test-vacuity.py` refuses. Measured 2026-09-05: main() holds
    # exactly one `subprocess.run` and one `sys.stdout.flush()`.
    assert spawn_lines, (
        "main() no longer spawns a subprocess; this test is aimed at code that "
        "has moved, and re-reading it is the fix, not deleting it")
    assert flush_lines, (
        "main() spawns the pytest child without flushing stdout first, so "
        "everything printed above it is still in the parent's buffer and lands "
        "after the child's output")
    assert min(flush_lines) < min(spawn_lines), (
        f"the flush is at line {min(flush_lines)} and the child is spawned at "
        f"{min(spawn_lines)}: flushing after the spawn changes nothing")


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-q"]))
