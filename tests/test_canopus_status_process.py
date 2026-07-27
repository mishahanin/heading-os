#!/usr/bin/env python3
"""A refusal an operator cannot read is a refusal that gets ignored.

Two surfaces, one question. `_print_attestation` is the only printer of the
record's reasons, and it prints a bounded slice of them; the bound has to read
as a bound. The evidence pack is where a human signs off, and the in-tree
plugins the comparison deliberately leaves alone are invisible anywhere else.
"""
import scripts.canopus as canopus
from scripts.utils.canopus_freeze import write_attestation

ROOT_DIGEST = "d" * 64


def _process(**overrides):
    """The shape `canopus_gate.process_facts` actually returns."""
    facts = {
        "plugins": {},
        "intree_plugins": [],
        "other_plugins": [],
        "option_plugins": [],
        "env_configured": [],
        "launcher": "bare",
        "workers": [],
    }
    facts.update(overrides)
    return facts


def _attested_with(tmp_path, reasons):
    """`_print_attestation(root, recomputed_root)` reads the record off disk."""
    write_attestation(tmp_path, {
        "recipe": "canopus-attest-v2",
        "root": ROOT_DIGEST,
        "attested": False,
        "reasons": list(reasons),
        "exit_status": 1,
        "attested_at": "2026-07-27T00:00:00+00:00",
        "frozen_tests": {},
        "process": _process(),
    })
    return tmp_path


def test_a_truncated_reason_list_says_it_was_truncated(tmp_path, capsys):
    """Seven reasons printed as five reads as five reasons, a lie by omission."""
    root = _attested_with(tmp_path, [f"reason {n}" for n in range(7)])

    canopus._print_attestation(root, ROOT_DIGEST)

    out = capsys.readouterr().out
    assert "reason 0" in out
    assert "2 more" in out
    # Truncation that names no destination is a dead end. The record is one
    # file away, and saying so is the difference between a bound and a wall.
    assert "attest.json" in out


def test_a_short_reason_list_says_nothing_about_truncation(tmp_path, capsys):
    root = _attested_with(tmp_path, ["only one"])

    canopus._print_attestation(root, ROOT_DIGEST)

    out = capsys.readouterr().out
    assert "only one" in out
    assert "more" not in out


def test_the_environment_refusal_reaches_the_operator_verbatim(tmp_path, capsys):
    root = _attested_with(
        tmp_path, ["the session was configured by the environment: PYTEST_ADDOPTS"])

    canopus._print_attestation(root, ROOT_DIGEST)

    assert "PYTEST_ADDOPTS" in capsys.readouterr().out


def test_the_pack_names_an_unfrozen_in_tree_plugin():
    """The sign-off page is where a human sees which plugin was not frozen."""
    from scripts.utils.canopus_pack import render_process

    page = render_process(
        _process(launcher="run-tests", intree_plugins=["plug/skipper.py"]),
        frozen_paths={"tests/conftest.py"},
    )

    assert "plug/skipper.py" in page
    assert "run-tests" in page
    assert "NOT FROZEN" in page


def test_the_pack_does_not_call_a_frozen_in_tree_plugin_unfrozen():
    """Marking every entry would pass the test above and say nothing."""
    from scripts.utils.canopus_pack import render_process

    page = render_process(
        _process(launcher="run-tests", intree_plugins=["tests/conftest.py"]),
        frozen_paths={"tests/conftest.py"},
    )

    assert "tests/conftest.py" in page
    assert "NOT FROZEN" not in page


def test_the_pack_names_the_environment_and_the_parsed_plugin_option():
    """PYTEST_ADDOPTS is the channel this wire is named for; it has to print."""
    from scripts.utils.canopus_pack import render_process

    page = render_process(
        _process(env_configured=["PYTEST_ADDOPTS", "PYTEST_CURRENT_TEST"],
                 option_plugins=["skipper"]),
        frozen_paths=set(),
    )

    assert "PYTEST_ADDOPTS" in page
    # A second name that appears in no prose on the page. Measured: asserting
    # PYTEST_ADDOPTS alone did NOT kill "delete the env row", because the
    # plugin-opt label names PYTEST_ADDOPTS as one of its own channels.
    assert "PYTEST_CURRENT_TEST" in page
    assert "skipper" in page
    # Labelled as the PARSED option, because the name can arrive on argv, in
    # PYTEST_ADDOPTS, or from an ini addopts, and a bare `-p` label tells an
    # operator someone typed it.
    assert "parsed" in page


def test_the_pack_renders_a_damaged_process_block_without_raising():
    """The record is a JSON file a human can edit; the pack must not traceback."""
    from scripts.utils.canopus_pack import render_process

    page = render_process(
        {"launcher": "run-tests", "plugins": 5, "intree_plugins": 7,
         "env_configured": None, "option_plugins": "skipper"},
        frozen_paths=set(),
    )

    assert "interpreter" in page
    assert "compared   none" in page


def test_the_pack_omits_the_origin_path_a_plugin_was_loaded_from():
    """The pack is pasted into sign-off artifacts; an origin is a local path."""
    from scripts.utils.canopus_pack import render_process

    page = render_process(
        _process(plugins={"dist:xdist": "/venv/xdist/plugin.py"}),
        frozen_paths=set(),
    )

    assert "dist:xdist" in page
    assert "/venv/xdist/plugin.py" not in page
