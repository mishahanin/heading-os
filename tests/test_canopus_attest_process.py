"""The attestation records what configured the interpreter, not only what ran."""
import os
from pathlib import Path

from scripts.utils import canopus_gate
from scripts.utils.canopus_gate import process_facts


class _Config:
    """Duck-typed stand-in for pytest's config; no pytest import here either."""

    def __init__(self, plugins=(), option_plugins=(), argv=()):
        self._plugins = list(plugins)
        self.option = type("O", (), {"plugins": list(option_plugins)})()
        self.invocation_params = type("P", (), {"args": tuple(argv)})()
        self.pluginmanager = self

    def list_name_plugin(self):
        return self._plugins


def _plugin(module_file):
    return type("M", (), {"__file__": module_file})()


def test_a_plugin_from_the_interpreter_library_is_not_in_tree(tmp_path, monkeypatch):
    """The library sits INSIDE the tree, because that is the real geometry.

    `.venv/` lives under the working tree, so "under root" alone marks every
    installed plugin as in-tree and the whole field says nothing. This test's
    first shape planted the origin under the REAL purelib while passing
    `root=tmp_path`: the origin was outside the root, `_intree_rel` answered at
    the root check, and the library loop never ran. It held identically with
    `_LIBRARY_DIRS` and its loop deleted, which is no test of them at all.

    Two plugins, one exclusion: the installed one is under a library directory
    inside the root, the other is a plain file beside it.
    """
    library = tmp_path / ".venv" / "lib" / "site-packages"
    (library / "pytest_cov").mkdir(parents=True)
    installed = library / "pytest_cov" / "plugin.py"
    installed.write_text("# an installed distribution plugin\n")
    own = tmp_path / "plug.py"
    own.write_text("def pytest_pyfunc_call(pyfuncitem):\n    return True\n")
    monkeypatch.setattr(
        canopus_gate, "_LIBRARY_DIRS", (*canopus_gate._LIBRARY_DIRS, library.resolve())
    )
    config = _Config([("pytest_cov", _plugin(str(installed))),
                      ("plug", _plugin(str(own)))])

    facts = process_facts(config, tmp_path)

    assert facts["plugins"]["pytest_cov"] == str(installed)
    assert facts["intree_plugins"] == ["plug.py"]


def test_a_plugin_from_the_working_tree_is_marked_as_in_tree(tmp_path):
    origin = tmp_path / "plug" / "skipper.py"
    origin.parent.mkdir(parents=True)
    origin.write_text("def pytest_pyfunc_call(pyfuncitem):\n    return True\n")
    config = _Config([("skipper", _plugin(str(origin)))])

    facts = process_facts(config, tmp_path)

    assert facts["intree_plugins"] == ["plug/skipper.py"]


def test_the_parsed_option_is_recorded_not_argv(tmp_path):
    """PYTEST_ADDOPTS and an ini `addopts` never reach invocation_params.args."""
    config = _Config(option_plugins=["plug.skipper"], argv=("-q", "tests/"))

    assert process_facts(config, tmp_path)["option_plugins"] == ["plug.skipper"]


def test_the_pytest_environment_is_recorded_by_name_never_by_value(tmp_path, monkeypatch):
    # The process running this test is itself a pytest session, and pytest sets
    # PYTEST_VERSION for the whole session and PYTEST_CURRENT_TEST around every
    # phase. Both are recorded, correctly and by design: this field records the
    # PYTEST_ environment as it stands, and filtering pytest's own names out
    # would be a denylist over the exact surface the field exists to describe.
    # The equality below is about what the RECORDER does with two names, so the
    # environment it reads is made hermetic here rather than the recorder taught
    # to look away. Measured on pytest 9.1.1, not assumed.
    for name in [key for key in os.environ if key.startswith("PYTEST_")]:
        monkeypatch.delenv(name)
    monkeypatch.setenv("PYTEST_ADDOPTS", "-p plug.skipper")
    monkeypatch.setenv("PYTEST_PLUGINS", "plug.skipper")

    facts = process_facts(_Config(), tmp_path)

    assert facts["env_configured"] == ["PYTEST_ADDOPTS", "PYTEST_PLUGINS"]
    assert "plug.skipper" not in repr(facts), "a value can carry a token; only names are recorded"


def test_a_plugin_with_no_resolvable_origin_is_recorded_as_null(tmp_path):
    facts = process_facts(_Config([("builtin", object())]), tmp_path)

    assert facts["plugins"]["builtin"] is None


def test_locating_the_interpreter_libraries_never_raises(monkeypatch, capsys):
    """This one is cached at module IMPORT, and no import of it sits in a handler.

    tests/conftest.py imports `freeze_gate` from inside `pytest_sessionstart`,
    which has no handler, and run-tests.py imports at its top level. A raise here
    therefore does not fail open the way `freeze_gate` deliberately does: it
    kills the session with an internal error before any gate reports a state.
    Degrading to `()` marks more plugins in-tree, which is the conservative
    direction for a field nothing judges yet.
    """
    import sysconfig

    def _explode(*args, **kwargs):
        raise RuntimeError("no such scheme on this interpreter")

    monkeypatch.setattr(sysconfig, "get_paths", _explode)

    assert canopus_gate._library_dirs() == ()
    assert "library directories could not be located" in capsys.readouterr().err


def test_the_launcher_is_recorded_as_provenance(tmp_path, monkeypatch):
    monkeypatch.delenv("CANOPUS_LAUNCHER", raising=False)
    assert process_facts(_Config(), tmp_path)["launcher"] == "bare"

    monkeypatch.setenv("CANOPUS_LAUNCHER", "run-tests")
    assert process_facts(_Config(), tmp_path)["launcher"] == "run-tests"
