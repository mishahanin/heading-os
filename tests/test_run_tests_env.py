"""The frozen runner hands its child an environment it chose, not one it inherited."""
import importlib.util
import sys
from pathlib import Path

import pytest

from scripts.utils.venv import venv_python

# run-tests.py calls ensure_venv() at import, and ensure_venv() calls os.execv
# when the running interpreter is not .venv/bin/python. Importing this module
# from a pytest launched on the system interpreter would therefore REPLACE the
# pytest process mid-collection, and the replacement exits silently green.
#
# WHEN that can happen is narrower than it reads, and is stated because the
# narrowing is what makes this guard look redundant to a later reader. The root
# tests/conftest.py sets venv._SENTINEL at import, before any test module loads,
# so under an ordinary run ensure_venv() is already a no-op here. The exposed
# case is a run where that conftest never loads. Measured on 2026-07-27 with the
# system interpreter: `pytest -q --noconftest tests/test_run_tests_env.py`
# prints `1 skipped` and exits 5 (this guard firing), while the same command on
# the unguarded tests/test_run_tests_runner.py prints ZERO bytes and exits 0.
# The re-exec is `[venv_python, sys.argv[0], ...]`, and under pytest sys.argv[0]
# is the pytest entry point rather than this file.
#
# run-tests.py is not safely importable from a test, for exactly that reason;
# this guard is what makes the exception to that statement safe rather than
# lucky.
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


def test_the_runner_hands_that_environment_to_the_child():
    """The call site, which every other test in this file leaves unguarded.

    child_env() can be perfect and unused: deleting `env=child_env()` from the
    one subprocess.run call keeps all seven tests above green and all five frozen
    contract cases green too, while the child silently inherits PYTEST_ADDOPTS
    again. This is the only assertion that fails when the wire is cut.

    Source inspection rather than an import, for the reason the module comment
    above gives: run-tests.py calls ensure_venv() at import time. The
    match is scoped to the call statement rather than the whole file because a
    whole-file substring is satisfiable by a docstring that merely mentions the
    keyword. That scoping assumes the call stays on one line; if it is ever
    wrapped, widen the slice rather than dropping back to a file-wide search.
    """
    source = (ROOT / "scripts" / "run-tests.py").read_text(encoding="utf-8")
    call = source[source.index("subprocess.run("):]
    call = call[:call.index("\n")]

    assert "env=child_env()" in call
