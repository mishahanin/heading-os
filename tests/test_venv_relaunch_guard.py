"""The suite's re-exec guard, measured rather than asserted about itself.

About twenty scripts call `ensure_venv()` at module scope and about twenty test
modules load a script by path. Under any interpreter that is not
`.venv/bin/python`, that call `os.execv`s the whole pytest process, which
inherits pytest's capture as file descriptor 1 and 2: every byte of the
relaunched run lands in a temp file nobody reads, so the session prints ZERO
bytes while exiting 0 on a passing set and 1 on a failing one. A run that prints
nothing is indistinguishable from one that never happened.

Until wire 2.2 the guard was three per-module copies of one line, each with a
test asserting the process-global variable it set. That shape could not hold:
deleting the line from one module left that module's own test passing, because
another module had already set the same variable. Worse, it was self-erasing --
a NEW unguarded module re-execs at collection, `ensure_venv` sets the sentinel
before `os.execv`, and in the silent relaunched run all three tests pass.

So the guard moved to tests/conftest.py, which is collected before any test
module, and the tests here replace the three that could not fail. Measured with
the conftest line removed: the child run below printed zero bytes and exited 0.
"""
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.utils import venv as _venv

ROOT = Path(__file__).resolve().parent.parent
TESTS = ROOT / "tests"

# Any line that SETS the sentinel, however it spells the name. Reading it, as
# this module does below, is not setting it.
_SETS_THE_SENTINEL = re.compile(
    r"""os\.environ(?:\.setdefault)?[\[(]\s*(?:_venv\._SENTINEL|["']"""
    + _venv._SENTINEL
    + r"""["'])"""
)


def test_the_guard_is_set_once_by_the_root_conftest():
    """Where it is, stated as a test, because "somewhere in the suite" was the bug.

    A per-module copy satisfies every other module's guard test, so the only
    property worth pinning is that the ONE file collected before every test
    module carries it.
    """
    assert _SETS_THE_SENTINEL.search((TESTS / "conftest.py").read_text(encoding="utf-8"))


def test_no_test_module_carries_its_own_copy_of_the_guard():
    """The cross-satisfying copies, refused as a class rather than one by one.

    A module that sets the sentinel for itself is indistinguishable in its own
    output from one that inherited it, which is exactly how three copies came to
    cover for each other. The root conftest is the only place it belongs.
    """
    strays = [
        path.relative_to(ROOT).as_posix()
        for path in sorted(TESTS.rglob("test_*.py"))
        if _SETS_THE_SENTINEL.search(path.read_text(encoding="utf-8"))
    ]
    assert strays == []


def _candidate_interpreters() -> list:
    """Interpreters that might not be this venv's, discovered rather than named.

    Two sources, neither a literal path: the interpreter this venv was BUILT
    from (`sys.base_prefix`), and the first `python3` on a PATH with the venv's
    own bin directory removed. Which of them differs from the venv interpreter
    is a property of the machine, so both are tried.
    """
    found = [Path(sys.base_prefix) / "bin" / "python3"]
    venv_bin = str(_venv.venv_python().parent)
    path = os.pathsep.join(
        part for part in os.environ.get("PATH", "").split(os.pathsep)
        if part and Path(part) != Path(venv_bin)
    )
    on_path = shutil.which("python3", path=path)
    if on_path:
        found.append(Path(on_path))
    return found


def _foreign_interpreter() -> Path:
    """An interpreter whose re-exec target really differs, or skip.

    `ensure_venv` compares RESOLVED paths, so a candidate that resolves to the
    same file as `.venv/bin/python` cannot reproduce the defect however it is
    spelled: it re-execs nothing. A skip here says the measurement could not run
    on this machine, which is the honest answer and not a pass.
    """
    target = _venv.venv_python().resolve()
    for candidate in _candidate_interpreters():
        if not candidate.is_file() or candidate.resolve() == target:
            continue
        probe = subprocess.run([str(candidate), "-c", "import pytest"],
                               capture_output=True, text=True, timeout=60,
                               check=False)
        if probe.returncode == 0:
            return candidate
    pytest.skip("no interpreter outside the venv, carrying pytest, to measure with")


def test_a_run_under_a_foreign_interpreter_still_prints_its_output():
    """The bite: the defect itself, reproduced end to end and then absent.

    tests/test_push_all_gate.py loads a script that calls `ensure_venv()` at
    module scope, so collecting it under an interpreter that is not the venv's is
    exactly the situation the guard exists for. The child's sentinel is stripped
    from the environment so the guard has to come from the collected
    tests/conftest.py and from nowhere else.

    The assertion is VISIBILITY, not success. The child may well fail: a script
    imported under the system interpreter can miss a pinned dependency, and the
    freeze gate speaks its own state at session start. What it may never do is
    produce a run with no output at all, which is what the re-exec produced --
    both file descriptors point at pytest's capture files, so neither stream
    reaches this process.
    """
    interpreter = _foreign_interpreter()
    env = {key: value for key, value in os.environ.items() if key != _venv._SENTINEL}

    proc = subprocess.run(
        [str(interpreter), "-m", "pytest", "tests/test_push_all_gate.py",
         "--collect-only", "-q", "-p", "no:cacheprovider"],
        cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=300,
        check=False,
    )

    assert (proc.stdout + proc.stderr).strip(), (
        "the child run printed nothing at all, which is what an ensure_venv "
        "re-exec inside pytest's capture looks like"
    )
