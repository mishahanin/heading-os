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
    base = {"collected": 1, "passed": 1, "failed": 0, "skipped": 0, "deselected": 0}
    base.update(counts)
    return base


def _build(frozen_tests, *, exit_status=0, root="a" * 64):
    return cf.build_attestation(
        root_digest=root,
        frozen_tests=frozen_tests,
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


def test_a_complete_clean_run_attests():
    record = _build({"tests/test_alpha.py": _tests(collected=3, passed=3)})
    assert record["attested"] is True
    assert record["reasons"] == []
    assert record["recipe"] == cf.ATTEST_RECIPE


def test_a_deselected_item_voids_attestation():
    record = _build({"tests/test_alpha.py": _tests(collected=3, passed=3, deselected=7)})
    assert record["attested"] is False
    assert any("7 items deselected" in reason for reason in record["reasons"])


def test_an_incomplete_tally_voids_attestation():
    # The xdist failure mode: items collected, only some reported back.
    record = _build({"tests/test_alpha.py": _tests(collected=28, passed=13)})
    assert record["attested"] is False
    assert any("13 of 28" in reason for reason in record["reasons"])


def test_a_marker_expression_that_touches_nothing_frozen_still_attests():
    # Regression for the defect that made scripts/run-tests.py unattestable: it
    # always passes -m "not acceptance", which deselects nothing here.
    record = _build({"tests/test_alpha.py": _tests(collected=3, passed=3, deselected=0)})
    assert record["attested"] is True


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
    assert any("collected nothing" in reason for reason in record["reasons"])


def test_a_freeze_with_no_test_files_attests_nothing():
    # "verify passed" read out of an evidence pack must never be satisfiable by
    # having no contract at all. The same rule applies to "the tests ran".
    record = _build({})
    assert record["attested"] is False
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


def test_a_non_numeric_counter_reads_as_absent(tmp_path, capsys):
    """The reporting side SUMS these counters, so a string in one is a crash.

    Measured: a record carrying "passed": "3" made `canopus verify` raise
    TypeError out of _print_attestation, and TypeError is outside the
    FreezeError/FreezeCorrupt/OSError set main() catches -- so the layer billed
    as the guarantee printed a raw traceback instead of its result. Damage reads
    as absence here, which can only ever be NOT ATTESTED.
    """
    record = _build({"tests/test_alpha.py": _tests()})
    record["frozen_tests"]["tests/test_alpha.py"]["passed"] = "3"
    cf.write_attestation(tmp_path, record)

    assert cf.read_attestation(tmp_path) is None
    assert "integer counters" in capsys.readouterr().err


def test_a_non_mapping_frozen_tests_reads_as_absent(tmp_path, capsys):
    record = _build({"tests/test_alpha.py": _tests()})
    record["frozen_tests"] = ["tests/test_alpha.py"]
    cf.write_attestation(tmp_path, record)

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
        "collected": 2, "passed": 0, "failed": 0, "skipped": 0, "deselected": 0,
    }


def test_tally_with_no_frozen_files_is_empty():
    assert cf.tally_collection([], ["tests/test_alpha.py"]) == {}


# ============================================================
# The pytest wiring
# ============================================================
#
# The root tests/conftest.py delegates every attestation hook to one
# AttestationRecorder. Tests build their own recorder against a throwaway root,
# which is the whole reason the state is an object: an earlier version
# monkeypatched the conftest module's globals and silently corrupted the LIVE
# session's tally, which the end-to-end check caught as "20 of 31 reported".

from scripts.utils.canopus_gate import AttestationRecorder  # noqa: E402


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
def frozen_engine(tmp_path):
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
    return tmp_path, target, manifest, AttestationRecorder(tmp_path)


def _session(tmp_path, target, **options):
    return _Session(_Config(["test_*.py"], **options), [_Item(target)])


def test_a_complete_run_attests(frozen_engine):
    tmp_path, target, manifest, rec = frozen_engine
    session = _session(tmp_path, target)
    rec.collect(session)
    rec.report(_Report(str(target), "passed"))
    rec.finish(session, 0)

    record = cf.read_attestation(tmp_path)
    assert record["attested"] is True
    assert record["root"] == manifest["root"]
    assert record["frozen_tests"]["tests/test_frozen.py"] == {
        "collected": 1, "passed": 1, "failed": 0, "skipped": 0, "deselected": 0,
    }
    assert cf.attestation_state(record, manifest["root"])[0] == cf.ATTESTED


def test_deselection_is_tallied_from_the_hook(frozen_engine):
    tmp_path, target, manifest, rec = frozen_engine
    session = _session(tmp_path, target)
    rec.collect(session)
    rec.deselected([_Item(target), _Item(target)])
    rec.finish(session, 0)

    record = cf.read_attestation(tmp_path)
    assert record["frozen_tests"]["tests/test_frozen.py"]["deselected"] == 2
    assert record["attested"] is False
    assert any("deselected" in reason for reason in record["reasons"])


def test_deselection_before_collection_is_still_tallied(frozen_engine):
    """The REAL pytest order: pytest_deselected fires before collection finish.

    -k, -m, --lf and --deselect all deselect from inside
    pytest_collection_modifyitems, which pytest runs BEFORE
    pytest_collection_finish. The test above calls the two in the opposite
    order, and that inversion hid a total failure of this axis: measured, a
    plain `pytest -k test_a` over a three-test frozen file printed "2
    deselected" and attested "none deselected", because the tally did not exist
    yet when the hook fired and collect() then seeded a fresh zero over it.
    """
    tmp_path, target, manifest, rec = frozen_engine
    session = _session(tmp_path, target)

    rec.deselected([_Item(target), _Item(target)])   # hook order: deselect ...
    rec.collect(session)                             # ... then collection finish
    rec.finish(session, 0)

    record = cf.read_attestation(tmp_path)
    assert record["frozen_tests"]["tests/test_frozen.py"]["deselected"] == 2
    assert record["attested"] is False
    assert any("deselected" in reason for reason in record["reasons"])


def test_deselection_before_controller_seeding_is_still_tallied(frozen_engine):
    """Same inversion on the xdist route, where the controller seeds from ids."""
    tmp_path, target, manifest, rec = frozen_engine
    session = _session(tmp_path, target)

    rec.deselected([_Item(target)])
    rec.seed_from_ids(session.config, ["tests/test_frozen.py::test_one"])
    rec.finish(session, 0)

    record = cf.read_attestation(tmp_path)
    assert record["frozen_tests"]["tests/test_frozen.py"]["deselected"] == 1
    assert record["attested"] is False


def test_a_worker_count_is_not_lowered_by_the_controllers_own_buffer(frozen_engine):
    """Folding the buffer in must never shrink a count merge_worker already set."""
    tmp_path, target, manifest, rec = frozen_engine
    session = _session(tmp_path, target)
    rec.collect(session)
    rec.merge_worker({"canopus_deselected": {"tests/test_frozen.py": 4}})

    rec.deselected([_Item(target)])   # a later fold must not undercut the worker
    rec.finish(session, 0)

    record = cf.read_attestation(tmp_path)
    assert record["frozen_tests"]["tests/test_frozen.py"]["deselected"] == 4


def test_deselection_ignores_items_outside_the_frozen_set(frozen_engine):
    tmp_path, target, manifest, rec = frozen_engine
    session = _session(tmp_path, target)
    rec.collect(session)
    rec.deselected([_Item(tmp_path / "tests" / "test_other.py")])
    rec.report(_Report(str(target), "passed"))
    rec.finish(session, 0)

    record = cf.read_attestation(tmp_path)
    assert record["frozen_tests"]["tests/test_frozen.py"]["deselected"] == 0
    assert record["attested"] is True


def test_a_worker_session_writes_nothing(frozen_engine):
    tmp_path, target, manifest, rec = frozen_engine
    session = _session(tmp_path, target)
    session.config.workerinput = {"workerid": "gw0"}
    rec.collect(session)
    rec.report(_Report(str(target), "passed"))
    rec.finish(session, 0)

    assert cf.read_attestation(tmp_path) is None


def test_a_failing_frozen_test_is_tallied(frozen_engine):
    tmp_path, target, manifest, rec = frozen_engine
    session = _session(tmp_path, target)
    rec.collect(session)
    rec.report(_Report(str(target), "failed"))
    rec.finish(session, 1)

    record = cf.read_attestation(tmp_path)
    assert record["frozen_tests"]["tests/test_frozen.py"]["failed"] == 1
    assert record["attested"] is False


def test_a_skipped_frozen_test_is_counted_but_still_attests(frozen_engine):
    tmp_path, target, manifest, rec = frozen_engine
    session = _session(tmp_path, target)
    rec.collect(session)
    rec.report(_Report(str(target), "skipped", when="setup"))
    rec.finish(session, 0)

    record = cf.read_attestation(tmp_path)
    assert record["frozen_tests"]["tests/test_frozen.py"]["skipped"] == 1
    assert record["attested"] is True


def test_reports_from_unfrozen_files_are_ignored(frozen_engine):
    tmp_path, target, manifest, rec = frozen_engine
    session = _session(tmp_path, target)
    rec.collect(session)
    rec.report(_Report(str(tmp_path / "tests" / "test_other.py"), "failed"))
    rec.report(_Report("/nowhere/at/all/test_x.py", "failed"))
    rec.report(_Report(str(target), "passed"))
    rec.finish(session, 0)

    record = cf.read_attestation(tmp_path)
    assert record["frozen_tests"]["tests/test_frozen.py"]["failed"] == 0
    assert record["attested"] is True


def test_a_path_restricted_run_records_a_reason_and_never_raises(frozen_engine):
    # The fatal branch is gone. This is the inner loop, not tampering: an
    # explicit path argument is a filter that no option sniff can see.
    tmp_path, target, manifest, rec = frozen_engine
    session = _Session(_Config(["test_*.py"]), [])
    rec.collect(session)
    rec.finish(session, 0)

    record = cf.read_attestation(tmp_path)
    assert record["attested"] is False
    assert any("collected nothing" in reason for reason in record["reasons"])


def test_no_freeze_writes_nothing(tmp_path):
    rec = AttestationRecorder(tmp_path)
    session = _session(tmp_path, tmp_path / "tests" / "test_frozen.py")
    rec.collect(session)
    rec.report(_Report(str(tmp_path / "x.py"), "passed"))
    rec.finish(session, 0)
    assert cf.read_attestation(tmp_path) is None


def test_every_conftest_hook_swallows_a_failure(monkeypatch, capsys):
    """The invariant: a broken recorder degrades to a printed line, never a raise.

    Record-keeping that can fail a run is more dangerous than the gap it closes,
    so each of the four hooks is checked, not just the one that seems riskiest.
    """
    conftest = next(
        module for module in list(sys.modules.values())
        if getattr(module, "__file__", None)
        == str(Path(__file__).resolve().parent / "conftest.py")
    )

    class _Exploding:
        def __getattr__(self, name):
            def _boom(*args, **kwargs):
                raise RuntimeError("boom")
            return _boom

    monkeypatch.setattr(conftest, "_canopus_recorder", lambda: _Exploding())
    conftest.pytest_collection_finish(object())
    conftest.pytest_deselected([])
    conftest.pytest_runtest_logreport(object())
    conftest.pytest_sessionfinish(object(), 0)
    # Restore before this test's own call report is emitted: while the live
    # recorder is patched, the hook drops that report and the session's tally
    # ends up one short. Record-keeping the suite can corrupt is the exact
    # defect this object exists to prevent.
    monkeypatch.undo()
    err = capsys.readouterr().err
    for fragment in ("attestation collection failed", "deselection tally failed",
                     "outcome tally failed", "could not write the attestation"):
        assert fragment in err


def test_a_failed_write_never_changes_the_run(frozen_engine, monkeypatch, capsys):
    from scripts.utils import canopus_gate

    tmp_path, target, manifest, rec = frozen_engine
    session = _session(tmp_path, target)
    rec.collect(session)

    def _explode(*args, **kwargs):
        raise OSError("disk gone")

    monkeypatch.setattr(canopus_gate, "write_attestation", _explode)
    with pytest.raises(OSError, match="disk gone"):
        rec.finish(session, 0)


def test_the_controller_seeds_its_tally_from_worker_node_ids(frozen_engine):
    """Under -n auto the controller runs no collection of its own.

    Measured, not assumed: without this route the canonical gate records
    "collected nothing" for every frozen file and can never attest.
    """
    tmp_path, target, manifest, rec = frozen_engine
    config = _Config(["test_*.py"])
    rec.seed_from_ids(config, [
        "tests/test_frozen.py::test_ok",
        "tests/test_frozen.py::test_other",
        "tests/test_elsewhere.py::test_x",
    ])
    assert rec.frozen["tests/test_frozen.py"]["collected"] == 2


def test_seeding_never_overwrites_a_tally_the_session_already_built(frozen_engine):
    tmp_path, target, manifest, rec = frozen_engine
    session = _session(tmp_path, target)
    rec.collect(session)
    rec.seed_from_ids(session.config, ["tests/test_frozen.py::a", "tests/test_frozen.py::b"])
    assert rec.frozen["tests/test_frozen.py"]["collected"] == 1


def test_a_worker_ships_its_deselection_counts_home(frozen_engine):
    tmp_path, target, manifest, rec = frozen_engine
    session = _session(tmp_path, target)
    session.config.workerinput = {"workerid": "gw0"}
    session.config.workeroutput = {}
    rec.collect(session)
    rec.deselected([_Item(target), _Item(target)])

    assert rec.finish(session, 0) is False
    assert session.config.workeroutput["canopus_deselected"] == {"tests/test_frozen.py": 2}
    assert cf.read_attestation(tmp_path) is None


def test_the_controller_folds_worker_deselections_in(frozen_engine):
    tmp_path, target, manifest, rec = frozen_engine
    config = _Config(["test_*.py"])
    rec.seed_from_ids(config, ["tests/test_frozen.py::a"])
    rec.merge_worker({"canopus_deselected": {"tests/test_frozen.py": 4}})
    rec.merge_worker({"canopus_deselected": {"tests/test_elsewhere.py": 9}})
    rec.merge_worker(None)

    assert rec.frozen["tests/test_frozen.py"]["deselected"] == 4


def test_clearing_the_freeze_removes_the_attestation(tmp_path):
    """Reproduced defect: the record outlived its freeze and was revived.

    clear_freeze unlinked only freeze.json, and the root hash is deterministic
    over frozen content plus the anchor path, so re-freezing identical test
    content resurrected the old record: a brand-new freeze with ZERO runs since
    printed ATTESTED. Clearing the freeze must clear what attests it.
    """
    cf.write_attestation(tmp_path, _build({"tests/test_alpha.py": _tests()}))
    cf.write_freeze(tmp_path, {
        "recipe": cf.RECIPE, "label": "x", "frozen_at": "2026-07-25T00:00:00+00:00",
        "anchor": "", "git_sha": "", "root": "a" * 64, "files": {}, "dirs": {},
    })
    cf.clear_freeze(tmp_path)

    assert cf.freeze_state_path(tmp_path).exists() is False
    assert cf.attest_state_path(tmp_path).exists() is False
    assert cf.read_attestation(tmp_path) is None


def test_clearing_is_idempotent_with_no_attestation_present(tmp_path):
    cf.clear_freeze(tmp_path)
    cf.clear_freeze(tmp_path)
    assert cf.read_attestation(tmp_path) is None


def test_worker_deselections_are_taken_at_face_value_not_summed(frozen_engine):
    """Every xdist worker collects the FULL set and deselects identically.

    Summing across workers multiplied the count by the worker number. It could
    never produce a false green, but a wrong number in an evidence record is
    still a wrong number.
    """
    tmp_path, target, manifest, rec = frozen_engine
    config = _Config(["test_*.py"])
    rec.seed_from_ids(config, ["tests/test_frozen.py::a"])
    rec.merge_worker({"canopus_deselected": {"tests/test_frozen.py": 4}})
    rec.merge_worker({"canopus_deselected": {"tests/test_frozen.py": 4}})
    rec.merge_worker({"canopus_deselected": {"tests/test_frozen.py": 4}})

    assert rec.frozen["tests/test_frozen.py"]["deselected"] == 4


def test_an_outlying_worker_count_still_registers(frozen_engine):
    # Workers should agree; if one reports more, take the larger rather than
    # silently under-reporting how much of the contract was filtered away.
    tmp_path, target, manifest, rec = frozen_engine
    config = _Config(["test_*.py"])
    rec.seed_from_ids(config, ["tests/test_frozen.py::a"])
    rec.merge_worker({"canopus_deselected": {"tests/test_frozen.py": 2}})
    rec.merge_worker({"canopus_deselected": {"tests/test_frozen.py": 6}})

    assert rec.frozen["tests/test_frozen.py"]["deselected"] == 6


def _counts(collected, passed):
    return {"collected": collected, "passed": passed, "failed": 0,
            "skipped": 0, "deselected": 0}


def test_a_subset_run_does_not_attest_against_the_baseline():
    from scripts.utils.canopus_freeze import build_attestation

    record = build_attestation(
        root_digest="a" * 64,
        frozen_tests={"tests/contract/s/test_a.py": _counts(1, 1)},
        exit_status=0,
        attested_at="2026-07-25T00:00:00+00:00",
        baseline={"tests/contract/s/test_a.py": 7},
    )

    assert record["attested"] is False
    assert any("collected 1 of 7" in reason for reason in record["reasons"])


def test_a_full_run_attests_against_the_baseline():
    from scripts.utils.canopus_freeze import build_attestation

    record = build_attestation(
        root_digest="a" * 64,
        frozen_tests={"tests/contract/s/test_a.py": _counts(7, 7)},
        exit_status=0,
        attested_at="2026-07-25T00:00:00+00:00",
        baseline={"tests/contract/s/test_a.py": 7},
    )

    assert record["attested"] is True
    assert record["reasons"] == []


def test_a_frozen_test_file_with_no_baseline_behaves_as_in_wire_1():
    from scripts.utils.canopus_freeze import build_attestation

    record = build_attestation(
        root_digest="a" * 64,
        frozen_tests={"tests/test_legacy.py": _counts(1, 1)},
        exit_status=0,
        attested_at="2026-07-25T00:00:00+00:00",
        baseline={},
    )

    assert record["attested"] is True
