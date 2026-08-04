"""The manifest CONTRACT/ENFORCER split, promoted from its frozen contract.

Shipped 2026-08-03 as v3 Change 2 and retired here: a contract left in
`tests/contract/` binds every later slice to this one's behaviour. Every ID is
kept, and two things changed in the move.

`_ROOT` is re-anchored one level up, and every `lock_state` call now passes the
anchor axis EXPLICITLY. The one-argument form asks the content question alone
and returns the greener reading, blind to a missing or disagreeing anchor;
`/scrutinize` raised that as M3 on 2026-08-03 and it is right. The defaults stay
in the signature because tightening them is an enforcement-surface change the
depth gate prices at a full slice, but after this promotion NOTHING in the
repository uses them, and
`test_every_production_call_of_lock_state_passes_the_anchor_axis` is what keeps
it that way.

WHAT THIS PINS, each of it by test rather than by promise:

- The contract root must NOT move when only enforcer bytes move. That is the
  whole slice, and it is the first test.
- The contract root MUST still move when the CONTRACT moves. A split that buys
  its convenience by loosening the thing the standard exists for would be a
  regression dressed as a feature, so the old guarantee is re-pinned here rather
  than assumed to survive.
- Enforcer drift is never SILENT. Cheaper than a retake is the goal; invisible is
  not. Drift reddens the lock and names the files.
- A re-pin is a RECORDED act, carrying what changed, and it clears the
  attestation: the enforcer set includes the test runner and conftest, so a run
  taken before the change was produced by a different checker.
- A v5 manifest is refused BY NAME. The root-hash payload changes here, and a new
  hash shape without a recipe bump reads as LOSS OF LOCK on a tree where nothing
  moved -- the module's own stated reason for every previous bump.

Measured over the 39 `anchor_replaced` records in the ledger on 2026-08-03:
21 were the enforcer bytes moving, and not one was a contract that changed.
That was the largest class of retake in the standard's whole history.
"""

from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
STAMP = "2026-01-01T00:00:00+00:00"


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    """A working tree with a contract file and an enforcer file, kept apart."""
    root = tmp_path / "tree"
    (root / "tests" / "contract").mkdir(parents=True)
    (root / "scripts").mkdir(parents=True)
    (root / "tests" / "contract" / "test_c.py").write_text(
        "def test_c():\n    assert True\n", encoding="utf-8")
    (root / "scripts" / "run-tests.py").write_text(
        "# the enforcer\n", encoding="utf-8")
    return root


@pytest.fixture
def anchor(tmp_path: Path) -> Path:
    path = tmp_path / "outside" / "gate-artifact.md"
    path.parent.mkdir(parents=True)
    path.write_text("# gate artifact\n", encoding="utf-8")
    return path


def _lock(report: dict) -> str:
    """`lock_state` with an anchor that AGREES, so only the content can redden it.

    Spelled here rather than at four call sites, and never left to the default.
    The two-axis default is the greener reading; passing an agreeing anchor makes
    every assertion below strictly stronger, because the lock has no second
    reason to be red.
    """
    from scripts.utils.canopus_freeze import ANCHOR_RECORDED, lock_state

    return lock_state(report, ANCHOR_RECORDED, report["recomputed_root"])


def _manifest(tree: Path, anchor: Path):
    from scripts.utils.canopus_freeze import build_manifest

    return build_manifest(
        [tree / "tests" / "contract"], tree,
        label="s", frozen_at=STAMP, anchor=anchor,
        content_only=[tree / "scripts" / "run-tests.py"])


# ============================================================
# SC-1 -- the enforcer leaves the contract root
# ============================================================

def test_editing_an_enforcer_byte_does_not_move_the_contract_root(tree, anchor):
    """SC-1. WHEN only enforcer bytes change, THE SYSTEM SHALL leave the contract
    root unchanged, so the approval a human committed still matches.

    This is the 21 records. Asserted on the ROOT itself rather than on any command
    that reads it, because every refusal downstream is a comparison against this
    one value.
    """
    before = _manifest(tree, anchor)["root"]
    (tree / "scripts" / "run-tests.py").write_text(
        "# the enforcer, edited\n", encoding="utf-8")
    after = _manifest(tree, anchor)["root"]

    assert before == after, (
        "an enforcer edit still moves the contract root, so the committed "
        "approval still stops matching and the retake ceremony is unchanged")


def test_the_enforcer_pin_DOES_move_when_an_enforcer_byte_changes(tree, anchor):
    """SC-1b. The other half, and without it SC-1 is satisfied by not recording
    the enforcer at all. Two claims, two hashes; the second one still moves."""
    from scripts.utils.canopus_freeze import enforcer_pin

    before = enforcer_pin(_manifest(tree, anchor))
    (tree / "scripts" / "run-tests.py").write_text(
        "# the enforcer, edited\n", encoding="utf-8")
    after = enforcer_pin(_manifest(tree, anchor))

    assert before and after
    assert before != after, "the enforcer pin is blind to an enforcer edit"


def test_the_enforcer_bytes_are_not_inside_the_contract_root_payload(tree, anchor):
    """SC-1c. Asserted structurally as well as behaviourally. SC-1 compares two
    hashes and passes if the enforcer digest happens to be absent for any reason;
    this names the payload, so an implementation that keeps the bytes in and
    merely stops noticing them cannot pass both."""
    from scripts.utils.canopus_freeze import CONTENT_KEY, root_hash_payload

    manifest = _manifest(tree, anchor)
    payload = root_hash_payload(manifest)

    assert CONTENT_KEY not in payload
    assert "scripts/run-tests.py" not in payload.get("files", {}), (
        "the enforcer file is still carried in the contract's files map")
    assert manifest[CONTENT_KEY]["scripts/run-tests.py"], (
        "the enforcer is not recorded anywhere, which is not a split but a hole")


# ============================================================
# SC-2 -- drift is never silent
# ============================================================

def test_an_unpinned_enforcer_edit_reddens_the_lock(tree, anchor):
    """SC-2. WHEN enforcer bytes have changed and no re-pin has been taken, THE
    SYSTEM SHALL NOT report the lock as held.

    Cheaper than a retake is the goal. Invisible is not: the enforcer set is the
    code that decides whether the contract moved, so a silently edited enforcer
    is a lock reporting on itself.
    """
    from scripts.utils.canopus_freeze import LOCK_HELD, verify_manifest

    manifest = _manifest(tree, anchor)
    (tree / "scripts" / "run-tests.py").write_text(
        "# weakened\n", encoding="utf-8")
    report = verify_manifest(manifest, tree)

    assert _lock(report) != LOCK_HELD
    assert report["enforcer_moved"] == ["scripts/run-tests.py"], (
        "the report does not name which enforcer file moved")


def test_the_contract_half_of_the_report_stays_green_under_enforcer_drift(tree, anchor):
    """SC-2b. The two claims are reported separately or the split buys nothing:
    an operator who cannot tell "the enforcer moved" from "your contract moved"
    is back to one undifferentiated red."""
    from scripts.utils.canopus_freeze import verify_manifest

    manifest = _manifest(tree, anchor)
    (tree / "scripts" / "run-tests.py").write_text("# weakened\n", encoding="utf-8")
    report = verify_manifest(manifest, tree)

    assert report["changed"] == []
    assert report["removed"] == []
    assert report["enforcer_moved"]


def test_a_clean_tree_reports_no_enforcer_drift(tree, anchor):
    """SC-2c. The negative control for SC-2. A drift flag that is always set is a
    flag nobody reads, and it would make every held lock red."""
    from scripts.utils.canopus_freeze import LOCK_HELD, verify_manifest

    report = verify_manifest(_manifest(tree, anchor), tree)

    assert report["enforcer_moved"] == []
    assert _lock(report) == LOCK_HELD


def test_a_deleted_enforcer_file_is_drift_and_not_an_exception(tree, anchor):
    """SC-2d. Deleting the checker must not be quieter than editing it. The
    natural implementation hashes what it finds and reports nothing for a file
    that is gone."""
    from scripts.utils.canopus_freeze import verify_manifest

    manifest = _manifest(tree, anchor)
    (tree / "scripts" / "run-tests.py").unlink()
    report = verify_manifest(manifest, tree)

    assert "scripts/run-tests.py" in report["enforcer_moved"]


# ============================================================
# SC-3 -- a re-pin is a recorded act
# ============================================================

def test_repin_records_the_new_pin_and_what_changed(tree, anchor):
    """SC-3. WHEN a re-pin is taken, THE SYSTEM SHALL record the new pin and
    append a ledger event carrying the PREVIOUS pin and the changed files.

    The previous pin is the part that matters. A record of where the enforcer is
    now says nothing; a record of where it was and where it went is what makes a
    weakening reconstructable after the fact.
    """
    from scripts.utils.canopus_freeze import (
        enforcer_pin,
        read_freeze,
        repin_enforcer,
        write_freeze,
    )

    manifest = _manifest(tree, anchor)
    write_freeze(tree, manifest)
    was = enforcer_pin(manifest)
    (tree / "scripts" / "run-tests.py").write_text("# edited\n", encoding="utf-8")

    event = repin_enforcer(tree, reason="the fix belonged inside the enforcer")

    assert event["previous_pin"] == was
    assert event["pin"] != was
    assert event["changed"] == ["scripts/run-tests.py"]
    assert enforcer_pin(read_freeze(tree)) == event["pin"], (
        "the freeze state still carries the old pin, so the lock stays red")


def test_repin_clears_the_attestation(tree, anchor):
    """SC-3b. WHEN a re-pin is taken, THE SYSTEM SHALL clear any attestation.

    The enforcer set holds the test runner, the interpreter chooser and conftest.
    A green run recorded before the change was produced by a DIFFERENT checker,
    so keeping it would let an edited enforcer inherit the previous run's word.
    """
    from scripts.utils.canopus_freeze import (
        attestation_state_path,
        repin_enforcer,
        write_freeze,
    )

    write_freeze(tree, _manifest(tree, anchor))
    attestation_state_path(tree).write_text('{"attested": true}', encoding="utf-8")
    (tree / "scripts" / "run-tests.py").write_text("# edited\n", encoding="utf-8")

    repin_enforcer(tree, reason="why")

    assert not attestation_state_path(tree).exists(), (
        "an attestation produced by the previous checker survived the re-pin")


def test_repin_refuses_without_a_reason(tree, anchor):
    """SC-3c. An unexplained re-pin is indistinguishable from a re-baseline, which
    is the sentence `approve --replace` already uses for the same act one layer
    up. A recorded pin with no account of why is a log entry, not evidence."""
    from scripts.utils.canopus_freeze import FreezeError, repin_enforcer, write_freeze

    write_freeze(tree, _manifest(tree, anchor))
    with pytest.raises(FreezeError):
        repin_enforcer(tree, reason="")


def test_repin_refuses_when_no_freeze_is_held(tree, anchor):
    """SC-3d. Re-pinning nothing must not silently succeed and write a pin that
    no lock will ever check."""
    from scripts.utils.canopus_freeze import FreezeError, repin_enforcer

    with pytest.raises(FreezeError):
        repin_enforcer(tree, reason="why")


def test_the_repin_reaches_the_command_an_operator_types(tree, anchor):
    """SC-3e. Wiring, end to end, because the four tests above pin a FUNCTION.

    The friction-counters slice and the yield-axes slice each shipped a correct
    function whose CLI path no contract test reached, and in both cases the gap
    was found by mutation rather than by the contract. Named here at step 4
    instead.
    """
    import subprocess
    import sys

    from scripts.utils.canopus_freeze import write_freeze

    write_freeze(tree, _manifest(tree, anchor))
    (tree / "scripts" / "run-tests.py").write_text("# edited\n", encoding="utf-8")
    done = subprocess.run(
        [sys.executable, str(_ROOT / "scripts" / "canopus.py"), "repin", "--reason", "why"],
        cwd=str(tree), capture_output=True, text=True, check=False)

    assert done.returncode == 0, done.stderr
    assert "repin" in (done.stdout + done.stderr).lower()


# ============================================================
# SC-4 -- the old guarantee survives the split
# ============================================================

def test_editing_the_CONTRACT_still_moves_the_contract_root(tree, anchor):
    """SC-4. WHEN the contract moves, THE SYSTEM SHALL still move the root.

    The regression this slice could most easily introduce, and the one that would
    be least visible: every test above would pass over an implementation that
    dropped BOTH maps out of the hash.
    """
    before = _manifest(tree, anchor)["root"]
    (tree / "tests" / "contract" / "test_c.py").write_text(
        "def test_c():\n    assert False\n", encoding="utf-8")

    assert _manifest(tree, anchor)["root"] != before


def test_a_contract_edit_still_reddens_the_lock(tree, anchor):
    """SC-4b. The same guarantee at the report layer, where the gate reads it."""
    from scripts.utils.canopus_freeze import LOCK_HELD, verify_manifest

    manifest = _manifest(tree, anchor)
    (tree / "tests" / "contract" / "test_c.py").write_text(
        "def test_c():\n    assert False\n", encoding="utf-8")
    report = verify_manifest(manifest, tree)

    assert _lock(report) != LOCK_HELD
    assert report["changed"] == ["tests/contract/test_c.py"]


def test_the_baseline_and_the_binding_stay_inside_the_contract_root(tree, anchor):
    """SC-4c. Three fields sit in the root hash for a stated reason each: the
    per-file baseline (editable down to 1 with no indicator moving), the anchor
    binding (edit it and win the working-copy fallback permanently), and the
    plugin set (append the plugin that skips the contract). A split that carried
    any of them out with the enforcer would reopen a hole that was closed on
    purpose."""
    from scripts.utils.canopus_freeze import root_hash_payload

    payload = root_hash_payload(_manifest(tree, anchor))

    for key in ("recipe", "anchor", "anchor_repo", "baseline", "plugins"):
        assert key in payload, f"{key} left the contract root"


def test_a_repin_cannot_be_used_to_move_the_contract(tree, anchor):
    """SC-4d. The new cheap path must not become a way around the expensive one.

    A re-pin recomputes the ENFORCER map only. If it also refreshed the contract
    digests, a builder would edit a frozen test, run `repin`, and hold a green
    lock over a contract nobody re-approved -- which is the one thing the whole
    standard exists to prevent, delivered by its own convenience feature.
    """
    from scripts.utils.canopus_freeze import (
        LOCK_HELD,
        read_freeze,
        repin_enforcer,
        verify_manifest,
        write_freeze,
    )

    write_freeze(tree, _manifest(tree, anchor))
    (tree / "tests" / "contract" / "test_c.py").write_text(
        "def test_c():\n    assert False\n", encoding="utf-8")

    repin_enforcer(tree, reason="trying to launder a contract edit")

    report = verify_manifest(read_freeze(tree), tree)
    assert _lock(report) != LOCK_HELD
    assert report["changed"] == ["tests/contract/test_c.py"]


# ============================================================
# SC-5 -- the recipe bump is what makes the change loud
# ============================================================

def test_the_recipe_is_bumped_so_an_old_manifest_is_refused_by_name(tree, anchor):
    """SC-5. WHEN a manifest carries the previous recipe, THE SYSTEM SHALL refuse
    it by name rather than report a silent loss of lock.

    The module's own stated reason for v2 and for v5: a new hash shape without a
    bump produces LOSS OF LOCK on a tree where nothing moved, which sends an
    operator hunting a file that never changed.
    """
    import json

    from scripts.utils.canopus_freeze import (
        FreezeCorrupt,
        RECIPE,
        freeze_state_path,
        read_freeze,
        write_freeze,
    )

    # Advanced to v7 by the `enforcer-set-bound` slice, which put the enforcer
    # NAMES back into the payload this one took them out of. The literal is
    # deliberate and is meant to be edited by hand at every bump: a test that
    # read RECIPE from the module would agree with any value the module happened
    # to hold, which is the one thing this test exists not to do.
    assert RECIPE == "canopus-freeze-v7", (
        f"the root-hash payload changed and the recipe is still {RECIPE}")

    write_freeze(tree, _manifest(tree, anchor))
    path = freeze_state_path(tree)
    stale = json.loads(path.read_text(encoding="utf-8"))
    stale["recipe"] = "canopus-freeze-v6"
    path.write_text(json.dumps(stale), encoding="utf-8")

    with pytest.raises(FreezeCorrupt) as caught:
        read_freeze(tree)
    assert "canopus-freeze-v6" in str(caught.value)


# ============================================================
# SC-6 -- the sign-off page counts what the cheap path was used for
# ============================================================

def test_the_evidence_page_counts_the_repins(tree, anchor):
    """SC-6. WHEN the evidence page is rendered, THE SYSTEM SHALL report how many
    re-pins the slice took.

    Without this the change trades a visible cost for an invisible one: 21
    retakes used to be loud precisely because they were expensive. A cheap act
    that nobody counts is how a weakened enforcer stops being noticeable.
    """
    from scripts.utils.canopus_friction import count_friction, render_friction

    ledger = [
        {"event": "freeze", "label": "s", "ts": STAMP},
        {"event": "repin", "label": "s", "ts": STAMP,
         "reason": "the fix belonged inside the enforcer"},
        {"event": "repin", "label": "s", "ts": STAMP, "reason": "again"},
    ]
    counts = count_friction(ledger, "s")

    assert counts["repins"] == 2
    assert "repin" in render_friction(counts).lower()


def test_the_repin_reaches_the_command_an_operator_types_only_when_committed(
        tree, anchor):
    """SC-7. WHEN enforcer bytes are UNCOMMITTED, THE SYSTEM SHALL refuse the
    re-pin and say which files must be committed first.

    This criterion was added after the operator asked what "trading security for
    speed" meant in C1, and the answer turned out to be that C1 overstated the
    loss. The thing the old design was said to protect -- an enforcer change
    passing through a human-committed record -- was never operator-gated: all 39
    retakes were run by the assistant, `git commit` included. What the old design
    really bought was that the new enforcer state landed in git at all.

    So the re-pin keeps that and improves on it. A commit carries a READABLE DIFF
    with an author and a timestamp, in the public engine repository; the old
    artifact line carried a hash that says only that something moved. The
    ceremony still falls from six commands to two, and `.canopus/` stops being
    the sole record of a change to the code that does the checking.
    """
    import subprocess
    import sys

    from scripts.utils.canopus_freeze import write_freeze

    subprocess.run(["git", "init", "-q"], cwd=str(tree), check=True)
    subprocess.run(["git", "add", "-A"], cwd=str(tree), check=True)
    subprocess.run(["git", "-c", "user.email=t@e", "-c", "user.name=t",
                    "commit", "-qm", "base"], cwd=str(tree), check=True)
    write_freeze(tree, _manifest(tree, anchor))
    (tree / "scripts" / "run-tests.py").write_text("# edited\n", encoding="utf-8")

    cli = str(_ROOT / "scripts" / "canopus.py")
    refused = subprocess.run([sys.executable, cli, "repin", "--reason", "why"],
                             cwd=str(tree), capture_output=True, text=True,
                             check=False)
    assert refused.returncode != 0, "a re-pin over uncommitted enforcer bytes was accepted"
    assert "scripts/run-tests.py" in refused.stderr, (
        "the refusal does not name the file that must be committed")

    subprocess.run(["git", "add", "-A"], cwd=str(tree), check=True)
    subprocess.run(["git", "-c", "user.email=t@e", "-c", "user.name=t",
                    "commit", "-qm", "the enforcer change"], cwd=str(tree), check=True)
    accepted = subprocess.run([sys.executable, cli, "repin", "--reason", "why"],
                              cwd=str(tree), capture_output=True, text=True,
                              check=False)
    assert accepted.returncode == 0, accepted.stderr


def test_the_repin_event_records_the_commit_that_carries_the_change(tree, anchor):
    """SC-7b. The ledger line points AT the commit, so the diff is one command
    away. A record that says a re-pin happened, without naming where the change
    can be read, sends the reader hunting through history for it."""
    from scripts.utils.canopus_freeze import repin_enforcer, write_freeze

    write_freeze(tree, _manifest(tree, anchor))
    (tree / "scripts" / "run-tests.py").write_text("# edited\n", encoding="utf-8")

    event = repin_enforcer(tree, reason="why", git_sha="a" * 40)

    assert event["git_sha"] == "a" * 40


def test_the_repin_count_is_per_label_like_every_other_friction_count(tree, anchor):
    """SC-6b. `count_friction` answers for ONE slice. A re-pin counter that
    summed the whole ledger would report a number that grows forever and means
    nothing on any single page."""
    from scripts.utils.canopus_friction import count_friction

    ledger = [
        {"event": "repin", "label": "mine", "ts": STAMP, "reason": "a"},
        {"event": "repin", "label": "someone-elses", "ts": STAMP, "reason": "b"},
    ]

    assert count_friction(ledger, "mine")["repins"] == 1


def test_every_production_call_of_lock_state_passes_the_anchor_axis():
    """M3 from the `/scrutinize` pass of 2026-08-03, closed where it can be.

    `lock_state(report)` answers the CONTENT question alone and returns the
    greener reading: `LOCK_HELD` with no view of a missing or a disagreeing
    anchor. Its sibling `attestation_state` refuses exactly that shape nine
    functions away — "required rather than defaulted. A default would let a
    caller that forgot it skip the comparison and print green."

    The right fix is to make the parameters required again. That is an
    enforcement-surface edit to `canopus_freeze.py`, which `slice-depth.py`
    prices at a full slice with a held freeze, and taking that price under some
    LATER slice's freeze would be gaming the gate rather than paying it. So the
    signature keeps its defaults and this guard keeps them unused: every call in
    `scripts/` passes all three arguments, and a future caller that forgets the
    anchor fails HERE, by name, instead of printing green.

    Walked with `ast` rather than grepped, so a call split across lines or
    nested inside an f-string is counted the same way the interpreter counts it.
    """
    import ast

    offenders = []
    for path in sorted((_ROOT / "scripts").rglob("*.py")):
        tree_ast = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree_ast):
            if not isinstance(node, ast.Call):
                continue
            name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            if name != "lock_state":
                continue
            if len(node.args) + len(node.keywords) < 3:
                offenders.append(f"{path.relative_to(_ROOT)}:{node.lineno}")

    assert not offenders, (
        f"these calls take the two-axis default, which cannot see a missing or "
        f"disagreeing anchor and reports the greener state: {offenders}")
