"""The shared test runner builds the correct gate command."""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("run_tests", ROOT / "scripts" / "run-tests.py")
run_tests = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(run_tests)


def test_gate_command_enforces_floor_and_excludes_acceptance():
    cmd = run_tests.build_command(acceptance=False)
    assert "-m" in cmd and "not acceptance" in cmd
    assert any(part.startswith("--cov-fail-under=") for part in cmd)


def test_acceptance_command_includes_marker_and_skips_floor():
    cmd = run_tests.build_command(acceptance=True)
    assert "acceptance" in cmd
    assert "not acceptance" not in cmd
    assert not any(part.startswith("--cov-fail-under=") for part in cmd)
