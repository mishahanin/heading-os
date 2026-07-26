"""Canopus wire 2.1 contract: the approval binding, and meaningful redness.

FROZEN AT FIX 1. These tests are the target the build is measured against, and
they are never edited to match an implementation. A contract that turns out to be
genuinely wrong reopens the approval gate, where it is re-approved deliberately
and the superseded anchor is retired on the record.

Every test imports the code under test INSIDE its body. That rule is what lets
this file collect before any of the implementation exists: a module-scope import
would stop collection, and a file that collects nothing cannot be frozen or
attested. It is also what makes the file hijackable, which is why the freeze
guards conftest.py in every ancestor and *.py at the tree root.

Carries SC-1 through SC-6 from
docs/superpowers/specs/2026-07-26-canopus-approval-binding-design.md.
"""
import subprocess
from pathlib import Path


# ============================================================
# Helpers
# ============================================================

def _repo(path: Path) -> Path:
    """A git repository with a deterministic synthetic identity.

    The identity is invented on purpose: this file ships in a public repository
    and must carry no real person, address, or host.
    """
    path.mkdir(parents=True, exist_ok=True)
    for argv in (
        ["init", "-q", "-b", "main"],
        ["config", "user.email", "builder@example.invalid"],
        ["config", "user.name", "Contract Builder"],
    ):
        subprocess.run(["git", "-C", str(path), *argv], check=True,
                       capture_output=True, text=True)
    return path


def _commit(path: Path, message: str) -> None:
    subprocess.run(["git", "-C", str(path), "add", "-A"], check=True,
                   capture_output=True, text=True)
    subprocess.run(["git", "-C", str(path), "commit", "-q", "-m", message],
                   check=True, capture_output=True, text=True)


def _tree(path: Path) -> Path:
    """A synthetic working tree carrying the gate script every root must have."""
    (path / "tests").mkdir(parents=True, exist_ok=True)
    (path / "tests" / "test_alpha.py").write_text(
        "def test_a():\n    assert True\n", encoding="utf-8"
    )
    (path / "scripts").mkdir(parents=True, exist_ok=True)
    (path / "scripts" / "run-tests.py").write_text("# stub test gate\n", encoding="utf-8")
    return path


def _write(base: Path, rel: str, body: str) -> Path:
    target = base / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body, encoding="utf-8")
    return target


# ============================================================
# SC-1: freeze binds to what was committed
# ============================================================

def test_sc1_freeze_refuses_a_committed_hash_that_disagrees(tmp_path, capsys):
    """The binding itself: what was approved is what may be frozen.

    Before this slice, freeze wrote the anchor line and then verified the line it
    had written, which is an instrument approving itself.

    The approval lives ONLY in the commit, and the working copy is scrubbed of
    it. That is deliberate: with the line present on disk, wire 2's working-copy
    rule already refuses, and this test would pass green before a line of the
    slice existed, asserting nothing about the binding it is here to prove.
    """
    from scripts.canopus import main

    tree = _tree(tmp_path / "tree")
    gate = _repo(tmp_path / "gate")
    artifact = gate / "approval.md"
    artifact.write_text(f"# gate\n\ncanopus-anchor: {'e' * 64}\n", encoding="utf-8")
    _commit(gate, "approve a set that is not this one")
    artifact.write_text("# gate\n", encoding="utf-8")

    code = main(["--root", str(tree), "freeze", "--label", "l",
                 "--anchor", str(artifact), "tests/test_alpha.py"])

    assert code == 1
    assert not (tree / ".canopus" / "freeze.json").exists()
    assert "e" * 64 in capsys.readouterr().err


def test_sc1_the_refusal_names_both_values(tmp_path, capsys):
    """A refusal that names neither hash cannot be acted on."""
    from scripts.canopus import main

    tree = _tree(tmp_path / "tree")
    gate = _repo(tmp_path / "gate")
    artifact = gate / "approval.md"
    artifact.write_text(f"canopus-anchor: {'e' * 64}\n", encoding="utf-8")
    _commit(gate, "approve")
    artifact.write_text("# gate\n", encoding="utf-8")  # see the test above

    main(["--root", str(tree), "freeze", "--label", "l",
          "--anchor", str(artifact), "tests/test_alpha.py"])
    err = capsys.readouterr().err

    assert "approved" in err
    assert "computed" in err


def test_sc1_approve_records_a_hash_freeze_then_accepts(tmp_path):
    """approve and freeze must agree, or every freeze is refused forever."""
    from scripts.canopus import main

    tree = _tree(tmp_path / "tree")
    gate = _repo(tmp_path / "gate")
    artifact = gate / "approval.md"
    artifact.write_text("# gate\n", encoding="utf-8")
    _commit(gate, "seed")

    assert main(["--root", str(tree), "approve", "--label", "l",
                 "--anchor", str(artifact), "tests/test_alpha.py"]) == 0
    assert not (tree / ".canopus" / "freeze.json").exists()
    _commit(gate, "fix 1")

    assert main(["--root", str(tree), "freeze", "--label", "l",
                 "--anchor", str(artifact), "tests/test_alpha.py"]) == 0


def test_sc1_freeze_no_longer_writes_the_anchor_itself(tmp_path):
    from scripts.canopus import main

    tree = _tree(tmp_path / "tree")
    gate = _repo(tmp_path / "gate")
    artifact = gate / "approval.md"
    artifact.write_text("# gate\n", encoding="utf-8")
    _commit(gate, "seed")
    before = artifact.read_bytes()

    main(["--root", str(tree), "freeze", "--label", "l",
          "--anchor", str(artifact), "tests/test_alpha.py"])

    assert artifact.read_bytes() == before


# ============================================================
# SC-2: the committed state governs the lock
# ============================================================

def test_sc2_a_rebaseline_reddens_the_lock(tmp_path):
    """The one path where the anchor is the only thing left standing.

    Release, edit the contract, re-freeze: the manifest now matches disk, so
    `held` is True and the lock turns entirely on the anchor. Appending the new
    hash to the working copy without committing must not rescue it.
    """
    from scripts.canopus import main
    from scripts.utils.canopus_freeze import ANCHOR_PREFIX, read_freeze

    tree = _tree(tmp_path / "tree")
    gate = _repo(tmp_path / "gate")
    artifact = gate / "approval.md"
    artifact.write_text("# gate\n", encoding="utf-8")
    _commit(gate, "seed")

    main(["--root", str(tree), "approve", "--label", "l",
          "--anchor", str(artifact), "tests/test_alpha.py"])
    _commit(gate, "fix 1")
    main(["--root", str(tree), "freeze", "--label", "l",
          "--anchor", str(artifact), "tests/test_alpha.py"])
    main(["--root", str(tree), "release", "--reason", "re-baseline"])

    (tree / "tests" / "test_alpha.py").write_text(
        "def test_a():\n    assert False\n", encoding="utf-8"
    )
    main(["--root", str(tree), "approve", "--label", "l", "--anchor", str(artifact),
          "--replace", "--reason", "widened", "tests/test_alpha.py"])
    # The new approval is written but deliberately NOT committed.
    main(["--root", str(tree), "freeze", "--label", "l",
          "--anchor", str(artifact), "tests/test_alpha.py"])

    manifest = read_freeze(tree)
    assert manifest is not None, "the re-freeze must be permitted; only the lock reddens"
    assert ANCHOR_PREFIX in artifact.read_text(encoding="utf-8")
    assert main(["--root", str(tree), "verify"]) == 1


def test_sc2_a_tracked_artifact_without_an_approval_never_reads_held(tmp_path):
    """Amber, not green. A hash nobody committed is not an approval."""
    from scripts.utils.canopus_freeze import LOCK_HELD, lock_state
    from scripts.utils.canopus_git import resolve_anchor

    gate = _repo(tmp_path / "gate")
    artifact = gate / "approval.md"
    artifact.write_text("# gate\n", encoding="utf-8")
    _commit(gate, "gate with no approval in it")
    with artifact.open("a", encoding="utf-8") as handle:
        handle.write(f"\ncanopus-anchor: {'b' * 64}\n")

    resolution = resolve_anchor({"anchor": str(artifact), "root": "b" * 64})
    report = {"recomputed_root": "b" * 64, "changed": [], "added": [],
              "removed": [], "held": True}

    assert lock_state(report, resolution.status, resolution.value) != LOCK_HELD


def test_sc2_a_deleted_artifact_still_reddens_the_lock(tmp_path):
    """`git show HEAD:<rel>` is existence-blind, so the committed value alone
    would report a held lock over an anchor that is gone."""
    from scripts.utils.canopus_freeze import LOSS_OF_LOCK, lock_state
    from scripts.utils.canopus_git import resolve_anchor

    gate = _repo(tmp_path / "gate")
    artifact = gate / "approval.md"
    artifact.write_text(f"canopus-anchor: {'d' * 64}\n", encoding="utf-8")
    _commit(gate, "approve")
    artifact.unlink()

    resolution = resolve_anchor({"anchor": str(artifact), "root": "d" * 64})
    report = {"recomputed_root": "d" * 64, "changed": [], "added": [],
              "removed": [], "held": True}

    assert lock_state(report, resolution.status, resolution.value) == LOSS_OF_LOCK


def test_sc2_the_committed_line_beats_an_appended_one(tmp_path):
    from scripts.utils.canopus_git import COMMITTED, read_committed_anchor

    gate = _repo(tmp_path / "gate")
    artifact = gate / "approval.md"
    artifact.write_text(f"canopus-anchor: {'a' * 64}\n", encoding="utf-8")
    _commit(gate, "approve")
    with artifact.open("a", encoding="utf-8") as handle:
        handle.write(f"\ncanopus-anchor: {'b' * 64}\n")

    assert read_committed_anchor(artifact) == (COMMITTED, "a" * 64)


# ============================================================
# SC-3 and SC-6: the third axis, and the fallback that stays honest
# ============================================================

def test_sc3_all_three_surfaces_print_the_approval_axis(tmp_path, capsys):
    from scripts.canopus import main

    tree = _tree(tmp_path / "tree")
    loose = tmp_path / "loose"
    loose.mkdir()
    artifact = loose / "approval.md"
    artifact.write_text("# gate\n", encoding="utf-8")

    main(["--root", str(tree), "freeze", "--label", "l",
          "--anchor", str(artifact), "tests/test_alpha.py"])
    capsys.readouterr()

    for command in ("status", "verify", "pack"):
        main(["--root", str(tree), command])
        assert "APPROVAL" in capsys.readouterr().out, command


def test_sc6_freezing_outside_a_repository_succeeds_and_says_so(tmp_path, capsys):
    """The operator whose gate artifact is a file in a folder still gets a lock,
    and can never mistake it for a verified approval."""
    from scripts.canopus import main
    from scripts.utils.canopus_freeze import APPROVAL_UNVERIFIED

    tree = _tree(tmp_path / "tree")
    loose = tmp_path / "loose"
    loose.mkdir()
    artifact = loose / "approval.md"
    artifact.write_text("# gate\n", encoding="utf-8")

    assert main(["--root", str(tree), "freeze", "--label", "l",
                 "--anchor", str(artifact), "tests/test_alpha.py"]) == 0
    capsys.readouterr()
    main(["--root", str(tree), "status"])

    assert APPROVAL_UNVERIFIED in capsys.readouterr().out


def test_sc3_each_unverifiable_reason_is_distinct(tmp_path):
    """A single "could not check" hides which of three worlds you are in."""
    from scripts.utils.canopus_freeze import APPROVAL_UNVERIFIED, approval_state

    reasons = set()
    for status in ("uncommitted", "no_repo", "no_git"):
        axis, reason = approval_state("a" * 64, status, None)
        assert axis == APPROVAL_UNVERIFIED
        reasons.add(reason)

    assert len(reasons) == 3


# ============================================================
# SC-4 and SC-5: redness that means something
# ============================================================

def test_sc4_a_vacuous_but_red_contract_is_refused(tmp_path):
    """The case wire 2 accepts today, measured rather than assumed.

    Every test here dies on ImportError, so the existing redness gate passes it.
    Every test also asserts only what a mock satisfies, so it asserts nothing.
    """
    from scripts.utils.canopus_contract import (
        missing_modules, parse_junit, run_null_stub, run_pytest_report,
        vacuity_refusal,
    )

    base = tmp_path / "tree"
    _write(base, "c/test_v.py",
           "def test_one():\n"
           "    from absent_thing import answer\n"
           "    assert answer() is not None\n"
           "\n\n"
           "def test_two():\n"
           "    from absent_thing import answer\n"
           "    assert answer\n")

    xml_text = run_pytest_report([base / "c"], base)
    _counts, outcomes = parse_junit(xml_text)
    vacuous = run_null_stub([base / "c"], base, missing_modules(xml_text))

    assert vacuity_refusal(outcomes, vacuous), (
        "a contract that passes wholly against mocks asserts nothing and must be refused"
    )


def test_sc5_a_real_assertion_is_not_labelled_vacuous(tmp_path):
    from scripts.utils.canopus_contract import (
        missing_modules, run_null_stub, run_pytest_report,
    )

    base = tmp_path / "tree"
    _write(base, "c/test_v.py",
           "def test_vacuous():\n"
           "    from absent_thing import answer\n"
           "    assert answer() is not None\n"
           "\n\n"
           "def test_real():\n"
           "    from absent_thing import answer\n"
           "    assert answer() == 42\n")

    xml_text = run_pytest_report([base / "c"], base)
    vacuous = run_null_stub([base / "c"], base, missing_modules(xml_text))

    assert ("c/test_v.py", "test_vacuous") in vacuous
    assert ("c/test_v.py", "test_real") not in vacuous


def test_sc5_the_stub_does_not_shadow_a_module_that_exists(tmp_path):
    """Matching on the first dotted segment mocked whole packages, so modules the
    contract legitimately imports came back as mocks and a good contract was
    refused as vacuous."""
    from scripts.utils.canopus_contract import run_null_stub

    base = tmp_path / "tree"
    _write(base, "c/test_v.py",
           "def test_real_module_survives():\n"
           "    from scripts.utils.canopus_freeze import ANCHOR_PREFIX\n"
           "    assert ANCHOR_PREFIX == 'canopus-anchor:'\n")

    passed = run_null_stub(
        [base / "c"], base, {"scripts.utils.canopus_absent_thing"}
    )

    assert ("c/test_v.py", "test_real_module_survives") in passed


def test_sc4_missing_modules_sees_a_missing_name_not_just_a_missing_module(tmp_path):
    """A module file that exists without the name the contract imports is the
    ordinary mid-build state, and the probe is blind to it without this."""
    from scripts.utils.canopus_contract import missing_modules, run_pytest_report

    base = tmp_path / "tree"
    _write(base, "present_module.py", "existing = 1\n")
    _write(base, "c/test_v.py",
           "def test_one():\n"
           "    from present_module import not_written_yet\n"
           "    assert not_written_yet\n")

    found = missing_modules(run_pytest_report([base / "c"], base))

    assert "present_module" in found


def test_sc5_failure_modes_tell_an_import_from_an_assertion(tmp_path):
    from scripts.utils.canopus_contract import parse_failure_modes, run_pytest_report

    base = tmp_path / "tree"
    _write(base, "c/test_v.py",
           "def test_import():\n"
           "    import absent_thing\n"
           "    assert absent_thing\n"
           "\n\n"
           "def test_assertion():\n"
           "    assert 1 == 2\n")

    modes = parse_failure_modes(run_pytest_report([base / "c"], base))

    assert modes[("c/test_v.py", "test_import")] == "import"
    assert modes[("c/test_v.py", "test_assertion")] == "assertion"


# ============================================================
# The enforcement path stays inside the guarantee
# ============================================================

def test_the_lock_decider_is_inside_the_documented_enforcer_set():
    """canopus_git resolves the anchor, so it decides LOCK HELD against LOSS OF
    LOCK. A decider outside the freeze is the same hole C4 closed for the write
    path, and the closure test exists so a new import cannot escape silently."""
    from scripts.utils.workspace import get_workspace_root

    skill = (get_workspace_root() / ".claude" / "skills" / "pre-impl"
             / "SKILL.md").read_text(encoding="utf-8")

    assert "--content scripts/utils/canopus_git.py" in skill


def test_approve_refuses_an_approval_that_is_already_committed(tmp_path):
    """`git checkout --` erases an uncommitted line, so refusing on the working
    copy alone lets a second approval over a different set slip past --replace."""
    from scripts.canopus import main

    tree = _tree(tmp_path / "tree")
    gate = _repo(tmp_path / "gate")
    artifact = gate / "approval.md"
    artifact.write_text("# gate\n", encoding="utf-8")
    _commit(gate, "seed")

    assert main(["--root", str(tree), "approve", "--label", "l",
                 "--anchor", str(artifact), "tests/test_alpha.py"]) == 0
    _commit(gate, "fix 1")
    subprocess.run(["git", "-C", str(gate), "checkout", "--", "approval.md"],
                   check=True, capture_output=True, text=True)

    assert main(["--root", str(tree), "approve", "--label", "l",
                 "--anchor", str(artifact), "tests/test_alpha.py"]) == 1
