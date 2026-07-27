"""The attestation records what configured the interpreter, and compares it.

A plugin's comparable identity is DERIVED from the module it came from, never
taken from its pytest registration name. Task 1 measured the raw names to be
uncomparable across processes: pytest registers an anonymous plugin under
`str(id(plugin))`, a conftest plugin's registration name is its absolute path,
and the freeze probe runs a different topology from the gate. A comparison over
raw names refuses every honest run.

The refusal therefore compares, in both directions, every `dist:` identity plus
every `intree:` identity whose origin is not a `conftest.py`. That is a property
rather than a list of channels: three revisions enumerated channels and review
found an escape past each in turn. An in-tree conftest and every `anon:` entry
are recorded as provenance beside it, each for a reason stated where the
partition is built.
"""
import os
import sysconfig
import types
from pathlib import Path

import pytest

from scripts.utils import canopus_gate
from scripts.utils.canopus_gate import process_facts

BASELINE = frozenset({"conftest", "xdist", "pytest_cov"})


class _Config:
    """Duck-typed stand-in for pytest's config; no pytest import here either."""

    def __init__(self, plugins=(), option_plugins=(), argv=()):
        self._plugins = list(plugins)
        self.option = type("O", (), {"plugins": list(option_plugins)})()
        self.invocation_params = type("P", (), {"args": tuple(argv)})()
        self.pluginmanager = self

    def list_name_plugin(self):
        return self._plugins


def _module(name, module_file=None):
    """A module plugin, which is what pytest registers for a plugin FILE.

    A real module object rather than a bare namespace: the identity is derived
    from the module the plugin came from, so a stand-in that is not a module
    tests a path the real recorder never takes.
    """
    module = types.ModuleType(name)
    if module_file is not None:
        module.__file__ = module_file
    return module


def _instance(module_name):
    """A plugin OBJECT, which is what pytest registers for xdist's DSession.

    An instance carries no `__file__` at all, so its distribution can only be
    read from the class that defined it. `json.decoder` stands in for a real
    imported module because a CLAIMED module name is only believed when
    something of that name was imported; `None` and `__channelexec__` are the
    two ways that claim fails.
    """
    return type("Plugin", (), {"__module__": module_name})()


@pytest.fixture(autouse=True)
def _no_ambient_plugin_environment(monkeypatch):
    """No test here inherits a PYTEST_PLUGINS the session happened to carry.

    A fixture rather than a `delenv` per test, because per-test hermeticity is
    remembered by the tests that already have it and forgotten by the next one
    written. This module reads the environment (`env_configured`, and every
    plugin-loading route that goes through it), so an ambient value would decide
    an assertion silently. The one test that is ABOUT the variable sets it
    itself, after this has cleared it.
    """
    monkeypatch.delenv("PYTEST_PLUGINS", raising=False)


# ============================================================
# The identity is derived from the origin, not from the name
# ============================================================


def test_a_plugin_from_the_interpreter_library_is_named_by_its_distribution(
        tmp_path, monkeypatch):
    """The library sits INSIDE the tree, because that is the real geometry.

    `.venv/` lives under the working tree, so "under root" alone marks every
    installed plugin as in-tree and the whole field says nothing. This test's
    first shape planted the origin under the REAL purelib while passing
    `root=tmp_path`: the origin was outside the root, `_intree_rel` answered at
    the root check, and the library loop never ran. It held identically with
    `_LIBRARY_DIRS` and its loop deleted, which is no test of them at all.

    Two plugins, one exclusion: the installed one is under a library directory
    inside the root and reads `dist:`, the other is a plain file beside it and
    reads `intree:`. Both are compared — a non-conftest in-tree plugin is
    compared under the property rule — so the assertion below is about which
    IDENTITY each one gets, which is what the library exclusion decides.
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
    config = _Config([("pytest_cov", _module("pytest_cov.plugin", str(installed))),
                      ("plug", _module("plug", str(own)))])

    facts = process_facts(config, tmp_path)

    assert facts["plugins"] == {"dist:pytest_cov": str(installed),
                                "intree:plug.py": str(own)}
    assert facts["intree_plugins"] == []


def _intree_plugin(tmp_path):
    origin = tmp_path / "plug" / "skipper.py"
    origin.parent.mkdir(parents=True, exist_ok=True)
    origin.write_text("def pytest_pyfunc_call(pyfuncitem):\n    return True\n")
    return origin


def test_an_in_tree_conftest_is_recorded_and_never_compared(tmp_path):
    """The one in-tree case that survives, pinned to the reason it survives.

    Which conftests load depends on what is COLLECTED, so the freeze probe (the
    contract directory) and the gate run (the whole suite) legitimately differ,
    and comparing them would refuse every honest run. A conftest also cannot
    reach the frozen contract's own results without moving frozen bytes:
    `tests/conftest.py` is in the `--content` set and everything inside the
    contract directory is frozen recursively.

    That reasoning is about conftests specifically, which is why every OTHER
    in-tree plugin is compared: it can only be there because something named it.
    """
    origin = tmp_path / "tests" / "conftest.py"
    origin.parent.mkdir(parents=True)
    origin.write_text("def pytest_pyfunc_call(pyfuncitem):\n    return None\n")
    config = _Config([(str(origin), _module("conftest", str(origin)))])

    facts = process_facts(config, tmp_path)

    assert facts["intree_plugins"] == ["tests/conftest.py"]
    assert facts["plugins"] == {}


def test_an_in_tree_plugin_no_channel_named_is_still_compared(tmp_path):
    """The third route, and the reason the rule stopped enumerating routes.

    `pytest_plugins = ["plug.skipper"]` declared in a test module reaches
    `_import_plugin_specs` through `consider_module`, which `_pytest/python.py`
    calls on every imported test module. It arrives through neither `-p` nor
    PYTEST_PLUGINS. And `GUARD_NAMES_ANCESTOR` watches only `conftest.py`, so a
    NEW `tests/test_aaa_evil.py` carrying that line moves no byte the freeze
    notices. Measured: it registered the in-tree skipper, hijacked the run, and
    landed in provenance while the record attested.

    So the rule is a PROPERTY: a non-conftest in-tree plugin is compared however
    it arrived, including by a route nobody has found yet. This test names no
    channel, which is the point of it.
    """
    origin = _intree_plugin(tmp_path)
    config = _Config([("plug.skipper", _module("plug.skipper", str(origin)))])

    facts = process_facts(config, tmp_path)

    assert facts["plugins"] == {"intree:plug/skipper.py": str(origin)}
    assert facts["intree_plugins"] == []


def test_an_in_tree_plugin_a_flag_named_joins_the_compared_set(tmp_path):
    """The hole review found in spec 1.3a, and the amendment that closes it.

    `-p plug.skipper` loads a module regardless of what is collected, and
    `_guard_ancestors` guards only the ancestors of FROZEN paths, so the module
    can sit in an existing unguarded directory and move no frozen byte. It was
    registered `intree:`, excused from the comparison, skipped every test in the
    run, and the record attested. Measured on pytest 9.1.1, which registers such
    a plugin under the `-p` spec itself.
    """
    origin = _intree_plugin(tmp_path)
    config = _Config([("plug.skipper", _module("plug.skipper", str(origin)))],
                     option_plugins=["plug.skipper"])

    facts = process_facts(config, tmp_path)

    assert facts["plugins"] == {"intree:plug/skipper.py": str(origin)}
    assert facts["intree_plugins"] == []


def test_an_in_tree_plugin_the_environment_named_joins_the_compared_set(
        tmp_path, monkeypatch):
    """PYTEST_PLUGINS is the same escape one channel over, and it was open.

    Read in the installed pytest: `consider_env()` hands the variable to
    `_import_plugin_specs` -> `import_plugin` -> `register(mod, modname)`, a path
    that touches `config.option.plugins` nowhere. Measured on a real session:
    `PYTEST_PLUGINS=plug.skipper` registered the in-tree module under
    `plug.skipper`, `option.plugins` carried only what `-p` gave it, the module
    skipped every test in the run, and the record attested. Explicitly named and
    collection-independent is the side of spec 1.3a that gets compared, whichever
    channel names it.
    """
    monkeypatch.setenv("PYTEST_PLUGINS", "plug.skipper")
    origin = _intree_plugin(tmp_path)
    config = _Config([("plug.skipper", _module("plug.skipper", str(origin)))])

    facts = process_facts(config, tmp_path)

    assert facts["plugins"] == {"intree:plug/skipper.py": str(origin)}
    assert facts["intree_plugins"] == []


def test_a_flag_naming_a_distribution_changes_nothing(tmp_path, monkeypatch):
    """The contract's own case: `-p` on a plugin the freeze recorded is silent.

    `option_plugins=["xdist"]` must keep attesting, which is why `-p` is never
    banned: the only measured cure for the entry-point route is
    PYTEST_DISABLE_PLUGIN_AUTOLOAD plus an explicit `-p` per allowed plugin.
    """
    library = tmp_path / ".venv" / "lib" / "site-packages"
    (library / "xdist").mkdir(parents=True)
    installed = library / "xdist" / "plugin.py"
    installed.write_text("# an installed distribution plugin\n")
    monkeypatch.setattr(
        canopus_gate, "_LIBRARY_DIRS", (*canopus_gate._LIBRARY_DIRS, library.resolve())
    )
    config = _Config([("xdist", _module("xdist.plugin", str(installed)))],
                     option_plugins=["xdist"])

    facts = process_facts(config, tmp_path)

    assert facts["plugins"] == {"dist:xdist": str(installed)}
    assert facts["intree_plugins"] == []


def test_many_registrations_from_one_distribution_collapse_to_one_identity(tmp_path):
    """Measured: sixty-six raw names collapse to seven identities.

    Forty-six of them are `_pytest` builtins registered under their own short
    names, and seventeen are memory addresses that differ between every process
    and every run. Both fold into the distribution that created them, which is
    what makes the set comparable across two processes at all.
    """
    config = _Config([
        ("terminal", _module("_pytest.terminal", "/venv/_pytest/terminal.py")),
        ("python", _module("_pytest.python", "/venv/_pytest/python.py")),
        ("dsession", _instance("json.decoder")),
        ("137890894547472", _instance("json.encoder")),
    ])

    facts = process_facts(config, tmp_path)

    assert sorted(facts["plugins"]) == ["dist:_pytest", "dist:json"]


def test_an_anonymous_plugin_is_recorded_as_provenance_and_never_compared(tmp_path):
    """An address with no readable module is downstream of a plugin that has one.

    It was created in-process by something already loaded, so the comparison
    sees its creator. Recorded so a human reading the record can see it was
    there; not compared, because its name is a memory address.
    """
    facts = process_facts(_Config([("137890894547472", _instance(None))]), tmp_path)

    assert facts["plugins"] == {}
    assert facts["other_plugins"] == ["anon:unresolved"]


def test_a_module_name_nothing_imported_is_not_a_distribution(tmp_path):
    """The xdist WorkerInteractor, measured on a real `-n 4` run of this suite.

    Every worker registers it under `str(id(plugin))`, and its class claims the
    module `__channelexec__` — execnet's synthetic namespace for source sent
    down a channel, which nothing ever imported. Believing the claim gave every
    worker a `dist:` entry no controller and no freeze probe could ever carry,
    so every honest parallel run refused. That is the failure this whole design
    ordering exists to catch, and it caught it a second time.
    """
    facts = process_facts(_Config([("128309483228752",
                                    _instance("__channelexec__"))]), tmp_path)

    assert facts["plugins"] == {}
    assert facts["other_plugins"] == ["anon:unresolved"]


def test_a_blocked_plugin_name_is_never_a_distribution(tmp_path):
    """`-p no:cacheprovider` registers the name with the plugin `None`.

    It names a plugin that is NOT loaded. Reading `type(None).__module__` gave
    it a distribution called `builtins`, which the freeze probe then carried
    (it passes `-p no:cacheprovider`) and the gate run did not.
    """
    facts = process_facts(_Config([("cacheprovider", None)]), tmp_path)

    assert facts["plugins"] == {}
    assert facts["other_plugins"] == ["name:cacheprovider"]


def test_a_class_registered_as_a_plugin_reads_its_own_module(tmp_path):
    """pytest registers `legacypath-tmpdir` as a class, not as an instance.

    `type(a class).__module__` is `builtins` for every class there is, so the
    class's own `__module__` is the only reading that names its distribution.
    """
    plugin = type("TmpdirPlugin", (), {"__module__": "json.decoder"})

    facts = process_facts(_Config([("legacypath-tmpdir", plugin)]), tmp_path)

    assert sorted(facts["plugins"]) == ["dist:json"]


def test_an_unresolvable_plugin_keeps_its_registration_name(tmp_path):
    """The last row of the table: not in the tree, no module, not an address."""
    facts = process_facts(_Config([("skipper", _instance(None))]), tmp_path)

    assert facts["plugins"] == {}
    assert facts["other_plugins"] == ["name:skipper"]


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


def test_locating_the_interpreter_libraries_never_raises(monkeypatch, capsys):
    """This one is cached at module IMPORT, and no import of it sits in a handler.

    tests/conftest.py imports `freeze_gate` from inside `pytest_sessionstart`,
    which has no handler, and run-tests.py imports at its top level. A raise here
    therefore does not fail open the way `freeze_gate` deliberately does: it
    kills the session with an internal error before any gate reports a state.
    Degrading to `()` marks more plugins in-tree, which is the conservative
    direction for the field that separates `intree:` from `dist:`.
    """
    def _explode(*args, **kwargs):
        raise RuntimeError("no such scheme on this interpreter")

    monkeypatch.setattr(sysconfig, "get_paths", _explode)

    assert canopus_gate._library_dirs() == ()
    assert "library directories could not be located" in capsys.readouterr().err


def test_the_interpreter_libraries_are_really_located():
    """The real computation, which every other test of it monkeypatches away.

    The exclusion test above replaces `_LIBRARY_DIRS` with a directory of its
    own and the never-raises test forces a raise, so an edit that made the real
    computation return `()` or read the wrong sysconfig keys left both of them
    green. In this repository's geometry `.venv/` sits UNDER the tree root, so
    that edit would make every installed plugin read as `intree:` and drop the
    whole compared set silently.
    """
    dirs = canopus_gate._library_dirs()

    assert dirs
    assert Path(sysconfig.get_paths()["purelib"]).resolve() in dirs
    # The stdlib key as well, and not for symmetry: `site.getsitepackages()`
    # already answers for purelib on this interpreter, so purelib alone stays
    # green when the sysconfig loop is emptied. The stdlib directory is reached
    # by that loop and by nothing else.
    assert Path(sysconfig.get_paths()["stdlib"]).resolve() in dirs


def test_the_launcher_is_recorded_as_provenance(tmp_path, monkeypatch):
    monkeypatch.delenv("CANOPUS_LAUNCHER", raising=False)
    assert process_facts(_Config(), tmp_path)["launcher"] == "bare"

    monkeypatch.setenv("CANOPUS_LAUNCHER", "run-tests")
    assert process_facts(_Config(), tmp_path)["launcher"] == "run-tests"


# ============================================================
# The refusal: a delta against the plugin set the freeze recorded
# ============================================================


def _facts(**overrides):
    facts = {"plugins": {"conftest": "tests/conftest.py",
                         "xdist": "/venv/xdist/plugin.py",
                         "pytest_cov": "/venv/pytest_cov/plugin.py"},
             "intree_plugins": ["tests/conftest.py"], "option_plugins": [],
             "env_configured": ["PYTEST_VERSION"], "launcher": "run-tests",
             "workers": []}
    facts.update(overrides)
    return facts


def _record(process, plugin_baseline=BASELINE):
    from scripts.utils.canopus_freeze import build_attestation

    return build_attestation(
        root_digest="d" * 64,
        frozen_tests={"tests/contract/s/test_a.py": {
            "collected": 2, "passed": 2, "skipped": 0, "failed": 0, "deselected": 0}},
        exit_status=0, attested_at="2026-07-27T00:00:00+00:00",
        baseline={"tests/contract/s/test_a.py": 2},
        process=process, plugin_baseline=plugin_baseline)


def test_an_honest_run_still_attests():
    assert _record(_facts())["attested"] is True


def test_a_plugin_the_freeze_did_not_record_refuses():
    poisoned = _facts(plugins=dict(_facts()["plugins"], skipper="/venv/evil/skipper.py"))

    record = _record(poisoned)

    assert record["attested"] is False
    assert any("skipper" in reason for reason in record["reasons"])


def test_a_plugin_that_vanished_refuses():
    thinned = {k: v for k, v in _facts()["plugins"].items() if k != "pytest_cov"}

    record = _record(_facts(plugins=thinned))

    assert record["attested"] is False
    assert any("pytest_cov" in reason for reason in record["reasons"])


def test_a_recorded_plugin_reaching_by_flag_refuses_nothing():
    """-p is never banned: the cure for entry-point plugins needs it."""
    assert _record(_facts(option_plugins=["xdist"]))["attested"] is True


def test_a_worker_whose_plugins_differ_refuses():
    split = _facts(workers=[["conftest", "xdist", "pytest_cov"],
                            ["conftest", "xdist", "pytest_cov", "skipper"]])

    record = _record(split)

    assert record["attested"] is False
    assert any("worker" in reason.lower() for reason in record["reasons"])


def test_workers_that_agree_refuse_nothing():
    agreed = _facts(workers=[["conftest", "xdist", "pytest_cov"],
                             ["pytest_cov", "conftest", "xdist"]])

    assert _record(agreed)["attested"] is True


def test_no_plugin_baseline_attests_nothing():
    record = _record(_facts(), plugin_baseline=None)

    assert record["attested"] is False
    assert any("baseline" in reason for reason in record["reasons"])


def test_a_missing_process_block_reads_as_damage():
    record = _record(None)

    assert record["attested"] is False
    assert any("process" in reason for reason in record["reasons"])


def test_the_recipe_moved_so_old_records_stop_applying():
    from scripts.utils.canopus_freeze import ATTEST_RECIPE, attestation_state, NOT_ATTESTED

    assert ATTEST_RECIPE == "canopus-attest-v2"
    state, reason = attestation_state(
        {"recipe": "canopus-attest-v1", "root": "d" * 64, "attested": True}, "d" * 64)
    assert state == NOT_ATTESTED
    assert "recipe" in reason
