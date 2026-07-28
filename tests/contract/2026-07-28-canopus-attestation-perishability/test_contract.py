"""The frozen contract for Canopus wire 3.2: an attestation that perishes.

Spec: docs/superpowers/specs/2026-07-28-canopus-attestation-perishability-design.md
Plan: docs/superpowers/plans/2026-07-28-canopus-attestation-perishability.md

The finding this contract binds, measured before it was argued: an attestation
binds to the frozen bytes and to nothing else, and the code under test is by
design NOT frozen. So a green record survived breaking the implementation, and
survived breaking it and running nothing at all.

EVERY import of the code under test is inside a test body, never at module
scope. The implementation does not exist yet, and a module-scope import stops
the file collecting, and a file that collects nothing cannot be frozen.
"""
import os
import re
import subprocess
import sys
from pathlib import Path

_ANSI = re.compile(r"\033\[[0-9;]*m")
REPO_ROOT = Path(__file__).resolve().parents[3]

GOOD_IMPL = "def add(a, b):\n    return a + b\n"
BROKEN_IMPL = "def add(a, b):\n    return a * b\n"


# ============================================================
# Helpers. Stdlib only at module scope, deliberately.
# ============================================================

def _attestation_line(out: str) -> str:
    """The state token from a reporting page, colour stripped.

    `"ATTESTED" in out` is not a test, because "NOT ATTESTED" contains it.
    """
    for raw in out.splitlines():
        line = _ANSI.sub("", raw).strip()
        if line.startswith("NOT ATTESTED"):
            return "NOT ATTESTED"
        if line.startswith("ATTESTED"):
            return "ATTESTED"
    return "(no attestation line)"


def _child_env(**extra):
    """A child environment with the parent session's pytest state scrubbed."""
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(("PYTEST_", "CANOPUS_", "COV_CORE_"))}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env.update(extra)
    return env


def _run_pytest(tree: Path, env) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "pytest", "tests/contract/demo/", "-q",
         "-p", "no:cacheprovider"],
        cwd=str(tree), env=env, capture_output=True, text=True, check=False)


def _canopus(tree: Path, *argv: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "canopus.py"),
         "--root", str(tree), *argv],
        cwd=str(REPO_ROOT), env=_child_env(),
        capture_output=True, text=True, check=False)


def _build_scratch_tree(root: Path) -> None:
    """A tree with a gate script, a contract importing one implementation file,
    and a conftest wiring the recorder to THIS root."""
    (root / "scripts").mkdir(parents=True)
    (root / "scripts" / "run-tests.py").write_text("# stub test gate\n",
                                                   encoding="utf-8")
    (root / "src").mkdir()
    (root / "tests" / "contract" / "demo").mkdir(parents=True)
    (root / "tests" / "contract" / "demo" / "test_contract.py").write_text(
        "def test_add_is_addition():\n"
        "    from src.calc import add\n"
        "    assert add(2, 3) == 5\n",
        encoding="utf-8")
    (root / "conftest.py").write_text(
        "import sys\n"
        "from pathlib import Path\n"
        "ROOT = Path(__file__).parent\n"
        "sys.path.insert(0, str(ROOT))\n"
        f"sys.path.insert(0, {str(REPO_ROOT)!r})\n"
        "from scripts.utils.canopus_gate import AttestationRecorder\n"
        "_R = None\n"
        "def _rec():\n"
        "    global _R\n"
        "    if _R is None:\n"
        "        _R = AttestationRecorder(ROOT)\n"
        "    return _R\n"
        "def pytest_collection_modifyitems(session, config, items):\n"
        "    _rec().collect(session)\n"
        "def pytest_deselected(items):\n"
        "    _rec().deselected(items)\n"
        "def pytest_runtest_logreport(report):\n"
        "    _rec().report(report)\n"
        "def pytest_sessionfinish(session, exitstatus):\n"
        "    _rec().finish(session, exitstatus)\n",
        encoding="utf-8")


def _frozen_scratch(tmp_path: Path):
    """A frozen scratch tree with the implementation absent, so the contract is
    red for a reason that means something. Returns (tree, implementation path)."""
    tree = tmp_path / "tree"
    _build_scratch_tree(tree)
    anchor = tmp_path / "outside" / "anchor.md"
    anchor.parent.mkdir(parents=True)
    anchor.write_text("# gate artifact\n", encoding="utf-8")
    frozen = _canopus(tree, "freeze", "--contract", "tests/contract/demo/",
                      "--label", "demo", "--anchor", str(anchor))
    assert frozen.returncode == 0, frozen.stdout + frozen.stderr
    return tree, tree / "src" / "calc.py"


def _green_kwargs(**overrides):
    return dict(
        {
            "root_digest": "a" * 64,
            "frozen_tests": {"tests/contract/x/test_a.py": {
                "collected": 1, "passed": 1, "failed": 0,
                "skipped": 0, "deselected": 0}},
            "exit_status": 0,
            "attested_at": "2026-07-28T00:00:00+00:00",
            "baseline": {"tests/contract/x/test_a.py": 1},
            "process": {"plugins": ["dist:pytest"]},
            "plugin_baseline": ["dist:pytest"],
            "exercised": {"scripts/thing.py": "b" * 64},
        },
        **overrides,
    )


# ============================================================
# SC-3: the four-case table, end to end through the real commands
# ============================================================

def test_sc3_the_four_case_table_that_opened_this_wire(tmp_path):
    """Cases C, C2 and D all read ATTESTED over a broken implementation before
    this slice. D is the sharpest, because it costs the builder nothing at all.
    B is the control, and it passed before the slice too, which is what made the
    other three look like a working mechanism."""
    tree, impl = _frozen_scratch(tmp_path)
    impl.write_text(GOOD_IMPL, encoding="utf-8")

    green = _run_pytest(tree, _child_env())
    assert green.returncode == 0, green.stdout + green.stderr
    assert _attestation_line(_canopus(tree, "verify").stdout) == "ATTESTED"

    impl.write_text(BROKEN_IMPL, encoding="utf-8")
    page = _ANSI.sub("", _canopus(tree, "verify").stdout)
    assert _attestation_line(page) == "NOT ATTESTED"
    assert "src/calc.py" in page

    _run_pytest(tree, _child_env(CANOPUS_NO_ATTEST="1"))
    assert _attestation_line(_canopus(tree, "verify").stdout) == "NOT ATTESTED"

    _run_pytest(tree, _child_env(CANOPUS_PLUGIN_DUMP=str(tmp_path / "dump.json")))
    assert _attestation_line(_canopus(tree, "verify").stdout) == "NOT ATTESTED"

    red = _run_pytest(tree, _child_env())
    assert red.returncode == 1
    assert _attestation_line(_canopus(tree, "verify").stdout) == "NOT ATTESTED"


def test_sc1_a_green_run_records_the_implementation_it_imported(tmp_path):
    """The set is not merely present; it names the file the contract judges."""
    import json

    tree, impl = _frozen_scratch(tmp_path)
    impl.write_text(GOOD_IMPL, encoding="utf-8")
    assert _run_pytest(tree, _child_env()).returncode == 0

    record = json.loads((tree / ".canopus" / "attest.json").read_text())
    assert record["attested"] is True
    assert "src/calc.py" in record["exercised"]
    assert "tests/contract/demo/test_contract.py" in record["exercised"]


def test_sc3b_the_record_comes_back_the_moment_the_code_does(tmp_path):
    """Perishability, not a one-way latch."""
    tree, impl = _frozen_scratch(tmp_path)
    impl.write_text(GOOD_IMPL, encoding="utf-8")
    assert _run_pytest(tree, _child_env()).returncode == 0
    impl.write_text(BROKEN_IMPL, encoding="utf-8")
    assert _attestation_line(_canopus(tree, "verify").stdout) == "NOT ATTESTED"

    impl.write_text(GOOD_IMPL, encoding="utf-8")
    assert _run_pytest(tree, _child_env()).returncode == 0
    assert _attestation_line(_canopus(tree, "verify").stdout) == "ATTESTED"


# ============================================================
# SC-2 and SC-6: what perishes the record, and what must not
# ============================================================

def test_sc2_one_changed_byte_in_an_exercised_file_perishes_the_record(tmp_path):
    import scripts.utils.canopus_freeze as cf

    root = tmp_path / "repo"
    root.mkdir()
    (root / "thing.py").write_text("x = 1\n", encoding="utf-8")
    record = cf.build_attestation(**_green_kwargs(
        exercised=cf.exercised_map(["thing.py"], root)))
    assert cf.attestation_state(record, "a" * 64, root)[0] == "ATTESTED"

    (root / "thing.py").write_text("x = 2\n", encoding="utf-8")
    state, reason = cf.attestation_state(record, "a" * 64, root)
    assert state == "NOT ATTESTED"
    assert "thing.py" in reason


def test_sc6_a_file_the_run_never_imported_does_not_perish_the_record(tmp_path):
    """An indicator that goes amber for reasons the operator knows are
    irrelevant is an indicator the operator learns to ignore."""
    import scripts.utils.canopus_freeze as cf

    root = tmp_path / "repo"
    root.mkdir()
    (root / "thing.py").write_text("x = 1\n", encoding="utf-8")
    record = cf.build_attestation(**_green_kwargs(
        exercised=cf.exercised_map(["thing.py"], root)))

    (root / "unrelated.md").write_text("a doc\n", encoding="utf-8")
    (root / "also_unrelated.py").write_text("y = 9\n", encoding="utf-8")
    assert cf.attestation_state(record, "a" * 64, root)[0] == "ATTESTED"


# ============================================================
# SC-4: not proved is not proved innocent
# ============================================================

def test_sc4a_a_record_with_no_exercised_set_is_refused_when_it_is_built(tmp_path):
    """A record that cannot perish is worse than no record: it reads green forever."""
    from scripts.utils.canopus_freeze import build_attestation

    for absent in (None, {}, "not a map"):
        record = build_attestation(**_green_kwargs(exercised=absent))
        assert record["attested"] is False


def test_sc4b_a_recorded_file_that_is_now_gone_is_refused(tmp_path):
    import scripts.utils.canopus_freeze as cf

    root = tmp_path / "repo"
    root.mkdir()
    (root / "thing.py").write_text("x = 1\n", encoding="utf-8")
    record = cf.build_attestation(**_green_kwargs(
        exercised=cf.exercised_map(["thing.py"], root)))
    (root / "thing.py").unlink()

    state, reason = cf.attestation_state(record, "a" * 64, root)
    assert state == "NOT ATTESTED"
    assert "gone" in reason


def test_sc4c_an_unreadable_file_is_recorded_as_a_gap_and_refused(tmp_path):
    """None is recorded rather than the entry dropped: dropping it would make
    "one file cannot be read" read as "a smaller, wholly readable set", which is
    the greener of the two."""
    from scripts.utils.canopus_freeze import build_attestation, exercised_map

    root = tmp_path / "repo"
    root.mkdir()
    mapped = exercised_map(["never-written.py"], root)
    assert mapped == {"never-written.py": None}
    assert build_attestation(**_green_kwargs(exercised=mapped))["attested"] is False


def test_sc4d_an_empty_set_matches_the_tree_forever_so_it_refuses(tmp_path):
    """The rule wire 3.1 settled over an empty claim set, applied here."""
    from scripts.utils.canopus_freeze import exercised_drift

    root = tmp_path / "repo"
    root.mkdir()
    assert len(exercised_drift({}, root)) == 1
    assert len(exercised_drift(None, root)) == 1


# ============================================================
# SC-5: the workers ran the tests, so the workers must be asked
# ============================================================

def test_sc5a_the_set_is_the_union_over_the_controller_and_every_worker(tmp_path):
    """Measured under -n 2 on one test file: the controller held 9 in-root
    modules, the two workers held 12 and 10, and the module under test plus two
    of its imports appeared ONLY in workers."""
    from scripts.utils.canopus_gate import AttestationRecorder

    recorder = AttestationRecorder(tmp_path)
    recorder.merge_worker({"canopus_exercised": {"b.py": "b" * 64}})
    recorder.merge_worker({"canopus_exercised": {"c.py": "c" * 64}})

    merged = recorder._merge_exercised({"a.py": "a" * 64}, distributed=True)
    assert merged == {"a.py": "a" * 64, "b.py": "b" * 64, "c.py": "c" * 64}


def test_sc5b_a_worker_that_reported_nothing_refuses_the_record(tmp_path):
    from scripts.utils.canopus_gate import AttestationRecorder

    recorder = AttestationRecorder(tmp_path)
    recorder.merge_worker({"canopus_exercised": None})
    assert recorder._merge_exercised({"a.py": "a" * 64}, distributed=True) is None


def test_sc5c_a_distributed_run_with_no_worker_sets_at_all_refuses(tmp_path):
    """A conftest that wires the session hooks but forgets pytest_testnodedown
    reaches this state silently, and the controller's own set is a real,
    non-empty, wholly readable map that excludes everything the workers ran."""
    from scripts.utils.canopus_gate import AttestationRecorder

    recorder = AttestationRecorder(tmp_path)
    assert recorder._merge_exercised({"a.py": "a" * 64}, distributed=True) is None
    assert recorder._merge_exercised({"a.py": "a" * 64}, distributed=False) == {
        "a.py": "a" * 64}


def test_sc5d_two_interpreters_disagreeing_about_one_file_refuses_it(tmp_path):
    """The file changed WHILE the run was in progress, and the record must not
    pick a winner."""
    from scripts.utils.canopus_gate import AttestationRecorder

    recorder = AttestationRecorder(tmp_path)
    recorder.merge_worker({"canopus_exercised": {"a.py": "b" * 64}})
    merged = recorder._merge_exercised({"a.py": "a" * 64}, distributed=True)
    assert merged["a.py"] is None


# ============================================================
# The shape of the check itself
# ============================================================

def test_the_reader_and_the_writer_share_one_hasher(tmp_path):
    """Two hand-rolled copies is how the writer and the reader end up
    disagreeing about the same file's bytes."""
    from scripts.utils.canopus_freeze import exercised_map, file_digest

    root = tmp_path / "repo"
    (root / "scripts").mkdir(parents=True)
    (root / "scripts" / "thing.py").write_text("x = 1\n", encoding="utf-8")
    assert exercised_map(["scripts/thing.py"], root) == {
        "scripts/thing.py": file_digest(root / "scripts" / "thing.py")}


def test_attestation_state_has_no_root_default_to_fail_open_through(tmp_path):
    """A default would let a caller that forgot the root skip the recompute and
    print green, which is the fail-open shape this check exists to close."""
    import pytest

    from scripts.utils.canopus_freeze import attestation_state

    with pytest.raises(TypeError):
        attestation_state({"recipe": "x"}, "a" * 64)


def test_a_record_from_the_previous_recipe_carries_no_set_so_it_is_refused(tmp_path):
    """A v2 record has no exercised block, and reading one as ATTESTED would be
    precisely the fail-open being closed."""
    import scripts.utils.canopus_freeze as cf

    stale = {"recipe": "canopus-attest-v2", "root": "a" * 64, "attested": True,
             "frozen_tests": {}, "reasons": []}
    assert cf.attestation_state(stale, "a" * 64, tmp_path)[0] == "NOT ATTESTED"


def test_the_drift_check_never_raises_on_a_hostile_record(tmp_path):
    """It is read from attestation_state, which every reporting surface calls
    and none of them may crash in."""
    from scripts.utils.canopus_freeze import exercised_drift

    root = tmp_path / "repo"
    root.mkdir()
    for hostile in ({"": "a" * 64}, {"../escape.py": "a" * 64}, {"x.py": 7},
                    {"x.py": None}, "text", 7, [1, 2]):
        assert isinstance(exercised_drift(hostile, root), list)


def test_sc7_the_reporting_page_says_how_wide_the_set_was(tmp_path):
    """The exercised set is exactly as wide as the run that produced it, so a
    narrow run buys a narrow guarantee. The page says how narrow."""
    tree, impl = _frozen_scratch(tmp_path)
    impl.write_text(GOOD_IMPL, encoding="utf-8")
    assert _run_pytest(tree, _child_env()).returncode == 0

    page = _ANSI.sub("", _canopus(tree, "verify").stdout)
    assert "files it imported" in page
