"""Frozen contract for Canopus wire 2.3: binding the interpreter.

Written before any implementation exists, frozen on operator approval, retired
when the slice ships. Every import of the code under test happens INSIDE a test
body: at freeze time the functions these tests call do not accept the arguments
they are called with, and a module-scope import would stop the file collecting
at all. A file that collects nothing cannot be frozen.

What this holds the slice to. The lock binds the repository and the attestation
says the contract ran. Between them sits the pytest process, and nothing binds
it: one environment variable and a two-line untracked plugin make every frozen
test report passed without executing, and the tool prints LOCK HELD, ATTESTED
and APPROVED.

The refusal is a COMPARISON against a plugin set captured at freeze time, not a
list of blocked routes. The first design listed three routes and the
architecture council measured a fourth that satisfied all of them: a `pytest11`
entry-point plugin sets no variable, passes no `-p`, and lives in site-packages.
A design that enumerates routes is a denylist wearing a different hat, and this
codebase has now produced that defect eleven times.

The happy-path cases come first and are load-bearing. Without them an
implementation that refuses EVERY record satisfies every refusal test here.
"""
import pytest

STAMP = "2026-07-27T00:00:00+00:00"
DIGEST = "d" * 64
FROZEN_TESTS = {"tests/contract/s/test_a.py": {
    "collected": 2, "passed": 2, "skipped": 0, "failed": 0, "deselected": 0}}
# What an honest run of this repository loads: its own conftest plugins and the
# distribution plugins the gate runs under. Names only; see the origin note below.
BASELINE = frozenset({"conftest", "xdist", "pytest_cov"})


def _process(**overrides):
    """A description of an honest run, minus whatever the caller poisons."""
    facts = {
        "plugins": {"conftest": "tests/conftest.py",
                    "xdist": "/venv/xdist/plugin.py",
                    "pytest_cov": "/venv/pytest_cov/plugin.py"},
        # The PARSED option, which is the only reading that sees all of argv,
        # PYTEST_ADDOPTS and an ini `addopts`. Recorded as provenance; the
        # refusal is the plugin delta, so this decides nothing on its own.
        "option_plugins": [],
        "env_configured": ["PYTEST_VERSION"],
        "launcher": "run-tests",
        # One entry per xdist worker: the plugin names that worker loaded.
        "workers": [],
    }
    facts.update(overrides)
    return facts


def _record(process, plugin_baseline=BASELINE):
    from scripts.utils.canopus_freeze import build_attestation

    return build_attestation(
        root_digest=DIGEST,
        frozen_tests=FROZEN_TESTS,
        exit_status=0,
        attested_at=STAMP,
        baseline={"tests/contract/s/test_a.py": 2},
        process=process,
        plugin_baseline=plugin_baseline,
    )


# ============================================================
# The refusal is a comparison, not a list of routes
# ============================================================


def test_an_honest_run_still_attests():
    """SC-1. First on purpose: it is what an always-refusing shortcut fails.

    Every refusal test below is satisfied by `attested = False`. This one is not,
    so the pair says "refuse the poisoned process AND only that one". Wire 2.2
    shipped a contract without this asymmetry and had to add it before freezing.
    """
    assert _record(_process())["attested"] is True


def test_a_plugin_the_freeze_did_not_record_cannot_attest():
    """SC-2. The whole defeat family, closed by one comparison.

    An entry-point plugin, a `-p` on argv, a `-p` inside PYTEST_ADDOPTS and a
    `-p` inside an ini `addopts` differ in how they arrive and not at all in what
    they leave behind: a name the freeze never saw.
    """
    poisoned = _process(plugins={"conftest": "tests/conftest.py",
                                 "xdist": "/venv/xdist/plugin.py",
                                 "pytest_cov": "/venv/pytest_cov/plugin.py",
                                 "skipper": "/venv/evil/skipper.py"})

    record = _record(poisoned)

    assert record["attested"] is False
    assert any("skipper" in reason for reason in record["reasons"])


def test_a_recorded_plugin_refuses_nothing():
    """SC-3, and the reason `-p` never needs banning.

    The first design refused any `-p`, which forbade
    PYTEST_DISABLE_PLUGIN_AUTOLOAD plus an explicit `-p` per allowed plugin, the
    only measured cure for the entry-point route. A comparison has no such
    conflict: xdist is in the baseline whether it arrived by entry point or by
    flag.
    """
    record = _record(_process(option_plugins=["xdist"]))

    assert record["attested"] is True


def test_a_plugin_that_vanished_from_the_run_cannot_attest():
    """A missing plugin is a changed interpreter too.

    Dropping pytest-cov changes what the run measured, and a comparison that only
    looked for ADDITIONS would call that honest. Re-freezing is the answer when
    the change is deliberate.
    """
    thinned = _process(plugins={"conftest": "tests/conftest.py",
                                "xdist": "/venv/xdist/plugin.py"})

    record = _record(thinned)

    assert record["attested"] is False
    assert any("pytest_cov" in reason for reason in record["reasons"])


def test_a_worker_whose_plugins_differ_cannot_attest():
    """SC-4. Under -n auto the controller records and the WORKERS execute.

    A controller-side reading describes an interpreter that ran nothing, so a
    plugin injected into the workers alone would pass a controller-only check.
    """
    split = _process(workers=[["conftest", "xdist", "pytest_cov"],
                              ["conftest", "xdist", "pytest_cov", "skipper"]])

    record = _record(split)

    assert record["attested"] is False
    assert any("worker" in reason.lower() for reason in record["reasons"])


def test_workers_that_agree_with_the_controller_refuse_nothing():
    """The other half of SC-4: the ordinary -n auto run must stay green."""
    agreed = _process(workers=[["conftest", "xdist", "pytest_cov"],
                               ["pytest_cov", "conftest", "xdist"]])

    assert _record(agreed)["attested"] is True


def test_a_freeze_with_no_plugin_baseline_attests_nothing():
    """SC-7. The rule build_attestation already applies to a freeze with no tests.

    Absence is not innocence: with nothing to compare against, every plugin set
    is equally acceptable, which is the state this slice exists to end.
    """
    record = _record(_process(), plugin_baseline=None)

    assert record["attested"] is False
    assert any("baseline" in reason for reason in record["reasons"])


def test_a_missing_process_block_reads_as_damage():
    """SC-7. An unrecorded process cannot be vouched for."""
    record = _record(None)

    assert record["attested"] is False
    assert any("process" in reason for reason in record["reasons"])


def test_the_recipe_moved_so_older_records_stop_applying():
    """SC-7. A v1 record predates the process block and must not be trusted."""
    from scripts.utils.canopus_freeze import (ATTEST_RECIPE, NOT_ATTESTED,
                                              attestation_state)

    assert ATTEST_RECIPE == "canopus-attest-v2"
    state, reason = attestation_state(
        {"recipe": "canopus-attest-v1", "root": DIGEST, "attested": True}, DIGEST)
    assert state == NOT_ATTESTED
    assert "recipe" in reason


def test_the_recorded_options_come_from_the_parsed_config_not_argv():
    """The one word the council's measurement turned on.

    Measured on pytest 9.1.1: `-p plug.skipper` on argv, the same inside
    PYTEST_ADDOPTS, and the same inside an ini `addopts` ALL reach
    `config.option.plugins`, while only the argv spelling appears in
    `invocation_params.args`. A reader written against argv sees one channel in
    three, and the failing test reports `2 passed` through all of them.
    """
    from pathlib import Path

    # Reached as a module ATTRIBUTE, never as `from ... import process_facts`.
    # An ImportError names its module, `missing_modules` reads that name out of
    # the report, and the vacuity run then mocks the enforcer that the freeze
    # gate itself imports: measured while writing this contract, the whole probe
    # died with "the freeze gate is red" and nothing could be measured. An
    # AttributeError names no module and leaves the prober alone.
    from scripts.utils import canopus_gate

    class _Config:
        option = type("O", (), {"plugins": ["plug.skipper"]})()
        invocation_params = type("P", (), {"args": ("-q", "tests/")})()
        pluginmanager = type("M", (), {"list_name_plugin": staticmethod(list)})()

    facts = canopus_gate.process_facts(_Config(), Path("."))

    assert facts["option_plugins"] == ["plug.skipper"]


# ============================================================
# The frozen runner chooses its child's environment
# ============================================================


@pytest.mark.parametrize("name", [
    "PYTEST_ADDOPTS",
    "PYTEST_PLUGINS",
    "PYTEST_DISABLE_PLUGIN_AUTOLOAD",
    "PYTEST_CURRENT_TEST",
    "PYTEST_ANYTHING_AT_ALL",
])
def test_no_pytest_variable_reaches_the_child(name, monkeypatch):
    """SC-5. Parametrized per variable, and that is the point.

    Wire 2.2 shipped a gate-level assertion that could not fail because it only
    asserted the gate's colour; its retirement had to replace it with an equality
    per variable, after which a two-name denylist failed two cases it had passed.
    A denylist must fail this test, not squeak past it.
    """
    import importlib.util
    from pathlib import Path

    root = Path(__file__).resolve().parents[3]
    spec = importlib.util.spec_from_file_location(
        "run_tests_contract", root / "scripts" / "run-tests.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    monkeypatch.setenv(name, "-p plug.skipper")

    assert name not in module.child_env()


# ============================================================
# The root composition sees what can shadow an import
# ============================================================


def _tree_with_one_test(root):
    target = root / "tests" / "test_alpha.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("def test_a():\n    assert True\n", encoding="utf-8")
    return target


def test_a_package_directory_at_the_root_joins_the_composition(tmp_path):
    """SC-6. pythonpath = ["."] makes the tree root the first sys.path entry.

    A directory dropped there shadows an import while every frozen byte stays
    intact, the hole stated in canopus_freeze.py's own comment. Measured before
    this contract was written: this case reports held True today.
    """
    from scripts.utils.canopus_freeze import build_manifest, verify_manifest

    target = _tree_with_one_test(tmp_path)
    manifest = build_manifest([target], tmp_path, label="l", frozen_at=STAMP)
    (tmp_path / "plug").mkdir()

    report = verify_manifest(manifest, tmp_path)

    assert report["held"] is False
    assert report["added"] == ["plug/"]


def test_a_directory_that_cannot_be_imported_does_not(tmp_path):
    """SC-6's other edge, and green before the implementation.

    Here because the fix's failure mode is over-reach: a guard that reddens on
    every new top-level directory is one an operator learns to release around.
    """
    from scripts.utils.canopus_freeze import build_manifest, verify_manifest

    target = _tree_with_one_test(tmp_path)
    manifest = build_manifest([target], tmp_path, label="l", frozen_at=STAMP)
    (tmp_path / "docs-2").mkdir()

    assert verify_manifest(manifest, tmp_path)["held"] is True


def test_a_generated_cache_directory_does_not_redden(tmp_path):
    """`__pycache__`.isidentifier() is True, so isidentifier alone is not the rule.

    Also green before the implementation, and also an over-reach guard: binding
    the lock to a directory the interpreter writes on its own reports LOSS OF
    LOCK for a change nobody made.
    """
    from scripts.utils.canopus_freeze import build_manifest, verify_manifest

    target = _tree_with_one_test(tmp_path)
    manifest = build_manifest([target], tmp_path, label="l", frozen_at=STAMP)
    (tmp_path / "__pycache__").mkdir(exist_ok=True)

    assert verify_manifest(manifest, tmp_path)["held"] is True


def test_the_recomputed_root_agrees_with_the_built_one(tmp_path):
    """Wire 2.2's blocker B1, guarded rather than remembered.

    `_guard_ancestors` builds with the TUPLE GUARD_NAMES_TREE_ROOT; `recompute`
    reads the LIST the manifest round-tripped through JSON. A discriminator
    written with `is`, or with `==` against the tuple, is true on one path and
    false on the other, directories enter the stored digest and never the
    recomputed one, and the tree reports LOSS OF LOCK forever with nothing moved.
    Green today because neither path sees directories at all; it fails the moment
    the discriminator is written wrongly, which is the only cheap guard against
    re-shipping B1.
    """
    from scripts.utils.canopus_freeze import build_manifest, verify_manifest

    target = _tree_with_one_test(tmp_path)
    (tmp_path / "plug").mkdir()
    manifest = build_manifest([target], tmp_path, label="l", frozen_at=STAMP)

    report = verify_manifest(manifest, tmp_path)

    assert report["held"] is True
    assert report["added"] == []


# ============================================================
# The operator can read the refusal
# ============================================================


def test_a_truncated_reason_list_says_that_it_was_truncated(tmp_path, capsys):
    """SC-8. Five reasons printed out of seven reads as seven having been five.

    A plugin delta arrives several reasons at a time, one per name, so the
    display bound stops being harmless exactly when it starts mattering.
    """
    import scripts.canopus as canopus
    from scripts.utils.canopus_freeze import write_attestation

    write_attestation(tmp_path, {
        "recipe": "canopus-attest-v2",
        "root": DIGEST,
        "attested": False,
        "reasons": [f"reason {n}" for n in range(7)],
        "exit_status": 1,
        "attested_at": STAMP,
        "frozen_tests": {},
        "process": _process(),
    })

    canopus._print_attestation(tmp_path, DIGEST)

    out = capsys.readouterr().out
    assert "reason 0" in out
    assert "2 more" in out
