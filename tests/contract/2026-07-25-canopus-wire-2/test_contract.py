"""Frozen contract for Canopus wire 2: the approved definition of done.

Approved at Fix 1 and frozen before any implementation exists. These assertions
are not editable by the builder. A contract that turns out to be genuinely wrong
reopens the approval gate; it is never edited in place.

THE AUTHORING RULE, and it is mechanical rather than stylistic: every import of
the code under test happens INSIDE a test body. At freeze time the implementation
does not exist, so a module-scope import would stop the file collecting at all,
and a file that collects nothing yields no item count. The count taken at freeze
time is what closes the node-id subset hole, so the rule is what makes the
contract measurable rather than merely present.

Coverage, stated honestly. These tests carry success criteria 1 through 9 of
`docs/superpowers/specs/2026-07-25-canopus-contract-promotion-design.md`.
SC-10 (the full gate green with the coverage ratchet not lowered) and SC-11 (no
operator data in the engine tree) are properties of the gate and of the existing
guards, not of any assertion here. A green contract does not prove those two.
"""
import json
from pathlib import Path

import pytest


def _tree(tmp_path: Path) -> Path:
    """A synthetic working tree carrying a test gate.

    The CLI refuses a root with no `scripts/run-tests.py`, because a tree with
    nowhere to check the freeze would take an inert one.
    """
    root = tmp_path / "tree"
    (root / "tests").mkdir(parents=True)
    (root / "scripts").mkdir(parents=True)
    (root / "scripts" / "run-tests.py").write_text("# stub test gate\n")
    (root / "tests" / "test_alpha.py").write_text("def test_a():\n    assert True\n")
    return root


def _anchor(tmp_path: Path) -> Path:
    """An anchor artifact outside the working tree, as the primitive requires."""
    path = tmp_path / "outside" / "gate-artifact.md"
    path.parent.mkdir(parents=True)
    path.write_text("# gate artifact\n")
    return path


def _contract_dir(root: Path, *, red: bool = True) -> Path:
    directory = root / "tests" / "contract" / "slice"
    directory.mkdir(parents=True, exist_ok=True)
    body = (
        "def test_a():\n    assert False\n\n\ndef test_b():\n    assert True\n"
        if red else
        "def test_a():\n    assert True\n\n\ndef test_b():\n    assert True\n"
    )
    (directory / "test_slice.py").write_text(body)
    return directory


def _digest_of(root: Path) -> str:
    return json.loads((root / ".canopus" / "freeze.json").read_text())["root"]


# ============================================================
# SC-1: content-only freeze
# ============================================================

def test_sc1_content_only_freezes_bytes_without_guarding_the_parent(tmp_path):
    """The enforcer files can be frozen without paralysing scripts/.

    Frozen as ordinary files they would install a composition guard on their
    parent, and a build that cannot create a file under scripts/ cannot build
    anything, which makes the required practice unenforceable.
    """
    from scripts.utils.canopus_freeze import build_manifest, verify_manifest

    root = _tree(tmp_path)
    manifest = build_manifest(
        [], root, label="contract", frozen_at="2026-07-25T00:00:00+00:00",
        content_only=[root / "scripts" / "run-tests.py"],
    )

    assert "scripts/run-tests.py" in manifest["files"]
    assert "scripts" not in manifest["dirs"]

    (root / "scripts" / "new_module.py").write_text("x = 1\n")
    assert verify_manifest(manifest, root)["held"] is True

    (root / "scripts" / "run-tests.py").write_text("# moved\n")
    assert verify_manifest(manifest, root)["held"] is False


# ============================================================
# SC-2: the collected-item baseline
# ============================================================

def test_sc2_the_baseline_enters_the_root_hash(tmp_path):
    """A baseline outside the hash could be edited down to 1 invisibly."""
    from scripts.utils.canopus_freeze import build_manifest

    root = _tree(tmp_path)
    common = dict(label="contract", frozen_at="2026-07-25T00:00:00+00:00")
    seven = build_manifest([root / "tests" / "test_alpha.py"], root,
                           baseline={"tests/test_alpha.py": 7}, **common)
    one = build_manifest([root / "tests" / "test_alpha.py"], root,
                         baseline={"tests/test_alpha.py": 1}, **common)

    assert seven["root"] != one["root"]


def test_sc2_freeze_contract_captures_the_baseline_from_a_real_run(tmp_path):
    from scripts.canopus import main

    root = _tree(tmp_path)
    anchor = _anchor(tmp_path)
    _contract_dir(root)

    assert main(["--root", str(root), "freeze", "--label", "contract",
                 "--anchor", str(anchor),
                 "--contract", "tests/contract/slice"]) == 0

    manifest = json.loads((root / ".canopus" / "freeze.json").read_text())
    assert manifest["baseline"] == {"tests/contract/slice/test_slice.py": 2}


# ============================================================
# SC-3: red before green, refused rather than promised
# ============================================================

def test_sc3_an_all_green_contract_is_refused(tmp_path, capsys):
    """A test that is green before the code exists asserts nothing."""
    from scripts.canopus import main

    root = _tree(tmp_path)
    anchor = _anchor(tmp_path)
    _contract_dir(root, red=False)

    assert main(["--root", str(root), "freeze", "--label", "contract",
                 "--anchor", str(anchor),
                 "--contract", "tests/contract/slice"]) == 1
    assert "asserts nothing" in capsys.readouterr().err
    assert not (root / ".canopus" / "freeze.json").exists()


def test_sc3_a_contract_file_that_collects_nothing_is_refused(tmp_path, capsys):
    """A module-scope import of absent code collects zero items.

    Freezing it would record a baseline for a file that yields nothing, which is
    the fail-open direction: the subset check would then pass against a count
    nobody can meet.
    """
    from scripts.canopus import main

    root = _tree(tmp_path)
    anchor = _anchor(tmp_path)
    directory = root / "tests" / "contract" / "slice"
    directory.mkdir(parents=True)
    (directory / "test_slice.py").write_text(
        "from scripts.utils.absent_module import thing\n\n\n"
        "def test_a():\n    assert thing\n"
    )

    assert main(["--root", str(root), "freeze", "--label", "contract",
                 "--anchor", str(anchor),
                 "--contract", "tests/contract/slice"]) == 1
    err = capsys.readouterr().err
    assert "collected nothing" in err
    assert "inside the test body" in err


# ============================================================
# SC-4: attestation against the baseline (the M1 hole from wire 1)
# ============================================================

def test_sc4_a_node_id_subset_does_not_attest_against_the_baseline():
    """`pytest file::test_one` on a seven-test frozen file reports 1 of 7.

    Wire 1 attested this case: it collects an item, reports it, fires no
    deselection hook, and the arithmetic balanced. The freeze-time count is what
    it is now compared against.
    """
    from scripts.utils.canopus_freeze import build_attestation

    record = build_attestation(
        root_digest="a" * 64,
        frozen_tests={"tests/contract/slice/test_slice.py": {
            "collected": 1, "passed": 1, "failed": 0, "skipped": 0, "deselected": 0,
        }},
        exit_status=0,
        attested_at="2026-07-25T00:00:00+00:00",
        baseline={"tests/contract/slice/test_slice.py": 7},
    )

    assert record["attested"] is False
    assert any("collected 1 of 7" in reason for reason in record["reasons"])


def test_sc4_a_complete_run_attests_against_the_baseline():
    from scripts.utils.canopus_freeze import build_attestation

    record = build_attestation(
        root_digest="a" * 64,
        frozen_tests={"tests/contract/slice/test_slice.py": {
            "collected": 7, "passed": 7, "failed": 0, "skipped": 0, "deselected": 0,
        }},
        exit_status=0,
        attested_at="2026-07-25T00:00:00+00:00",
        baseline={"tests/contract/slice/test_slice.py": 7},
    )

    assert record["attested"] is True
    assert record["reasons"] == []


# ============================================================
# SC-5: the recipe breaks loudly
# ============================================================

def test_sc5_a_v1_manifest_is_refused_as_corrupt(tmp_path):
    """Accepting both recipes would mean a v1 manifest carries no baseline and
    the subset check silently does nothing, which is worse than a refusal."""
    from scripts.utils.canopus_freeze import (
        FreezeCorrupt, freeze_state_path, read_freeze,
    )

    root = _tree(tmp_path)
    state = freeze_state_path(root)
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text(json.dumps({
        "recipe": "canopus-freeze-v1", "label": "old", "frozen_at": "",
        "anchor": "", "git_sha": "", "root": "0" * 64, "files": {}, "dirs": {},
    }))

    with pytest.raises(FreezeCorrupt, match="canopus-freeze-v1"):
        read_freeze(root)


# ============================================================
# SC-6: freeze records the anchor line itself
# ============================================================

def test_sc6_freeze_records_the_anchor_line_and_verify_holds(tmp_path):
    """Nobody transcribes the hash.

    The council that reviewed wire 1 was unanimous that a human cannot be the
    comparator of a hex string; the same argument makes them a poor transcriber.
    """
    from scripts.canopus import main

    root = _tree(tmp_path)
    anchor = _anchor(tmp_path)

    assert main(["--root", str(root), "freeze", "tests/test_alpha.py",
                 "--label", "contract", "--anchor", str(anchor)]) == 0

    assert f"canopus-anchor: {_digest_of(root)}" in anchor.read_text()
    assert main(["--root", str(root), "verify"]) == 0


def test_sc6_an_anchor_already_recording_a_hash_is_refused(tmp_path, capsys):
    from scripts.canopus import main

    root = _tree(tmp_path)
    anchor = _anchor(tmp_path)
    anchor.write_text("canopus-anchor: " + "b" * 64 + "\n")
    before = anchor.read_bytes()

    assert main(["--root", str(root), "freeze", "tests/test_alpha.py",
                 "--label", "contract", "--anchor", str(anchor)]) == 1
    assert "already records" in capsys.readouterr().err
    assert anchor.read_bytes() == before
    assert not (root / ".canopus" / "freeze.json").exists()


# ============================================================
# SC-7: probe shows the contract without freezing it
# ============================================================

def test_sc7_probe_prints_per_test_outcomes_and_writes_nothing(tmp_path, capsys):
    """The gate catches an entirely vacuous contract; this table is what lets a
    human catch a partly vacuous one."""
    from scripts.canopus import main

    root = _tree(tmp_path)
    _contract_dir(root)

    assert main(["--root", str(root), "probe", "tests/contract/slice"]) == 0

    out = capsys.readouterr().out
    assert "test_a" in out
    assert "test_b" in out
    assert not (root / ".canopus").exists()


# ============================================================
# SC-8: the Fix 2 evidence page
# ============================================================

def test_sc8_pack_reports_both_axes_and_what_is_not_covered(tmp_path, capsys):
    from scripts.canopus import main

    root = _tree(tmp_path)
    anchor = _anchor(tmp_path)
    assert main(["--root", str(root), "freeze", "tests/test_alpha.py",
                 "--label", "contract", "--anchor", str(anchor)]) == 0
    capsys.readouterr()

    assert main(["--root", str(root), "pack"]) == 0

    out = capsys.readouterr().out
    for section in ("LOCK HELD", "NOT ATTESTED", "continuity", "staleness",
                    "not covered"):
        assert section in out


def test_sc8_pack_never_raises_on_damaged_state(tmp_path, capsys):
    """The pack is read at the one moment the operator decides to keep the work.
    A traceback there is worse than a missing section."""
    from scripts.canopus import main
    from scripts.utils.canopus_freeze import attest_state_path

    root = _tree(tmp_path)
    anchor = _anchor(tmp_path)
    assert main(["--root", str(root), "freeze", "tests/test_alpha.py",
                 "--label", "contract", "--anchor", str(anchor)]) == 0

    damaged = attest_state_path(root)
    damaged.parent.mkdir(parents=True, exist_ok=True)
    damaged.write_text("{not json")

    assert main(["--root", str(root), "pack"]) == 0


# ============================================================
# SC-9: the skill writes real files and freezes on approval
# ============================================================

def test_sc9_pre_impl_writes_real_contract_files_and_freezes():
    """Layer 1, deterministic: the skill's own text is the artifact here.

    The prose-draft label is the marker of the gap this slice closes. While it
    is present, the contract approved at Fix 1 is still a description of tests
    rather than the tests themselves.
    """
    from scripts.utils.workspace import get_workspace_root

    skill = get_workspace_root() / ".claude" / "skills" / "pre-impl" / "SKILL.md"
    text = skill.read_text(encoding="utf-8")

    assert "tests/contract/" in text
    assert "scripts/canopus.py freeze" in text
    assert "CEO-UNAPPROVED DRAFT" not in text
