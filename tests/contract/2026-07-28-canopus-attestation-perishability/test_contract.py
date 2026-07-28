"""The frozen contract for Canopus wire 3.2: an attestation that perishes.

Spec: docs/superpowers/specs/2026-07-28-canopus-attestation-perishability-design.md
Plan: docs/superpowers/plans/2026-07-28-canopus-attestation-perishability.md

The finding this contract binds, measured before it was argued: an attestation
binds to the FROZEN bytes and to nothing else, and the code under test is by
design NOT frozen. So a green record survived breaking the implementation, and
survived breaking it and running nothing at all.

A first design recorded the files the run IMPORTED and was withdrawn before any
freeze, on a measurement taken in this repository: a test file that deletes its
own module from `sys.modules` was already absent from the recorded set at
session finish. This contract binds the replacement, which is the working tree,
and SC-6 below is that grave with a headstone on it.

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

CLEAN_TREE = {"recipe": "canopus-tree-v1", "head": "a" * 40, "dirty": {}}
MOVED_TREE = {"recipe": "canopus-tree-v1", "head": "a" * 40,
              "dirty": {"scripts/thing.py": "b" * 64}}


# ============================================================
# Helpers. Stdlib only at module scope, deliberately.
# ============================================================

def _scrubbed_env(**extra):
    """A child environment with the parent session's git and pytest state gone.

    GIT_ names because this suite runs inside the repository's own pre-push
    hook, and git exports GIT_DIR to a hook, so an unscrubbed child would
    resolve the ENGINE's repository instead of the fixture's. PYTEST_ and
    coverage names because an inherited PYTEST_ADDOPTS configures the scratch
    child into something other than the run these tests mean to make.
    """
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(("GIT_", "PYTEST_", "CANOPUS_", "COV_CORE_"))}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env.update(extra)
    return env


def _git(directory: Path, *argv: str):
    return subprocess.run(["git", "-C", str(directory), *argv], check=True,
                          capture_output=True, text=True, env=_scrubbed_env())


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


def _run_pytest(tree: Path, env=None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "pytest", "tests/contract/demo/", "-q",
         "-p", "no:cacheprovider"],
        cwd=str(tree), env=env or _scrubbed_env(),
        capture_output=True, text=True, check=False)


def _canopus(tree: Path, *argv: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "canopus.py"),
         "--root", str(tree), *argv],
        cwd=str(REPO_ROOT), env=_scrubbed_env(),
        capture_output=True, text=True, check=False)


def _build_scratch_tree(root: Path) -> None:
    """A git working copy with a gate script, a contract importing one
    implementation file, and a conftest wiring the recorder to THIS root."""
    (root / "scripts").mkdir(parents=True)
    (root / "scripts" / "run-tests.py").write_text("# stub test gate\n",
                                                   encoding="utf-8")
    (root / "src").mkdir()
    (root / "tests" / "contract" / "demo").mkdir(parents=True)
    # The scratch contract DELETES the module it just used, which makes every
    # end-to-end test here a regression anchor against reintroducing a
    # `sys.modules` design. Measured live in this repository before it was
    # written down: tests/test_alert_no_import_cycle.py deletes every module
    # whose name matches a pattern, including its own, so a file that ran was
    # already absent from the finish-time set.
    (root / "tests" / "contract" / "demo" / "test_contract.py").write_text(
        "import sys\n"
        "\n"
        "def test_add_is_addition():\n"
        "    from src.calc import add\n"
        "    assert add(2, 3) == 5\n"
        "    sys.modules.pop('src.calc', None)\n"
        "    sys.modules.pop('src', None)\n",
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
    (root / ".gitignore").write_text(".canopus/\n", encoding="utf-8")
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "builder@example.invalid")
    _git(root, "config", "user.name", "Builder")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "the tree before the implementation")


def _frozen_scratch(tmp_path: Path):
    """A frozen git scratch tree with the implementation absent, so the contract
    is red for a reason that means something. Returns (tree, implementation)."""
    tree = tmp_path / "tree"
    tree.mkdir()
    _build_scratch_tree(tree)
    anchor = tmp_path / "outside" / "anchor.md"
    anchor.parent.mkdir(parents=True)
    anchor.write_text("# gate artifact\n", encoding="utf-8")
    frozen = _canopus(tree, "freeze", "--contract", "tests/contract/demo/",
                      "--label", "demo", "--anchor", str(anchor))
    assert frozen.returncode == 0, frozen.stdout + frozen.stderr
    return tree, tree / "src" / "calc.py"


def _commit_all(tree: Path, message: str) -> None:
    _git(tree, "add", ".")
    _git(tree, "commit", "-q", "-m", message)


def _green(tmp_path: Path):
    """A frozen scratch tree, implementation written and committed, run green."""
    tree, impl = _frozen_scratch(tmp_path)
    impl.write_text(GOOD_IMPL, encoding="utf-8")
    _commit_all(tree, "the implementation")
    run = _run_pytest(tree)
    assert run.returncode == 0, run.stdout + run.stderr
    assert _attestation_line(_canopus(tree, "verify").stdout) == "ATTESTED"
    return tree, impl


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
            "tree_at_start": dict(CLEAN_TREE),
            "tree_at_finish": dict(CLEAN_TREE),
        },
        **overrides,
    )


# ============================================================
# SC-1 and SC-3: the table that opened this wire, end to end
# ============================================================

def test_sc3_the_four_case_table_that_opened_this_wire(tmp_path):
    """Cases C, C2 and D all read ATTESTED over a broken implementation before
    this slice. D is the sharpest, because it costs the builder nothing at all.
    B is the control, and it passed before the slice too, which is what made the
    other three look like a working mechanism."""
    tree, impl = _green(tmp_path)

    impl.write_text(BROKEN_IMPL, encoding="utf-8")
    page = _ANSI.sub("", _canopus(tree, "verify").stdout)
    assert _attestation_line(page) == "NOT ATTESTED"
    assert "src/calc.py" in page

    _run_pytest(tree, _scrubbed_env(CANOPUS_NO_ATTEST="1"))
    assert _attestation_line(_canopus(tree, "verify").stdout) == "NOT ATTESTED"

    _run_pytest(tree, _scrubbed_env(CANOPUS_PLUGIN_DUMP=str(tmp_path / "dump.json")))
    assert _attestation_line(_canopus(tree, "verify").stdout) == "NOT ATTESTED"

    red = _run_pytest(tree)
    assert red.returncode == 1
    assert _attestation_line(_canopus(tree, "verify").stdout) == "NOT ATTESTED"


def test_sc1_a_green_run_records_the_tree_it_ran_against(tmp_path):
    """The record is not merely present; it carries a head and a dirty map."""
    import json

    tree, _impl = _green(tmp_path)
    record = json.loads((tree / ".canopus" / "attest.json").read_text())

    assert record["attested"] is True
    assert len(record["tree"]["head"]) == 40
    assert record["tree"]["dirty"] == {}


def test_sc3b_the_record_comes_back_the_moment_the_tree_does(tmp_path):
    """Perishability, not a one-way latch."""
    tree, impl = _green(tmp_path)

    impl.write_text(BROKEN_IMPL, encoding="utf-8")
    assert _attestation_line(_canopus(tree, "verify").stdout) == "NOT ATTESTED"

    impl.write_text(GOOD_IMPL, encoding="utf-8")
    assert _run_pytest(tree).returncode == 0
    assert _attestation_line(_canopus(tree, "verify").stdout) == "ATTESTED"


# ============================================================
# SC-2 and SC-6: what perishes it, including v1's grave
# ============================================================

def test_sc2_editing_any_tracked_file_perishes_the_record(tmp_path):
    """Any tracked file, with no run in between. The record's meaning is
    "nothing has changed since the gate ran", not "these modules have not"."""
    tree, _impl = _green(tmp_path)

    (tree / "scripts" / "run-tests.py").write_text("# edited\n", encoding="utf-8")
    page = _ANSI.sub("", _canopus(tree, "verify").stdout)
    assert _attestation_line(page) == "NOT ATTESTED"
    assert "scripts/run-tests.py" in page


def test_sc6a_a_non_python_file_perishes_the_record(tmp_path):
    """The withdrawn v1 could not see this at all: `sys.modules` never carries a
    YAML config, and behaviour can be flipped without a single module moving."""
    tree, _impl = _green(tmp_path)

    (tree / "settings.yaml").write_text("flag: true\n", encoding="utf-8")
    page = _ANSI.sub("", _canopus(tree, "verify").stdout)
    assert _attestation_line(page) == "NOT ATTESTED"
    assert "settings.yaml" in page


def test_sc6b_a_module_the_run_deleted_from_sys_modules_still_perishes_it(tmp_path):
    """v1's grave, and it was measured live in this repository rather than
    imagined: a test that deletes every module whose name matches a pattern
    deletes its own, so the file ran and was then absent from the recorded set.
    The tree does not care what an interpreter remembers.

    The edited file is `src/calc.py`, which is NOT frozen and which the scratch
    contract removes from `sys.modules` before the session ends. Editing a
    FROZEN file instead would prove nothing here: the root binding already
    catches that, and a test green for the older mechanism is a test that says
    nothing about this one.
    """
    tree, impl = _green(tmp_path)

    impl.write_text(GOOD_IMPL + "\n# edited after the green run\n", encoding="utf-8")

    page = _ANSI.sub("", _canopus(tree, "verify").stdout)
    assert _attestation_line(page) == "NOT ATTESTED"
    assert "src/calc.py" in page


def test_sc6c_an_untracked_new_file_perishes_the_record(tmp_path):
    """A new module in a new package is the shape a builder reaches for, and
    default porcelain would have collapsed it to a directory name carrying no
    hash."""
    tree, _impl = _green(tmp_path)

    (tree / "newpkg").mkdir()
    (tree / "newpkg" / "mod.py").write_text("y = 1\n", encoding="utf-8")

    page = _ANSI.sub("", _canopus(tree, "verify").stdout)
    assert _attestation_line(page) == "NOT ATTESTED"
    assert "newpkg/mod.py" in page


def test_sc6d_a_commit_perishes_the_record(tmp_path):
    """Committing changes nothing on disk and everything about what the tree
    IS. A state that watched only dirty paths would call a commit no change."""
    tree, _impl = _green(tmp_path)

    (tree / "note.md").write_text("a note\n", encoding="utf-8")
    _commit_all(tree, "a commit after the green run")

    page = _ANSI.sub("", _canopus(tree, "verify").stdout)
    assert _attestation_line(page) == "NOT ATTESTED"
    assert "HEAD" in page


# ============================================================
# SC-4 and SC-5: the race, and refusing what cannot be described
# ============================================================

def test_sc4_a_tree_that_moved_during_the_run_refuses_the_record():
    """The bytes on disk at finish are not the bytes that were imported. Two
    samples are what turn that from an assumption into a check."""
    from scripts.utils.canopus_freeze import build_attestation

    record = build_attestation(**_green_kwargs(tree_at_finish=dict(MOVED_TREE)))
    assert record["attested"] is False
    assert any("while the run was in progress" in r for r in record["reasons"])


def test_sc5a_a_record_with_no_tree_state_is_refused():
    """A record that cannot perish is worse than no record: it reads green
    forever. Not proved is not proved innocent."""
    from scripts.utils.canopus_freeze import build_attestation

    for absent in (None, {}, "not a state"):
        assert build_attestation(
            **_green_kwargs(tree_at_finish=absent))["attested"] is False
        assert build_attestation(
            **_green_kwargs(tree_at_start=absent))["attested"] is False


def test_sc5b_a_root_that_is_not_a_git_working_copy_answers_rather_than_raising(tmp_path):
    """It narrows the tool deliberately, and it must say so instead of crashing."""
    from scripts.utils.canopus_tree import tree_state

    plain = tmp_path / "plain"
    plain.mkdir()
    assert tree_state(plain) is None


def test_sc5c_a_tree_that_cannot_be_described_now_is_not_read_as_unchanged():
    import scripts.utils.canopus_freeze as cf

    record = cf.build_attestation(**_green_kwargs())
    assert cf.attestation_state(record, "a" * 64, dict(CLEAN_TREE))[0] == "ATTESTED"
    assert cf.attestation_state(record, "a" * 64, None)[0] == "NOT ATTESTED"


# ============================================================
# The shape of the check itself
# ============================================================

def test_attestation_state_has_no_current_tree_default_to_fail_open_through():
    """A default would let a caller that forgot the tree skip the comparison and
    print green, which is the fail-open shape this check exists to close."""
    import pytest

    from scripts.utils.canopus_freeze import attestation_state

    with pytest.raises(TypeError):
        attestation_state({"recipe": "x"}, "a" * 64)


def test_a_record_from_the_previous_recipe_carries_no_tree_so_it_is_refused():
    """A v2 record has no tree block, and reading one as ATTESTED would be
    precisely the fail-open being closed."""
    import scripts.utils.canopus_freeze as cf

    stale = {"recipe": "canopus-attest-v2", "root": "a" * 64, "attested": True,
             "frozen_tests": {}, "reasons": []}
    assert cf.attestation_state(stale, "a" * 64, dict(CLEAN_TREE))[0] == "NOT ATTESTED"


def test_the_drift_comparison_never_raises_on_a_hostile_state():
    """It is read from `attestation_state`, which every reporting surface calls
    and none of them may crash in."""
    from scripts.utils.canopus_freeze import tree_drift

    for hostile in (None, {}, "text", 7, [1, 2], {"head": "a" * 40},
                    {"recipe": "canopus-tree-v1", "head": 7, "dirty": {}},
                    {"recipe": "canopus-tree-v1", "head": "a" * 40, "dirty": 7}):
        assert isinstance(tree_drift(hostile, dict(CLEAN_TREE)), list)
        assert isinstance(tree_drift(dict(CLEAN_TREE), hostile), list)


def test_the_drift_comparison_reads_no_disk_and_runs_no_git():
    """`canopus_freeze` is the module the gate calls at every pytest session
    start, and its import tail is stdlib plus `atomic`. Its half of this feature
    is a pure comparison of two structures; the git is `canopus_tree`'s."""
    import scripts.utils.canopus_freeze as cf

    assert not hasattr(cf, "subprocess")
    assert not hasattr(cf, "git_output")
    assert cf.tree_drift(dict(CLEAN_TREE), dict(CLEAN_TREE)) == []


def test_sc7_the_page_says_what_the_record_is_bound_to(tmp_path):
    """A local record is evidence, not proof, and the page an operator signs off
    from must say which it holds."""
    tree, _impl = _green(tmp_path)
    page = _ANSI.sub("", _canopus(tree, "pack").stdout)

    assert "evidence rather than proof" in page
    assert "outside the state" in page
    assert "is not a git working" in page
