"""Canopus wire 2: the contract runner and its two refusal conditions."""
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


def test_run_contract_does_not_write_an_attestation(tmp_path, monkeypatch):
    from scripts.utils.canopus_contract import run_contract
    from scripts.utils.canopus_freeze import attest_state_path

    _write(tmp_path, "c/test_one.py", "def test_a():\n    assert False\n")
    run_contract([tmp_path / "c"], tmp_path)

    assert not attest_state_path(tmp_path).exists()


def test_recorder_writes_nothing_when_attestation_is_disabled(tmp_path, monkeypatch):
    from scripts.utils.canopus_gate import AttestationRecorder

    monkeypatch.setenv("CANOPUS_NO_ATTEST", "1")
    recorder = AttestationRecorder(tmp_path)
    recorder.frozen = {"tests/test_a.py": {
        "collected": 1, "passed": 1, "failed": 0, "skipped": 0, "deselected": 0,
    }}

    class _Config:
        pass

    class _Session:
        config = _Config()

    assert recorder.finish(_Session(), 0) is False


def test_missing_modules_names_what_the_contract_could_not_import(tmp_path):
    from scripts.utils.canopus_contract import missing_modules, run_pytest_report

    _write(tmp_path, "c/test_one.py",
           "def test_a():\n    from absent_thing import answer\n    assert answer() == 42\n")

    assert "absent_thing" in missing_modules(run_pytest_report([tmp_path / "c"], tmp_path))


def test_a_test_that_passes_against_a_mock_asserts_nothing(tmp_path):
    """The construction proves it: a MagicMock satisfies any shape."""
    from scripts.utils.canopus_contract import run_null_stub

    _write(tmp_path, "c/test_one.py",
           "def test_vacuous():\n"
           "    from absent_thing import answer\n"
           "    assert answer() is not None\n"
           "\n\n"
           "def test_real():\n"
           "    from absent_thing import answer\n"
           "    assert answer() == 42\n")

    passed = run_null_stub([tmp_path / "c"], tmp_path, {"absent_thing"})

    assert ("c/test_one.py", "test_vacuous") in passed
    assert ("c/test_one.py", "test_real") not in passed


def test_the_stub_does_not_shadow_a_sibling_module_that_exists(tmp_path):
    """The blocker a plan review caught before any code was written.

    Matching on the first dotted segment made one absent `scripts.utils.X` mock
    the whole `scripts` package, so modules that exist came back as MagicMock,
    every test passed, and the wholly-vacuous refusal fired on a good contract.
    """
    from scripts.utils.canopus_contract import run_null_stub

    _write(tmp_path, "c/test_one.py",
           "def test_real_module_survives():\n"
           "    from scripts.utils.canopus_freeze import ANCHOR_PREFIX\n"
           "    assert ANCHOR_PREFIX == 'canopus-anchor:'\n")

    passed = run_null_stub(
        [tmp_path / "c"], tmp_path, {"scripts.utils.canopus_absent_thing"}
    )

    assert ("c/test_one.py", "test_real_module_survives") in passed
    # It passes because the REAL constant was imported, not because a mock
    # satisfied the comparison: a MagicMock never equals that string.


def test_the_stub_does_not_shadow_a_module_that_merely_starts_with_a_stubbed_name(
    tmp_path,
):
    """Pins the DOT in the matcher's separator, not just the first-segment case.

    The sibling test above pins `startswith(name.split('.')[0])`. This one pins
    the other way of dropping the dot: a bare `fullname.startswith(name)`. With
    `absent` stubbed, that mutation answers for `absent_extra`, a module that
    EXISTS and whose compute() returns None, so `assert compute() is not None`
    passes against the mock and a contract test that asserts something real is
    labelled vacuous.
    """
    from scripts.utils.canopus_contract import run_null_stub

    _write(tmp_path, "absent_extra.py", "def compute():\n    return None\n")
    _write(tmp_path, "c/test_one.py",
           "def test_prefix_sibling_survives():\n"
           "    from absent_extra import compute\n"
           "    assert compute() is not None\n")

    passed = run_null_stub([tmp_path / "c"], tmp_path, {"absent"})

    # It fails under the stub because the REAL compute() ran and returned None.
    assert ("c/test_one.py", "test_prefix_sibling_survives") not in passed


def test_the_stub_run_leaves_no_file_behind_in_the_tree(tmp_path):
    """The contract directory is frozen recursively; a written conftest would
    read as tampering."""
    from scripts.utils.canopus_contract import run_null_stub

    _write(tmp_path, "c/test_one.py",
           "def test_a():\n    from absent_thing import x\n    assert x\n")
    before = sorted(p.name for p in (tmp_path / "c").iterdir())

    run_null_stub([tmp_path / "c"], tmp_path, {"absent_thing"})

    assert sorted(p.name for p in (tmp_path / "c").iterdir()) == before


def test_a_wholly_vacuous_contract_is_refused():
    from scripts.utils.canopus_contract import vacuity_refusal

    outcomes = [("c/test_one.py", "test_a", "failed"),
                ("c/test_one.py", "test_b", "failed")]
    vacuous = {("c/test_one.py", "test_a"), ("c/test_one.py", "test_b")}

    reasons = vacuity_refusal(outcomes, vacuous)

    assert len(reasons) == 1
    assert "asserts nothing" in reasons[0]


def test_partial_vacuity_is_reported_and_not_refused():
    """One red test that still asserts something is a contract worth freezing."""
    from scripts.utils.canopus_contract import vacuity_refusal

    outcomes = [("c/test_one.py", "test_a", "failed"),
                ("c/test_one.py", "test_b", "failed")]

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
                ("c/test_one.py", "test_red", "failed")]
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


def test_failure_modes_tell_an_import_from_an_assertion(tmp_path):
    from scripts.utils.canopus_contract import parse_failure_modes, run_pytest_report

    _write(tmp_path, "c/test_one.py",
           "def test_import():\n    import absent_thing\n    assert absent_thing\n"
           "\n\n"
           "def test_assertion():\n    assert 1 == 2\n")

    modes = parse_failure_modes(run_pytest_report([tmp_path / "c"], tmp_path))

    assert modes[("c/test_one.py", "test_import")] == "import"
    assert modes[("c/test_one.py", "test_assertion")] == "assertion"


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
