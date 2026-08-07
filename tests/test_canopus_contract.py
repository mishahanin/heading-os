"""Canopus wire 2: the contract runner and its two refusal conditions."""
import json
import os
import textwrap
from pathlib import Path

import pytest


def _write(tmp_path: Path, rel: str, body: str) -> Path:
    target = tmp_path / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body, encoding="utf-8")
    return target


def test_contract_files_lists_test_modules_only(tmp_path):
    from scripts.utils.canopus_contract import contract_files

    _write(tmp_path, "c/test_one.py", "def test_a():\n    assert False\n")
    _write(tmp_path, "c/helper.py", "x = 1\n")
    _write(tmp_path, "c/nested/test_two.py", "def test_b():\n    assert False\n")

    assert contract_files([tmp_path / "c"], tmp_path) == [
        "c/nested/test_two.py", "c/test_one.py",
    ]


def test_a_contract_file_outside_the_root_is_refused_not_a_traceback(tmp_path):
    """Measured 2026-08-07: `canopus.py probe <a file outside --root>` died with
    a raw `ValueError` out of `Path.relative_to`, because `main` catches
    `ContractError` and `OSError` and this was neither.

    The file EXISTS, so this is not a missing-path case. It is the ordinary
    mistake of naming a real contract in another tree without moving `--root`
    with it, and `main`'s own stated policy is that a filesystem fault produces
    a refusal the operator can act on rather than a stack trace.
    """
    import pytest

    from scripts.utils.canopus_contract import ContractError, contract_files

    inside = tmp_path / "root"
    outside = tmp_path / "elsewhere"
    _write(outside, "test_stray.py", "def test_a():\n    assert False\n")
    inside.mkdir(exist_ok=True)

    with pytest.raises(ContractError) as refusal:
        contract_files([outside / "test_stray.py"], inside)

    # The root is NAMED, because "outside the tree" without saying which tree
    # leaves the operator no way to tell a wrong --root from a wrong path.
    assert str(inside.resolve()) in str(refusal.value)


def test_the_cli_turns_that_refusal_into_an_exit_code_and_no_traceback(tmp_path,
                                                                      capsys):
    """The other half: the refusal has to reach `main`'s handler, not the user.

    Asserted through the CLI rather than the primitive, because the defect was
    never in the primitive's raising; it was that the exception class raised
    there was one `main` did not catch.
    """
    import scripts.canopus as canopus

    outside = tmp_path / "elsewhere"
    _write(outside, "test_stray.py", "def test_a():\n    assert False\n")
    inside = tmp_path / "root"
    inside.mkdir(exist_ok=True)

    status = canopus.main(["--root", str(inside), "probe",
                           str(outside / "test_stray.py")])

    assert status == 1
    assert "outside the tree" in capsys.readouterr().err


def test_run_contract_counts_items_and_outcomes(tmp_path):
    from scripts.utils.canopus_contract import run_contract

    _write(tmp_path, "c/test_one.py",
           "def test_a():\n    assert False\n\n\ndef test_b():\n    assert True\n")

    counts, outcomes = run_contract([tmp_path / "c"], tmp_path)

    assert counts == {"c/test_one.py": 2}
    assert sorted((name, outcome) for _rel, name, outcome in outcomes) == [
        ("test_a", "failure"), ("test_b", "passed"),
    ]


def test_run_contract_reports_zero_items_for_a_module_scope_import_error(tmp_path):
    from scripts.utils.canopus_contract import run_contract

    _write(tmp_path, "c/test_one.py",
           "from scripts.utils.does_not_exist import thing\n\n\n"
           "def test_a():\n    assert thing\n")

    counts, _outcomes = run_contract([tmp_path / "c"], tmp_path)

    assert counts.get("c/test_one.py", 0) == 0


def test_run_contract_still_measures_the_siblings_of_a_broken_module(tmp_path):
    """One unimportable file must not blank the whole set.

    Without --continue-on-collection-errors pytest aborts the session on the
    first collection error, so every sibling reads as "collected nothing" and the
    refusal blames files that are fine.
    """
    from scripts.utils.canopus_contract import run_contract

    _write(tmp_path, "c/test_broken.py",
           "from scripts.utils.does_not_exist import thing\n\n\n"
           "def test_a():\n    assert thing\n")
    _write(tmp_path, "c/test_fine.py",
           "def test_a():\n    assert False\n\n\ndef test_b():\n    assert True\n")

    counts, _outcomes = run_contract([tmp_path / "c"], tmp_path)

    assert counts.get("c/test_fine.py", 0) == 2
    assert counts.get("c/test_broken.py", 0) == 0


def test_parse_junit_does_not_count_a_collection_failure_as_an_item():
    """The xunit1 shape, verbatim. A collection error carries a file attribute.

    Counted, it would be one red item for a module that yields nothing, which
    passes both refusal conditions and freezes a baseline of 1.
    """
    from scripts.utils.canopus_contract import parse_junit

    counts, outcomes = parse_junit(
        '<testsuites><testsuite>'
        '<testcase classname="" name="c.test_one" file="c/test_one.py" time="0.0">'
        '<error message="collection failure">ImportError</error></testcase>'
        '</testsuite></testsuites>'
    )

    assert counts == {}
    assert outcomes == []


def test_parse_junit_still_counts_a_genuine_setup_error():
    """A fixture that raises is a real collected item, and it is red."""
    from scripts.utils.canopus_contract import parse_junit

    counts, outcomes = parse_junit(
        '<testsuites><testsuite>'
        '<testcase classname="c.test_one" name="test_a" file="c/test_one.py" time="0.0">'
        '<error message="failed on setup with &quot;RuntimeError: boom&quot;">x</error>'
        '</testcase></testsuite></testsuites>'
    )

    assert counts == {"c/test_one.py": 1}
    assert outcomes == [("c/test_one.py", "test_a", "error")]


def test_parse_junit_refuses_a_doctype():
    from scripts.utils.canopus_contract import ContractError, parse_junit

    with pytest.raises(ContractError, match="DOCTYPE"):
        parse_junit(
            '<!DOCTYPE testsuite [<!ENTITY a "aaa">]>\n'
            '<testsuite><testcase file="c/test_one.py" name="test_a"/></testsuite>'
        )


def test_refusal_reasons_rejects_an_all_green_contract():
    from scripts.utils.canopus_contract import refusal_reasons

    reasons = refusal_reasons(
        {"c/test_one.py": 2},
        [("c/test_one.py", "test_a", "passed"), ("c/test_one.py", "test_b", "passed")],
        ["c/test_one.py"],
    )

    assert any("green before the code exists" in reason for reason in reasons)


def test_refusal_reasons_accepts_a_set_with_one_failure():
    from scripts.utils.canopus_contract import refusal_reasons

    assert refusal_reasons(
        {"c/test_one.py": 2},
        [("c/test_one.py", "test_a", "failure"), ("c/test_one.py", "test_b", "passed")],
        ["c/test_one.py"],
    ) == []


def test_refusal_reasons_counts_an_error_as_red():
    from scripts.utils.canopus_contract import refusal_reasons

    assert refusal_reasons(
        {"c/test_one.py": 1},
        [("c/test_one.py", "test_a", "error")],
        ["c/test_one.py"],
    ) == []


def test_refusal_reasons_rejects_a_file_that_collected_nothing():
    from scripts.utils.canopus_contract import refusal_reasons

    reasons = refusal_reasons(
        {"c/test_one.py": 1},
        [("c/test_one.py", "test_a", "failure")],
        ["c/test_one.py", "c/test_two.py"],
    )

    assert any("collected nothing" in reason and "c/test_two.py" in reason
               for reason in reasons)
    assert any("inside the test body" in reason for reason in reasons)


def test_no_pytest_variable_reaches_the_contract_child(tmp_path, monkeypatch):
    """The scrub is a blanket prefix, so an invented name is scrubbed too.

    PYTEST_ANYTHING_AT_ALL is the case a denylist of the names someone thought of
    passes and a prefix does not. The control variable is here so a scrub that
    emptied the environment outright could not pass: the trace id a daemon
    exported still has to reach the child (.claude/rules/trace-id.md).
    """
    from scripts.utils.canopus_contract import run_pytest_report

    _write(tmp_path, "c/test_one.py",
           "import json, os, pathlib\n"
           "def test_a():\n"
           "    pathlib.Path('env.json').write_text(json.dumps(sorted(os.environ)))\n")
    injected = {
        "PYTEST_ADDOPTS": "-q",
        "PYTEST_PLUGINS": "plug.skipper",
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        "PYTEST_ANYTHING_AT_ALL": "1",
    }
    for name, value in injected.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("X31C_TRACE_ID", "trace-for-the-child")

    run_pytest_report([tmp_path / "c"], tmp_path)

    seen = json.loads((tmp_path / "env.json").read_text(encoding="utf-8"))
    assert not (set(injected) & set(seen))
    assert "X31C_TRACE_ID" in seen


def test_a_test_that_passes_against_a_mock_asserts_nothing(tmp_path):
    """The construction proves it: a stand-in satisfies any shape assertion."""
    from scripts.utils.canopus_contract import run_null_stub

    _write(tmp_path, "c/test_one.py",
           "def test_vacuous():\n"
           "    from absent_thing import answer\n"
           "    assert answer() is not None\n"
           "\n\n"
           "def test_real():\n"
           "    from absent_thing import answer\n"
           "    assert answer() == 42\n")

    passed = run_null_stub([tmp_path / "c"], tmp_path)

    assert ("c/test_one.py", "test_vacuous") in passed
    assert ("c/test_one.py", "test_real") not in passed


def test_the_stub_does_not_shadow_a_sibling_module_that_exists(tmp_path):
    """The blocker a plan review caught before any code was written.

    Matching on the first dotted segment made one absent `scripts.utils.X` stub
    the whole `scripts` package, so modules that exist came back as stand-ins,
    every test passed, and the wholly-vacuous refusal fired on a good contract.

    Two things now hold that line, and this pins the second. The claim set is
    the contract's own AST, so nothing under `scripts.utils` is claimed except
    the one name written here; and a claimed name that RESOLVES is wrapped
    rather than replaced, so the real module's real constant is what the import
    binds.
    """
    from scripts.utils.canopus_contract import run_null_stub

    _write(tmp_path, "c/test_one.py",
           "def test_real_module_survives():\n"
           "    from scripts.utils.canopus_note import BODY_FIELD\n"
           "    assert BODY_FIELD == 'body'\n")

    passed = run_null_stub([tmp_path / "c"], tmp_path)

    assert ("c/test_one.py", "test_real_module_survives") in passed
    # It passes because the REAL constant was imported, not because a stand-in
    # satisfied the comparison: a Stub never equals that string.


def test_a_wrapped_module_keeps_the_values_it_already_has(tmp_path):
    """A claimed module that exists must not lose the names it already carries.

    The stand-in is supplied only for what the module DECLINES to answer. Supply
    everything instead and `compute()` comes back a truthy Stub under both value
    sets, so `assert compute() is not None` passes twice and a test asserting
    something real about existing code is labelled vacuous. That is the
    direction that refuses a good contract.

    The name is deliberate: this file's earlier revision reached the same
    assertion through the finder's prefix rule, by stubbing `absent` and
    watching `absent_extra` survive. That route is closed by construction now,
    because the claim set is exactly what the contract imports, and `absent` is
    not in it. The prefix rule is pinned where it lives, in the finder's tests.
    """
    from scripts.utils.canopus_contract import run_null_stub

    _write(tmp_path, "absent_extra.py", "def compute():\n    return None\n")
    _write(tmp_path, "c/test_one.py",
           "def test_prefix_sibling_survives():\n"
           "    from absent_extra import compute\n"
           "    assert compute() is not None\n")

    passed = run_null_stub([tmp_path / "c"], tmp_path)

    # It fails under the stub because the REAL compute() ran and returned None.
    assert ("c/test_one.py", "test_prefix_sibling_survives") not in passed


def test_the_stub_run_leaves_no_file_behind_in_the_tree(tmp_path):
    """The contract directory is frozen recursively; a written conftest would
    read as tampering."""
    from scripts.utils.canopus_contract import run_null_stub

    _write(tmp_path, "c/test_one.py",
           "def test_a():\n    from absent_thing import x\n    assert x\n")
    before = sorted(p.name for p in (tmp_path / "c").iterdir())

    run_null_stub([tmp_path / "c"], tmp_path)

    assert sorted(p.name for p in (tmp_path / "c").iterdir()) == before


def test_run_null_stub_catches_the_from_none_bypass(tmp_path):
    """The defect this slice exists to close, at the seam that closes it."""
    from scripts.utils.canopus_contract import run_null_stub

    contract = tmp_path / "c"
    contract.mkdir()
    (contract / "test_one.py").write_text(
        "def test_a():\n"
        "    try:\n"
        "        from absent_thing import answer\n"
        "    except ImportError:\n"
        "        raise AssertionError('not implemented yet') from None\n"
        "    assert answer() is not None\n",
        encoding="utf-8",
    )

    assert run_null_stub([contract], tmp_path) == {("c/test_one.py", "test_a")}


def test_run_null_stub_catches_a_bypass_over_a_partial_module(tmp_path):
    """The half a sink cannot see: the module exists, the name does not."""
    from scripts.utils.canopus_contract import run_null_stub

    (tmp_path / "halfbuilt.py").write_text("EXISTS = 1\n", encoding="utf-8")
    contract = tmp_path / "c"
    contract.mkdir()
    (contract / "test_one.py").write_text(
        "def test_a():\n"
        "    try:\n"
        "        from halfbuilt import NOT_THERE_YET\n"
        "    except ImportError:\n"
        "        raise AssertionError('not implemented yet') from None\n"
        "    assert NOT_THERE_YET is not None\n",
        encoding="utf-8",
    )

    assert run_null_stub([contract], tmp_path) == {("c/test_one.py", "test_a")}


def test_run_null_stub_does_not_accuse_a_container_assertion(tmp_path):
    """The v1 regression, pinned. THIS is what the second stub run buys.

    Measured on the prototype: under one stub this test passes and is labelled
    vacuous. Under two stubs carrying different lengths it passes once and fails
    once, so it is not invariant to the value and asserts something after all.
    """
    from scripts.utils.canopus_contract import run_null_stub

    contract = tmp_path / "c"
    contract.mkdir()
    (contract / "test_one.py").write_text(
        "def test_a():\n"
        "    from absent_thing import listing\n"
        "    assert len(listing()) == 0\n",
        encoding="utf-8",
    )

    assert run_null_stub([contract], tmp_path) == set()


def test_run_null_stub_leaves_a_real_value_assertion_red(tmp_path):
    from scripts.utils.canopus_contract import run_null_stub

    (tmp_path / "present.py").write_text(
        "def answer():\n    return 1\n", encoding="utf-8"
    )
    contract = tmp_path / "c"
    contract.mkdir()
    (contract / "test_one.py").write_text(
        "def test_a():\n"
        "    from present import answer\n"
        "    assert answer() == 42\n",
        encoding="utf-8",
    )

    assert run_null_stub([contract], tmp_path) == set()


def test_run_null_stub_separates_a_vacuous_test_from_a_real_one(tmp_path):
    """Per test, never collapsed to the file."""
    from scripts.utils.canopus_contract import run_null_stub

    (tmp_path / "present.py").write_text(
        "def answer():\n    return 1\n", encoding="utf-8"
    )
    contract = tmp_path / "c"
    contract.mkdir()
    (contract / "test_one.py").write_text(
        "def test_weak():\n"
        "    from absent_thing import answer\n"
        "    assert answer() is not None\n"
        "\n\n"
        "def test_strong():\n"
        "    from present import answer\n"
        "    assert answer() == 42\n",
        encoding="utf-8",
    )

    assert run_null_stub([contract], tmp_path) == {("c/test_one.py", "test_weak")}


def test_run_null_stub_stubs_a_wholly_absent_dotted_import(tmp_path):
    """Python resolves the PARENT first, so a claim on the full name is not enough.

    Measured: a finder claiming `brandnew.pkg` alone is never consulted, because
    `brandnew` resolves to nothing and the import dies there. The test then stays
    red for its original reason and is never labelled, which is an ESCAPE rather
    than a false accusation.

    Promoted here when the wire 3.1 contract was retired. Prefix expansion and
    the plugin's call to it are each pinned in tests/test_canopus_nullstub.py,
    but this is the only case that drives the whole shape through `run_null_stub`
    rather than resting on the two halves agreeing.
    """
    from scripts.utils.canopus_contract import run_null_stub

    contract = tmp_path / "c"
    contract.mkdir()
    (contract / "test_one.py").write_text(
        "def test_a():\n"
        "    from brandnew.pkg import thing\n"
        "    assert thing() is not None\n",
        encoding="utf-8",
    )

    assert run_null_stub([contract], tmp_path) == {("c/test_one.py", "test_a")}


def test_a_skipped_test_is_not_proved_to_assert_anything(tmp_path):
    """Not proved is not proved innocent, and a skip is the cheapest bypass.

    `pytest.skip("not implemented yet")` at the top of a vacuous test is one call
    and leaves the same `skipped` token under BOTH value sets, so an intersection
    of PASSES alone would never see it and the freeze would proceed. Written with
    `unittest.SkipTest` rather than `pytest.skip` so the claim set stays off
    pytest's own package while the session that reads the outcome is running
    inside it.
    """
    from scripts.utils.canopus_contract import run_null_stub

    contract = tmp_path / "c"
    contract.mkdir()
    (contract / "test_one.py").write_text(
        "def test_a():\n"
        "    import unittest\n"
        "    raise unittest.SkipTest('not implemented yet')\n",
        encoding="utf-8",
    )

    assert run_null_stub([contract], tmp_path) == {("c/test_one.py", "test_a")}


def test_a_contract_that_names_no_module_refuses_rather_than_returning_empty(
    tmp_path,
):
    """No name to claim means no stand-in ran, so NO test was measured.

    The revision this replaces returned an empty set here, and an empty set is
    what a completed measurement that found nothing vacuous also returns. The two
    are opposite claims read off one value: the caller printed no vacuity word,
    exited 0, and wrote the manifest. Measured through the CLI on
    `def test_a(): assert 1 == 2` before the fix: `probe` exited 0 and `freeze`
    froze it.

    Refusing costs the caller nothing it had: with no name claimed there was
    never a verdict to lose. It also keeps the cost guard the empty return was
    partly there for, because the two stub sessions are still never spent.
    """
    from scripts.utils.canopus_contract import ContractError, run_null_stub

    contract = tmp_path / "c"
    contract.mkdir()
    (contract / "test_one.py").write_text(
        "def test_a():\n    assert 1 == 2\n", encoding="utf-8"
    )

    with pytest.raises(ContractError) as excinfo:
        run_null_stub([contract], tmp_path)
    assert "NOT measured" in str(excinfo.value)


def test_a_claim_set_emptied_by_the_passability_filter_also_refuses(tmp_path):
    """The same state reached one step later, and it must land the same way.

    `contract_imports` reads a name here, so the refusal cannot key off the AST
    being silent; `_passable_claims` then drops it because it carries the wire
    separator, and what reaches the child is again nothing. Whether the set was
    empty on arrival or emptied on the way, no stand-in ran.
    """
    from scripts.utils.canopus_contract import ContractError, run_null_stub
    from scripts.utils.canopus_nullstub import STUB_NAME_SEPARATOR

    # `__import__` rather than `importlib.import_module`, so the file carries no
    # plain `import` statement either; otherwise `importlib` survives the filter
    # and this measures a set that was never empty.
    unpassable = "absent" + STUB_NAME_SEPARATOR + "thing"
    contract = tmp_path / "c"
    contract.mkdir()
    (contract / "test_one.py").write_text(
        "def test_a():\n"
        f"    __import__({unpassable!r})\n"
        "    assert False\n",
        encoding="utf-8",
    )

    with pytest.raises(ContractError) as excinfo:
        run_null_stub([contract], tmp_path)
    assert "NOT measured" in str(excinfo.value)


def test_a_vacuous_test_whose_absent_import_lives_in_a_conftest_is_caught(tmp_path):
    """Building the subject in a fixture is ordinary pytest, and it was an escape.

    The contract's only absent import sits in `conftest.py`, which the test-module
    glob never read, so the claim set came back empty, nothing was stubbed, and
    the probe returned a verdict it had not taken. The test is the canonical
    vacuous shape the two-stub rule exists to catch: `len(...) == 0` against a
    subject that does not exist.
    """
    from scripts.utils.canopus_contract import run_null_stub

    contract = tmp_path / "c"
    contract.mkdir()
    (contract / "conftest.py").write_text(
        "import pytest\n"
        "\n\n"
        "@pytest.fixture\n"
        "def widget():\n"
        "    from absent_thing import Widget\n"
        "    return Widget()\n",
        encoding="utf-8",
    )
    (contract / "test_one.py").write_text(
        "def test_widget_is_not_none(widget):\n"
        "    assert widget is not None\n",
        encoding="utf-8",
    )

    assert run_null_stub([contract], tmp_path) == {
        ("c/test_one.py", "test_widget_is_not_none")
    }


_ONE_PASSING_REPORT = (
    '<testsuites><testsuite>'
    '<testcase classname="c.test_one" name="test_a" file="c/test_one.py" '
    'time="0.0"/>'
    '</testsuite></testsuites>'
)
_ROOT_LEVEL_PASSING_REPORT = (
    '<testsuites><testsuite>'
    '<testcase classname="test_one" name="test_a" file="test_one.py" '
    'time="0.0"/>'
    '</testsuite></testsuites>'
)
_EMPTY_REPORT = '<testsuites><testsuite></testsuite></testsuites>'
_TWO_TEST_RED_REPORT = (
    '<testsuites><testsuite>'
    '<testcase classname="c.test_one" name="test_a" file="c/test_one.py" '
    'time="0.0"><failure message="assert 0">t</failure></testcase>'
    '<testcase classname="c.test_one" name="test_b" file="c/test_one.py" '
    'time="0.0"><failure message="assert 0">t</failure></testcase>'
    '</testsuite></testsuites>'
)
_TWO_FILE_RED_REPORT = (
    '<testsuites><testsuite>'
    '<testcase classname="c.test_one" name="test_a" file="c/test_one.py" '
    'time="0.0"><failure message="assert 0">t</failure></testcase>'
    '<testcase classname="c.test_two" name="test_b" file="c/test_two.py" '
    'time="0.0"><failure message="assert 0">t</failure></testcase>'
    '</testsuite></testsuites>'
)
_ERROR_REPORT = (
    '<testsuites><testsuite>'
    '<testcase classname="c.test_one" name="test_a" file="c/test_one.py" time="0.0">'
    '<error message="failed on setup with &quot;TypeError: expected str&quot;">t'
    '</error></testcase>'
    '</testsuite></testsuites>'
)
_ERROR_BESIDE_A_GREEN_REPORT = (
    '<testsuites><testsuite>'
    '<testcase classname="c.test_one" name="test_a" file="c/test_one.py" time="0.0">'
    '<error message="failed on setup with &quot;TypeError: expected str&quot;">t'
    '</error></testcase>'
    '<testcase classname="c.test_one" name="test_b" file="c/test_one.py" '
    'time="0.0"/>'
    '</testsuite></testsuites>'
)


def _capture_probe_env(monkeypatch, reports=None, baseline=None):
    """Stand in for the child and hand back the STUB environments it was given.

    The two stub runs are what this task's verdict is made of, so the parent half
    of the handshake is worth reading directly rather than only through a child
    that would have to be believed.

    Three children now, not two: with no `expected_population` supplied the probe
    runs its own unstubbed BASELINE first, and that run is the only witness to
    which tests were supposed to be there. It is told apart from the stub runs by
    the one thing that distinguishes them at the seam, an `extra_env` carrying
    the stub handshake, and it is served *baseline* and kept out of `seen` so
    every caller below still reads `seen[0]` as the first STUB run.
    """
    from scripts.utils import canopus_contract

    seen: list[dict] = []
    bodies = list(reports or [_ONE_PASSING_REPORT, _ONE_PASSING_REPORT])
    real = baseline if baseline is not None else _ONE_PASSING_REPORT

    def _fake(paths, root, **kwargs):
        extra_env = dict(kwargs.get("extra_env") or {})
        if not extra_env:
            return real
        seen.append(extra_env)
        return bodies[len(seen) - 1]

    monkeypatch.setattr(canopus_contract, "run_pytest_report", _fake)
    return seen


def _one_import_contract(tmp_path, body="def test_a():\n    import absent_thing\n"):
    contract = tmp_path / "c"
    contract.mkdir()
    (contract / "test_one.py").write_text(body, encoding="utf-8")
    return contract


def test_the_probe_sets_exactly_the_variables_the_plugin_reads(tmp_path, monkeypatch):
    """The parent-to-child handshake, pinned from both ends at once.

    `MODULES_VAR` and `VALUES_VAR` are read from the plugin here rather than
    spelled again, so renaming either constant on one side only fails this test
    instead of leaving a green suite whose verdict is silently always empty: the
    child would read an unset variable, claim nothing, and both runs would agree
    on a red the rule never fires over.
    """
    from scripts.utils.canopus_contract import run_null_stub
    from scripts.utils.canopus_nullstub import MODULES_VAR, STUB_VALUES, VALUES_VAR

    contract = _one_import_contract(tmp_path)
    seen = _capture_probe_env(monkeypatch)

    run_null_stub([contract], tmp_path)

    assert [env[VALUES_VAR] for env in seen] == ["A", "B"]
    assert [env[MODULES_VAR] for env in seen] == ["absent_thing", "absent_thing"]
    # The labels are the plugin's own value-set keys, not two letters that happen
    # to match today.
    assert set(STUB_VALUES) == {"A", "B"}


def test_the_probe_hands_the_child_the_engine_the_tree_and_the_outer_path(
    tmp_path, monkeypatch
):
    """Three path entries, and each one buys something different.

    The engine root is where `-p scripts.utils.canopus_nullstub` resolves from.
    The contract root is what makes the tree's own modules importable, so a named
    module that EXISTS is wrapped rather than stubbed whole. The inherited entry
    is the caller's, and dropping it would change what the contract can import
    between the real run and the probe.
    """
    from scripts.utils import canopus_contract
    from scripts.utils.canopus_contract import run_null_stub

    monkeypatch.setenv("PYTHONPATH", "/outer/synthetic/path")
    contract = _one_import_contract(tmp_path)
    seen = _capture_probe_env(monkeypatch)

    run_null_stub([contract], tmp_path)

    engine_root = str(Path(canopus_contract.__file__).resolve().parents[2])
    parts = seen[0]["PYTHONPATH"].split(os.pathsep)
    assert parts[0] == engine_root
    assert str(tmp_path.resolve()) in parts
    assert "/outer/synthetic/path" in parts


def test_the_probe_path_carries_no_empty_trailing_entry(tmp_path, monkeypatch):
    """An empty entry on PYTHONPATH is the current directory, silently.

    With nothing inherited the join leaves a trailing separator, and the child
    then treats its own working directory as a search root, which is not a path
    this probe chose to hand it.
    """
    from scripts.utils.canopus_contract import run_null_stub

    monkeypatch.delenv("PYTHONPATH", raising=False)
    contract = _one_import_contract(tmp_path)
    seen = _capture_probe_env(monkeypatch)

    run_null_stub([contract], tmp_path)

    assert "" not in seen[0]["PYTHONPATH"].split(os.pathsep)


def test_a_collected_name_carrying_the_separator_is_dropped_and_reported(
    tmp_path, monkeypatch, capsys
):
    """The AST reader over-reports on purpose, and some of it is not a name.

    `pytest.importorskip` carries a `reason`, and every string constant among a
    dynamic import's arguments is collected, prose included. A collected string
    holding the separator this probe joins on would split in the child into
    fragments the contract never named, and a fragment can claim a module that
    exists. A comma cannot appear in an importable dotted name, so dropping it
    loses no claim, and the drop is said out loud rather than made quietly.
    """
    from scripts.utils.canopus_contract import run_null_stub

    contract = _one_import_contract(
        tmp_path,
        "import pytest\n"
        "def test_a():\n"
        "    pytest.importorskip('absent_thing', reason='needs, the thing')\n",
    )
    seen = _capture_probe_env(monkeypatch)

    run_null_stub([contract], tmp_path)

    assert seen[0]["CANOPUS_AST_MODULES"] == "absent_thing,pytest"
    assert "needs, the thing" in capsys.readouterr().err


_ORDER_NAMES = (
    "alfa_mod", "bravo_mod", "charlie_mod", "delta_mod",
    "echo_mod", "foxtrot_mod", "golf_mod", "hotel_mod",
)


def test_the_claim_set_reaches_the_child_in_a_stable_order(tmp_path, monkeypatch):
    """Two runs of one contract must be one probe, not two.

    `contract_imports` returns a SET, whose iteration order is a function of the
    interpreter's hash seed and therefore differs between runs of the same
    contract. Handing that order to the child unsorted makes the claim string a
    property of the process rather than of the contract, and an operator
    comparing two probes is then comparing two different inputs.

    Eight names rather than two on purpose: an unsorted set matching sorted order
    by accident is one arrangement in 8!, so this pins the sort rather than
    catching it on most runs.
    """
    from scripts.utils.canopus_contract import run_null_stub
    from scripts.utils.canopus_nullstub import MODULES_VAR

    contract = _one_import_contract(
        tmp_path,
        "def test_a():\n"
        + "".join(f"    import {name}\n" for name in reversed(_ORDER_NAMES)),
    )
    seen = _capture_probe_env(monkeypatch)

    run_null_stub([contract], tmp_path)

    assert seen[0][MODULES_VAR] == ",".join(sorted(_ORDER_NAMES))


def test_a_root_level_contract_file_is_not_mistaken_for_its_own_package(
    tmp_path, monkeypatch
):
    """A contract file at the root has no package prefix to collide with.

    Read the file NAME as a package prefix and `test_one.py` becomes one, so a
    collected string that happens to equal it refuses the whole contract with a
    message about a package that does not exist. The AST reader collects every
    string constant among a dynamic import's arguments, so producing such a
    string takes no contrivance beyond writing it.
    """
    from scripts.utils.canopus_contract import run_null_stub
    from scripts.utils.canopus_nullstub import MODULES_VAR

    (tmp_path / "test_one.py").write_text(
        "def test_a():\n    __import__('test_one.py')\n", encoding="utf-8"
    )
    # The faked report names the contract file this contract actually has, at the
    # root. Spelled `c/test_one.py` it would describe a file that is not in the
    # set, and the lost-file guard below would refuse before this assertion ran.
    seen = _capture_probe_env(
        monkeypatch, [_ROOT_LEVEL_PASSING_REPORT, _ROOT_LEVEL_PASSING_REPORT]
    )

    run_null_stub([tmp_path / "test_one.py"], tmp_path)

    assert seen[0][MODULES_VAR] == "test_one.py"


def test_only_the_stub_plugins_own_lines_are_echoed_from_the_child(
    tmp_path, monkeypatch, capsys
):
    """The marker, not the stream.

    A contract child's ordinary stderr is the child's business, and echoing all
    of it buries the one line that is this side's business under whatever the
    contract and its libraries chose to print. The child is faked here because
    pytest CAPTURES a test's own stderr inside the child, so a passing contract
    cannot put noise on the stream this filter reads.
    """
    from scripts.utils import canopus_contract

    _write(tmp_path, "c/test_one.py", "def test_a():\n    assert True\n")

    class _Proc:
        returncode = 0
        stdout = ""
        stderr = (
            "CHILD-NOISE-MARKER\n"
            "canopus-nullstub: resolving ghost raised RuntimeError(); "
            "stubbing it instead\n"
        )

    def _fake_run(command, **kwargs):
        report = Path(command[command.index("--junit-xml") + 1])
        report.write_text(_ONE_PASSING_REPORT, encoding="utf-8")
        return _Proc()

    monkeypatch.setattr(canopus_contract.subprocess, "run", _fake_run)

    canopus_contract.run_pytest_report([tmp_path / "c"], tmp_path)

    err = capsys.readouterr().err
    assert "canopus-nullstub: resolving ghost" in err
    assert "CHILD-NOISE-MARKER" not in err


def test_the_two_probe_runs_must_have_measured_the_same_tests(tmp_path, monkeypatch):
    """An intersection over two different populations is not evidence.

    A run that collected a different set of tests from its partner has not been
    compared with it, and the quiet answer is an empty verdict, which reads
    exactly like "measured, nothing vacuous".
    """
    from scripts.utils.canopus_contract import ContractError, run_null_stub

    contract = _one_import_contract(tmp_path)
    _capture_probe_env(monkeypatch, [_ONE_PASSING_REPORT, _EMPTY_REPORT])

    with pytest.raises(ContractError, match="did not measure the same tests"):
        run_null_stub([contract], tmp_path)


def test_a_probe_that_collected_nothing_is_not_a_clean_verdict(tmp_path, monkeypatch):
    """Both runs empty is the fail-open the intersection cannot see on its own.

    A stub that breaks collection outright leaves two empty populations, they
    agree, the intersection is empty, and a contract whose every test asserts
    nothing walks through the refusal on the strength of a probe that never ran
    a test.
    """
    from scripts.utils.canopus_contract import ContractError, run_null_stub

    contract = _one_import_contract(tmp_path)
    _capture_probe_env(monkeypatch, [_EMPTY_REPORT, _EMPTY_REPORT])

    with pytest.raises(ContractError, match="collected no test at all"):
        run_null_stub([contract], tmp_path)


def test_run_null_stub_refuses_a_stub_run_that_executed_nothing(tmp_path):
    """Zero executed tests is not "nothing was vacuous".

    A stub run that collected nothing measured nothing, and the caller cannot
    tell that from a run where every test genuinely failed under the stub.
    """
    import pytest

    from scripts.utils.canopus_contract import ContractError, run_null_stub

    contract = tmp_path / "c"
    contract.mkdir()
    (contract / "test_one.py").write_text(
        "import pytest\n"
        "pytest.skip('nothing runs here', allow_module_level=True)\n"
        "\n"
        "def test_a():\n"
        "    from absent_thing import answer\n"
        "    assert answer() is not None\n",
        encoding="utf-8",
    )

    with pytest.raises(ContractError):
        run_null_stub([contract], tmp_path)


def test_run_null_stub_refuses_when_the_two_runs_collected_different_tests(
    tmp_path, monkeypatch
):
    """An intersection over two different populations means nothing.

    The verdict is "passed under BOTH", so a test present in one run and absent
    from the other silently counts as "did not pass both times", which reads as
    "asserts something" whatever it actually does. The two runs differ only in
    the stub VALUES, so a collection that depends on them is precisely the
    surprise worth refusing on.
    """
    import scripts.utils.canopus_contract as contract_mod
    from scripts.utils.canopus_contract import ContractError, run_null_stub

    contract = tmp_path / "c"
    contract.mkdir()
    (contract / "test_one.py").write_text(
        "def test_a():\n"
        "    from absent_thing import answer\n"
        "    assert answer() is not None\n",
        encoding="utf-8",
    )

    real = contract_mod.run_pytest_report
    seen = []

    def _drop_a_test_on_the_second_run(*args, **kwargs):
        xml_text = real(*args, **kwargs)
        seen.append(1)
        if len(seen) == 2:
            return xml_text.replace("<testcase", "<skipped-case", 1)
        return xml_text

    monkeypatch.setattr(
        contract_mod, "run_pytest_report", _drop_a_test_on_the_second_run
    )

    with pytest.raises(ContractError):
        run_null_stub([contract], tmp_path)


def test_the_probe_refuses_to_stub_a_package_prefix_of_the_contract_itself(tmp_path):
    """A stub over the contract's own package would poison collection silently.

    A stub lands in `sys.modules` and answers every later import from there, so
    the contract's own test modules would be collected out of a stand-in and the
    verdict would describe nothing. Refused rather than measured.
    """
    from scripts.utils.canopus_contract import ContractError, run_null_stub

    contract = _one_import_contract(
        tmp_path, "def test_a():\n    import c\n    assert c\n"
    )

    with pytest.raises(ContractError, match="package prefix of its own"):
        run_null_stub([contract], tmp_path)


def test_the_childs_stub_diagnostics_reach_the_caller(tmp_path, capsys):
    """The only surviving trace of a swallowed exception must not be dropped.

    The plugin stubs a name whose resolution raised rather than dying, and says
    so on its own stderr. A caller that discards the child's stderr turns a
    first-party module that blows up on import into a bare vacuity refusal with
    no hint of the real cause.

    Constructed so the raise happens where the plugin catches it: resolving the
    prefix `boom.sub` imports `boom`, whose body raises.
    """
    from scripts.utils.canopus_contract import run_null_stub

    package = tmp_path / "boom"
    package.mkdir()
    (package / "__init__.py").write_text(
        "raise RuntimeError('boom')\n", encoding="utf-8"
    )
    contract = _one_import_contract(
        tmp_path,
        "def test_a():\n"
        "    from boom.sub.deep import thing\n"
        "    assert thing\n",
    )

    run_null_stub([contract], tmp_path)

    err = capsys.readouterr().err
    assert "canopus-nullstub:" in err
    assert "boom.sub" in err


def test_a_contract_file_lost_to_both_stub_runs_is_refused(tmp_path, monkeypatch):
    """The measured escape: a file that vanishes under BOTH stubs is acquitted.

    A contract file carrying a module-scope reference to a real module's real
    value stops collecting the moment that module is stubbed whole, and the two
    stub runs then AGREE on the truncated population, so the same-population
    guard passes, the non-empty guard passes, and every test in the lost file is
    silently excused. Measured on a two-file contract whose every test asserts
    nothing: the probe returned one vacuous pair, `vacuity_refusal` returned
    nothing, and the contract froze.

    It can only ever fire on the escape, and the baseline is what proves that
    rather than an argument about which caller ran the probe: the file is refused
    because the REAL run collected a test in it and the stub runs collected none.
    """
    from scripts.utils.canopus_contract import ContractError, run_null_stub

    contract = _one_import_contract(tmp_path)
    _write(tmp_path, "c/test_two.py", "def test_b():\n    import absent_thing\n")
    _capture_probe_env(monkeypatch, baseline=_TWO_FILE_RED_REPORT)

    with pytest.raises(ContractError, match="c/test_two.py"):
        run_null_stub([contract], tmp_path)


def test_a_probe_child_that_did_not_finish_is_refused(tmp_path, monkeypatch):
    """Exit 0 and 1 are the only outcomes a completed session writes.

    Measured: a session interrupted mid-run exits 2 and still writes a PARTIAL
    JUnit report. Two probe children truncated the same way agree with each
    other, so the verdict is computed over the survivors and reads as a
    measurement. Exits 2, 3 and 4 all mean the session did not complete.
    """
    from scripts.utils import canopus_contract
    from scripts.utils.canopus_contract import ContractError, run_null_stub

    contract = _one_import_contract(tmp_path)

    class _Interrupted:
        returncode = 2
        stdout = ""
        stderr = "!!!!! KeyboardInterrupt !!!!!"

    def _fake_run(command, **kwargs):
        report = Path(command[command.index("--junit-xml") + 1])
        report.write_text(_ONE_PASSING_REPORT, encoding="utf-8")
        return _Interrupted()

    monkeypatch.setattr(canopus_contract.subprocess, "run", _fake_run)

    with pytest.raises(ContractError, match="pytest exited 2"):
        run_null_stub([contract], tmp_path)


def test_a_module_scope_skip_under_the_stub_is_refused_not_labelled(tmp_path):
    """The second-order damage of a lost file, closed by the same exit-code rule.

    A contract file that skips at MODULE level under the stub exits 5 while
    xunit1 still writes ONE synthetic testcase named after the MODULE. So the
    file counts an item, the per-file guard sees nothing wrong, the population is
    not empty, and the verdict came back carrying `('c/test_one.py',
    'c.test_one')`: an id that is not a test, which a caller would print to the
    operator as a vacuous test that does not exist. Run against a real child,
    because the whole finding turns on which exit and which testcase pytest
    actually writes.
    """
    from scripts.utils.canopus_contract import ContractError, run_null_stub

    contract = _one_import_contract(
        tmp_path,
        "import unittest\n"
        "raise unittest.SkipTest('nothing runs here')\n"
        "\n\n"
        "def test_a():\n"
        "    from absent_thing import answer\n"
        "    assert answer() is not None\n",
    )

    with pytest.raises(ContractError, match="pytest exited 5"):
        run_null_stub([contract], tmp_path)


def test_the_baseline_run_still_reads_a_report_from_any_exit(tmp_path, monkeypatch):
    """The exit-code refusal is the PROBE's, and must not reach the baseline.

    A contract that has not been implemented yet exits nonzero, and that is the
    state the baseline run exists to observe. Only a caller that asks for an
    allowed set gets the refusal.
    """
    from scripts.utils import canopus_contract

    _write(tmp_path, "c/test_one.py", "def test_a():\n    assert False\n")

    class _Interrupted:
        returncode = 2
        stdout = ""
        stderr = ""

    def _fake_run(command, **kwargs):
        report = Path(command[command.index("--junit-xml") + 1])
        report.write_text(_ONE_PASSING_REPORT, encoding="utf-8")
        return _Interrupted()

    monkeypatch.setattr(canopus_contract.subprocess, "run", _fake_run)

    assert canopus_contract.run_pytest_report(
        [tmp_path / "c"], tmp_path
    ) == _ONE_PASSING_REPORT


def test_a_red_test_the_stub_runs_never_collected_is_refused(tmp_path, monkeypatch):
    """The per-file guard closes the file; this closes the test.

    A file can survive the stub runs at full count while the TEST inside it is
    gone: a module-scope skip under the stub is recorded by xunit1 as one
    synthetic testcase named after the module, so the file still counts one item
    and the per-file guard sees nothing wrong. The real run's own population is
    the only thing that knows which tests were supposed to be there.
    """
    from scripts.utils.canopus_contract import ContractError, run_null_stub

    contract = _one_import_contract(tmp_path)
    _capture_probe_env(monkeypatch)

    with pytest.raises(ContractError, match="test_gone"):
        run_null_stub(
            [contract], tmp_path,
            expected_population=[("c/test_one.py", "test_gone", "failure")],
        )


def test_a_green_test_the_stub_runs_never_collected_is_not_refused(
    tmp_path, monkeypatch
):
    """Only RED tests are evidence, here as everywhere else in this probe.

    A test that PASSED in the real run never had an absent import for the stub to
    resolve, so its absence from the stub population proves nothing and refusing
    on it would be a false accusation.
    """
    from scripts.utils.canopus_contract import run_null_stub

    contract = _one_import_contract(tmp_path)
    _capture_probe_env(monkeypatch)

    assert run_null_stub(
        [contract], tmp_path,
        expected_population=[
            ("c/test_one.py", "test_a", "failure"),
            ("c/test_one.py", "test_green_and_absent", "passed"),
        ],
    ) == {("c/test_one.py", "test_a")}


def test_the_real_population_is_optional(tmp_path, monkeypatch):
    """The two-argument call stays exactly what it was.

    The parameter is keyword-only with a default so every documented caller keeps
    working; a caller that does not supply it simply does not get this guard.
    """
    from scripts.utils.canopus_contract import run_null_stub

    contract = _one_import_contract(tmp_path)
    _capture_probe_env(monkeypatch)

    assert run_null_stub([contract], tmp_path) == {("c/test_one.py", "test_a")}


def test_an_errored_test_under_the_stub_is_named_and_not_acquitted(
    tmp_path, monkeypatch
):
    """`error` is not innocence, and the acquittal was a measured escape.

    Measured on a contract whose two tests both assert nothing and share a
    fixture handing a stub value to a stdlib API that type-checks it
    (`open(CONFIG['path'])`): both tests errored under both stub runs, `error`
    was in neither the passed nor the skipped set, the intersection came back
    empty, and the wholly-vacuous refusal never fired. The identical contract
    spelled with `pytest.skip` was refused.

    The answer is the per-test label the skip case already gets, not a refusal of
    the whole contract: an outcome invariant to the stub value was not proved
    innocent, and that is a statement about ONE test.
    """
    from scripts.utils.canopus_contract import run_null_stub

    contract = _one_import_contract(tmp_path)
    _capture_probe_env(monkeypatch, [_ERROR_REPORT, _ERROR_REPORT])

    assert run_null_stub([contract], tmp_path) == {("c/test_one.py", "test_a")}


def test_an_errored_test_does_not_cost_its_neighbours_their_verdict(
    tmp_path, monkeypatch
):
    """The cost the refusal charged, and the reason it was reversed.

    Refusing the whole contract for one errored test threw away the verdict on
    every honest test beside it. Measured on four ordinary fixture shapes
    (`json.loads`, `Path(...) / x`, `re.compile`, `datetime.strptime`), all four
    refused, three of them fully honest contracts. Here the errored test is
    named and the green one beside it is not, so a contract carrying a test that
    asserts something is unaffected.
    """
    from scripts.utils.canopus_contract import run_null_stub

    contract = _one_import_contract(tmp_path)
    _capture_probe_env(
        monkeypatch, [_ERROR_BESIDE_A_GREEN_REPORT, _ERROR_BESIDE_A_GREEN_REPORT]
    )

    assert run_null_stub([contract], tmp_path) == {
        ("c/test_one.py", "test_a"), ("c/test_one.py", "test_b"),
    }


def test_the_errored_tests_are_reported_on_stderr(tmp_path, monkeypatch, capsys):
    """The instrument names its own contribution, or the label is unreadable.

    An error under the stub is most often this probe's stand-in meeting a library
    that type-checks its argument, so an operator reading a vacuity refusal has to
    be able to tell which entries came from the instrument rather than from their
    contract. Silence here turns a manufactured accusation into an unexplained
    one.
    """
    from scripts.utils.canopus_contract import run_null_stub

    contract = _one_import_contract(tmp_path)
    _capture_probe_env(monkeypatch, [_ERROR_REPORT, _ERROR_REPORT])

    run_null_stub([contract], tmp_path)

    err = capsys.readouterr().err
    assert "c/test_one.py::test_a" in err
    assert "ERRORED" in err


def test_an_error_in_only_one_stub_run_is_still_not_proved_innocent(
    tmp_path, monkeypatch
):
    """One instrument reading out of two is not half a measurement.

    An errored run has no outcome to compare, so the differential is undefined
    for that test, and undefined is not innocent. It is named on the same rule
    that already governs a test skipped in one run and passing in the other, and
    the stderr report above is what makes the instrument's hand visible.
    """
    from scripts.utils.canopus_contract import run_null_stub

    contract = _one_import_contract(tmp_path)
    _capture_probe_env(monkeypatch, [_ONE_PASSING_REPORT, _ERROR_REPORT])

    assert run_null_stub([contract], tmp_path) == {("c/test_one.py", "test_a")}


def test_a_healthy_fixture_that_errors_under_the_stub_keeps_the_contract(tmp_path):
    """The measured false accusation, against a real child rather than a fake.

    `json.loads(RAW)` in a fixture is ordinary pytest and the authoring rule
    permits it, so the stub reaching `json.loads` errors the test through no
    fault of the contract. Before this change that ERROR refused the whole
    contract, including the honest test beside it, and a gate that refuses this
    shape is one an operator routes around.
    """
    from scripts.utils.canopus_contract import run_null_stub, vacuity_refusal

    _write(tmp_path, "present.py", "def answer():\n    return 1\n")
    _write(
        tmp_path, "c/test_one.py",
        "import json\n"
        "import pytest\n\n\n"
        "@pytest.fixture\n"
        "def subject():\n"
        "    from absent_thing import RAW\n"
        "    return json.loads(RAW)\n\n\n"
        "def test_errors_in_its_fixture(subject):\n"
        "    assert subject['k'] == 3\n\n\n"
        "def test_asserts_a_real_value():\n"
        "    from present import answer\n"
        "    assert answer() == 42\n",
    )

    vacuous = run_null_stub([tmp_path / "c"], tmp_path)

    assert ("c/test_one.py", "test_asserts_a_real_value") not in vacuous
    outcomes = [
        ("c/test_one.py", "test_errors_in_its_fixture", "error"),
        ("c/test_one.py", "test_asserts_a_real_value", "failure"),
    ]
    assert vacuity_refusal(outcomes, vacuous) == []


def test_a_file_that_lost_only_some_of_its_tests_is_refused(tmp_path):
    """The measured escape the presence test could not see.

    A file that loses only SOME of its tests keeps its key in both stub runs'
    per-file counts, so a guard asking whether the file is PRESENT sees nothing.
    Measured, one file and two tests, both vacuous: the real run collected 2 and
    both were red, the stub runs collected 1, the verdict named one pair,
    `vacuity_refusal` found `cases` outside `vacuous`, and the contract froze.

    The channel is a module-scope comparison between two attributes of a stubbed
    module, which the stub answers the same way under both value sets, so the
    `if` guarding the second test flips and that test is never defined.
    """
    from scripts.utils.canopus_contract import ContractError, run_null_stub

    _write(tmp_path, "realmod.py", "X = 1\nY = 2\n")
    _write(
        tmp_path, "c/test_one.py",
        "import realmod\n"
        "HIDDEN = (realmod.X == realmod.Y)\n\n\n"
        "def test_one():\n"
        "    from realmod.child import thing\n"
        "    assert thing() == thing()\n\n\n"
        "if not HIDDEN:\n"
        "    def test_two():\n"
        "        from realmod.child import other\n"
        "        assert other() == other()\n",
    )

    with pytest.raises(ContractError, match="c/test_one.py"):
        run_null_stub([tmp_path / "c"], tmp_path)


def test_the_lost_test_refusal_names_it_rather_than_asking_if_the_key_is_there(
    tmp_path, monkeypatch
):
    """The same finding at the seam, and the message carries the per-file tally.

    A file's KEY in the stub counts is satisfied by one surviving test. The real
    run's own population is the only thing that knows which tests were supposed
    to be there, which is why the probe now runs its own baseline when the caller
    supplies none, and the counts beside the names are what point at the
    module-scope statement that lost them.
    """
    from scripts.utils.canopus_contract import ContractError, run_null_stub

    contract = _one_import_contract(
        tmp_path,
        "def test_a():\n    import absent_thing\n\n\n"
        "def test_b():\n    import absent_thing\n",
    )
    _capture_probe_env(monkeypatch, baseline=_TWO_TEST_RED_REPORT)

    with pytest.raises(ContractError, match="c/test_one.py"):
        run_null_stub([contract], tmp_path)


def test_a_file_the_real_run_also_lost_is_not_blamed_on_the_stub(tmp_path):
    """The refusal must not state something false, or abort the wrong command.

    `probe` calls this function unconditionally, before any table is printed, so
    a file the REAL run never collected reached the operator as "the real run
    collected it, so the stub is what lost it" and took the whole per-file table
    down with it. `refusal_reasons` owns that file and diagnoses it correctly;
    this probe must leave it alone.
    """
    from scripts.utils.canopus_contract import run_null_stub

    _write(tmp_path, "c/test_one.py",
           "def test_a():\n"
           "    from absent_thing import answer\n"
           "    assert answer() is not None\n")
    _write(tmp_path, "c/test_two.py",
           "raise RuntimeError('this module blows up at import')\n")

    assert run_null_stub([tmp_path / "c"], tmp_path) == {
        ("c/test_one.py", "test_a"),
    }


def test_two_test_classes_in_one_file_do_not_collide(tmp_path):
    """xunit1 puts the bare METHOD name in `name`, and the class in `classname`.

    Measured: `TestVacuous.test_x` (vacuous) and `TestHonest.test_x` (a real
    assertion) collapsed to one `(file, name)` pair, which landed in the vacuous
    set from one and in the case list from the other, so `cases <= vacuous` held
    and the whole contract was refused. `class TestRead` beside `class TestWrite`
    is not an adversarial shape.
    """
    from scripts.utils.canopus_contract import (
        run_contract, run_null_stub, vacuity_refusal,
    )

    _write(tmp_path, "present.py", "def answer():\n    return 1\n")
    _write(
        tmp_path, "c/test_one.py",
        "class TestVacuous:\n"
        "    def test_x(self):\n"
        "        from absent_thing import answer\n"
        "        assert answer() is not None\n\n\n"
        "class TestHonest:\n"
        "    def test_x(self):\n"
        "        from present import answer\n"
        "        assert answer() == 42\n",
    )

    _counts, outcomes = run_contract([tmp_path / "c"], tmp_path)
    vacuous = run_null_stub([tmp_path / "c"], tmp_path)

    assert vacuous == {("c/test_one.py", "TestVacuous.test_x")}
    assert vacuity_refusal(outcomes, vacuous) == []


def test_a_module_level_function_keeps_its_bare_name(tmp_path):
    """The qualification must not widen a name the frozen contract asserts.

    For a module-level function xunit1's `classname` is the MODULE path, so
    there is no class to qualify with and the name stays exactly `test_a`.
    Qualifying unconditionally would rewrite every id the frozen contract pins.
    """
    from scripts.utils.canopus_contract import run_contract

    _write(tmp_path, "c/test_one.py", "def test_a():\n    assert False\n")

    _counts, outcomes = run_contract([tmp_path / "c"], tmp_path)

    assert outcomes == [("c/test_one.py", "test_a", "failure")]


def test_a_nested_test_class_carries_its_whole_chain(tmp_path):
    """Two classes deep is still one name, and the chain is what keeps it unique.

    Truncating to the innermost class would collapse `TestA.TestInner.test_x` and
    `TestB.TestInner.test_x`, which is the same defect one level down.
    """
    from scripts.utils.canopus_contract import run_contract

    _write(
        tmp_path, "c/test_one.py",
        "class TestOuter:\n"
        "    class TestInner:\n"
        "        def test_x(self):\n"
        "            assert False\n",
    )

    _counts, outcomes = run_contract([tmp_path / "c"], tmp_path)

    assert outcomes == [
        ("c/test_one.py", "TestOuter.TestInner.test_x", "failure"),
    ]


def test_failure_modes_key_on_the_same_qualified_name(tmp_path):
    """One key space, or the two readers of one report cannot be joined.

    The CLI looks a test's failure mode up by the id the outcome list carries, so
    a `parse_junit` that qualifies a method name and a `parse_failure_modes` that
    does not would miss on every method and print no mode at all.
    """
    from scripts.utils.canopus_contract import parse_failure_modes, run_pytest_report

    _write(
        tmp_path, "c/test_one.py",
        "class TestThing:\n"
        "    def test_x(self):\n"
        "        assert 1 == 2\n",
    )

    modes = parse_failure_modes(run_pytest_report([tmp_path / "c"], tmp_path))

    assert modes == {("c/test_one.py", "TestThing.test_x"): "assertion"}


def test_a_collected_name_carrying_a_nul_byte_is_dropped_and_reported(
    tmp_path, monkeypatch, capsys
):
    """The AST reader over-reports, and a NUL cannot cross the process boundary.

    `__import__("a\\x00b")` puts a NUL-bearing string into the claim set, and
    joining that into an environment variable raises `ValueError: embedded null
    byte` out of `subprocess` rather than the `ContractError` this module
    promises. Dropped in the same place, and for the same reason, as a name
    carrying the separator: a NUL cannot appear in an importable dotted name, so
    nothing that could ever be imported is lost.
    """
    from scripts.utils.canopus_contract import run_null_stub
    from scripts.utils.canopus_nullstub import MODULES_VAR

    contract = _one_import_contract(
        tmp_path,
        "def test_a():\n"
        "    __import__('absent_thing')\n"
        "    __import__('a\\x00b')\n",
    )
    seen = _capture_probe_env(monkeypatch)

    run_null_stub([contract], tmp_path)

    assert seen[0][MODULES_VAR] == "absent_thing"
    assert "not claimed" in capsys.readouterr().err


def test_the_separator_and_the_marker_are_defined_once_in_the_plugin():
    """One rule about the child's wire format, in the module that owns it.

    The separator this side joins on and the marker this side greps for are both
    the CHILD's format, and the argument that already imports `MODULES_VAR` from
    the plugin applies verbatim: two definitions of one rule are a rename on one
    side away from a child that claims nothing and a verdict that is silently
    always empty. Read from the source rather than by identity, because a
    one-character string literal is interned and two definitions of `","` would
    compare identical anyway.
    """
    from scripts.utils import canopus_contract, canopus_nullstub

    assert (
        canopus_contract.STUB_NAME_SEPARATOR
        == canopus_nullstub.STUB_NAME_SEPARATOR
    )
    assert (
        canopus_contract.NULLSTUB_STDERR_MARKER
        is canopus_nullstub.NULLSTUB_STDERR_MARKER
    )
    source = Path(canopus_contract.__file__).read_text(encoding="utf-8")
    assert "STUB_NAME_SEPARATOR = " not in source
    assert "NULLSTUB_STDERR_MARKER = " not in source


def test_the_callers_timeout_reaches_every_probe_child(tmp_path, monkeypatch):
    """Nothing pinned this, and the fallback happens to be the same default.

    Drop the forwarding and every other test still passes, because
    `run_pytest_report`'s own default is 900 too. A caller asking for 61 seconds
    would silently get 900, and the probe is the slowest thing this tool runs.

    THREE children, and the number is the honest statement of the cost: the
    timeout is per child, so a caller asking for 61 seconds is asking for a
    worst case of 183.
    """
    from scripts.utils import canopus_contract
    from scripts.utils.canopus_contract import run_null_stub

    contract = _one_import_contract(tmp_path)
    seen: list = []

    def _fake(paths, root, **kwargs):
        seen.append(kwargs.get("timeout"))
        return _ONE_PASSING_REPORT

    monkeypatch.setattr(canopus_contract, "run_pytest_report", _fake)

    run_null_stub([contract], tmp_path, timeout=61)

    assert seen == [61, 61, 61]


def test_a_wholly_vacuous_contract_is_refused():
    from scripts.utils.canopus_contract import vacuity_refusal

    outcomes = [("c/test_one.py", "test_a", "failure"),
                ("c/test_one.py", "test_b", "failure")]
    vacuous = {("c/test_one.py", "test_a"), ("c/test_one.py", "test_b")}

    reasons = vacuity_refusal(outcomes, vacuous)

    assert len(reasons) == 1
    assert "asserts nothing" in reasons[0]


def test_the_vacuity_refusal_names_the_other_readings():
    """The refusal must not assert vacuity as the only explanation."""
    from scripts.utils.canopus_contract import vacuity_refusal

    reasons = vacuity_refusal(
        [("c/test_one.py", "test_a", "failure")], {("c/test_one.py", "test_a")}
    )

    assert len(reasons) == 1
    assert "asserts nothing" in reasons[0]
    assert "not installed" in reasons[0]


def test_the_vacuity_refusal_names_the_unmeasured_error_reading():
    """The reading the stderr report names, said again where the verdict is.

    A test that ERRORED under both stub runs is labelled vacuous by the rule
    that an outcome invariant to the stub value was not proved innocent, and an
    error is most often this probe's own stand-in reaching a caller that
    type-checks its argument. When every entry in `vacuous` arrived that way the
    bare sentence "the contract's redness asserts nothing" is false: it was not
    measured. The refusal cannot tell those entries apart, so it names the
    reading unconditionally instead of implying the one it cannot prove.
    """
    from scripts.utils.canopus_contract import vacuity_refusal

    reasons = vacuity_refusal(
        [("c/test_one.py", "test_a", "failure")], {("c/test_one.py", "test_a")}
    )

    assert "ERRORED" in reasons[0]
    assert "not measured" in reasons[0]


def test_partial_vacuity_is_reported_and_not_refused():
    """One red test that still asserts something is a contract worth freezing."""
    from scripts.utils.canopus_contract import vacuity_refusal

    outcomes = [("c/test_one.py", "test_a", "failure"),
                ("c/test_one.py", "test_b", "failure")]

    assert vacuity_refusal(outcomes, {("c/test_one.py", "test_a")}) == []


def test_only_the_red_tests_are_weighed_as_evidence_of_vacuity():
    """The filter, pinned by the one case where it changes the answer.

    A test that asserts the code is still ABSENT passes for real and FAILS under
    the stub, which makes the absent import succeed. Counting that green test as
    a case would leave `cases` outside `vacuous` and let a contract whose every
    red test asserts nothing walk through the refusal on the strength of a test
    that was never evidence either way.
    """
    from scripts.utils.canopus_contract import vacuity_refusal

    outcomes = [("c/test_one.py", "test_absence", "passed"),
                ("c/test_one.py", "test_red", "failure")]
    vacuous = {("c/test_one.py", "test_red")}

    assert vacuity_refusal(outcomes, vacuous)


def test_an_all_green_contract_is_not_this_refusals_business():
    """`refusal_reasons` owns that case, and owning it twice worsens the message.

    Without the emptiness guard an all-green contract has no red cases at all,
    the empty set is a subset of everything, and this refusal fires with a reason
    about mocks over a contract that never ran one.
    """
    from scripts.utils.canopus_contract import vacuity_refusal

    outcomes = [("c/test_one.py", "test_a", "passed")]

    assert vacuity_refusal(outcomes, {("c/test_one.py", "test_a")}) == []


def test_one_skipped_test_does_not_defeat_the_refusal():
    """The fail-open the filter shipped with: `outcome != "passed"`.

    `_outcome` emits four tokens, and a skipped test is never in `vacuous`,
    which is built from what PASSED under the stub. Under the old filter one
    skip put a member in `cases` that could not be in `vacuous`, the subset
    failed, and a wholly vacuous contract froze.
    """
    from scripts.utils.canopus_contract import vacuity_refusal

    outcomes = [("c/test_one.py", "test_a", "failure"),
                ("c/test_one.py", "test_b", "failure"),
                ("c/test_one.py", "test_skipped", "skipped")]
    vacuous = {("c/test_one.py", "test_a"), ("c/test_one.py", "test_b")}

    assert vacuity_refusal(outcomes, vacuous)


def test_an_xfail_reaches_the_filter_as_skipped_and_not_as_red(tmp_path):
    """Why the skip bypass has a second door with a different name.

    xunit1 records an expected failure as a `skipped` child, so `xfail` is the
    same escape hatch spelled differently. Pinned against real pytest rather
    than asserted about it, because the whole finding turns on which token the
    reporter actually writes.
    """
    from scripts.utils.canopus_contract import run_contract

    _write(tmp_path, "c/test_one.py",
           "import pytest\n\n\n"
           "@pytest.mark.xfail(reason='not implemented')\n"
           "def test_x():\n    assert False\n")

    _counts, outcomes = run_contract([tmp_path / "c"], tmp_path)

    assert [outcome for _rel, _name, outcome in outcomes] == ["skipped"]


def test_failure_modes_tell_an_import_from_an_assertion(tmp_path):
    from scripts.utils.canopus_contract import parse_failure_modes, run_pytest_report

    _write(tmp_path, "c/test_one.py",
           "def test_import():\n    import absent_thing\n    assert absent_thing\n"
           "\n\n"
           "def test_assertion():\n    assert 1 == 2\n")

    modes = parse_failure_modes(run_pytest_report([tmp_path / "c"], tmp_path))

    assert modes[("c/test_one.py", "test_import")] == "import"
    assert modes[("c/test_one.py", "test_assertion")] == "assertion"


def test_a_docstring_saying_assert_does_not_make_a_failure_an_assertion(tmp_path):
    """The label describes the failure, never the test's own prose.

    Measured on wire 2.2's contract at its Fix 1 probe: eleven tests failing on
    one identical TypeError printed as seven assertions and four others, decided
    entirely by which docstrings happened to use the word "assert". A label that
    reads the operator's prose back to them is worse than no label, because it
    is read as a measurement.
    """
    from scripts.utils.canopus_contract import parse_failure_modes, run_pytest_report

    _write(tmp_path, "c/test_one.py",
           'def test_type_error():\n'
           '    """This one asserts a green through the gate."""\n'
           '    dict(**{1: 2})\n')

    modes = parse_failure_modes(run_pytest_report([tmp_path / "c"], tmp_path))

    assert modes[("c/test_one.py", "test_type_error")] == "other"


def test_parse_failure_modes_refuses_a_doctype():
    """The third reader of a report goes through the one guarded entry point.

    A second unguarded `ElementTree.fromstring` here would reintroduce the entity
    class that `_parse_report` exists to remove, and it would do it silently,
    because every other test in this file would still pass.
    """
    from scripts.utils.canopus_contract import ContractError, parse_failure_modes

    with pytest.raises(ContractError, match="DOCTYPE"):
        parse_failure_modes(
            '<!DOCTYPE testsuite [<!ENTITY a "aaa">]>\n'
            '<testsuite><testcase file="c/test_one.py" name="test_a"/></testsuite>'
        )


def test_contract_imports_sees_an_import_hidden_in_a_try_block(tmp_path):
    """The whole point: `from None` erases the MESSAGE, never the STATEMENT.

    The AST is what the interpreter executes, so an import cannot be hidden from
    it by anything the author writes in the handler.
    """
    from scripts.utils.canopus_contract import contract_imports

    contract = tmp_path / "c"
    contract.mkdir()
    (contract / "test_one.py").write_text(
        "def test_a():\n"
        "    try:\n"
        "        from absent_thing import answer\n"
        "    except ImportError:\n"
        "        raise AssertionError('nope') from None\n"
        "    assert answer() is not None\n",
        encoding="utf-8",
    )

    assert contract_imports([contract], tmp_path) == {"absent_thing"}


def test_contract_imports_reads_a_conftest_beside_the_test_module(tmp_path):
    """A conftest inside a contract directory is contract source.

    Its fixtures are where the absent code is most naturally reached, so a reader
    that globs `test_*.py` alone misses the contract's only import and reports
    that the contract names nothing.
    """
    from scripts.utils.canopus_contract import contract_imports

    contract = tmp_path / "c"
    (contract / "nested").mkdir(parents=True)
    (contract / "conftest.py").write_text(
        "def _build():\n    from absent_thing import Widget\n    return Widget\n",
        encoding="utf-8",
    )
    (contract / "nested" / "conftest.py").write_text(
        "def _also():\n    from deeper_absence import Thing\n    return Thing\n",
        encoding="utf-8",
    )
    (contract / "nested" / "test_one.py").write_text(
        "def test_a(widget):\n    assert widget\n", encoding="utf-8"
    )

    assert contract_imports([contract], tmp_path) == {
        "absent_thing", "deeper_absence",
    }


def test_contract_imports_reads_the_conftest_beside_a_named_test_file(tmp_path):
    """A file argument brings its own directory's conftest, because pytest does.

    `probe tests/contract/slice/test_contract.py` loads that conftest for the
    run, so a reader that ignored it would report an empty claim set and turn a
    perfectly measurable contract into a refusal.
    """
    from scripts.utils.canopus_contract import contract_imports

    contract = tmp_path / "c"
    contract.mkdir()
    (contract / "conftest.py").write_text(
        "def _build():\n    from absent_thing import Widget\n    return Widget\n",
        encoding="utf-8",
    )
    (contract / "test_one.py").write_text(
        "def test_a(widget):\n    assert widget\n", encoding="utf-8"
    )

    assert contract_imports([contract / "test_one.py"], tmp_path) == {"absent_thing"}


def test_contract_files_still_counts_test_modules_only(tmp_path):
    """The conftest widening is the AST reader's alone, and this is the fence.

    `contract_files` feeds the manifest baseline and the collected-nothing
    refusal. A conftest yields no test items, so counting one there would record
    a baseline of 0 for a file that can never move off it and refuse every
    contract that carries one.
    """
    from scripts.utils.canopus_contract import contract_files

    contract = tmp_path / "c"
    contract.mkdir()
    (contract / "conftest.py").write_text("x = 1\n", encoding="utf-8")
    (contract / "test_one.py").write_text(
        "def test_a():\n    assert False\n", encoding="utf-8"
    )

    assert contract_files([contract], tmp_path) == ["c/test_one.py"]


def test_contract_imports_reads_plain_and_dotted_imports(tmp_path):
    from scripts.utils.canopus_contract import contract_imports

    contract = tmp_path / "c"
    contract.mkdir()
    (contract / "test_one.py").write_text(
        "import sys\n"
        "import a.b.c\n"
        "from d.e import f\n"
        "\n"
        "def test_a():\n"
        "    assert sys\n",
        encoding="utf-8",
    )

    assert contract_imports([contract], tmp_path) == {"sys", "a.b.c", "d.e"}


def test_contract_imports_ignores_a_relative_import(tmp_path):
    """A relative import names no absolute module, so there is nothing to claim.

    Recording it as its bare `.module` text would make the finder claim a name no
    import statement can produce, which is a claim that can only ever be wrong.
    """
    from scripts.utils.canopus_contract import contract_imports

    contract = tmp_path / "c"
    contract.mkdir()
    (contract / "test_one.py").write_text(
        "def test_a():\n"
        "    from . import sibling\n"
        "    assert sibling\n",
        encoding="utf-8",
    )

    assert contract_imports([contract], tmp_path) == set()


def test_contract_imports_ignores_a_dotted_relative_import(tmp_path):
    """The other half of the guard, pinned separately from the bare form above.

    `from . import sibling` has `module=None` AND `level=1`, so the surviving
    `and node.module` half of the guard excludes it on its own: deleting
    `node.level == 0` alone still passes that test. `from .sibling import thing`
    has `module='sibling'` and `level=1`: with only `node.module` guarding, that
    reads as the absolute module `sibling`, a name no import statement in this
    file can produce.
    """
    from scripts.utils.canopus_contract import contract_imports

    contract = tmp_path / "c"
    contract.mkdir()
    (contract / "test_one.py").write_text(
        "def test_a():\n"
        "    from .sibling import thing\n"
        "    assert thing\n",
        encoding="utf-8",
    )

    assert contract_imports([contract], tmp_path) == set()


def test_contract_imports_sees_a_literal_importlib_dotted_call(tmp_path):
    """`importlib.import_module("x")` names no `Import`/`ImportFrom` node at all.

    A contract that reaches its code under test this way would otherwise be
    invisible to the AST reader entirely, the same one-keyword escape this slice
    exists to close, wearing a different costume.
    """
    from scripts.utils.canopus_contract import contract_imports

    contract = tmp_path / "c"
    contract.mkdir()
    (contract / "test_one.py").write_text(
        "import importlib\n"
        "def test_a():\n"
        "    mod = importlib.import_module('absent_thing')\n"
        "    assert mod\n",
        encoding="utf-8",
    )

    assert contract_imports([contract], tmp_path) == {"importlib", "absent_thing"}


def test_contract_imports_sees_a_literal_bare_import_module_call(tmp_path):
    """The `ast.Name` callee shape: `from importlib import import_module`."""
    from scripts.utils.canopus_contract import contract_imports

    contract = tmp_path / "c"
    contract.mkdir()
    (contract / "test_one.py").write_text(
        "from importlib import import_module\n"
        "def test_a():\n"
        "    mod = import_module('absent_thing')\n"
        "    assert mod\n",
        encoding="utf-8",
    )

    assert contract_imports([contract], tmp_path) == {"importlib", "absent_thing"}


def test_contract_imports_sees_a_literal_pytest_importorskip_call(tmp_path):
    from scripts.utils.canopus_contract import contract_imports

    contract = tmp_path / "c"
    contract.mkdir()
    (contract / "test_one.py").write_text(
        "import pytest\n"
        "def test_a():\n"
        "    pytest.importorskip('absent_thing')\n",
        encoding="utf-8",
    )

    assert contract_imports([contract], tmp_path) == {"pytest", "absent_thing"}


def test_contract_imports_sees_a_literal_dunder_import_call(tmp_path):
    from scripts.utils.canopus_contract import contract_imports

    contract = tmp_path / "c"
    contract.mkdir()
    (contract / "test_one.py").write_text(
        "def test_a():\n"
        "    mod = __import__('absent_thing')\n"
        "    assert mod\n",
        encoding="utf-8",
    )

    assert contract_imports([contract], tmp_path) == {"absent_thing"}


def test_contract_imports_sees_a_keyword_importlib_dotted_call(tmp_path):
    """`importlib.import_module(name="x")` is a second spelling, not a synonym.

    The call-branch's own guard used to be `node.args`, so a literal string
    reachable only through a keyword slipped past it with nothing collected,
    the exact one-line escape G1 exists to close.
    """
    from scripts.utils.canopus_contract import contract_imports

    contract = tmp_path / "c"
    contract.mkdir()
    (contract / "test_one.py").write_text(
        "import importlib\n"
        "def test_a():\n"
        "    mod = importlib.import_module(name='absent_thing')\n"
        "    assert mod\n",
        encoding="utf-8",
    )

    assert contract_imports([contract], tmp_path) == {"importlib", "absent_thing"}


def test_contract_imports_sees_a_keyword_dunder_import_call(tmp_path):
    """The `__import__` keyword spelling: `__import__(name="x")`."""
    from scripts.utils.canopus_contract import contract_imports

    contract = tmp_path / "c"
    contract.mkdir()
    (contract / "test_one.py").write_text(
        "def test_a():\n"
        "    mod = __import__(name='absent_thing')\n"
        "    assert mod\n",
        encoding="utf-8",
    )

    assert contract_imports([contract], tmp_path) == {"absent_thing"}


def test_contract_imports_sees_a_keyword_importorskip_call(tmp_path):
    """The `pytest.importorskip` keyword spelling: `modname="x"`."""
    from scripts.utils.canopus_contract import contract_imports

    contract = tmp_path / "c"
    contract.mkdir()
    (contract / "test_one.py").write_text(
        "import pytest\n"
        "def test_a():\n"
        "    pytest.importorskip(modname='absent_thing')\n",
        encoding="utf-8",
    )

    assert contract_imports([contract], tmp_path) == {"pytest", "absent_thing"}


def test_contract_imports_sees_every_keyword_not_just_the_first(tmp_path):
    """A collection loop stopping after one candidate would still pass every
    other test in this file, because none of them puts a second string
    constant ahead of the module name in the same call.

    `minversion` sorts before `modname` in `pytest.importorskip`'s signature,
    so a keyword-argument value list built as `[kw.value for kw in
    node.keywords]` puts `'1.0'` first and `'absent_thing'` second. A loop
    truncated to `candidates[:1]` collects `'1.0'` and drops the module name
    entirely, the exact escape `pytest.importorskip(minversion='1.0',
    modname='absent_thing')` opens in real code.
    """
    from scripts.utils.canopus_contract import contract_imports

    contract = tmp_path / "c"
    contract.mkdir()
    (contract / "test_one.py").write_text(
        "import pytest\n"
        "def test_a():\n"
        "    pytest.importorskip(minversion='1.0', modname='absent_thing')\n",
        encoding="utf-8",
    )

    assert contract_imports([contract], tmp_path) == {
        "pytest", "1.0", "absent_thing",
    }


def test_contract_imports_ignores_a_non_string_literal_dynamic_import_argument(
    tmp_path,
):
    """A non-string constant must not enter the returned set at all.

    `isinstance(value_node.value, str)` is the guard this pins. Delete it and
    `__import__(1)` puts the integer 1 into the set, which is fine here but
    breaks a caller that does `",".join(sorted(modules))` with a raw
    `TypeError` instead of the `ContractError` every other bad-input path in
    this module raises.
    """
    from scripts.utils.canopus_contract import contract_imports

    contract = tmp_path / "c"
    contract.mkdir()
    (contract / "test_one.py").write_text(
        "def test_a():\n"
        "    mod = __import__(1)\n"
        "    assert mod\n",
        encoding="utf-8",
    )

    assert contract_imports([contract], tmp_path) == set()


def test_contract_imports_ignores_a_non_literal_dynamic_import_argument(tmp_path):
    """A name computed at run time is invisible to a static reader, by construction.

    This pins that the reader adds nothing for the call itself rather than
    guessing or raising: only the ordinary `import importlib` statement
    contributes.
    """
    from scripts.utils.canopus_contract import contract_imports

    contract = tmp_path / "c"
    contract.mkdir()
    (contract / "test_one.py").write_text(
        "import importlib\n"
        "def test_a():\n"
        "    name = 'absent_thing'\n"
        "    mod = importlib.import_module(name)\n"
        "    assert mod\n",
        encoding="utf-8",
    )

    assert contract_imports([contract], tmp_path) == {"importlib"}


def test_contract_imports_refuses_a_file_it_cannot_parse(tmp_path):
    """A syntax error must not read as "this contract imports nothing".

    An empty set means nothing is stubbed, every test stays red for its original
    reason, and the vacuity refusal cannot fire. Silence here is the same defect
    class this slice exists to remove.
    """
    from scripts.utils.canopus_contract import ContractError, contract_imports

    contract = tmp_path / "c"
    contract.mkdir()
    (contract / "test_one.py").write_text("def test_a(:\n", encoding="utf-8")

    with pytest.raises(ContractError, match="could not be parsed"):
        contract_imports([contract], tmp_path)


def test_contract_imports_refuses_a_file_that_is_not_valid_utf8(tmp_path):
    """`Path.read_text(encoding="utf-8")` raises `UnicodeDecodeError` on bad bytes.

    Bytes that are not valid UTF-8 raise a `ValueError`, not an `OSError` or a
    `SyntaxError`. Uncaught, it would escape as a raw traceback instead of the
    `ContractError` every other unreadable-contract path raises.
    """
    from scripts.utils.canopus_contract import ContractError, contract_imports

    contract = tmp_path / "c"
    contract.mkdir()
    (contract / "test_one.py").write_bytes(b"\xe9\xe9\xe9\n")

    with pytest.raises(ContractError, match="could not be parsed"):
        contract_imports([contract], tmp_path)


def test_contract_imports_refuses_a_file_it_cannot_read(tmp_path, monkeypatch):
    """The `OSError` half of the handler, exercised deterministically.

    Not via `chmod 000`: this suite may run as root, where chmod does not deny
    read access, so the test would pass locally and lie in CI.
    """
    from scripts.utils.canopus_contract import ContractError, contract_imports

    contract = tmp_path / "c"
    contract.mkdir()
    (contract / "test_one.py").write_text(
        "def test_a():\n    assert True\n", encoding="utf-8"
    )

    def _deny_read(self, *args, **kwargs):
        raise PermissionError("permission denied")

    monkeypatch.setattr(Path, "read_text", _deny_read)

    with pytest.raises(ContractError, match="could not be parsed"):
        contract_imports([contract], tmp_path)


def test_two_slices_sharing_one_basename_are_both_collected(tmp_path):
    """The probe child must run in the import mode the repository pins.

    Every Canopus slice writes its contract to
    `tests/contract/{date}-{slug}/test_contract.py`, so two slices collide on
    module basename under pytest's default prepend mode; `pyproject.toml` pins
    `--import-mode=importlib` precisely to remove that class. `-o addopts=`
    neutralises the repository's addopts wholesale, which took the pin with it,
    so the probe child ran in a DIFFERENT import mode from the gate.

    It was never an escape, because all three children carry the same flags and
    every guard compares like with like. The cost was a false diagnosis:
    measured on this exact shape, the second slice was silently dropped from the
    report and the builder was told to move imports that were already inside the
    test body, advice that cannot work on a collision it does not describe.
    """
    from scripts.utils.canopus_contract import parse_junit, run_pytest_report

    _write(tmp_path, "c/slice-a/test_contract.py",
           "def test_slice_a():\n"
           "    from ghost_a import build\n"
           "    assert build().name == 'a'\n")
    _write(tmp_path, "c/slice-b/test_contract.py",
           "def test_slice_b():\n"
           "    from ghost_b import build\n"
           "    assert build().name == 'b'\n")

    counts, outcomes = parse_junit(run_pytest_report([tmp_path / "c"], tmp_path))

    assert counts == {
        "c/slice-a/test_contract.py": 1,
        "c/slice-b/test_contract.py": 1,
    }
    assert {name for _rel, name, _outcome in outcomes} == {
        "test_slice_a", "test_slice_b",
    }


def test_the_own_package_guard_sees_a_prefix_the_contract_never_spelled(tmp_path):
    """The collision guard must weigh the claims the CHILD makes, not the literals.

    Prefix expansion happens in the child, so a contract importing
    `<own_top>.helper` contributes no literal `<own_top>` for the guard to
    intersect, while the child claims `<own_top>` as an unresolvable prefix and
    stands a stub in for the contract's own package. That is precisely the
    rootdir the guard's own comment says it was written for, and it was the one
    case the literal intersection could not see.
    """
    from scripts.utils.canopus_contract import ContractError, run_null_stub

    contract = _one_import_contract(
        tmp_path,
        "def test_a():\n    from c.helper import build\n    assert build()\n",
    )

    with pytest.raises(ContractError, match="package prefix of its own"):
        run_null_stub([contract], tmp_path)


def test_the_own_package_guard_leaves_a_prefix_that_really_resolves_alone(tmp_path):
    """A prefix the child would NOT claim must not earn a refusal here either.

    `_expand_claims` deliberately leaves a prefix that resolves to a real package
    to `PathFinder`, so nothing stands in for it and the contract's own files are
    collected for real. Expanding the guard's input SYNTACTICALLY, over every
    dotted prefix rather than over the unresolvable ones, would refuse this
    contract for a stub the child never installs.
    """
    from scripts.utils.canopus_contract import run_null_stub

    contract = tmp_path / "scripts"
    contract.mkdir()
    (contract / "test_one.py").write_text(
        "def test_a():\n"
        "    from scripts.utils.absent_thing import build\n"
        "    assert build().name == 'x'\n",
        encoding="utf-8",
    )

    assert run_null_stub([contract], tmp_path) == set()


def test_the_cli_does_not_exit_0_having_measured_nothing_on_an_exiting_ancestor(
    tmp_path, monkeypatch, capsys
):
    """The crossing the finding rode through: unit and CLI were tested apart.

    `_expand_claims` is called directly by `canopus`'s own CLI process, in
    `run_null_stub`'s own-package collision guard above, to predict the child's
    claim before any child is spawned. There is no probe-child process boundary
    around that call, so an ordinary `sys.exit(0)` in an ancestor package's
    `__init__.py` — reached only because the contract names the deeper
    `sneaky.mid.leaf` — used to walk past `except Exception`, past
    `cmd_freeze`'s and `cmd_probe`'s own `except ContractError`, and out of
    `main()` uncaught: the CLI exited 0 having printed nothing and measured
    nothing, which the operator reads as a clean pass.

    `monkeypatch.syspath_prepend` puts the ancestor on THIS process's
    `sys.path` only. The pytest children this command spawns (the real
    baseline run and the two stub runs) read `PYTHONPATH` from the
    environment, never this process's `sys.path`, so none of them ever see the
    ancestor and the escape stays isolated to the one call site the finding
    names — exactly the crossing a unit test on `_expand_claims` alone, or a
    CLI test built without this isolation, cannot exercise.
    """
    import scripts.canopus as canopus

    root = tmp_path / "root"
    (root / "scripts").mkdir(parents=True)
    (root / "scripts" / "run-tests.py").write_text("", encoding="utf-8")
    contract = root / "tests" / "contract" / "sysexit-slice"
    contract.mkdir(parents=True)
    (contract / "test_contract.py").write_text(
        "def test_claims_a_deep_prefix():\n"
        "    from sneaky.mid.leaf import answer\n"
        "    assert len(answer()) == 0\n",
        encoding="utf-8",
    )
    ancestor = tmp_path / "ancestor"
    (ancestor / "sneaky" / "mid").mkdir(parents=True)
    (ancestor / "sneaky" / "__init__.py").write_text(
        "import sys\nsys.exit(0)\n", encoding="utf-8"
    )
    (ancestor / "sneaky" / "mid" / "__init__.py").write_text("", encoding="utf-8")
    monkeypatch.syspath_prepend(str(ancestor))

    try:
        exit_code = canopus.main(["--root", str(root), "probe", str(contract)])
    except SystemExit as exc:
        pytest.fail(
            f"canopus.main escaped via an uncaught SystemExit({exc.code}) "
            f"having measured nothing, printing nothing on the way out"
        )

    # A returned int, not a raised SystemExit, is the property this test pins:
    # `main()` ran to its own end and reported a real verdict, whatever that
    # verdict is, rather than having the interpreter torn down from inside an
    # ancestor's import.
    assert isinstance(exit_code, int)
    captured = capsys.readouterr()
    assert captured.out or captured.err


def test_the_greedy_payload_carries_nothing_the_contract_did_not_write(tmp_path):
    """Step 11 mutation SC-3 M7, which survived the frozen contract.

    That contract asserted two literals were PRESENT in the collected set, which
    is satisfied while the set also carries a vocabulary the contract never
    wrote. It is the "a word appears somewhere" shape this whole probe exists to
    refuse, in the probe's own contract.

    Exclusion is what has to hold, because inclusion is what a candidate
    manufacturing refusals would also satisfy: a payload carrying strings from
    anywhere but the contract turns every honest substring assertion into a
    refusal the instrument invented.
    """
    from scripts.utils.canopus_contract import contract_literals

    _write(tmp_path, "c/test_one.py",
           "def test_a():\n"
           "    from absent_thing import render\n"
           "    assert 'the exact sentence' in render()\n")

    literals = contract_literals([tmp_path / "c"], tmp_path)

    assert literals == {"absent_thing", "the exact sentence"}


def test_the_refusal_carries_one_candidates_cure_and_not_the_others(tmp_path):
    """Step 11 mutation SC-4 M12, which survived the frozen contract.

    That contract asserted the word "none" was absent from a refusal naming
    `greedy`. The cure sentences deliberately avoid each other's NAMES, so
    joining all three introduced no "none" and the assertion held while the
    refusal recited the whole glossary. A test written against a word cannot see
    a regression the words were chosen to avoid.

    Asserted against the cure TEXT, which is the thing that must not be
    recited, rather than against a token that happens to appear in it.
    """
    from scripts.utils.canopus_contract import (
        _CANDIDATE_CURE,
        pass_candidate_refusal,
    )

    reasons = pass_candidate_refusal(
        [("c/t.py", "test_one", "failure")],
        {"none": set(), "echo": set(), "greedy": {("c/t.py", "test_one")}},
    )

    assert len(reasons) == 1
    assert _CANDIDATE_CURE["greedy"] in reasons[0]
    assert _CANDIDATE_CURE["none"] not in reasons[0]
    assert _CANDIDATE_CURE["echo"] not in reasons[0]


def test_a_payload_too_large_for_one_environment_value_is_capped_not_raised():
    """The size half of the boundary `passable_literals` already guards for NUL.

    Linux caps ONE `execve` string at MAX_ARG_STRLEN (32 pages, 131072 bytes)
    and answers E2BIG above it, which `subprocess` raises as `OSError` -- not
    the `ContractError` this module promises its callers, so `canopus.py` would
    catch it under "the frozen contract could not be read" and file it in the
    ledger as `unreadable`: the wrong sentence about the wrong file, and the
    wrong cause counted in the yield report.

    Measured on this repository 2026-08-04: the whole of `tests/` read as one
    contract produced a 798034-byte payload, six times over the ceiling, so this
    is reachable by a contract SET rather than only in theory.

    Asserts the joined payload -- the thing that actually crosses the boundary
    -- and not the list length, because a cap that bounded the count while the
    strings stayed enormous would pass a count assertion and still raise E2BIG.
    """
    from scripts.utils.canopus_contract import PAYLOAD_BUDGET, passable_literals
    from scripts.utils.canopus_nullstub import greedy_payload

    literals = {f"{index:06d}" + "x" * 4096 for index in range(64)}
    assert sum(len(v) + 1 for v in literals) > PAYLOAD_BUDGET, (
        "the fixture no longer exceeds the budget, so this test cannot fail"
    )

    payload = greedy_payload(passable_literals(literals))

    assert len(payload.encode("utf-8")) <= PAYLOAD_BUDGET
    assert PAYLOAD_BUDGET < 131072, (
        "the budget must leave head-room under MAX_ARG_STRLEN for the marker, "
        "the separators and the platform's own accounting"
    )


def test_the_cap_keeps_the_short_needles_a_substring_assertion_greps_for():
    """Smallest first, because dropping the wrong end guts the probe.

    A substring assertion greps for a short needle; what makes a payload
    enormous is a docstring paragraph. A cap that dropped the short strings
    would leave the greedy candidate unable to satisfy the very assertions it
    exists to expose, so the probe would quietly stop refusing weak contracts
    while still printing a candidate line.
    """
    from scripts.utils.canopus_contract import PAYLOAD_BUDGET, passable_literals

    needle = "refused"
    literals = {needle} | {
        f"{index:06d}" + "y" * 8192 for index in range(PAYLOAD_BUDGET // 8192 + 4)
    }

    kept = passable_literals(literals)

    assert needle in kept, "the cap dropped the short literal, not the long ones"


def test_the_cap_reports_itself_rather_than_narrowing_the_probe_in_silence():
    """A probe that measured part of a contract must not print the same page.

    The rule `run_pass_candidates` already follows for a candidate that lost
    tests. Dropping can only make the greedy candidate satisfy LESS, so it can
    only fail to refuse a weak contract -- but an operator reading a clean page
    still has to be told the instrument was narrowed.
    """
    import io
    from contextlib import redirect_stderr

    from scripts.utils.canopus_contract import PAYLOAD_BUDGET, passable_literals

    small = io.StringIO()
    with redirect_stderr(small):
        passable_literals({"ok", "fine"})
    assert small.getvalue() == "", "a payload under budget reported a drop"

    loud = io.StringIO()
    with redirect_stderr(loud):
        passable_literals({f"{i:06d}" + "z" * 8192
                           for i in range(PAYLOAD_BUDGET // 8192 + 4)})
    assert "greedy pass-candidate" in loud.getvalue()
    assert "dropped" in loud.getvalue()


# ============================================================
# interpreter_notice — the environment question, not the inode question
# ============================================================

def test_the_notice_fires_when_a_venv_symlinks_to_the_invoking_interpreter(tmp_path):
    """The layout `python -m venv` produces, which the first spelling read as
    "the same interpreter" and passed over in silence.

    Built with real symlinks rather than described, because the defect lived
    ENTIRELY in symlink following: the contract's own SC-3 case compares two
    paths that do not exist on disk, where `Path.resolve()` has nothing to
    follow and so cannot expose it. A stdlib venv symlinks `.venv/bin/python`
    to the very interpreter the operator typed, so resolving both sides
    collapsed them onto one real file — and the one case this notice exists for
    is precisely the one it then stopped reporting.

    The two ARE different environments. `pyvenv.cfg` beside `bin/` is what puts
    the venv's `site-packages` on the child's path, so the plugin set the freeze
    captures under the left-hand path is not the set the right-hand one loads.
    That difference is the whole subject of the sentence.
    """
    from scripts.utils.canopus_contract import interpreter_notice

    system = tmp_path / "usr" / "bin"
    system.mkdir(parents=True)
    real = system / "python3"
    real.write_text("#!/bin/sh\n", encoding="utf-8")
    venv_bin = tmp_path / "project" / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    (venv_bin / "python").symlink_to(real)

    notice = interpreter_notice(venv_bin / "python", real)

    assert notice, (
        "a venv whose interpreter symlinks to the invoking one was called the "
        "same environment, so the capture that reads a different site-packages "
        "passed in silence")
    assert str(venv_bin / "python") in notice
    assert str(real) in notice
    assert "\n" not in notice


def test_two_names_for_one_interpreter_in_one_venv_say_nothing(tmp_path):
    """The pairing, without which the test above is satisfied by shouting always.

    `python3` beside `python` inside the same `bin/` is the same environment
    under a second name — the alias every venv ships. A notice here is the noise
    that trains an operator to stop reading the line.
    """
    from scripts.utils.canopus_contract import interpreter_notice

    venv_bin = tmp_path / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    (venv_bin / "python").write_text("#!/bin/sh\n", encoding="utf-8")
    (venv_bin / "python3").symlink_to(venv_bin / "python")

    assert interpreter_notice(venv_bin / "python3", venv_bin / "python") == ""


def test_two_interpreters_sharing_a_directory_are_still_told_apart(tmp_path):
    """The narrowing must not become a widening one directory over.

    Comparing the containing directory ALONE would call these two the same, and
    they are not: distinct real files, distinct standard libraries, distinct
    plugin sets. The identity carries the real file beside the directory so this
    stays strictly narrower than the comparison it replaced.
    """
    from scripts.utils.canopus_contract import interpreter_notice

    shared = tmp_path / "usr" / "bin"
    shared.mkdir(parents=True)
    (shared / "python3.11").write_text("#!/bin/sh\n", encoding="utf-8")
    (shared / "python3.12").write_text("#!/bin/sh\n", encoding="utf-8")

    assert interpreter_notice(shared / "python3.11", shared / "python3.12")


# ----------------------------------------------------------------------------
# The gap reading: which tests survived every wrong implementation
# ----------------------------------------------------------------------------

def test_a_test_red_under_every_candidate_is_measured_not_a_gap():
    """The reading `--after-build` actually produces on a real target.

    A test that FAILED under all three candidates and a test no candidate ever
    ran are indistinguishable in the taken map: neither appears in it. They are
    opposites. The first is the best a test can do, the second is a test nobody
    measured, and a reading that cannot separate them is not evidence. The
    collected map is what separates them, and this is the case that would be
    refused without it: on a real target most tests go red under replacement,
    so the two-argument form would refuse nearly the whole suite.
    """
    from scripts.utils.canopus_contract import verification_gaps

    outcomes = [("tests/test_subject.py", "test_bites", "passed")]
    pair = {("tests/test_subject.py", "test_bites")}

    assert verification_gaps(
        outcomes,
        {"none": set(), "echo": set(), "greedy": set()},
        {"none": set(pair), "echo": set(pair), "greedy": set(pair)},
    ) == []


def test_the_two_argument_form_cannot_see_a_test_that_bit():
    """The cost of the weaker call, pinned rather than left to be discovered.

    Handed passing sets alone, the same run as the test above has no way to know
    the candidates ever collected that test, so it refuses instead of clearing
    it. That is the honest answer for the evidence supplied and it is the reason
    `probe --after-build` supplies the collected map; a later reader who removes
    the third argument as redundant will find this test rather than a page that
    quietly refuses a whole suite.
    """
    from scripts.utils.canopus_contract import ContractError, verification_gaps

    with pytest.raises(ContractError):
        verification_gaps(
            [("tests/test_subject.py", "test_bites", "passed")],
            {"none": set(), "echo": set(), "greedy": set()},
        )


def test_a_test_one_candidate_never_collected_is_not_cleared():
    """Two verdicts out of three do not add up to "under every candidate".

    The candidate that never collected the test returned no verdict on it, so
    the claim the reading makes cannot be made. Refused by name, on the rule the
    wholly-uncollected test already follows: not measured is not proved
    innocent.
    """
    from scripts.utils.canopus_contract import ContractError, verification_gaps

    pair = {("tests/test_subject.py", "test_partly_seen")}

    with pytest.raises(ContractError) as excinfo:
        verification_gaps(
            [("tests/test_subject.py", "test_partly_seen", "passed")],
            {"none": set(pair), "echo": set(pair), "greedy": set()},
            {"none": set(pair), "echo": set(pair), "greedy": set()},
        )

    assert "test_partly_seen" in str(excinfo.value)


def test_no_candidate_at_all_is_refused_rather_than_clearing_everything():
    """`all()` over no candidates is True for every test in the population.

    An empty map would therefore name the WHOLE suite as green under every
    candidate, which is the shape of a completed measurement and the content of
    none. It is the uncollected-test defect one level up, and it is refused for
    the same reason.
    """
    from scripts.utils.canopus_contract import ContractError, verification_gaps

    with pytest.raises(ContractError):
        verification_gaps([("tests/test_subject.py", "test_a", "passed")], {})


def test_a_supplied_but_empty_collected_map_measures_nothing():
    """Supplied-and-empty is a statement, not the absence of one.

    Read by truthiness it fell through to the union-of-passing-sets reading,
    answering a weaker question than the caller asked and doing it silently. A
    caller that handed over the collected map and filled it with nothing has
    said no candidate collected anything, so nothing is measured and the whole
    population is refused. `test_bites` passed under every candidate here, so
    the truthiness reading cleared it; only the identity reading refuses.
    """
    from scripts.utils.canopus_contract import ContractError, verification_gaps

    pair = {("tests/test_subject.py", "test_bites")}

    with pytest.raises(ContractError) as excinfo:
        verification_gaps(
            [("tests/test_subject.py", "test_bites", "passed")],
            {"none": set(pair), "echo": set(pair), "greedy": set(pair)},
            {},
        )

    assert "test_bites" in str(excinfo.value)


def test_the_refusal_does_not_claim_a_measured_test_was_never_measured():
    """What the two-argument refusal may say about a test that bit.

    It opened "these tests were never put in front of a wrong implementation",
    and that sentence is false for exactly the tests this form cannot see: a
    test put in front of all three candidates that went red under every one of
    them is absent from the taken map for the best possible reason. On the
    2026-08-07 run of `tests/test_canopus_steps.py` that was 14 of 21. The
    refusal is correct to fire; it is not entitled to say why.
    """
    from scripts.utils.canopus_contract import ContractError, verification_gaps

    with pytest.raises(ContractError) as excinfo:
        verification_gaps(
            [("tests/test_subject.py", "test_bites", "passed")],
            {"none": set(), "echo": set(), "greedy": set()},
        )

    assert "never put in front of" not in str(excinfo.value)
    assert "not known to have been put in front of" in str(excinfo.value)


# ----------------------------------------------------------------------------
# The claim narrowing that makes replacement survivable
# ----------------------------------------------------------------------------

def test_a_stdlib_module_is_never_replaced(tmp_path, capsys):
    """Measured 2026-08-07, and it is why this function exists at all.

    `probe --after-build tests/test_canopus_steps.py` claims what that file and
    the conftest beside it import, which includes `os`. Armed, the candidates
    replaced it, `os.environ` read `None`, pytest's own teardown died on
    `'NoneType' object has no attribute 'pop'`, and no JUnit report was written,
    so the parent could report only that the contract could not be measured.

    `os` is FROZEN on this interpreter rather than a file on disk, so it is here
    beside `json` deliberately: a reader that treated "no file" as "does not
    resolve" would keep exactly this claim.
    """
    from scripts.utils.canopus_contract import replaceable_claims

    assert replaceable_claims(["json", "os", "pathlib"], tmp_path) == []
    assert "not replacing" in capsys.readouterr().err


def test_the_trees_own_code_is_what_a_candidate_may_replace(tmp_path):
    """The other half: the narrowing must not narrow to nothing.

    A module of this repository, named directly rather than through its parent
    package, is the ordinary subject of an after-build reading and is kept.
    Asserted against a module that really is on disk here, because the whole
    classification is a question about where a name resolves.
    """
    from pathlib import Path

    from scripts.utils.canopus_contract import replaceable_claims

    engine_root = Path(__file__).resolve().parents[1]

    assert replaceable_claims(
        ["scripts.utils.canopus_contract"], engine_root
    ) == ["scripts.utils.canopus_contract"]


def test_a_claim_that_would_sweep_the_instrument_in_is_dropped(tmp_path, capsys):
    """The second measured death, by a different route from the first.

    Narrowed to the tree's own code, the same command died again:
    `scripts.utils` is a PREFIX of this probe's own plugin module, a claim
    reaches every name below it, so the plugin replaced ITSELF. `CANDIDATES`
    read `None` and pytest reported
    `INTERNALERROR TypeError: argument of type 'NoneType' is not iterable`.

    The message must name the plugin, because the operator's next action is to
    name the subject's own module instead of its parent package, and they cannot
    do that without knowing which package is forbidden and why.
    """
    from pathlib import Path

    from scripts.utils.canopus_contract import replaceable_claims
    from scripts.utils import canopus_nullstub

    engine_root = Path(__file__).resolve().parents[1]

    assert replaceable_claims(["scripts.utils"], engine_root) == []
    assert canopus_nullstub.__name__ in capsys.readouterr().err


def test_a_name_that_does_not_resolve_here_is_kept(tmp_path):
    """Unresolvable is not the same answer as elsewhere, and is load-bearing.

    A name that resolves nowhere has nothing live to destroy, so keeping it
    leaves the absent-name path exactly as it behaved before this narrowing
    existed. It is also how a subject importable only from the contract root
    survives: resolution runs in the PARENT, under the parent's `sys.path`, the
    same trade `run_null_stub` makes when it borrows `_expand_claims` to predict
    the child's claim set.
    """
    from scripts.utils.canopus_contract import replaceable_claims

    assert replaceable_claims(
        ["absent_subject"], tmp_path
    ) == ["absent_subject"]


# ----------------------------------------------------------------------------
# The skip-marker reader: which shapes it reads, and which it does not
# ----------------------------------------------------------------------------

def _skip_marker_tree(root: Path, body: str) -> Path:
    """A scratch contract directory holding one test module. Returns the dir."""
    target = root / "contract"
    target.mkdir()
    (target / "test_sample.py").write_text(
        textwrap.dedent(body), encoding="utf-8"
    )
    return target


def test_an_unreasoned_skip_on_a_class_is_named(tmp_path):
    """A whole class walked through the refusal the reader was added to be.

    The walk read `FunctionDef` and `AsyncFunctionDef` decorators only, so a
    bare `@pytest.mark.skip` on a CLASS was invisible while pytest skipped every
    method under it. Measured before this test existed: a file whose class held
    two tests reported `2 skipped` and the reader named nothing at all. Class
    bodies are the ordinary shape for contracts in this repository, so this is
    the commoner half of the door, not the exotic one.

    The class is named by its own name, on the same rule the function arm
    follows: the refusal names what a reader can grep the contract for.
    """
    from scripts.utils.canopus_contract import skip_markers_without_reason

    target = _skip_marker_tree(tmp_path, """
        import pytest

        @pytest.mark.skip
        class TestParked:
            def test_one(self):
                assert False

            def test_two(self):
                assert False

        def test_loose():
            assert False
    """)

    assert skip_markers_without_reason([target], tmp_path) == ["TestParked"]


def test_an_unreasoned_pytestmark_inside_a_class_body_is_named(tmp_path):
    """The other spelling of the same skip, which pytest honours identically.

    `pytestmark = pytest.mark.skip` in a class body skips every test in that
    class, exactly as the decorator above does. The module-level arm of this
    reader deliberately walks `tree.body` alone so a class's `pytestmark` is
    never mistaken for the module's own; that correctness left the class's own
    marker read by nothing.
    """
    from scripts.utils.canopus_contract import skip_markers_without_reason

    target = _skip_marker_tree(tmp_path, """
        import pytest

        class TestParked:
            pytestmark = pytest.mark.xfail

            def test_one(self):
                assert False
    """)

    assert skip_markers_without_reason([target], tmp_path) == ["TestParked"]


def test_a_reasoned_class_skip_is_left_alone(tmp_path):
    """The calibration in the other direction, so the class arm is not a blanket.

    A class carrying a stated reason is a documented parking, which is exactly
    what this refusal asks for. Refusing it too would teach the operator to
    route around the gate, which the reader's own fail-open rule already
    refuses to do.
    """
    from scripts.utils.canopus_contract import skip_markers_without_reason

    target = _skip_marker_tree(tmp_path, """
        import pytest

        @pytest.mark.skip(reason="the upstream fixture lands in the next slice")
        class TestParked:
            def test_one(self):
                assert False

        class TestAlsoParked:
            pytestmark = pytest.mark.skip(reason="same fixture, same slice")

            def test_two(self):
                assert False
    """)

    assert skip_markers_without_reason([target], tmp_path) == []


# ----------------------------------------------------------------------------
# The third thing a test can be: skipped, which is neither a gap nor a bite
# ----------------------------------------------------------------------------

def test_a_run_in_which_nothing_ran_produces_no_reading():
    """The reading this slice's own instrument printed over a suite nobody ran.

    Measured at HEAD before this test existed, through the CLI: a module
    carrying `pytestmark = pytest.mark.skip` over two tests printed
    `survived 0 of 2`, the green line `none  every test went red under at least
    one candidate`, and exited 0, with zero tests run. A skipped test is absent
    from the taken map for the one reason that is not evidence, so it was folded
    into the bucket the page describes in words as the measurement working.

    Refused rather than answered, on the rule the empty candidate map already
    follows: an answer computed over no tests that ran names none of them, which
    is the shape of a completed measurement and the content of none.
    """
    from scripts.utils.canopus_contract import ContractError, verification_gaps

    pair = ("tests/test_subject.py", "test_parked")

    with pytest.raises(ContractError) as excinfo:
        verification_gaps(
            [(*pair, "skipped")],
            {"none": {pair}, "echo": {pair}, "greedy": {pair}},
            {"none": {pair}, "echo": {pair}, "greedy": {pair}},
        )

    assert "test_parked" in str(excinfo.value)


def test_a_skipped_test_is_not_weighed_against_the_candidates_at_all():
    """It is not a gap, it is not unmeasured, and it is not a test that bit.

    The skipped test here was collected by no candidate, which is the shape that
    used to raise the unmeasured refusal over it. Nothing was owed: no candidate
    could have measured a test that never ran, and refusing the whole reading
    because of one parked test would cost the reading the tests that DID run.
    The bite alongside it is the one the reading is about, and it still bites.
    """
    from scripts.utils.canopus_contract import verification_gaps

    ran = ("tests/test_subject.py", "test_survives")
    parked = ("tests/test_subject.py", "test_parked")

    assert verification_gaps(
        [(*ran, "passed"), (*parked, "skipped")],
        {"none": {ran}, "echo": {ran}, "greedy": {ran}},
        {"none": {ran}, "echo": {ran}, "greedy": {ran}},
    ) == [ran]


def test_the_never_ran_reader_names_the_parked_tests_and_nothing_else():
    """One definition of "this test never ran", read by the page and the reading.

    The reading has to drop these from the population and the page has to name
    them, and two implementations of one rule is how a page and the answer it
    prints come to disagree. The mixed row is the edge: a pair that appears once
    skipped and once not DID run, so it is not named here.
    """
    from scripts.utils.canopus_contract import tests_that_never_ran

    assert tests_that_never_ran([
        ("tests/test_subject.py", "test_ran", "passed"),
        ("tests/test_subject.py", "test_parked", "skipped"),
        ("tests/test_subject.py", "test_mixed", "skipped"),
        ("tests/test_subject.py", "test_mixed", "failure"),
    ]) == [("tests/test_subject.py", "test_parked")]
