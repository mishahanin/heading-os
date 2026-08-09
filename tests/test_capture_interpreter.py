"""The retired contract for `capture-interpreter` — the baseline described the caller.

Shipped 2026-08-05 and promoted here from
`tests/contract/2026-08-04-capture-interpreter/`, where it was frozen. All five
IDs are kept: a contract retired by deletion takes its coverage with it.

Nothing in this file depends on its own depth in the tree, unlike the previous
slice's promotion, which carried a `parents[3]` that silently became wrong two
directories up.

The `/scrutinize` pass on this slice found that SC-3's two paths do not exist on
disk, so `resolve()` had nothing to follow and the comparison defect hid behind
them. The tests that close that live in `tests/test_canopus_contract.py` and
`tests/test_venv_relaunch_guard.py`, on REAL symlinks. Neither set replaces the
other: these decide the rule, those decide it against the filesystem.

ONE defect, paid for on 2026-08-04 rather than reasoned about. `freeze` captures
its plugin baseline from a pytest CHILD, and `run_pytest_report` launches that
child with `sys.executable` — whichever interpreter invoked the CLI. Invoked as
bare `python`, which on that machine is the system interpreter, the freeze
recorded `['dist:_pytest', 'dist:anyio', 'dist:pytest_asyncio']` while every run
of the suite loads `['dist:pytest_cov', 'dist:xdist']`. Two DISJOINT sets, so no
run could ever attest that freeze.

The failure is silent and late. Nothing refuses at capture time; the symptom
arrives after a full suite run, as seventeen lines of "a plugin the freeze did
not record was loaded", worded as though a plugin had been injected. The
plugin set is inside `root_hash_payload`, so the correction cost a full retake.

`run-tests.py` already solved the same problem for itself with `ensure_venv()` at
import. `scripts/canopus.py` cannot copy that: it IS imported by
`tests/test_canopus_cli.py`, and a re-exec at import time would take the suite
down with it. So the choice moves to the one place that launches the child.

Every test imports the code under test INSIDE its body and takes its own scratch
tree, so nothing here reads the engine's working tree.
"""

import sys
from pathlib import Path

import pytest


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    root = tmp_path / "tree"
    (root / "tests" / "contract").mkdir(parents=True)
    (root / "tests" / "contract" / "test_c.py").write_text(
        "def test_c():\n    assert True\n", encoding="utf-8")
    return root


def _captured_command(monkeypatch, tree: Path) -> list:
    """The argv `run_pytest_report` would launch, without launching it.

    The child is stubbed at `subprocess.run` rather than at any Canopus seam, so
    what this reads is the real command builder's real output. A stub one layer
    higher would be a test of the stub.
    """
    from scripts.utils import canopus_contract

    seen: list = []

    class _Result:
        returncode = 0
        stdout = ""
        stderr = ""

    def _fake_run(command, **kwargs):
        seen.append(list(command))
        # The caller reads the JUnit report off disk afterwards; write the
        # minimum that parses so the failure under test is the ARGV, never a
        # missing file.
        for index, arg in enumerate(command):
            if arg == "--junit-xml":
                Path(command[index + 1]).write_text(
                    '<?xml version="1.0"?><testsuites><testsuite name="p" '
                    'tests="0"></testsuite></testsuites>', encoding="utf-8")
        return _Result()

    monkeypatch.setattr(canopus_contract.subprocess, "run", _fake_run)
    canopus_contract.run_pytest_report([tree / "tests" / "contract"], tree)
    assert seen, "the command builder never launched a child"
    return seen[0]


# ============================================================
# SC-1 — the child is launched by the interpreter that runs the suite
# ============================================================

def test_the_contract_child_is_launched_by_the_project_interpreter(monkeypatch, tree):
    """SC-1. WHEN the project venv interpreter exists, THE SYSTEM SHALL launch
    the contract child with it, whatever interpreter invoked the CLI.

    `sys.executable` is replaced with a decoy that is not the venv, which is
    exactly the shape of a bare-`python` invocation. Asserting against the decoy
    rather than merely against "some interpreter" is the point: a command that
    still names the caller passes any weaker assertion.
    """
    from scripts.utils import canopus_contract
    from scripts.utils.venv_guard import venv_python

    monkeypatch.setattr(sys, "executable", "/decoy/python")
    command = _captured_command(monkeypatch, tree)

    assert command[0] == str(venv_python()), (
        f"the child inherited the invoking interpreter: {command[0]!r}. The "
        f"plugin baseline it captures then describes an environment the suite "
        f"never runs in, and nothing says so until a full run refuses.")
    assert canopus_contract  # imported for the seam the helper patched


def test_a_tree_without_a_project_interpreter_falls_back_to_the_invoking_one(
    monkeypatch, tree
):
    """SC-2 [failure-mode]. WHEN there is no project venv, THE SYSTEM SHALL use
    the invoking interpreter.

    A public clone that has not run `uv sync`, or an operator on a system-wide
    install, has no `.venv`. Refusing there would make `probe` and `freeze`
    unusable for a case that is not an error, and today's behaviour is already
    correct for it. The fix must not narrow that.
    """
    from scripts.utils import canopus_contract

    monkeypatch.setattr(sys, "executable", "/decoy/python")
    monkeypatch.setattr(canopus_contract, "venv_python",
                        lambda: Path("/nowhere/.venv/bin/python"))
    command = _captured_command(monkeypatch, tree)

    assert command[0] == "/decoy/python"


def test_the_choice_is_one_named_function(monkeypatch):
    """SC-1b. THE SYSTEM SHALL answer "which interpreter" from ONE named
    function, so every child this module ever launches inherits the answer.

    One launcher exists today. A second spelling of the rule is how the next one
    comes to disagree with it, and the disagreement would be silent: both return
    a path that runs pytest.
    """
    from scripts.utils.canopus_contract import contract_interpreter
    from scripts.utils.venv_guard import venv_python

    monkeypatch.setattr(sys, "executable", "/decoy/python")

    assert contract_interpreter() == venv_python()


# ============================================================
# SC-3 — a capture by a different interpreter says so, at capture time
# ============================================================

def test_a_capture_by_a_different_interpreter_is_announced(monkeypatch):
    """SC-3 [observability]. WHEN the capturing interpreter is not the one that
    invoked the command, THE SYSTEM SHALL say so.

    SC-1 makes the capture right; this makes it legible. Without it the operator
    who typed the wrong `python` learns nothing at the moment it matters and
    everything an hour later, in a form that names plugins rather than
    interpreters. Both paths appear in the sentence, because "a different
    interpreter" without saying WHICH sends the reader back to guessing.

    ONE line is asserted alongside the content, and not for tidiness. Measured at
    probe time: with the two `in` checks alone this test was TAKEN by the greedy
    pass candidate, whose whole implementation is every string literal in the
    contract joined by newlines — a stand-in nobody would accept, satisfying an
    assertion about what a sentence CONTAINS. A single line is also what the
    freeze output actually needs, so the strengthening is a real requirement
    rather than a shim against the probe.
    """
    from scripts.utils.canopus_contract import interpreter_notice

    notice = interpreter_notice(Path("/project/.venv/bin/python"),
                                Path("/usr/bin/python"))

    assert notice, "a capture by another interpreter passed in silence"
    assert "/project/.venv/bin/python" in notice
    assert "/usr/bin/python" in notice
    assert "\n" not in notice, (
        f"the notice is not one line, so it is a dump rather than a sentence: "
        f"{notice!r}")


def test_agreeing_interpreters_say_nothing(monkeypatch):
    """SC-3b. WHEN the two are the same, THE SYSTEM SHALL say nothing.

    Without this, SC-3 is satisfied by printing on every invocation, which is
    the shape that trains an operator to stop reading the line — and the line is
    the whole point.
    """
    from scripts.utils.canopus_contract import interpreter_notice

    same = Path("/project/.venv/bin/python")

    assert interpreter_notice(same, same) == ""
