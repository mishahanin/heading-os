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
