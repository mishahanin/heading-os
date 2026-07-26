"""Tests for the Canopus freeze check inside the test gate."""
import importlib.util
import subprocess
from pathlib import Path

import pytest

from scripts.utils.canopus_freeze import build_manifest, write_freeze
from scripts.utils.canopus_gate import freeze_gate

STAMP = "2026-01-01T00:00:00+00:00"
REPO_ROOT = Path(__file__).resolve().parent.parent


def _git_init(directory: Path) -> None:
    """Make *directory* a repository, so the gate artifact has a HEAD to read."""
    for argv in (
        ["init", "-q", "-b", "main"],
        ["config", "user.email", "builder@example.invalid"],
        ["config", "user.name", "Builder"],
    ):
        subprocess.run(["git", "-C", str(directory), *argv], check=True,
                       capture_output=True, text=True)


def _git_commit(directory: Path, message: str) -> None:
    subprocess.run(["git", "-C", str(directory), "add", "-A"], check=True,
                   capture_output=True, text=True)
    subprocess.run(["git", "-C", str(directory), "commit", "-q", "-m", message],
                   check=True, capture_output=True, text=True)


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    root = tmp_path / "tree"
    (root / "tests").mkdir(parents=True)
    (root / "tests" / "test_alpha.py").write_text("def test_a():\n    assert True\n")
    return root


@pytest.fixture
def anchor(tmp_path: Path) -> Path:
    path = tmp_path / "outside" / "gate-artifact.md"
    path.parent.mkdir(parents=True)
    path.write_text("# gate artifact\n")
    return path


def test_gate_is_silent_with_no_freeze(tree, capsys):
    assert freeze_gate(tree) == 0
    assert capsys.readouterr().out == ""


def test_gate_passes_when_the_lock_is_held(tree, anchor, capsys):
    manifest = build_manifest([tree / "tests"], tree, label="demo",
                              frozen_at=STAMP, anchor=anchor)
    write_freeze(tree, manifest)
    anchor.write_text(f"canopus-anchor: {manifest['root']}\n")
    assert freeze_gate(tree) == 0
    assert "LOCK HELD" in capsys.readouterr().out


def test_gate_is_amber_but_passing_without_a_recorded_anchor(tree, anchor, capsys):
    manifest = build_manifest([tree / "tests"], tree, label="demo",
                              frozen_at=STAMP, anchor=anchor)
    write_freeze(tree, manifest)
    assert freeze_gate(tree) == 0
    assert "LOCK UNCONFIRMED" in capsys.readouterr().out


def test_gate_fails_on_loss_of_lock(tree, anchor, capsys):
    manifest = build_manifest([tree / "tests"], tree, label="demo",
                              frozen_at=STAMP, anchor=anchor)
    write_freeze(tree, manifest)
    anchor.write_text(f"canopus-anchor: {manifest['root']}\n")
    (tree / "tests" / "test_alpha.py").write_text("def test_a():\n    assert False\n")
    assert freeze_gate(tree) == 1
    assert "LOSS OF LOCK" in capsys.readouterr().out


def test_gate_fails_on_a_corrupt_manifest(tree, capsys):
    (tree / ".canopus").mkdir(parents=True)
    (tree / ".canopus" / "freeze.json").write_text("{ not json")
    assert freeze_gate(tree) == 1
    assert "release --force" in capsys.readouterr().out


def test_gate_answers_rather_than_raising_on_an_unusable_anchor_path(tree, anchor):
    """The gate must NEVER raise. A raise here fails OPEN.

    Measured, not imagined: with an anchor whose directory holds an embedded NUL
    byte, `subprocess.run` raises ValueError, which is neither an OSError nor a
    SubprocessError, so it walked straight out of git_output, past freeze_gate's
    `except OSError`, and out of pytest_sessionstart as
    `ValueError: embedded null byte`. The enforcement layer of the freeze
    guarantee crashed the harness instead of reporting a state, and the
    pre-wire-2.1 path returned cleanly on the identical input.

    The same seam has a second, likelier route: `text=True` decoding raises
    UnicodeDecodeError, a ValueError subclass, on a gate artifact that is not
    UTF-8. It is masked today only because read_anchor fails first on that file.
    """
    manifest = build_manifest([tree / "tests"], tree, label="demo",
                              frozen_at=STAMP, anchor=anchor)
    manifest["anchor"] = str(anchor.parent.parent / "out\x00side" / "gate-artifact.md")
    write_freeze(tree, manifest)

    assert freeze_gate(tree) == 1


# ============================================================
# The gate's own wiring: which resolver it calls, and what it prints
# ============================================================
# In a bare temporary directory resolve_anchor and anchor_state agree, so
# substituting one for the other is invisible. These two put the gate artifact
# inside a repository, which is the only place the precedence decision is
# observable, and pin the gate to the answer the repository gives.


def test_gate_reads_the_committed_anchor_and_not_the_working_file(tree, anchor, capsys):
    """A line appended to the working copy must not reach LOCK HELD from the gate.

    This is the hole wire 2.1 exists to close, asserted at the surface that
    actually fires. Calling anchor_state here instead reads the working file,
    finds the freshly appended hash, and prints green over an approval nobody
    committed.
    """
    _git_init(anchor.parent)
    _git_commit(anchor.parent, "the gate artifact, with no approval in it")
    manifest = build_manifest([tree / "tests"], tree, label="demo",
                              frozen_at=STAMP, anchor=anchor)
    write_freeze(tree, manifest)
    with anchor.open("a", encoding="utf-8") as handle:
        handle.write(f"\ncanopus-anchor: {manifest['root']}\n")

    assert freeze_gate(tree) == 0
    out = capsys.readouterr().out
    assert "LOCK UNCONFIRMED" in out
    assert "LOCK HELD" not in out


def test_gate_says_why_the_approval_is_unverified(tree, anchor, capsys):
    """An unexplained amber is what costs the operator, so the gate prints the reason.

    The lock axis already falls to amber on an uncommitted approval. This line
    adds the REASON, and it is the fourth approval surface: the other three are
    commands an operator chooses to type, and this one runs at every pytest
    session start.
    """
    _git_init(anchor.parent)
    _git_commit(anchor.parent, "the gate artifact, with no approval in it")
    manifest = build_manifest([tree / "tests"], tree, label="demo",
                              frozen_at=STAMP, anchor=anchor)
    write_freeze(tree, manifest)
    with anchor.open("a", encoding="utf-8") as handle:
        handle.write(f"\ncanopus-anchor: {manifest['root']}\n")

    assert freeze_gate(tree) == 0
    out = capsys.readouterr().out
    assert "APPROVAL UNVERIFIED" in out
    assert "no approval is recorded in the committed state" in out


def test_run_tests_calls_the_gate_before_pytest():
    """The wiring is the whole point, so assert it explicitly.

    Source inspection rather than an import: run-tests.py calls ensure_venv() at
    import time, which re-execs the interpreter outside .venv. Matched on the
    call name only, not the exact argument text, so renaming a local does not
    fail a test that is about ordering.
    """
    source = (REPO_ROOT / "scripts" / "run-tests.py").read_text()
    assert "freeze_gate(" in source
    assert source.index("freeze_gate(") < source.index("subprocess.run(")


# ============================================================
# The conftest hook — the gate over the CLASS of pytest invocations
# ============================================================
# run-tests.py runs once at the end of a slice, or not at all. Bare
# `pytest tests/test_thing.py` is the inner-loop command a build runs dozens of
# times, and it has to refuse the same way. The hook is directly testable, so it
# is tested behaviourally rather than by reading the source.


@pytest.fixture
def conftest_module():
    """A second copy of tests/conftest.py, loaded by path.

    Importing it fresh keeps the monkeypatched _ENGINE_ROOT off the conftest the
    live session is running under. Executing it twice is inert: it sets one env
    default and defines hooks.
    """
    path = REPO_ROOT / "tests" / "conftest.py"
    spec = importlib.util.spec_from_file_location("canopus_conftest_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_conftest_hook_is_silent_when_no_freeze_is_active(conftest_module, tree, monkeypatch, capsys):
    monkeypatch.setattr(conftest_module, "_ENGINE_ROOT", tree)
    conftest_module.pytest_sessionstart(session=None)
    assert capsys.readouterr().out == ""


def test_conftest_hook_passes_while_the_lock_is_held(conftest_module, tree, anchor, monkeypatch, capsys):
    manifest = build_manifest([tree / "tests"], tree, label="demo",
                              frozen_at=STAMP, anchor=anchor)
    write_freeze(tree, manifest)
    anchor.write_text(f"canopus-anchor: {manifest['root']}\n")
    monkeypatch.setattr(conftest_module, "_ENGINE_ROOT", tree)
    conftest_module.pytest_sessionstart(session=None)
    assert "LOCK HELD" in capsys.readouterr().out


def test_conftest_hook_aborts_the_session_when_the_contract_moved(
    conftest_module, tree, anchor, monkeypatch
):
    """A moved contract stops the run before collection, so bare pytest cannot
    reach green on a target the builder moved."""
    manifest = build_manifest([tree / "tests"], tree, label="demo",
                              frozen_at=STAMP, anchor=anchor)
    write_freeze(tree, manifest)
    anchor.write_text(f"canopus-anchor: {manifest['root']}\n")
    (tree / "tests" / "test_alpha.py").write_text("def test_a():\n    assert False\n")
    monkeypatch.setattr(conftest_module, "_ENGINE_ROOT", tree)
    with pytest.raises(pytest.UsageError, match="frozen test contract moved"):
        conftest_module.pytest_sessionstart(session=None)


def test_conftest_hook_aborts_on_a_corrupt_manifest(conftest_module, tree, monkeypatch):
    (tree / ".canopus").mkdir(parents=True)
    (tree / ".canopus" / "freeze.json").write_text("{ not json")
    monkeypatch.setattr(conftest_module, "_ENGINE_ROOT", tree)
    with pytest.raises(pytest.UsageError):
        conftest_module.pytest_sessionstart(session=None)
