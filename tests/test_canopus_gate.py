"""Tests for the Canopus freeze check inside the test gate."""
import importlib.util
from pathlib import Path

import pytest

from scripts.utils.canopus_freeze import build_manifest, write_freeze
from scripts.utils.canopus_gate import freeze_gate

STAMP = "2026-01-01T00:00:00+00:00"
REPO_ROOT = Path(__file__).resolve().parent.parent


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
