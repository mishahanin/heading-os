"""The herdr stub agreed with every argv it was handed.

`write_herdr_stub` recorded argv and exited 0, for anything. So the whole
bootstrap corpus ran against a binary that could not disagree, and a green suite
meant only that the script had run, never that it had said anything herdr would
accept.

MEASURED 2026-09-03: no test in this repository compared a herdr argument
against the real CLI or its schema. `test_yard_bootstrap_lint.py` fed an
invented payload; `FAKE_HERDR` in two files returned success for everything
except `agent list`. Three wrong shapes reached the operator's machine behind
that:

    data.worktree.path read one level too high  -> every real YARD unprovisioned
    result.workspace.cwd                        -> a key at no depth, KeyError
                                                   eaten by `except Exception`
    herdr worktree create fix-router            -> the command takes no
                                                   positional at all

The repair is that the stub now VALIDATES. `tests/herdr_contract.check` reads
`tests/fixtures/herdr-cli-contract.json`, generated from `herdr <cmd> --help` by
`scripts/dev/capture-herdr-contract.py`, and the stub exits 2 on a violation --
which is what herdr 0.8.2 was measured to do for an unknown option, a missing
value, or an unexpected positional.

The point of generating rather than writing the contract: a contract the caller
writes is the caller agreeing with itself, which is the defect one level up.
`test_the_committed_contract_still_matches_the_installed_binary` compares the
committed copy against a live binary whenever one is present.

NOT CLAIMED. This is a grammar. It sees the shape of an argument and never its
meaning, so `--workspace some-branch-name` passes here: whether a string names a
live workspace is a question only the server answers. It says nothing about any
response body.

Run: python3 -m pytest tests/test_a_stub_that_confirmed_every_shape_it_was_given.py
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tests import herdr_contract  # noqa: E402
from tests.herdr_contract import CONTRACT, check, load  # noqa: E402
from tests.conftest import write_herdr_stub  # noqa: E402


# ============================================================
# The contract itself
# ============================================================

def test_the_contract_is_present_and_covers_what_the_engine_calls():
    """A floor. An empty contract makes `check` refuse everything, which would
    look like a very strict test and measure nothing about the engine."""
    contract = load()
    commands = contract["commands"]

    assert len(commands) >= 14, (
        f"the captured contract shrank to {len(commands)} commands; it covered "
        f"14 on 2026-09-03 and nothing has been removed from the engine")
    for required in ("pane list", "pane report-metadata", "pane run",
                     "notification show", "workspace get", "worktree create",
                     "worktree remove", "plugin config-dir"):
        assert required in commands, (
            f"{required!r} left the contract while the engine still calls it")
    assert contract["herdr_version"].startswith("herdr "), contract["herdr_version"]
    assert contract["captured"] >= "2026-09-03"


def test_every_captured_command_names_the_caller_that_needs_it():
    """A contract entry with no caller is an entry nobody maintains."""
    commands = load()["commands"]
    # The floor, outside the loop: with an empty capture every assertion below
    # runs zero times and this passes over a contract that checks nothing.
    # 14 commands on 2026-09-03.
    assert len(commands) >= 14, f"only {len(commands)} command(s) captured"
    for name, spec in commands.items():
        assert spec.get("called_from"), (
            f"{name!r} is captured but does not say who calls it")


# ============================================================
# The checker, both directions, on the three real mistakes
# ============================================================

@pytest.mark.parametrize("argv", [
    ["pane", "list", "--workspace", "w48"],
    ["pane", "report-metadata", "w48:p1", "--source", "heading-os.yard",
     "--token", "hos=ok", "--title", "YARD ready"],
    ["pane", "run", "w48:p1", "HEADING_OS_YARD=1 exec claude"],
    ["notification", "show", "a title", "--body", "a body", "--sound", "request"],
    ["notification", "show", "a title", "--body", "a body"],
    ["workspace", "get", "w48"],
    ["workspace", "rename", "w48", "YARD/test-123"],
    ["workspace", "close", "w48"],
    ["worktree", "create", "--branch", "fix-router"],
    ["worktree", "remove", "--workspace", "w47"],
    ["worktree", "remove", "--workspace", "w47", "--force"],
    ["plugin", "config-dir", "heading-os.yard"],
    ["agent", "list"],
])
def test_a_call_the_engine_makes_is_accepted(argv):
    """The half that must not be lost. A checker that refuses everything
    satisfies every refusal test and breaks every honest caller."""
    assert check(argv) is None, f"{argv} was refused: {check(argv)}"


@pytest.mark.parametrize("argv,expected", [
    # The exact line that stood in scripts/herdr/README.md until 2026-09-03.
    (["worktree", "create", "fix-router"], "at most 0 positional"),
    # The flag a reader would reach for, which does not exist on `remove`.
    (["worktree", "remove", "--branch", "fix-router"], "unknown option: --branch"),
    (["worktree", "remove", "--workspace"], "missing value for --workspace"),
    (["workspace", "get"], "at least 1 positional"),
    (["pane", "report-metadata", "w48:p1"], None),          # source is optional here
    (["pane", "list", "--cwd", "somewhere"], "unknown option: --cwd"),
    (["notification", "show"], "at least 1 positional"),
    (["workspace", "delete", "w48"], "unknown command"),
    ([], "missing subcommand"),
])
def test_a_wrong_call_is_refused_with_the_reason(argv, expected):
    problem = check(argv)
    if expected is None:
        assert problem is None, f"{argv} was refused: {problem}"
    else:
        assert problem and expected in problem, (
            f"{argv} gave {problem!r}, expected {expected!r}")


def test_the_checker_reads_the_committed_file_and_not_a_default(
        tmp_path, monkeypatch):
    """Empty the contract and the checker must break, not pass.

    A checker that silently falls back to a permissive default is the stub it
    replaced, wearing the name of a check.

    REDIRECTED, never overwritten. This wrote `{"commands": {}}` over the real
    fixture and restored it in a `finally`, and under `-n auto` the other
    workers read the file inside that window: two of them failed with
    `JSONDecodeError: Expecting value: line 1 column 1` on the empty file left
    mid-write. A test scoped to itself that is in fact scoped to the whole run
    is the same defect this whole branch is about, so it is not repeated here.
    """
    empty = tmp_path / "herdr-cli-contract.json"
    empty.write_text('{"commands": {}}', encoding="utf-8")
    monkeypatch.setattr(herdr_contract, "CONTRACT", empty)

    assert check(["pane", "list"]) is not None, (
        "with an empty contract the checker still accepted a call, so it is "
        "not reading the file it claims to")

    # And the real one is untouched, which is the half the old shape lost.
    assert CONTRACT.exists() and json.loads(CONTRACT.read_text())["commands"]


# ============================================================
# The stub, driven as an executable
# ============================================================

def test_the_stub_exits_two_on_a_call_herdr_would_reject(tmp_path):
    """The observable consequence, at the real entry point.

    Asserting `check()` alone would leave the stub free to ignore it, which is
    exactly how the old stub passed: the knowledge existed nowhere near the
    binary that answered.
    """
    stub = write_herdr_stub(tmp_path / "stub")
    proc = subprocess.run([str(stub.binary), "worktree", "create", "fix-router"],
                          capture_output=True, text=True, timeout=60)

    assert proc.returncode == 2, (
        f"the stub accepted a call herdr rejects (exit {proc.returncode})")
    assert "positional" in proc.stderr, proc.stderr
    assert stub.calls == [["worktree", "create", "fix-router"]], (
        "a refused call must still be recorded; a test needs to see what was "
        "attempted, not only that it failed")


def test_the_stub_still_passes_a_call_herdr_accepts(tmp_path):
    stub = write_herdr_stub(tmp_path / "stub")
    proc = subprocess.run([str(stub.binary), "worktree", "create",
                           "--branch", "fix-router"],
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr


def test_the_hostile_stub_keeps_its_exit_code(tmp_path):
    """`exit_code=` is for the deliberately-failing herdr, and validation must
    not quietly turn its 3 into a 2."""
    stub = write_herdr_stub(tmp_path / "stub", exit_code=3)
    proc = subprocess.run([str(stub.binary), "agent", "list"],
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 3, proc.stderr


# ============================================================
# The pin against the real binary
# ============================================================

def test_the_committed_contract_still_matches_the_installed_binary():
    """The captured contract is a measurement, and measurements go stale.

    Skipped where herdr is not installed, which is most CI. That is stated
    rather than hidden: on such a machine this file checks the engine against a
    frozen contract and NOT against herdr, and the freeze is what the skip
    leaves unverified.
    """
    if shutil.which("herdr") is None:
        pytest.skip("herdr is not installed here; the committed contract "
                    "cannot be compared against a live binary")

    generator = ROOT / "scripts" / "dev" / "capture-herdr-contract.py"
    proc = subprocess.run([sys.executable, str(generator)],
                          cwd=str(ROOT), capture_output=True, text=True,
                          timeout=300)
    assert proc.returncode == 0, proc.stderr

    live = json.loads(proc.stdout)
    committed = json.loads(CONTRACT.read_text(encoding="utf-8"))

    assert live["commands"] == committed["commands"], (
        "the installed herdr's CLI grammar no longer matches the committed "
        "contract. Re-capture it with `python scripts/dev/"
        "capture-herdr-contract.py --write` and read the diff: a command that "
        "changed shape is a caller that needs checking, not a fixture that "
        "needs overwriting.\n"
        f"installed: {live['herdr_version']}\n"
        f"committed: {committed['herdr_version']}")
