"""Tests for the Canopus freeze check inside the single test gate."""
from pathlib import Path

import pytest

from scripts.utils.canopus_freeze import build_manifest, write_freeze
from scripts.utils.canopus_gate import freeze_gate

STAMP = "2026-01-01T00:00:00+00:00"


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
    import time, which re-execs the interpreter outside .venv.
    """
    source = (Path(__file__).resolve().parent.parent / "scripts" / "run-tests.py").read_text()
    assert "freeze_gate(" in source
    assert source.index("freeze_gate(root)") < source.index("subprocess.run(cmd")
