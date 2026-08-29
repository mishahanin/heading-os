#!/usr/bin/env python3
"""scripts/run-tests.py said it ran the coverage floor. Nothing in it ever did.

The module docstring read "Default mode runs the regression suite ... with the
coverage floor from pyproject", and the file contradicted itself twice below:
`build_command` attaches no coverage argument in either mode, and the
COVERAGE_FLOOR comment block states "ENFORCED IN CI, NOT HERE (2026-08-20)" and
"Keeping it out of pyproject addopts also means single-file `pytest tests/x.py`
runs are never blocked by partial coverage". A reader trusting the docstring
believed pushing through this gate enforced the ratchet. It never has.

The defect was prose, so these tests pin the BEHAVIOUR the corrected prose now
describes: neither mode measures coverage, and pyproject carries no coverage
addopts to supply one. They were green before the docstring was corrected and
they are green after - what they buy is that the docstring cannot drift back
into truth-by-accident, because the day someone adds `--cov` here these fail and
the prose has to be revisited.

Also closed here: the loose end COVERAGE_FLOOR names in its own comment - "It is
documentation until a test ties it to the ci.yml value". A constant documenting
a ratchet nobody keeps in step is a floor nobody keeps.
"""
from __future__ import annotations

import importlib.util
import re
import sys
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_COV_FLAG = re.compile(r"--cov\b|--cov-fail-under|--cov-report")


@pytest.fixture(scope="module")
def gate():
    spec = importlib.util.spec_from_file_location(
        "run_tests_gate", ROOT / "scripts" / "run-tests.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize("acceptance", [False, True])
def test_neither_mode_measures_coverage(gate, acceptance):
    argv = gate.build_command(acceptance)
    assert argv, "build_command returned no command at all"
    assert not [a for a in argv if _COV_FLAG.search(a)], argv


def test_pyproject_supplies_no_coverage_addopts():
    """The docstring credited pyproject for the floor. It carries none."""
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    addopts = data["tool"]["pytest"]["ini_options"].get("addopts", "")
    assert not _COV_FLAG.search(addopts), addopts


def test_the_regression_mode_still_runs_the_suite_it_is_for(gate):
    """Guard on the thing the gate does do, so "no --cov" is not read as "no run"."""
    argv = gate.build_command(False)
    assert argv[1:4] == ["-m", "pytest", "-q"]
    assert "not acceptance" in argv


def test_the_acceptance_mode_still_selects_the_sign_off_gates(gate):
    argv = gate.build_command(True)
    # `-m` appears twice: `python -m pytest`, then pytest's own marker flag.
    assert argv[len(argv) - 1 - argv[::-1].index("-m") + 1] == "acceptance"


def test_the_documented_ratchet_matches_the_gate_that_enforces_it(gate):
    """COVERAGE_FLOOR calls itself "the documented home of the ratchet: raise it
    here, and raise the --cov-fail-under in ci.yml in the same change" - and then
    says nothing reads it. This is what reads it."""
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    found = re.findall(r"--cov-fail-under=(\d+)", ci)
    assert found, "no --cov-fail-under in ci.yml: the floor moved and this is stale"
    assert {int(v) for v in found} == {gate.COVERAGE_FLOOR}
