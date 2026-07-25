"""Canopus v3: the attestation record.

Exercised through the public helpers. Attestation answers a different question
from the manifest: the manifest says the contract did not move, attestation says
the contract actually ran. A builder that cannot edit a frozen test can still
decline to run it, and every filtered pytest invocation reaches green with the
frozen bytes intact.
"""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

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


def test_tally_counts_only_frozen_files():
    counts = cf.tally_collection(
        ["tests/test_alpha.py", "tests/test_beta.py"],
        ["tests/test_alpha.py", "tests/test_alpha.py", "tests/test_other.py"],
    )
    assert counts["tests/test_alpha.py"]["collected"] == 2
    assert counts["tests/test_beta.py"]["collected"] == 0
    assert "tests/test_other.py" not in counts
    assert counts["tests/test_alpha.py"] == {
        "collected": 2, "passed": 0, "failed": 0, "skipped": 0,
    }


def test_tally_with_no_frozen_files_is_empty():
    assert cf.tally_collection([], ["tests/test_alpha.py"]) == {}


# ============================================================
# The pytest wiring
# ============================================================
#
# The hooks live in the ROOT tests/conftest.py, which pytest has already
# imported by the time this module runs. Reaching it through sys.modules by file
# path exercises the shipped code rather than a copy that can drift, and calling
# the hooks with light fakes keeps the test deterministic: driving a nested
# pytest through `pytester` would need the engine on the child's sys.path and
# would test the harness more than the hooks.

_CONFTEST = next(
    module for module in list(sys.modules.values())
    if getattr(module, "__file__", None) == str(Path(__file__).resolve().parent / "conftest.py")
)


class _Config:
    def __init__(self, patterns, **options):
        self._patterns = patterns
        defaults = {
            "keyword": None, "markexpr": None, "deselect": None, "ignore": None,
            "ignore_glob": None, "lf": False, "failedfirst": False, "stepwise": False,
        }
        defaults.update(options)
        self.option = SimpleNamespace(**defaults)

    def getini(self, name):
        assert name == "python_files"
        return self._patterns


class _Item:
    def __init__(self, path):
        self.path = path


class _Session:
    def __init__(self, config, items):
        self.config = config
        self.items = items


class _Report:
    def __init__(self, fspath, outcome, when="call"):
        self.fspath = fspath
        self.outcome = outcome
        self.when = when


@pytest.fixture
def frozen_engine(tmp_path, monkeypatch):
    """A throwaway tree with one frozen test file, wired as the hooks' root."""
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    target = tests_dir / "test_frozen.py"
    target.write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    anchor = tmp_path.parent / f"anchor-{tmp_path.name}.md"
    anchor.write_text("placeholder\n", encoding="utf-8")
    manifest = cf.build_manifest(
        [target], tmp_path,
        label="attest-fixture", frozen_at="2026-07-25T00:00:00+00:00", anchor=anchor,
    )
    cf.write_freeze(tmp_path, manifest)
    monkeypatch.setattr(_CONFTEST, "_ENGINE_ROOT", tmp_path)
    monkeypatch.setattr(_CONFTEST, "_CANOPUS", {})
    return tmp_path, target, manifest


def _session(tmp_path, target, **options):
    return _Session(_Config(["test_*.py"], **options), [_Item(target)])


def test_an_unfiltered_run_attests(frozen_engine):
    tmp_path, target, manifest = frozen_engine
    session = _session(tmp_path, target)
    _CONFTEST.pytest_collection_finish(session)
    _CONFTEST.pytest_runtest_logreport(_Report(str(target), "passed"))
    _CONFTEST.pytest_sessionfinish(session, 0)

    record = cf.read_attestation(tmp_path)
    assert record["attested"] is True
    assert record["root"] == manifest["root"]
    assert record["frozen_tests"]["tests/test_frozen.py"] == {
        "collected": 1, "passed": 1, "failed": 0, "skipped": 0,
    }
    assert cf.attestation_state(record, manifest["root"])[0] == cf.ATTESTED


def test_a_filtered_run_records_the_reason_and_does_not_attest(frozen_engine):
    tmp_path, target, manifest = frozen_engine
    session = _session(tmp_path, target, keyword="ok")
    _CONFTEST.pytest_collection_finish(session)
    _CONFTEST.pytest_sessionfinish(session, 0)

    record = cf.read_attestation(tmp_path)
    assert record["attested"] is False
    assert record["unfiltered"] is False
    assert any("-k" in reason for reason in record["reasons"])


def test_a_failing_frozen_test_is_tallied(frozen_engine):
    tmp_path, target, manifest = frozen_engine
    session = _session(tmp_path, target)
    _CONFTEST.pytest_collection_finish(session)
    _CONFTEST.pytest_runtest_logreport(_Report(str(target), "failed"))
    _CONFTEST.pytest_sessionfinish(session, 1)

    record = cf.read_attestation(tmp_path)
    assert record["frozen_tests"]["tests/test_frozen.py"]["failed"] == 1
    assert record["attested"] is False


def test_a_skipped_frozen_test_is_counted_but_still_attests(frozen_engine):
    tmp_path, target, manifest = frozen_engine
    session = _session(tmp_path, target)
    _CONFTEST.pytest_collection_finish(session)
    _CONFTEST.pytest_runtest_logreport(_Report(str(target), "skipped", when="setup"))
    _CONFTEST.pytest_sessionfinish(session, 0)

    record = cf.read_attestation(tmp_path)
    assert record["frozen_tests"]["tests/test_frozen.py"]["skipped"] == 1
    assert record["attested"] is True


def test_reports_from_unfrozen_files_are_ignored(frozen_engine):
    tmp_path, target, manifest = frozen_engine
    session = _session(tmp_path, target)
    _CONFTEST.pytest_collection_finish(session)
    _CONFTEST.pytest_runtest_logreport(_Report(str(tmp_path / "tests" / "test_other.py"), "failed"))
    _CONFTEST.pytest_runtest_logreport(_Report("/nowhere/at/all/test_x.py", "failed"))
    _CONFTEST.pytest_sessionfinish(session, 0)

    record = cf.read_attestation(tmp_path)
    assert record["frozen_tests"]["tests/test_frozen.py"]["failed"] == 0
    assert record["attested"] is True


def test_removing_a_frozen_test_from_collection_fails_the_session(frozen_engine):
    tmp_path, target, manifest = frozen_engine
    session = _Session(_Config(["test_*.py"]), [])
    with pytest.raises(pytest.UsageError, match="were not collected"):
        _CONFTEST.pytest_collection_finish(session)


def test_an_explicit_filter_downgrades_the_same_case_to_a_reason(frozen_engine):
    # Same zero-collection state, but the operator asked for a subset. That is
    # iteration, not removal, so it records a reason instead of failing the run.
    tmp_path, target, manifest = frozen_engine
    session = _Session(_Config(["test_*.py"], keyword="nothing"), [])
    _CONFTEST.pytest_collection_finish(session)
    _CONFTEST.pytest_sessionfinish(session, 5)

    record = cf.read_attestation(tmp_path)
    assert record["attested"] is False
    assert any("collected nothing" in reason for reason in record["reasons"])


def test_no_freeze_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(_CONFTEST, "_ENGINE_ROOT", tmp_path)
    monkeypatch.setattr(_CONFTEST, "_CANOPUS", {})
    session = _session(tmp_path, tmp_path / "tests" / "test_frozen.py")
    _CONFTEST.pytest_collection_finish(session)
    _CONFTEST.pytest_runtest_logreport(_Report(str(tmp_path / "x.py"), "passed"))
    _CONFTEST.pytest_sessionfinish(session, 0)
    assert cf.read_attestation(tmp_path) is None


def test_a_broken_collection_never_breaks_the_run(frozen_engine, capsys):
    tmp_path, target, manifest = frozen_engine

    class _Exploding:
        @property
        def config(self):
            raise RuntimeError("boom")

    _CONFTEST.pytest_collection_finish(_Exploding())
    assert "attestation collection failed" in capsys.readouterr().err


def test_a_failed_write_never_changes_the_run(frozen_engine, monkeypatch, capsys):
    tmp_path, target, manifest = frozen_engine
    session = _session(tmp_path, target)
    _CONFTEST.pytest_collection_finish(session)

    def _explode(*args, **kwargs):
        raise OSError("disk gone")

    monkeypatch.setattr(cf, "write_attestation", _explode)
    _CONFTEST.pytest_sessionfinish(session, 0)
    assert "could not write the attestation" in capsys.readouterr().err
