"""Reproducibility invariants: the lockfile, python pin, and coverage gate exist.

The coverage gate half of that sentence was a claim and not a test until
2026-09-01: this file asserted the pin and the lockfile, and nothing anywhere
looked at `--cov-fail-under`. A docstring that names three invariants and
measures two is the shape that makes the next reader stop looking.
"""
import re
import tomllib
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
CI_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"


def test_python_version_pinned():
    pin = (ROOT / ".python-version").read_text(encoding="utf-8").strip()
    assert pin.startswith("3.11"), f"expected 3.11.x, got {pin!r}"


def test_requires_python_declared():
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert data["project"]["requires-python"] == ">=3.11"


def test_uv_lock_committed():
    lock = ROOT / "uv.lock"
    assert lock.is_file() and lock.stat().st_size > 0


def _run_commands(workflow: dict) -> list[str]:
    """Every `run:` string in the workflow, in job/step order.

    Parsed, not grepped. The coverage step sits under a comment block that
    already contains the digits "35" and "43%", so a substring search over the
    file text is green whether or not the flag is still on the command line.
    """
    commands = []
    for job in (workflow.get("jobs") or {}).values():
        for step in (job.get("steps") or []):
            run = step.get("run")
            if isinstance(run, str):
                commands.append(run)
    return commands


def test_coverage_gate_is_armed_in_ci():
    """CI must fail on a coverage floor, and the floor must be a real number.

    `--cov-fail-under=0` and a deleted flag are the two ways this gate goes
    quiet without anything going red, and both are indistinguishable from a
    healthy run in the build log.
    """
    workflow = yaml.safe_load(CI_WORKFLOW.read_text(encoding="utf-8"))
    commands = _run_commands(workflow)
    assert commands, "no run steps parsed out of ci.yml"

    gated = [c for c in commands if "--cov-fail-under" in c]
    assert gated, "no CI step gates on --cov-fail-under"

    for command in gated:
        floor = re.search(r"--cov-fail-under[= ](\d+(?:\.\d+)?)", command)
        assert floor, command
        assert float(floor.group(1)) > 0, f"the coverage floor is disarmed: {command}"
        assert "--cov=scripts" in command, (
            "the floor is measured over nothing unless --cov names a source", command)
