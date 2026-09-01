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

`main()` was unbound until 2026-09-01, and it is the half of this file that the
push gate actually runs. `.githooks/pre-push` and `scripts/push-all.py` both
invoke `scripts/run-tests.py` and read its EXIT STATUS; every test above and in
`tests/test_run_tests_env.py` stops at `build_command` and `child_env`, which are
inputs. Three one-token edits to `main` were measured on 2026-09-01 against those
two files plus six neighbours (the floor guard, the six-walls sweep, the canopus
contract, the git-hook installer, the data-repo gate) and all three SURVIVED:

    return proc.returncode            -> return 0        a red suite pushes green
    cmd = build_command(args.accept…) -> build_command(True)
                                                         the gate runs the few
                                                         acceptance gates instead
                                                         of the regression suite
    if proc.returncode == 0:          -> != 0            the banner says PASS
                                                         over a failed run

The first is `.claude/rules/lethal-trifecta`-adjacent in shape and identical to
the shipped `.githooks/pre-push-data` defect: a gate that prints its verdict and
returns success. The last three tests below are the ones that fail on each.
"""
import importlib.util
import re
import types
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


# ============================================================
# main() — the surface the push gate reads
# ============================================================


def _fake_child(monkeypatch, returncode: int, seen: dict):
    """Bind a stand-in `subprocess` INSIDE the module, never on the stdlib.

    `monkeypatch.setattr(run_tests.subprocess, "run", ...)` would rebind
    `subprocess.run` process-wide, for this test and every other one sharing the
    interpreter. Rebinding the module's own `subprocess` NAME reaches only this
    module's call site, which is the one under test.
    """
    def _run(cmd, **kwargs):
        seen["cmd"] = list(cmd)
        seen["kwargs"] = kwargs
        return types.SimpleNamespace(returncode=returncode, args=cmd)

    monkeypatch.setattr(run_tests, "subprocess",
                        types.SimpleNamespace(run=_run))


def test_a_failing_suite_leaves_the_gate_with_a_failing_exit_code(monkeypatch, capsys):
    """The whole reason this script exists. `return 0` here is a silent push."""
    seen = {}
    _fake_child(monkeypatch, 1, seen)
    monkeypatch.setattr("sys.argv", ["run-tests.py"])

    assert run_tests.main() == 1, (
        "scripts/run-tests.py reported success over a pytest child that exited "
        "non-zero. .githooks/pre-push and scripts/push-all.py read this status "
        "and nothing else, so a red suite would push."
    )
    assert "FAIL" in capsys.readouterr().out


def test_a_passing_suite_exits_zero_and_says_so(monkeypatch, capsys):
    """The paired green case, so 'always fail' is not a way to pass the test above."""
    seen = {}
    _fake_child(monkeypatch, 0, seen)
    monkeypatch.setattr("sys.argv", ["run-tests.py"])

    assert run_tests.main() == 0
    assert "PASS" in capsys.readouterr().out


def test_the_banner_and_the_exit_code_agree(monkeypatch, capsys):
    """A human watching the terminal reads the banner, not the status.

    `.githooks/pre-push-data` shipped printing its refusal and exiting 0, so the
    two halves are pinned separately: a run that exits non-zero must not print
    the success word.
    """
    _fake_child(monkeypatch, 2, {})
    monkeypatch.setattr("sys.argv", ["run-tests.py"])

    code = run_tests.main()
    out = capsys.readouterr().out

    assert code == 2
    assert "PASS" not in out, out


def test_the_default_invocation_runs_the_regression_suite_not_the_gates(monkeypatch):
    """`build_command(True)` in main empties the gate and nothing else notices.

    Acceptance mode is a handful of sign-off gates; running it instead of the
    regression suite is a gate that passes because it measured almost nothing.
    """
    seen = {}
    _fake_child(monkeypatch, 0, seen)
    monkeypatch.setattr("sys.argv", ["run-tests.py"])

    run_tests.main()

    assert "not acceptance" in seen["cmd"], seen["cmd"]


def test_the_acceptance_flag_still_reaches_the_child(monkeypatch):
    """The other arm, so 'hardcode regression mode' cannot satisfy the test above."""
    seen = {}
    _fake_child(monkeypatch, 0, seen)
    monkeypatch.setattr("sys.argv", ["run-tests.py", "--acceptance"])

    run_tests.main()

    assert "acceptance" in seen["cmd"] and "not acceptance" not in seen["cmd"]
