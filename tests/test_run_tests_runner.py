"""The shared test runner builds the correct gate command, and the floor is enforced.

The coverage floor moved on 2026-08-20. It used to sit in the pre-push argv, where
it cost 37.9 s on every push (measured A/B twice: 124.7 s with coverage against
86.8 s without) and could not fail, because it demanded 27% while the suite
delivered 43.44%. It now sits on the CI unit-tests step, which is where a coverage
regression can actually be stopped.

That move is only safe as a PAIR. Dropping `--cov` from the runner without adding
`--cov-fail-under` to CI leaves the floor enforced nowhere, and a coverage
regression then ships in silence. So this file asserts both halves: the runner
must NOT carry the flag, and ci.yml MUST.
"""
import importlib.util
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("run_tests", ROOT / "scripts" / "run-tests.py")
run_tests = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(run_tests)

CI_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"


def test_gate_command_excludes_acceptance_and_leaves_coverage_to_ci():
    cmd = run_tests.build_command(acceptance=False)
    assert "-m" in cmd and "not acceptance" in cmd
    assert not any(part.startswith("--cov") for part in cmd), (
        "the pre-push gate must not measure coverage; it costs ~38 s per push and "
        "the floor is enforced in CI. See COVERAGE_FLOOR in scripts/run-tests.py."
    )


def test_acceptance_command_includes_marker_and_skips_floor():
    cmd = run_tests.build_command(acceptance=True)
    assert "acceptance" in cmd
    assert "not acceptance" not in cmd
    assert not any(part.startswith("--cov-fail-under=") for part in cmd)


def test_ci_enforces_the_coverage_floor_the_runner_gave_up():
    """The other half of the pair. Without this, the move loses the guarantee."""
    assert CI_WORKFLOW.is_file(), "CI workflow missing; the floor has no enforcer"
    text = CI_WORKFLOW.read_text(encoding="utf-8")
    found = re.findall(r"--cov-fail-under=(\d+)", text)
    assert found, (
        "no --cov-fail-under in .github/workflows/ci.yml: the coverage floor is "
        "now enforced nowhere. Either restore it in CI or restore it in "
        "scripts/run-tests.py build_command()."
    )
    assert any(int(v) >= 30 for v in found), (
        f"the CI coverage floor {found} is too low to be a floor at all; the "
        "suite measured 43.44% on the operator's machine, and CI covers less "
        "(no data overlay, no marp-cli, no LFS fixtures) but not that much less."
    )


def test_the_documented_floor_and_the_enforced_floor_agree():
    """A constant nobody reads is a comment. Hold it to the value CI uses."""
    documented = getattr(run_tests, "COVERAGE_FLOOR", None)
    assert isinstance(documented, int), "COVERAGE_FLOOR must survive as the documented home"
    enforced = [int(v) for v in re.findall(r"--cov-fail-under=(\d+)",
                                           CI_WORKFLOW.read_text(encoding="utf-8"))]
    assert documented in enforced, (
        f"scripts/run-tests.py documents COVERAGE_FLOOR={documented} but CI enforces "
        f"{enforced}. One of the two is stale."
    )
