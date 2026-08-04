"""The frozen contract for `enforcer-set-bound` — v3 Change 2's three loose ends.

Change 2 took the enforcer BYTES out of the contract root so an enforcer edit
costs a `repin` instead of a six-command retake. It took the enforcer NAMES out
with them, and nothing put them back. Measured 2026-08-04 on a synthetic tree:
a freeze over ten enforcers and a freeze over nine compute the SAME root, so a
`release --window` followed by a `freeze` with a shorter `--content` list drops
an enforcer, leaves the committed approval matching, and reads LOCK HELD and
APPROVED. From then on that enforcer is editable under a green lock, invisibly.

Two smaller ends from the same slice are closed here rather than left:
`repin_enforcer` clears the attestation even when nothing moved, and
`loss_of_lock_sentences` decides whether the CONTRACT moved from the file lists
instead of from the root comparison, so it can say "the ENFORCER moved, not the
contract" over a tree whose contract root disagrees.

Every test imports the code under test INSIDE its body: the behaviour does not
exist yet, and a module-scope import of a changed signature would stop the file
collecting.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

STAMP = "2026-01-01T00:00:00+00:00"
_CLI = Path(__file__).resolve().parents[3] / "scripts" / "canopus.py"


# ============================================================
# Scratch trees. Nothing here reads the engine's own working tree.
# ============================================================

@pytest.fixture
def tree(tmp_path: Path) -> Path:
    """A working tree with one contract file and TWO enforcer files.

    Two, not one: the whole slice is about the enforcer SET, and a set of one
    cannot be narrowed without becoming empty, which is the special case rather
    than the ordinary one.
    """
    root = tmp_path / "tree"
    (root / "tests" / "contract").mkdir(parents=True)
    (root / "scripts").mkdir(parents=True)
    (root / "tests" / "contract" / "test_c.py").write_text(
        "def test_c():\n    assert True\n", encoding="utf-8")
    (root / "scripts" / "run-tests.py").write_text("# enforcer one\n", encoding="utf-8")
    (root / "scripts" / "gate.py").write_text("# enforcer two\n", encoding="utf-8")
    return root


@pytest.fixture
def anchor(tmp_path: Path) -> Path:
    """A scratch gate artifact stating one criterion, as `freeze` demands."""
    path = tmp_path / "outside" / "gate-artifact.md"
    path.parent.mkdir(parents=True)
    path.write_text("# gate artifact\n\n"
                    "## Phase 1 — Success criteria\n\n"
                    "- **SC-1** WHEN a scratch slice runs, THE SYSTEM SHALL "
                    "behave as the test says.\n", encoding="utf-8")
    return path


def _manifest(tree: Path, anchor: Path, enforcers):
    from scripts.utils.canopus_freeze import build_manifest

    return build_manifest(
        [tree / "tests" / "contract"], tree,
        label="s", frozen_at=STAMP, anchor=anchor,
        content_only=[tree / rel for rel in enforcers])


_BOTH = ("scripts/run-tests.py", "scripts/gate.py")


# ============================================================
# CLI helpers. The end-to-end route is the one the hole was found on.
# ============================================================

def _git(repo: Path, *argv: str) -> None:
    subprocess.run(["git", "-C", str(repo), *argv], check=True,
                   capture_output=True, text=True)


def _gate_repo(anchor: Path) -> Path:
    """The anchor's directory as a repository with a SYNTHETIC identity.

    Invented on purpose: this file ships in a public repository and carries no
    real person or host. The seed commit is empty so no caller's artifact is
    committed behind its back.
    """
    gate = anchor.parent
    for argv in (["init", "-q", "-b", "main"],
                 ["config", "user.email", "builder@example.invalid"],
                 ["config", "user.name", "Builder"],
                 ["commit", "-q", "--allow-empty", "-m", "seed"]):
        _git(gate, *argv)
    return gate


def _cli(tree: Path, *argv: str):
    """The CLI as a subprocess, so `--root` is the only tree it can reach."""
    return subprocess.run(
        [sys.executable, str(_CLI), "--root", str(tree), *argv],
        capture_output=True, text=True, cwd=str(tree))


def _content_flags(enforcers):
    flags = []
    for rel in enforcers:
        flags += ["--content", rel]
    return flags


# ============================================================
# SC-1 — the contract root binds the enforcer NAMES
# ============================================================

def test_dropping_an_enforcer_from_the_frozen_set_moves_the_contract_root(tree, anchor):
    """SC-1. WHEN a freeze is taken over a different set of enforcer NAMES,
    THE SYSTEM SHALL compute a different contract root.

    The measurement that opened this slice, asserted on the root itself rather
    than on any command that reads it: every refusal downstream is a comparison
    against this one value, so if the roots agree here nothing downstream can
    refuse.
    """
    both = _manifest(tree, anchor, _BOTH)["root"]
    fewer = _manifest(tree, anchor, ("scripts/run-tests.py",))["root"]

    assert both != fewer, (
        "a freeze over nine enforcers still computes the same root as a freeze "
        "over ten, so the committed approval keeps matching and the dropped "
        "enforcer is editable under a green lock")


def test_dropping_the_LAST_enforcer_moves_the_contract_root_too(tree, anchor):
    """SC-1b. The empty set is the case a `if names:` guard silently exempts,
    and it is the worst one: no enforcer is watched at all."""
    one = _manifest(tree, anchor, ("scripts/run-tests.py",))["root"]
    none = _manifest(tree, anchor, ())["root"]

    assert one != none, (
        "narrowing the enforcer set to EMPTY leaves the contract root where it "
        "was, so a freeze that watches no enforcer at all reads as the one that "
        "was approved")


def test_the_root_payload_carries_the_enforcer_names_explicitly(tree, anchor):
    """SC-1c. Named in the payload, not inferred from two hashes differing.

    Two roots comparing unequal is also what an implementation that started
    hashing the enforcer BYTES produces, and that implementation fails SC-2
    while passing SC-1. Asserting the payload tells the two apart.
    """
    from scripts.utils.canopus_freeze import root_hash_payload

    payload = root_hash_payload(_manifest(tree, anchor, _BOTH))

    assert payload["content_names"] == ["scripts/gate.py", "scripts/run-tests.py"], (
        "the root payload does not carry the enforcer names as a sorted list")


def test_a_freeze_over_a_narrower_enforcer_set_is_refused_by_the_committed_approval(
    tree, anchor
):
    """SC-1d [integration]. The whole hole, end to end through the CLI.

    `approve` over both enforcers, a human COMMIT of the artifact, then `freeze`
    with one `--content` flag removed. This is the sequence measured on
    2026-08-04, and today it exits 0 and takes the lock.
    """
    gate = _gate_repo(anchor)

    approved = _cli(tree, "approve", "tests/contract", "--label", "s",
                    "--anchor", str(anchor), *_content_flags(_BOTH))
    assert approved.returncode == 0, approved.stderr
    _git(gate, "add", anchor.name)
    _git(gate, "commit", "-q", "-m", "the approval, over both enforcers")

    narrowed = _cli(tree, "freeze", "tests/contract", "--label", "s",
                    "--anchor", str(anchor),
                    *_content_flags(("scripts/run-tests.py",)))

    assert narrowed.returncode == 1, (
        "a freeze that drops an enforcer was accepted against an approval "
        f"taken over both:\n{narrowed.stdout}\n{narrowed.stderr}")
    assert "the committed approval does not match" in narrowed.stderr, (
        "the freeze was refused for some OTHER reason than the approval "
        f"disagreeing:\n{narrowed.stderr}")
    assert not (tree / ".canopus" / "freeze.json").exists(), (
        "the narrowed freeze was refused and still wrote a manifest")


def test_a_freeze_over_the_APPROVED_enforcer_set_is_taken_and_holds(tree, anchor):
    """SC-1e [integration]. The positive half, and without it SC-1d is satisfied
    by refusing every freeze there is.

    The same sequence as SC-1d with the `--content` list left alone: the freeze
    is taken, and `verify` reads LOCK HELD and APPROVED off the committed
    artifact. An implementation that closes the hole by making the root a
    function of something the freeze cannot reproduce fails HERE, not there.
    """
    gate = _gate_repo(anchor)

    approved = _cli(tree, "approve", "tests/contract", "--label", "s",
                    "--anchor", str(anchor), *_content_flags(_BOTH))
    assert approved.returncode == 0, approved.stderr
    _git(gate, "add", anchor.name)
    _git(gate, "commit", "-q", "-m", "the approval, over both enforcers")

    taken = _cli(tree, "freeze", "tests/contract", "--label", "s",
                 "--anchor", str(anchor), *_content_flags(_BOTH))
    assert taken.returncode == 0, f"{taken.stdout}\n{taken.stderr}"
    assert (tree / ".canopus" / "freeze.json").is_file()

    checked = _cli(tree, "verify")
    assert checked.returncode == 0, checked.stderr
    assert "LOCK HELD" in checked.stdout
    assert "APPROVED" in checked.stdout


# ============================================================
# SC-2 — and Change 2's saving survives it
# ============================================================

def test_editing_an_enforcer_byte_still_leaves_the_contract_root_alone(tree, anchor):
    """SC-2. WHEN only enforcer BYTES change and the name set does not,
    THE SYSTEM SHALL leave the contract root unchanged.

    Change 2's entire benefit, and the reason the names go into the payload
    while the digests stay out. Binding the whole enforcer map would close the
    hole and undo the 21 records the split was taken for.
    """
    before = _manifest(tree, anchor, _BOTH)["root"]
    (tree / "scripts" / "gate.py").write_text("# enforcer two, edited\n", encoding="utf-8")
    after = _manifest(tree, anchor, _BOTH)["root"]

    assert before == after, (
        "an enforcer edit moves the contract root again, so the committed "
        "approval stops matching and the retake ceremony Change 2 removed is back")


def test_the_root_payload_carries_no_enforcer_DIGEST(tree, anchor):
    """SC-2b. The exclusion, asserted against the digest VALUE.

    A payload carrying the whole content map satisfies SC-1 and SC-1c both, and
    is caught only here. Asserted on the digest string rather than on a key
    name, because a key renamed is still the same leak.
    """
    from scripts.utils.canopus_freeze import enforcer_map, root_hash_payload

    manifest = _manifest(tree, anchor, _BOTH)
    digests = set(enforcer_map(manifest).values())
    assert digests, "the fixture froze no enforcer, so this test proves nothing"

    rendered = json.dumps(root_hash_payload(manifest), sort_keys=True)
    for digest in digests:
        assert digest not in rendered, (
            "an enforcer digest is inside the root payload, so an enforcer edit "
            "moves the contract root")


# ============================================================
# SC-3 — a deleted enforcer is still the ENFORCER's axis
# ============================================================

def test_a_deleted_enforcer_reddens_the_lock_without_moving_the_contract_root(
    tree, anchor
):
    """SC-3. WHEN a recorded enforcer file is absent from disk, THE SYSTEM SHALL
    report it on `enforcer_moved` and SHALL NOT move the recomputed contract root.

    The trap in binding the names: derive them from DISK and a deleted enforcer
    reads as a moved contract, sending the operator to a re-approval when the
    cure is to restore the file or take a new freeze. The name set is a recorded
    expectation, like the baseline and the plugin set beside it, never a
    re-measurement.
    """
    from scripts.utils.canopus_freeze import verify_manifest

    manifest = _manifest(tree, anchor, _BOTH)
    (tree / "scripts" / "gate.py").unlink()

    report = verify_manifest(manifest, tree)

    assert report["enforcer_moved"] == ["scripts/gate.py"], (
        "a deleted enforcer is not reported on its own axis")
    assert report["held"] is False
    assert report["recomputed_root"] == manifest["root"], (
        "deleting an enforcer moved the recomputed CONTRACT root, so the tree "
        "now reads as a contract nobody approved")


# ============================================================
# SC-4 — the payload shape change is named
# ============================================================

def test_the_previous_recipe_is_refused_by_name(tree, anchor):
    """SC-4. WHEN a manifest carrying the previous recipe is read, THE SYSTEM
    SHALL refuse it by name.

    The module's own rule for every previous bump: a new payload shape without
    one reads as LOSS OF LOCK on a tree where nothing moved, and sends an
    operator hunting a file that never changed.
    """
    from scripts.utils.canopus_freeze import (
        RECIPE, FreezeCorrupt, read_freeze, write_freeze)

    assert RECIPE == "canopus-freeze-v7", (
        "the root payload changed shape and the recipe did not")

    stale = _manifest(tree, anchor, _BOTH)
    stale["recipe"] = "canopus-freeze-v6"
    write_freeze(tree, stale)

    with pytest.raises(FreezeCorrupt) as caught:
        read_freeze(tree)
    assert "canopus-freeze-v6" in str(caught.value)


# ============================================================
# SC-5 — the contract sentence is decided by the root, not by the file lists
# ============================================================

def _resolution(anchor: Path, value: str):
    from scripts.utils.canopus_freeze import ANCHOR_RECORDED
    from scripts.utils.canopus_git import AnchorResolution

    return AnchorResolution(anchor=anchor, status=ANCHOR_RECORDED, value=value,
                            approval="", approval_reason="", source="")


def test_a_moved_contract_root_is_said_even_when_an_enforcer_also_moved(tree, anchor):
    """SC-5. WHEN the recomputed contract root disagrees with the stored root
    AND an enforcer also moved, THE SYSTEM SHALL say the contract moved too.

    Measured 2026-08-04: with the stored root replaced by a value the tree does
    not compute and one enforcer byte edited, the only sentence said is "The
    ENFORCER moved, NOT the contract", which is false of that tree and names the
    cheap cure. The operator re-pins and the lock stays red.
    """
    from scripts.utils.canopus_freeze import verify_manifest
    from scripts.utils.canopus_gate import loss_of_lock_sentences

    manifest = _manifest(tree, anchor, _BOTH)
    manifest["root"] = "0" * 64
    (tree / "scripts" / "gate.py").write_text("# enforcer two, edited\n", encoding="utf-8")

    report = verify_manifest(manifest, tree)
    assert report["changed"] == [] and report["added"] == [] and report["removed"] == [], (
        "the fixture moved a contract FILE, so this test would pass on the old "
        "file-list branch and prove nothing")
    assert report["enforcer_moved"] == ["scripts/gate.py"]

    said = " ".join(loss_of_lock_sentences(
        report, _resolution(anchor, report["recomputed_root"])))

    assert "ENFORCER moved" in said, "the enforcer sentence was dropped"
    assert "The frozen contract moved" in said, (
        "the contract root disagrees and the operator is told only to re-pin")


def test_an_enforcer_moving_ALONE_still_does_not_accuse_the_contract(tree, anchor):
    """SC-5b. The other half. Without it SC-5 is satisfied by always saying both,
    which puts back the undifferentiated red the split was taken to remove."""
    from scripts.utils.canopus_freeze import verify_manifest
    from scripts.utils.canopus_gate import loss_of_lock_sentences

    manifest = _manifest(tree, anchor, _BOTH)
    (tree / "scripts" / "gate.py").write_text("# enforcer two, edited\n", encoding="utf-8")

    report = verify_manifest(manifest, tree)
    assert report["recomputed_root"] == manifest["root"], (
        "the fixture moved the contract root, so this test is not the ALONE case")

    said = " ".join(loss_of_lock_sentences(
        report, _resolution(anchor, report["recomputed_root"])))

    assert "ENFORCER moved" in said
    assert "The frozen contract moved" not in said, (
        "an enforcer edit alone is being reported as a moved contract again")


def test_the_report_names_whether_the_root_moved(tree, anchor):
    """SC-5c. The signal itself, on the report rather than re-derived by each
    reader. `lock_state`, the gate and the CLI all ask this question, and a fact
    spelled three ways drifts in two of them."""
    from scripts.utils.canopus_freeze import verify_manifest

    manifest = _manifest(tree, anchor, _BOTH)
    clean = verify_manifest(manifest, tree)
    assert clean["root_moved"] is False
    assert clean["held"] is True, "an untouched tree does not read as held"

    manifest["root"] = "0" * 64
    moved = verify_manifest(manifest, tree)
    assert moved["root_moved"] is True
    assert moved["held"] is False, (
        "the contract root moved and nothing else did, and the lock still "
        "reads held")


# ============================================================
# SC-6 — a re-pin that finds nothing keeps the attestation
# ============================================================

def test_a_repin_that_finds_no_change_keeps_the_attestation(tree, anchor):
    """SC-6. WHEN a re-pin finds no enforcer byte changed, THE SYSTEM SHALL
    leave the attestation in place.

    `repin` is accepted when nothing moved on purpose: it is what an operator
    reaches for when they BELIEVE the enforcer moved, and recording that they
    checked is better than telling them there was nothing to do. Charging a full
    suite re-run for having checked is a tax on the right behaviour, and the
    reason the attestation goes — a green run produced by a different checker —
    is simply untrue when the checker's bytes are identical.
    """
    from scripts.utils.canopus_freeze import (
        attestation_state_path, repin_enforcer, write_freeze)

    write_freeze(tree, _manifest(tree, anchor, _BOTH))
    attestation = attestation_state_path(tree)
    attestation.write_text('{"green": true}\n', encoding="utf-8")

    event = repin_enforcer(tree, reason="checked, believed an enforcer had moved",
                           git_sha="0" * 40)

    assert event["changed"] == [], "the fixture moved an enforcer byte"
    assert attestation.is_file(), (
        "a re-pin over identical enforcer bytes still discarded the attestation, "
        "so checking costs a full suite re-run")


def test_the_repin_line_does_not_claim_a_clearing_that_did_not_happen(tree, anchor):
    """SC-6c [integration]. The terminal has to agree with the disk.

    `cmd_repin` closes with "The attestation is cleared" unconditionally, so an
    operator whose re-pin moved nothing is told a full suite re-run is owed. A
    fix that keeps the file and keeps the sentence has moved the defect from the
    disk to the screen rather than closed it.
    """
    from scripts.utils.canopus_freeze import attestation_state_path, write_freeze

    write_freeze(tree, _manifest(tree, anchor, _BOTH))
    attestation_state_path(tree).write_text('{"green": true}\n', encoding="utf-8")

    done = _cli(tree, "repin", "--reason", "checked, believed an enforcer had moved")

    assert done.returncode == 0, done.stderr
    assert "0 enforcer files re-recorded" in done.stdout
    assert "attestation is cleared" not in done.stdout, (
        "nothing moved, the attestation survived, and the operator was still "
        "told it was cleared")
    assert "is untouched" in done.stdout, (
        "the closing paragraph was deleted rather than corrected, so the "
        "re-pin no longer says the contract root stood still")


def test_a_repin_that_finds_a_change_still_discards_the_attestation(tree, anchor):
    """SC-6b. The half that must not be lost. The enforcer set holds the test
    runner and `conftest.py`, so a green run recorded before those bytes changed
    was produced by a DIFFERENT checker and may not speak for this one."""
    from scripts.utils.canopus_freeze import (
        attestation_state_path, repin_enforcer, write_freeze)

    write_freeze(tree, _manifest(tree, anchor, _BOTH))
    attestation = attestation_state_path(tree)
    attestation.write_text('{"green": true}\n', encoding="utf-8")
    (tree / "scripts" / "gate.py").write_text("# enforcer two, edited\n", encoding="utf-8")

    event = repin_enforcer(tree, reason="the enforcer moved and was re-pinned",
                           git_sha="0" * 40)

    assert event["changed"] == ["scripts/gate.py"]
    assert not attestation.is_file(), (
        "an enforcer changed and the previous green run still speaks for the "
        "new checker")
