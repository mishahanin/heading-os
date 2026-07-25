"""Canopus v3: the attestation record.

Exercised through the public helpers. Attestation answers a different question
from the manifest: the manifest says the contract did not move, attestation says
the contract actually ran. A builder that cannot edit a frozen test can still
decline to run it, and every filtered pytest invocation reaches green with the
frozen bytes intact.
"""
import json

from scripts.utils import canopus_freeze as cf


def _tests(**counts):
    base = {"collected": 1, "passed": 1, "failed": 0, "skipped": 0}
    base.update(counts)
    return base


def _build(frozen_tests, *, reasons=(), exit_status=0, root="a" * 64):
    return cf.build_attestation(
        root_digest=root,
        frozen_tests=frozen_tests,
        filter_reasons=reasons,
        exit_status=exit_status,
        attested_at="2026-07-25T10:42:11+00:00",
    )


def test_frozen_test_files_selects_by_configured_patterns():
    manifest = {"files": {
        "tests/test_alpha.py": "x",
        "tests/helpers.py": "x",
        "scripts/utils/canopus_freeze.py": "x",
    }}
    assert cf.frozen_test_files(manifest, ["test_*.py"]) == ["tests/test_alpha.py"]


def test_frozen_test_files_honours_an_unusual_convention():
    manifest = {"files": {"tests/alpha_test.py": "x", "tests/test_alpha.py": "x"}}
    assert cf.frozen_test_files(manifest, ["*_test.py"]) == ["tests/alpha_test.py"]


def test_frozen_test_files_tolerates_a_manifest_without_files():
    assert cf.frozen_test_files({}, ["test_*.py"]) == []


def test_clean_unfiltered_run_attests():
    record = _build({"tests/test_alpha.py": _tests(collected=3, passed=3)})
    assert record["attested"] is True
    assert record["unfiltered"] is True
    assert record["reasons"] == []
    assert record["recipe"] == cf.ATTEST_RECIPE


def test_a_filter_option_voids_attestation_with_a_reason():
    record = _build({"tests/test_alpha.py": _tests()}, reasons=["-k restricted the run"])
    assert record["attested"] is False
    assert record["unfiltered"] is False
    assert "-k restricted the run" in record["reasons"]


def test_a_nonzero_exit_status_voids_attestation():
    record = _build({"tests/test_alpha.py": _tests()}, exit_status=1)
    assert record["attested"] is False
    assert any("exited 1" in reason for reason in record["reasons"])


def test_a_failing_frozen_test_voids_attestation():
    record = _build({"tests/test_alpha.py": _tests(failed=1, passed=0)})
    assert record["attested"] is False
    assert any("tests/test_alpha.py" in reason for reason in record["reasons"])


def test_a_frozen_test_file_that_collected_nothing_voids_attestation():
    record = _build({"tests/test_alpha.py": _tests(collected=0, passed=0)})
    assert record["attested"] is False
    assert record["unfiltered"] is False


def test_a_freeze_with_no_test_files_attests_nothing():
    # "verify passed" read out of an evidence pack must never be satisfiable by
    # having no contract at all. The same rule applies to "the tests ran".
    record = _build({})
    assert record["attested"] is False
    assert record["unfiltered"] is False
    assert any("no test files" in reason for reason in record["reasons"])


def test_skips_are_counted_and_do_not_void_attestation():
    record = _build({"tests/test_alpha.py": _tests(collected=4, passed=3, skipped=1)})
    assert record["attested"] is True
    assert record["frozen_tests"]["tests/test_alpha.py"]["skipped"] == 1


def test_state_is_attested_only_on_an_exact_root_match():
    record = _build({"tests/test_alpha.py": _tests()})
    state, reason = cf.attestation_state(record, "a" * 64)
    assert state == cf.ATTESTED
    assert reason == ""
    state, reason = cf.attestation_state(record, "b" * 64)
    assert state == cf.NOT_ATTESTED
    assert "different root" in reason


def test_state_handles_absent_unknown_and_unqualified_records():
    assert cf.attestation_state(None, "a" * 64)[0] == cf.NOT_ATTESTED
    assert cf.attestation_state({}, "a" * 64)[0] == cf.NOT_ATTESTED
    assert cf.attestation_state("not a record", "a" * 64)[0] == cf.NOT_ATTESTED
    unknown = {"recipe": "other", "root": "a" * 64, "attested": True}
    assert cf.attestation_state(unknown, "a" * 64)[0] == cf.NOT_ATTESTED
    unqualified = _build({"tests/test_alpha.py": _tests()}, exit_status=1)
    assert cf.attestation_state(unqualified, "a" * 64)[0] == cf.NOT_ATTESTED


def test_round_trip_through_disk(tmp_path):
    record = _build({"tests/test_alpha.py": _tests()})
    cf.write_attestation(tmp_path, record)
    assert cf.attest_state_path(tmp_path).is_file()
    assert cf.read_attestation(tmp_path) == record


def test_a_damaged_attestation_reads_as_absent_and_never_raises(tmp_path, capsys):
    path = cf.attest_state_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\xff\xfe not json at all")
    assert cf.read_attestation(tmp_path) is None
    assert "canopus" in capsys.readouterr().err
    # A JSON scalar is valid JSON and still not a record.
    path.write_text("42", encoding="utf-8")
    assert cf.read_attestation(tmp_path) is None
    assert "canopus" in capsys.readouterr().err


def test_absent_attestation_is_none_and_silent(tmp_path, capsys):
    assert cf.read_attestation(tmp_path) is None
    assert capsys.readouterr().err == ""


def test_written_record_is_sorted_and_newline_terminated(tmp_path):
    record = _build({"tests/test_beta.py": _tests(), "tests/test_alpha.py": _tests()})
    cf.write_attestation(tmp_path, record)
    raw = cf.attest_state_path(tmp_path).read_text(encoding="utf-8")
    assert raw.endswith("\n")
    assert list(json.loads(raw)["frozen_tests"]) == [
        "tests/test_alpha.py", "tests/test_beta.py",
    ]
