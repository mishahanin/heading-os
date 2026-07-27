"""The frozen runner hands its child an environment it chose, not one it inherited."""
import importlib.util
import os
import sys
from pathlib import Path

import pytest

from scripts.utils.venv import venv_python

# run-tests.py calls ensure_venv() at import, and ensure_venv() calls os.execv
# when the running interpreter is not .venv/bin/python. Importing this module
# from a pytest launched on the system interpreter would therefore REPLACE the
# pytest process mid-collection with `python tests/test_run_tests_env.py`, which
# exits silently green. canopus_gate.py's module docstring already states that
# run-tests.py "is not safely importable from a test"; this guard is what makes
# the exception to that statement safe rather than lucky.
if Path(sys.executable).resolve() != venv_python().resolve():
    pytest.skip("run-tests.py re-execs at import; importable only under .venv",
                allow_module_level=True)

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("run_tests", ROOT / "scripts" / "run-tests.py")
run_tests = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(run_tests)


@pytest.mark.parametrize("name", [
    "PYTEST_ADDOPTS",
    "PYTEST_PLUGINS",
    "PYTEST_DISABLE_PLUGIN_AUTOLOAD",
    "PYTEST_CURRENT_TEST",
    "PYTEST_ANYTHING_AT_ALL",
])
def test_no_pytest_variable_reaches_the_child(name, monkeypatch):
    """Parametrized per variable on purpose.

    Wire 2.2 shipped a gate-level assertion that could not fail because it only
    ever asserted the gate's colour, and its retirement had to replace it with an
    equality per variable. A two-name denylist must fail this test.
    """
    monkeypatch.setenv(name, "-p plug.skipper")

    assert name not in run_tests.child_env()


def test_the_rest_of_the_environment_survives(monkeypatch):
    monkeypatch.setenv("HEADING_OS_TZ", "Etc/GMT-4")

    assert run_tests.child_env()["HEADING_OS_TZ"] == "Etc/GMT-4"


def test_the_child_is_stamped_as_launched_by_the_runner():
    assert run_tests.child_env()["CANOPUS_LAUNCHER"] == "run-tests"
