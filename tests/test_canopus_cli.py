"""Tests for the Canopus CLI (wire 1)."""
import json
import os
import re
import shlex
import subprocess
from pathlib import Path

import pytest

import scripts.canopus as canopus
from scripts.canopus import main
from scripts.utils.canopus_freeze import read_ledger
from scripts.utils.canopus_git import NO_REPO


def _make_tree(root: Path) -> Path:
    """A synthetic working tree that carries a test gate.

    The gate script is what makes a freeze mean anything, so the CLI refuses a
    --root without one. Every synthetic root here therefore ships a stub.
    """
    (root / "tests").mkdir(parents=True)
    (root / "tests" / "test_alpha.py").write_text("def test_a():\n    assert True\n")
    (root / "scripts").mkdir(parents=True)
    (root / "scripts" / "run-tests.py").write_text("# stub test gate\n")
    return root


@pytest.fixture
def tree(tmp_path: Path, monkeypatch) -> Path:
    root = _make_tree(tmp_path / "tree")
    monkeypatch.chdir(root)
    return root


@pytest.fixture
def anchor(tmp_path: Path) -> Path:
    """A scratch gate artifact, carrying the criteria section A12 now demands.

    `approve --contract` and `freeze --contract` refuse an artifact stating no
    success criteria at all, because a contract checked against zero criteria
    passes by having nothing to satisfy. Every scratch artifact here therefore
    states one, and `_write_contract` claims it.
    """
    path = tmp_path / "outside" / "gate-artifact.md"
    path.parent.mkdir(parents=True)
    path.write_text("# gate artifact\n\n"
                    "## Phase 1 — Success criteria\n\n"
                    "- **SC-1** WHEN a scratch slice runs, THE SYSTEM SHALL "
                    "behave as the test says.\n")
    return path


def _run(argv, tree):
    # --root is a top-level option, so it must precede the subcommand.
    return main(["--root", str(tree), *argv])


def _root_of(tree: Path) -> str:
    return json.loads((tree / ".canopus" / "freeze.json").read_text())["root"]


def _freeze(tree, anchor):
    return _run(["freeze", "tests/test_alpha.py", "--label", "demo",
                 "--anchor", str(anchor)], tree)


def test_freeze_prints_the_root_hash(tree, anchor, capsys):
    assert _freeze(tree, anchor) == 0
    out = capsys.readouterr().out
    # The CANOPUS headline, not "the first line". Advisories printed while the
    # candidate manifest is built (vacuity, contract, plugins) precede it, and
    # this test only passed on line one because a plain freeze had none of them
    # until the no-plugin-baseline notice was hoisted out of the contract block.
    headline = [line for line in out.splitlines() if "CANOPUS" in line]
    assert headline and "root " in headline[0]


def test_freeze_requires_an_anchor(tree, capsys):
    """An anchorless freeze is the one route to a PASSING gate that never leaves
    this clone: release, edit the contract, re-freeze, amber, exit 0. With an
    anchor the same sequence fails, because the artifact still records the
    previously approved hash. So the CLI refuses to take one."""
    with pytest.raises(SystemExit) as excinfo:
        _run(["freeze", "tests/test_alpha.py", "--label", "demo"], tree)
    assert excinfo.value.code != 0
    assert "--anchor" in capsys.readouterr().err
    assert not (tree / ".canopus" / "freeze.json").exists()


def test_freeze_refuses_when_one_is_already_active(tree, anchor, capsys):
    _freeze(tree, anchor)
    assert _freeze(tree, anchor) == 1
    assert "already active" in capsys.readouterr().err


def test_freeze_refuses_a_missing_path(tree, anchor, capsys):
    assert _run(["freeze", "tests/nope.py", "--label", "demo",
                 "--anchor", str(anchor)], tree) == 1
    assert "does not exist" in capsys.readouterr().err


def test_freeze_refuses_an_anchor_inside_the_tree(tree, capsys):
    inside = tree / "gate.md"
    inside.write_text("# nope\n")
    assert _freeze(tree, inside) == 1
    assert "inside the working tree" in capsys.readouterr().err


def test_verify_of_an_anchorless_manifest_is_unconfirmed(tree, capsys):
    """The CLI cannot take an anchorless freeze any more, but a manifest written
    by the library directly (or by an older CLI) still has to read cleanly."""
    from scripts.utils.canopus_freeze import build_manifest, write_freeze

    write_freeze(tree, build_manifest([tree / "tests" / "test_alpha.py"], tree,
                                      label="demo", frozen_at="2026-01-01T00:00:00+00:00"))
    assert _run(["verify"], tree) == 0
    out = capsys.readouterr().out
    assert "LOCK UNCONFIRMED" in out
    assert "no anchor was recorded at freeze time" in out


def test_verify_with_an_unrecorded_anchor_is_unconfirmed(tree, anchor, capsys):
    _freeze(tree, anchor)
    anchor.write_text("# gate artifact\n")   # the line removed by hand
    assert _run(["verify"], tree) == 0
    assert "LOCK UNCONFIRMED" in capsys.readouterr().out


def test_verify_with_an_agreeing_anchor_holds(tree, anchor, capsys):
    _freeze(tree, anchor)
    anchor.write_text(f"# gate\n\ncanopus-anchor: {_root_of(tree)}\n")
    assert _run(["verify"], tree) == 0
    assert "LOCK HELD" in capsys.readouterr().out


def test_verify_with_a_disagreeing_anchor_is_loss_of_lock(tree, anchor, capsys):
    _freeze(tree, anchor)
    anchor.write_text("# gate\n\ncanopus-anchor: " + "0" * 64 + "\n")
    assert _run(["verify"], tree) == 1
    assert "LOSS OF LOCK" in capsys.readouterr().out


def test_verify_with_a_vanished_anchor_is_loss_of_lock(tree, anchor, capsys):
    _freeze(tree, anchor)
    anchor.unlink()
    assert _run(["verify"], tree) == 1
    assert "LOSS OF LOCK" in capsys.readouterr().out


def test_verify_reports_the_changed_file(tree, anchor, capsys):
    _freeze(tree, anchor)
    (tree / "tests" / "test_alpha.py").write_text("def test_a():\n    assert False\n")
    assert _run(["verify"], tree) == 1
    out = capsys.readouterr().out
    assert "LOSS OF LOCK" in out
    assert "tests/test_alpha.py" in out


def test_re_baselining_a_contract_is_refused_at_freeze_time(tree, anchor, capsys):
    """The hole a required --anchor closes: release, edit, re-freeze.

    In wire 1 the re-freeze succeeded and `verify` caught it afterwards. Wire 2
    refused it on the WORKING copy of the artifact, which refused the sequence
    everywhere, including outside any repository. This slice MOVES that
    guarantee rather than dropping it: the refusal now needs a COMMITTED
    approval to disagree with, so the sequence is built on a scratch repository
    and the approval is committed before the first freeze. Where nobody
    committed an approval, or the artifact is a file in a folder, the same
    sequence proceeds and reports amber instead.

    Deliberately widening a frozen set is the `approve --replace --reason` path,
    which carries a reason and a ledger entry.
    """
    gate = _init_gate_repo(anchor)
    assert _run(["approve", "tests/test_alpha.py", "--label", "demo",
                 "--anchor", str(anchor)], tree) == 0
    approved = _recorded(anchor)
    _git(gate, "add", anchor.name)
    _git(gate, "commit", "-q", "-m", "the approval")

    assert _freeze(tree, anchor) == 0
    assert _root_of(tree) == approved
    assert _run(["release", "--window", "--reason", "re-baseline"], tree) == 0
    capsys.readouterr()

    (tree / "tests" / "test_alpha.py").write_text("def test_a():\n    assert False\n")
    assert _freeze(tree, anchor) == 1
    assert approved in capsys.readouterr().err
    assert not (tree / ".canopus" / "freeze.json").exists()


def test_verify_without_an_active_freeze_fails(tree, capsys):
    assert _run(["verify"], tree) == 1
    assert "no active freeze" in capsys.readouterr().err


def test_verify_on_a_corrupt_manifest_fails(tree, anchor, capsys):
    _freeze(tree, anchor)
    (tree / ".canopus" / "freeze.json").write_text("{ not json")
    assert _run(["verify"], tree) == 1
    assert "unreadable" in capsys.readouterr().err


def test_an_unreadable_member_fails_the_command_without_a_traceback(
    tree, anchor, monkeypatch, capsys
):
    """The layer billed as the guarantee must not present a raw stack trace.

    The gate already wraps verify_manifest so an unreadable member (permissions,
    a vanished mount) fails closed rather than tracebacking; the CLI makes the
    same calls and now catches the same thing.
    """
    _freeze(tree, anchor)
    capsys.readouterr()

    def boom(*_args, **_kwargs):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(canopus, "verify_manifest", boom)
    assert _run(["verify"], tree) == 1
    assert "could not be read" in capsys.readouterr().err


def test_release_records_the_event_and_clears_the_manifest(tree, anchor):
    """The `pack` in the middle is the ship-evidence precondition, not scenery.

    From 2026-08-03 a `--ship` refuses unless the ledger carries an evidence
    render for this freeze, so a test that ships must render one. It stays here
    rather than moving to the evidence tests because what it pins is unchanged:
    a release records its event and clears the manifest.
    """
    _freeze(tree, anchor)
    assert _run(["pack"], tree) == 0
    assert _run(["release", "--ship", "--reason", "wire 1 shipped"], tree) == 0
    assert not (tree / ".canopus" / "freeze.json").exists()
    events = [
        json.loads(line)["event"]
        for line in (tree / ".canopus" / "history.jsonl").read_text().strip().splitlines()
    ]
    assert events == ["freeze", "pack", "release"]


def test_release_without_an_active_freeze_fails(tree, capsys):
    assert _run(["release", "--ship"], tree) == 1
    assert "no active freeze" in capsys.readouterr().err


def test_force_release_clears_a_corrupt_manifest_and_logs_it(tree, anchor):
    _freeze(tree, anchor)
    (tree / ".canopus" / "freeze.json").write_text("{ not json")
    assert _run(["release", "--force", "--window", "--reason",
                 "encoding false alarm"], tree) == 0
    assert not (tree / ".canopus" / "freeze.json").exists()
    events = [
        json.loads(line)["event"]
        for line in (tree / ".canopus" / "history.jsonl").read_text().strip().splitlines()
    ]
    assert events == ["freeze", "force_release"]


def test_status_reports_no_freeze(tree, capsys):
    assert _run(["status"], tree) == 0
    assert "no active freeze" in capsys.readouterr().out


def test_status_reports_an_active_freeze_and_its_anchor(tree, anchor, capsys):
    _freeze(tree, anchor)
    assert _run(["status"], tree) == 0
    out = capsys.readouterr().out
    assert "demo" in out
    assert "1 file" in out
    # The manifest stores the RESOLVED anchor path. Comparing the raw fixture
    # path breaks wherever the temp root is a symlink (macOS /var -> /private/var).
    assert str(anchor.resolve()) in out


def test_status_says_the_root_guard_watches_directories_too(tree, anchor, capsys):
    """The filter line is the only place a guard's scope is printed.

    It was added because `dir tests/` alone over-states the guard. Printing
    `watching *.py` for the tree root under-states it in the same way, from the
    wire 2.3 change that put importable subdirectories into the root
    composition: an operator reads a narrower guard than the one that will
    redden on them.
    """
    _freeze(tree, anchor)
    assert _run(["status"], tree) == 0
    out = capsys.readouterr().out

    assert "dir   ./  (members, watching *.py + importable directories)" in out
    # The ancestor guards are unchanged and must NOT gain the phrase: they watch
    # conftest.py and no directory at all.
    assert "watching conftest.py + importable" not in out


def test_status_reports_the_lock_state_not_just_the_stored_root(tree, anchor, capsys):
    """`status` on a MOVED contract must not look like `status` on an intact one.

    It already pays for a full verify_manifest; an earlier revision threw the
    answer away and printed the manifest's STORED root, so the two outputs were
    byte-identical on the lock axis. Telling an operator the lock is on while it
    is broken is the one failure the whole tool exists to prevent.
    """
    _freeze(tree, anchor)
    anchor.write_text(f"canopus-anchor: {_root_of(tree)}\n")

    assert _run(["status"], tree) == 0
    assert canopus.LOCK_HELD in capsys.readouterr().out

    (tree / "tests" / "test_alpha.py").write_text("def test_a():\n    assert False\n")

    # Reporting only: status describes state, verify is the command that fails.
    assert _run(["status"], tree) == 0
    out = capsys.readouterr().out
    assert canopus.LOSS_OF_LOCK in out
    assert "verify" in out
    assert _run(["verify"], tree) == 1


def test_status_survives_an_attestation_with_a_non_numeric_counter(tree, anchor, capsys):
    """A damaged record must not turn a report into a TypeError traceback."""
    _freeze(tree, anchor)
    attest = tree / ".canopus" / "attest.json"
    attest.write_text(json.dumps({
        "recipe": "canopus-attest-v1",
        "root": _root_of(tree),
        "attested": True,
        "reasons": [],
        "exit_status": 0,
        "attested_at": "2026-07-25T10:42:11+00:00",
        "frozen_tests": {"tests/test_alpha.py": {"passed": "3", "skipped": 0}},
    }))

    assert _run(["status"], tree) == 0
    assert canopus.NOT_ATTESTED in capsys.readouterr().out


def test_freeze_accepts_paths_relative_to_root_from_any_cwd(tmp_path, monkeypatch, anchor):
    """--root exists so the frozen tree need not be the cwd."""
    root = _make_tree(tmp_path / "elsewhere")
    monkeypatch.chdir(tmp_path)
    assert main(["--root", str(root), "freeze", "tests/test_alpha.py",
                 "--label", "demo", "--anchor", str(anchor)]) == 0
    assert (root / ".canopus" / "freeze.json").exists()


def test_root_defaults_to_the_engine_root_not_the_shell_cwd():
    """A freeze taken from a subdirectory used to print a root hash, exit 0, and
    write its state where nothing ever looks — the operator told the lock was on
    while it was inert. The default is the script's own repository root."""
    parser = canopus.build_parser()
    args = parser.parse_args(["status"])
    assert Path(args.root) == canopus.ENGINE_ROOT
    assert (canopus.ENGINE_ROOT / "scripts" / "run-tests.py").is_file()


def test_a_root_without_a_test_gate_is_refused(tmp_path, anchor, capsys):
    """A tree with no scripts/run-tests.py has nowhere for the freeze to be
    checked, so freezing it would be inert. Refuse rather than pretend."""
    gateless = tmp_path / "gateless"
    (gateless / "tests").mkdir(parents=True)
    (gateless / "tests" / "test_alpha.py").write_text("def test_a():\n    assert True\n")
    result = main(["--root", str(gateless), "freeze", "tests/test_alpha.py",
                   "--label", "demo", "--anchor", str(anchor)])
    assert result == 1
    assert "no scripts/run-tests.py" in capsys.readouterr().err
    assert not (gateless / ".canopus").exists()


def test_a_root_without_a_test_gate_is_refused_for_every_subcommand(tmp_path, capsys):
    gateless = tmp_path / "gateless"
    gateless.mkdir()
    for argv in (["status"], ["verify"],
                 ["release", "--force", "--window", "--reason", "x"]):
        assert main(["--root", str(gateless), *argv]) == 1
    assert capsys.readouterr().err.count("no scripts/run-tests.py") == 3


def test_freeze_resolves_a_relative_anchor_against_root_not_cwd(tmp_path, monkeypatch):
    """A relative --anchor is anchored to --root, exactly like the positional paths."""
    root = _make_tree(tmp_path / "root-tree")

    real_anchor = tmp_path / "notes" / "gate-artifact.md"
    real_anchor.parent.mkdir(parents=True)
    real_anchor.write_text("# gate artifact\n")

    # Neither root itself nor its parent -- an unrelated cwd.
    cwd = tmp_path / "unrelated" / "deep"
    cwd.mkdir(parents=True)
    monkeypatch.chdir(cwd)

    result = main(["--root", str(root), "freeze", "tests/test_alpha.py",
                    "--label", "demo", "--anchor", "../notes/gate-artifact.md"])
    assert result == 0
    manifest = json.loads((root / ".canopus" / "freeze.json").read_text())
    assert manifest["anchor"] == str(real_anchor.resolve())


def test_freeze_does_not_silently_anchor_to_a_decoy_under_the_cwd(tmp_path, monkeypatch):
    """The same relative --anchor string, resolved against a spurious cwd, points at a
    decoy file that happens to exist there. The fix must not anchor to it."""
    root = _make_tree(tmp_path / "root-tree")

    # No real anchor exists under tmp_path/notes/ -- only a same-named decoy
    # under the cwd's own parent, which the buggy cwd-relative resolution
    # would have silently picked up.
    cwd = tmp_path / "unrelated" / "deep"
    cwd.mkdir(parents=True)
    decoy = tmp_path / "unrelated" / "notes" / "gate-artifact.md"
    decoy.parent.mkdir(parents=True)
    decoy.write_text("# decoy, never approved\n")
    monkeypatch.chdir(cwd)

    result = main(["--root", str(root), "freeze", "tests/test_alpha.py",
                    "--label", "demo", "--anchor", "../notes/gate-artifact.md"])
    assert result == 1
    assert not (root / ".canopus" / "freeze.json").exists()


def test_verify_anchor_override_inside_the_working_tree_is_refused(tree, anchor, capsys):
    """The verify override must be refused the same way a freeze-time anchor is."""
    _freeze(tree, anchor)
    inside = tree / "gate.md"
    inside.write_text("# nope\n")
    assert _run(["verify", "--anchor", str(inside)], tree) == 1
    assert "inside the working tree" in capsys.readouterr().err


def test_verify_anchor_override_that_does_not_exist_is_refused(tree, anchor, tmp_path, capsys):
    """A nonexistent override fails fast rather than sliding into the `missing`
    path and reporting LOSS OF LOCK.

    The distinction is deliberate and worth pinning: `missing` means an anchor
    that WAS recorded has vanished, which is a real red signal about the build.
    A typo in a --anchor argument is an operator mistake about a file that was
    never the anchor, and it earns a refusal that names the path, not a verdict
    about the contract.
    """
    _freeze(tree, anchor)
    assert _run(["verify", "--anchor", str(tmp_path / "outside" / "absent.md")], tree) == 1
    err = capsys.readouterr().err
    assert "does not exist or is not a file" in err
    assert "LOSS OF LOCK" not in err


# ============================================================
# The second indicator axis: attestation
# ============================================================

def _attest(tree, root_digest, *, qualified=True, deselected=0, passed=3, skipped=0):
    from scripts.utils import canopus_freeze as cf
    from scripts.utils.canopus_tree import tree_state

    # None on a `tree` with no git identity, which is every caller of this
    # helper except the two tests that give it one (see `_git_init_tree`): the
    # record then carries no usable tree state and refuses on that ground too,
    # same as an unrecorded process block already does. Passed for BOTH start
    # and finish, because nothing in this synthetic setup runs a build between
    # the two samples.
    current_tree = tree_state(tree)
    record = cf.build_attestation(
        root_digest=root_digest,
        frozen_tests={"tests/test_alpha.py": {
            "collected": passed + skipped, "passed": passed,
            "failed": 0 if qualified else 1, "skipped": skipped,
            "deselected": deselected,
        }},
        exit_status=0,
        attested_at="2026-07-25T10:42:11+00:00",
        # An honest interpreter, and the set the freeze recorded for it. A
        # record with no process block reads as damage from wire 2.3 onward, so
        # a helper about counters still has to describe one.
        process={"plugins": {"dist:xdist": "/venv/xdist/plugin.py"},
                 "intree_plugins": [], "other_plugins": [], "option_plugins": [],
                 "env_configured": [], "launcher": "bare", "workers": []},
        plugin_baseline=["dist:xdist"],
        tree_at_start=current_tree,
        tree_at_finish=current_tree,
    )
    cf.write_attestation(tree, record)
    return record


def _git_init_tree(tree: Path) -> None:
    """Give *tree* its own git identity, with `.canopus/` ignored like the real repo.

    `tree_state` is defined relative to git, and most of this file's `tree`
    fixture instances have no git identity at all -- deliberately, since they
    are not exercising the recorder. The two tests that need a genuine ATTESTED
    state through the CLI need `tree_state(tree)` to answer the same way at
    record time and at verify time, and the identity is what makes that
    possible. `.canopus/` is ignored for the same reason the real engine repo
    ignores it: without that, the freeze and attestation files this test writes
    would show up as newly dirty between the two reads and the record would
    perish on its own bookkeeping.
    """
    (tree / ".gitignore").write_text(".canopus/\n", encoding="utf-8")
    for argv in (["init", "-q", "-b", "main"],
                 ["config", "user.email", "builder@example.invalid"],
                 ["config", "user.name", "Builder"],
                 ["add", "-A"],
                 ["commit", "-q", "-m", "seed"]):
        _git(tree, *argv)


def test_verify_reports_attested_when_the_run_matches(tree, anchor, capsys):
    _git_init_tree(tree)
    _freeze(tree, anchor)
    root = _root_of(tree)
    anchor.write_text(f"# gate\n\ncanopus-anchor: {root}\n")
    _attest(tree, root)
    # The setup's own output is dropped, so the assertions below read `verify`
    # and nothing else. The freeze prints a NOT ATTESTED advisory of its own
    # (this tree has no plugin baseline), and a buffer holding both commands
    # would fail the absence assertion on the wrong command's words.
    capsys.readouterr()
    assert _run(["verify"], tree) == 0
    out = capsys.readouterr().out
    assert "LOCK HELD" in out
    assert "NOT ATTESTED" not in out
    assert "ATTESTED" in out
    assert "3 frozen tests passed" in out
    # The sign-off line said "+0 dirty" for the cleanest tree there is: the count
    # was right and the word was a lie, on the one line an operator reads to
    # approve. Found at step 11 of the yield-axes slice on that slice's own
    # evidence page. Both halves are asserted, because dropping the word while
    # keeping a zero would read as an omission rather than as a clean tree.
    assert "clean tree" in out
    assert "dirty" not in out

    # The CLI must compare the record against the REAL current tree, not
    # against the tree the record itself carries -- that comparison would
    # always agree with itself and print ATTESTED forever. An edit to a file
    # that is not even part of the frozen set is enough to prove it: the tree
    # axis covers the whole working copy, and this is the wiring at the
    # `verify` call site (scripts/canopus.py `_print_attestation`).
    (tree / "scripts" / "run-tests.py").write_text("# stub test gate, edited\n")
    assert _run(["verify"], tree) == 0
    out = capsys.readouterr().out
    assert "NOT ATTESTED" in out
    assert "scripts/run-tests.py" in out


def test_verify_reports_not_attested_when_no_run_has_attested(tree, anchor, capsys):
    _freeze(tree, anchor)
    anchor.write_text(f"# gate\n\ncanopus-anchor: {_root_of(tree)}\n")
    assert _run(["verify"], tree) == 0
    out = capsys.readouterr().out
    assert "LOCK HELD" in out
    assert "NOT ATTESTED" in out
    assert "no run has attested" in out


def test_an_attestation_against_another_root_does_not_count(tree, anchor, capsys):
    _freeze(tree, anchor)
    anchor.write_text(f"# gate\n\ncanopus-anchor: {_root_of(tree)}\n")
    _attest(tree, "b" * 64)
    assert _run(["verify"], tree) == 0
    out = capsys.readouterr().out
    assert "NOT ATTESTED" in out
    assert "different root hash" in out


def test_a_deselecting_run_prints_its_reasons(tree, anchor, capsys):
    _freeze(tree, anchor)
    root = _root_of(tree)
    anchor.write_text(f"# gate\n\ncanopus-anchor: {root}\n")
    _attest(tree, root, deselected=7)
    assert _run(["verify"], tree) == 0
    out = capsys.readouterr().out
    assert "NOT ATTESTED" in out
    assert "7 items deselected" in out


def test_loss_of_lock_still_shows_the_attestation_axis(tree, anchor, capsys):
    # Without a git identity, `tree_state(tree)` is None for this whole test and
    # the record is disqualified on TREE grounds (no usable tree description)
    # before the ROOT axis below is ever reached -- so a broken root comparison
    # in `attestation_state` could not fail this test. `_git_init_tree` gives
    # the record a real tree state so the root axis is what actually decides it.
    _git_init_tree(tree)
    _freeze(tree, anchor)
    root = _root_of(tree)
    anchor.write_text(f"# gate\n\ncanopus-anchor: {root}\n")
    _attest(tree, root)
    (tree / "tests" / "test_alpha.py").write_text("def test_a():\n    assert False\n")
    assert _run(["verify"], tree) == 1
    out = capsys.readouterr().out
    assert "LOSS OF LOCK" in out
    # The edit moved the root, so the earlier attestation stops applying without
    # anyone having to remember to delete it. Pinned by the reason text, not
    # just the presence of NOT ATTESTED: the edited file is both frozen and
    # git-tracked, so it *also* dirties the tree, and a broken root comparison
    # would still read NOT ATTESTED via that independent tree-drift path. Only
    # the specific "different root hash" reason proves the root axis fired.
    assert "NOT ATTESTED" in out
    assert "different root hash" in out


def test_loss_of_lock_also_names_the_trees_own_half(tree, anchor, capsys):
    """`_print_attestation`'s supplementary block only fires when `reason`
    equals one of `REASON_DIFFERENT_RECIPE` / `REASON_DIFFERENT_ROOT`,
    imported from `canopus_freeze` rather than hand-typed here as a second
    copy. The top-level "different root hash" line above comes straight from
    `attestation_state`'s own return value regardless of whether that
    comparison still matches, so it alone cannot prove the branch fired; only
    the supplementary per-path reason line does.
    """
    _git_init_tree(tree)
    _freeze(tree, anchor)
    root = _root_of(tree)
    anchor.write_text(f"# gate\n\ncanopus-anchor: {root}\n")
    _attest(tree, root)
    (tree / "tests" / "test_alpha.py").write_text("def test_a():\n    assert False\n")
    assert _run(["verify"], tree) == 1
    out = capsys.readouterr().out
    assert "different root hash" in out
    assert ("reason   a path appeared since the attesting run: "
            "tests/test_alpha.py") in out


def test_status_carries_the_attestation_line(tree, anchor, capsys):
    _git_init_tree(tree)
    _freeze(tree, anchor)
    root = _root_of(tree)
    anchor.write_text(f"# gate\n\ncanopus-anchor: {root}\n")
    _attest(tree, root, skipped=2)
    assert _run(["status"], tree) == 0
    out = capsys.readouterr().out
    assert "ATTESTED" in out
    assert "2 skipped" in out


def test_a_damaged_attestation_changes_no_exit_code(tree, anchor, capsys):
    from scripts.utils import canopus_freeze as cf

    _freeze(tree, anchor)
    anchor.write_text(f"# gate\n\ncanopus-anchor: {_root_of(tree)}\n")
    cf.attest_state_path(tree).write_text("{ not json", encoding="utf-8")
    assert _run(["verify"], tree) == 0
    assert _run(["status"], tree) == 0
    assert "NOT ATTESTED" in capsys.readouterr().out


def test_freeze_accepts_content_only_with_no_positional_paths(tree, anchor):
    (tree / "scripts" / "helper.py").write_text("x = 1\n")

    assert _run(["freeze", "--label", "demo", "--anchor", str(anchor),
                 "--content", "scripts/helper.py"], tree) == 0

    manifest = json.loads((tree / ".canopus" / "freeze.json").read_text())
    assert "scripts/helper.py" in manifest["files"]
    assert "scripts" not in manifest["dirs"]


def test_freeze_requires_at_least_one_path(tree, anchor, capsys):
    assert _run(["freeze", "--label", "demo", "--anchor", str(anchor)], tree) == 1
    assert "at least one path" in capsys.readouterr().err


def _write_contract(tree, red=True):
    """Two tests: one red under `red=True`, one green either way.

    The red one dies on an ABSENT import and then asserts a value no stand-in can
    satisfy, so it is red for real, red under both stub value sets, and therefore
    never vacuous. The absent import is load-bearing rather than decoration: a
    contract whose source names no module at all is one the vacuity probe now
    refuses, because with no name to stand in for, nothing is stubbed and nothing
    is measured. `red=False` keeps its import-free all-green shape, which
    `refusal_reasons` owns and the probe is never reached for.
    """
    directory = tree / "tests" / "contract" / "slice"
    directory.mkdir(parents=True, exist_ok=True)
    # The docstring claims SC-1 from the `anchor` fixture. A12: approve and
    # freeze refuse a contract leaving a stated criterion claimed by nothing.
    first = ('def test_a():\n'
             '    """SC-1."""\n'
             "    from absent_thing import answer\n"
             "    assert answer() == 42\n"
             if red else
             'def test_a():\n    """SC-1."""\n    assert True\n')
    (directory / "test_contract.py").write_text(
        first + "\n\ndef test_b():\n    assert True\n"
    )
    return directory


def test_freeze_contract_records_the_baseline(tree, anchor):
    _write_contract(tree)

    assert _run(["freeze", "--label", "demo", "--anchor", str(anchor),
                 "--contract", "tests/contract/slice"], tree) == 0

    manifest = json.loads((tree / ".canopus" / "freeze.json").read_text())
    assert manifest["baseline"] == {"tests/contract/slice/test_contract.py": 2}


def _wire_the_recorder(tree):
    """Give a synthetic tree the conftest hook the engine's own tests carry.

    The plugin baseline is captured from the contract's own pytest child, and
    that child only records anything if a conftest routes session finish into
    the recorder. The engine has one; a scratch tree does not, which is why a
    freeze over a bare synthetic tree records no baseline and says so.
    """
    engine = Path(canopus.__file__).resolve().parents[1]
    (tree / "tests" / "conftest.py").write_text(
        "import sys\n"
        f"sys.path.insert(0, {str(engine)!r})\n"
        "from scripts.utils.canopus_gate import AttestationRecorder\n"
        f"_recorder = AttestationRecorder({str(tree)!r})\n"
        "def pytest_sessionfinish(session, exitstatus):\n"
        "    _recorder.finish(session, exitstatus)\n",
        encoding="utf-8",
    )


def test_freeze_contract_captures_the_plugin_baseline(tree, anchor):
    """The captured set, end to end through the child that runs the contract.

    Captured rather than derived: `recompute` cannot re-run pytest, and a field
    inside the root hash that recompute cannot reproduce is a permanent LOSS OF
    LOCK on a tree where nothing moved.
    """
    _write_contract(tree)
    _wire_the_recorder(tree)

    assert _run(["freeze", "--label", "demo", "--anchor", str(anchor),
                 "--contract", "tests/contract/slice"], tree) == 0

    manifest = json.loads((tree / ".canopus" / "freeze.json").read_text())
    assert "dist:_pytest" in manifest["plugins"]
    # Names only, and normalised ones. An origin is an absolute path inside a
    # `.venv`, and the conftest that wired this run registers under its own
    # absolute path; either inside the root hash would redden the next clone.
    assert all(name.startswith("dist:") for name in manifest["plugins"])
    assert str(tree) not in json.dumps(manifest["plugins"])


def test_a_freeze_without_a_contract_says_it_carries_no_plugin_baseline(
        tree, anchor, capsys):
    """The silent half of the same state, which review found and nothing covered.

    A freeze over plain paths runs no pytest child, so it captures nothing and
    can never attest. The DIRECTION is right and mandated (SC-7: absence is not
    innocence), and saying nothing about it is the defect: the operator meets it
    days later as a gate refusal naming a baseline they never knew was expected.
    """
    assert _freeze(tree, anchor) == 0

    assert json.loads((tree / ".canopus" / "freeze.json").read_text())["plugins"] == []
    out = capsys.readouterr().out
    assert "NO plugin baseline" in out
    assert "without --contract" in out


def test_a_freeze_that_captured_no_plugin_set_says_so(tree, anchor, capsys):
    """Silence here becomes an unexplained NOT ATTESTED days later.

    A tree with no recorder wired writes no dump, so the freeze carries no
    plugin baseline and nothing can ever attest against it. That is the
    fail-closed direction and it is stated at the freeze, not left to surface as
    a gate refusal nobody can account for.
    """
    _write_contract(tree)

    assert _run(["freeze", "--label", "demo", "--anchor", str(anchor),
                 "--contract", "tests/contract/slice"], tree) == 0

    assert json.loads((tree / ".canopus" / "freeze.json").read_text())["plugins"] == []
    assert "recorded no plugin set" in capsys.readouterr().out


def test_freeze_refuses_an_all_green_contract(tree, anchor, capsys):
    _write_contract(tree, red=False)

    assert _run(["freeze", "--label", "demo", "--anchor", str(anchor),
                 "--contract", "tests/contract/slice"], tree) == 1
    assert "asserts nothing" in capsys.readouterr().err
    assert not (tree / ".canopus" / "freeze.json").exists()


def test_probe_prints_outcomes_and_writes_nothing(tree, capsys):
    _write_contract(tree)

    code = _run(["probe", "tests/contract/slice"], tree)

    out = capsys.readouterr().out
    assert code == 0
    assert "test_a" in out and "test_b" in out
    assert not (tree / ".canopus").exists()


def test_probe_exits_one_on_a_contract_that_would_be_refused(tree):
    _write_contract(tree, red=False)
    assert _run(["probe", "tests/contract/slice"], tree) == 1


def test_pack_exits_nonzero_with_no_freeze(tree, capsys):
    assert _run(["pack"], tree) == 1
    assert "no active freeze" in capsys.readouterr().err


def test_pack_reports_all_three_axes_and_the_uncovered_list(tree, anchor, capsys):
    assert _freeze(tree, anchor) == 0
    assert _run(["pack"], tree) == 0
    out = capsys.readouterr().out
    # Amber on the lock axis, because `freeze` no longer writes the anchor line
    # and nothing here approved one. A `LOCK HELD` assertion left in place would
    # have made this rename look done while pinning a state the tool no longer
    # produces from a bare freeze.
    assert "LOCK UNCONFIRMED" in out
    assert "APPROVAL" in out
    assert "NOT ATTESTED" in out          # no run has attested this freeze yet
    assert "not covered" in out
    # The heading alone passed while the list under it named three wire-2 gaps
    # and none of wire 2.3's. This is the slice's principal residual: the plugin
    # comparison is by top-level module name, so a same-named plugin from another
    # distribution reaches the greenest reading this page produces. An operator
    # signs off from here, so it has to be here and not only in docs/EXTENDING.md.
    assert "by top-level module NAME" in out
    # The page may say it does not KNOW whether mutations ran. It may never say
    # they did not: nothing on this machine records either answer, and the claim
    # was measurably false the one time it mattered.
    assert "Whether mutation testing ran is not recorded here" in out
    assert "Mutation testing has not run" not in out
    assert "continuity" in out
    assert "staleness" in out
    # The interpreter section is wired into pack by ONE line. Every unit test
    # for its renderer calls the renderer directly, so deleting that line left
    # all of them green while the operator saw nothing.
    assert "interpreter" in out
    # The two disclosure surfaces -- this page and docs/EXTENDING.md -- diverged
    # once already: a fix applied to one and not its twin. Pinned here so a
    # future edit to either cannot silently reopen the gap: the operator signs
    # off from THIS page, so every item docs/EXTENDING.md discloses has to be
    # named here too, not only there.
    assert ".git/info/exclude" in out
    assert "assume-unchanged and skip-worktree" in out
    assert "always a false positive, never a false negative" in out
    assert "submodule" in out and "hashes to None" in out
    # Promoted from the wire 3.2 frozen contract when that contract retired.
    # These three say what the ATTESTED line above is WORTH, and the operator
    # signs off from this page: the record is a local gitignored file anyone
    # who can write it can forge (evidence, not proof), ignored files are
    # outside the tree state entirely, and a root that is not a git working
    # copy can never reach ATTESTED at all. Nothing else in the suite pins
    # them, so deleting any one of the three would go unnoticed here.
    assert "evidence rather than proof" in out
    assert "outside the state" in out
    assert "is not a git working" in out
    # The friction section, end to end. Its own unit tests call the renderer
    # directly, and the wiring test in tests/test_canopus_friction.py can only
    # reach the AST of scripts/canopus.py; neither reads what the operator sees.
    # The frozen contract could not make this claim at all -- a pack test written
    # before the code existed ended in a skip, and nothing refuses a skipped
    # contract test, so it would have shipped as a criterion that never ran.
    assert "friction" in out
    assert "A floor, not a total" in out


def test_pack_never_raises_on_damaged_state(tree, anchor, capsys):
    """The pack is read at the one moment the operator decides to keep the work.
    A traceback there is worse than a missing section.

    Ported from the wire 2 contract when that contract was retired. The suite
    already pinned `verify` and `status` over a damaged attestation, and pack was
    assumed to inherit the same tolerance from the shared reader. It does not
    inherit anything a test checks: a pack that read the attestation file
    directly instead of through `read_attestation` passed every other test here.
    """
    from scripts.utils import canopus_freeze as cf

    assert _freeze(tree, anchor) == 0
    cf.attest_state_path(tree).write_text("{ not json", encoding="utf-8")
    capsys.readouterr()

    assert _run(["pack"], tree) == 0

    # Degraded to a missing record rather than a crash: damage reads as absence,
    # so the axis still prints and the sections below it still render.
    out = capsys.readouterr().out
    assert "NOT ATTESTED" in out
    assert "continuity" in out
    assert "staleness" in out


def test_pack_compares_the_record_against_the_real_current_tree(tree, anchor, capsys):
    """`pack`'s ATTESTED/NOT ATTESTED banner is rendered by `_print_attestation`,
    the same shared function `verify` calls -- so this exercises the same
    comparison `test_verify_reports_attested_when_the_run_matches` pins,
    through the `pack` command instead of `verify`. The dedicated call site
    `pack` carries beyond that shared banner -- the `attestation_state` call
    inside `cmd_pack` that feeds the `staleness` section's fallback reason --
    is pinned separately below, because it can print ATTESTED-shaped silence
    while this banner correctly says NOT ATTESTED.
    """
    _git_init_tree(tree)
    _freeze(tree, anchor)
    root = _root_of(tree)
    anchor.write_text(f"# gate\n\ncanopus-anchor: {root}\n")
    _attest(tree, root)
    capsys.readouterr()

    assert _run(["pack"], tree) == 0
    out = capsys.readouterr().out
    assert "NOT ATTESTED" not in out
    assert "ATTESTED" in out

    (tree / "scripts" / "run-tests.py").write_text("# stub test gate, edited\n")
    assert _run(["pack"], tree) == 0
    out = capsys.readouterr().out
    assert "NOT ATTESTED" in out
    assert "scripts/run-tests.py" in out


def test_pack_staleness_reason_reflects_the_real_current_tree(tree, anchor, capsys):
    """`cmd_pack` (scripts/canopus.py:996) has its OWN `attestation_state` call,
    separate from the one inside `_print_attestation` (line 257) that renders
    the main banner. Its `reason` only surfaces in the `staleness` section, and
    only when the record carries no usable `attested_at` -- so a record with a
    real tree but no timestamp is what it takes to observe this call site at
    all. Comparing the record against ITSELF there (instead of the real
    current tree) would silently drop the reason instead of naming the path
    that moved: exactly the fail-open this whole slice exists to close, on the
    call site's own terms rather than through the shared banner.
    """
    from scripts.utils import canopus_freeze as cf

    _git_init_tree(tree)
    _freeze(tree, anchor)
    root = _root_of(tree)
    anchor.write_text(f"# gate\n\ncanopus-anchor: {root}\n")
    record = _attest(tree, root)
    record = dict(record)
    del record["attested_at"]
    cf.write_attestation(tree, record)
    capsys.readouterr()

    (tree / "scripts" / "run-tests.py").write_text("# stub test gate, edited\n")
    assert _run(["pack"], tree) == 0
    out = capsys.readouterr().out
    # Scoped to the staleness section specifically: the shared banner at
    # line 257 already names the path via a separate, correct read, and an
    # assertion against the whole output would pass on that alone even if
    # this call site's own reason went silent.
    staleness = out.rsplit("staleness", 1)[1]
    assert "no attestation to age" in staleness
    assert "scripts/run-tests.py" in staleness


def test_pack_samples_the_tree_exactly_once(tree, anchor, capsys, monkeypatch):
    """`cmd_pack` used to call `tree_state` twice behind ONE verdict: once
    directly, to feed its own `attestation_state` read for the `staleness`
    section, and once more inside `_print_attestation` for the main banner --
    two separate git walks, and a window between them for the tree to move
    and the two readings to disagree. `_print_attestation` now takes a
    `current_tree` parameter so `cmd_pack` can sample once and hand the same
    dict to both readers.
    """
    _git_init_tree(tree)
    _freeze(tree, anchor)
    root = _root_of(tree)
    anchor.write_text(f"# gate\n\ncanopus-anchor: {root}\n")
    _attest(tree, root)
    capsys.readouterr()

    calls = []
    real_tree_state = canopus.tree_state

    def counting_tree_state(path):
        calls.append(path)
        return real_tree_state(path)

    monkeypatch.setattr(canopus, "tree_state", counting_tree_state)

    assert _run(["pack"], tree) == 0
    assert len(calls) == 1, f"tree_state sampled {len(calls)} times, expected 1"


# ============================================================
# The third indicator axis: approval
# ============================================================

def test_status_prints_the_approval_axis(tree: Path, anchor: Path, capsys):
    assert _run(["freeze", "--label", "l", "--anchor", str(anchor),
                 "tests/test_alpha.py"], tree) == 0
    capsys.readouterr()

    assert _run(["status"], tree) == 0
    out = capsys.readouterr().out
    assert "APPROVAL UNVERIFIED" in out


def test_verify_and_pack_print_the_approval_axis_too(tree: Path, anchor: Path, capsys):
    """All three surfaces, or an operator learns to read the one that omits it."""
    assert _run(["freeze", "--label", "l", "--anchor", str(anchor),
                 "tests/test_alpha.py"], tree) == 0
    capsys.readouterr()

    _run(["verify"], tree)
    assert "APPROVAL UNVERIFIED" in capsys.readouterr().out

    _run(["pack"], tree)
    assert "APPROVAL UNVERIFIED" in capsys.readouterr().out


def test_verify_names_the_copy_the_hash_came_from(tree: Path, anchor: Path, capsys):
    """APPROVED beside LOSS OF LOCK is a legitimate pair, and it needs explaining.

    The approval axis binds to the freeze that was TAKEN; the lock binds to the
    tree RIGHT NOW. So this pair reads "a human approved this freeze, and the
    contract has moved since". The detail line prints a hash that came from HEAD
    while labelling it with a working-tree path, so an operator who opens that
    file could find a different hash and no explanation.

    The approval arrives through `approve` plus a commit, because `freeze` no
    longer writes the line and a commit of an artifact carrying no line records
    no approval at all.
    """
    gate = _init_gate_repo(anchor)

    assert _run(["approve", "tests/test_alpha.py", "--label", "demo",
                 "--anchor", str(anchor)], tree) == 0
    approved = _recorded(anchor)
    _git(gate, "add", anchor.name)
    _git(gate, "commit", "-q", "-m", "approve")

    assert _freeze(tree, anchor) == 0
    assert _root_of(tree) == approved
    capsys.readouterr()

    (tree / "tests" / "test_alpha.py").write_text("def test_a():\n    assert False\n")

    assert _run(["verify"], tree) == 1
    out = capsys.readouterr().out
    assert canopus.LOSS_OF_LOCK in out
    assert f"records {approved} (APPROVED)" in out
    # The approval axis on the LOSS OF LOCK path, which is a separate print from
    # the parenthetical above. Two occurrences: the origin label on the anchor
    # detail line, and the axis line _print_approval writes.
    assert out.count("APPROVED") == 2


def test_verify_keeps_the_origin_parenthetical_off_a_bare_anchor(tree: Path, anchor: Path,
                                                                capsys):
    """The parenthetical says WHICH COPY the hash came from, so it earns its keep
    only where the answer is not obvious.

    Under no_repo and no_git the hash came from the working file the detail line
    already names, so labelling it adds a word and no information. Printing it
    unconditionally passes every other test in this file, which is why the BARE
    form is pinned here rather than left to the reader of the guard.

    The `anchor` fixture is a file in a folder with no repository around it, so
    `approve` is what puts the line on disk for the detail line to read back.
    """
    assert _run(["approve", "tests/test_alpha.py", "--label", "demo",
                 "--anchor", str(anchor)], tree) == 0
    assert _freeze(tree, anchor) == 0
    approved = _root_of(tree)
    assert _recorded(anchor) == approved
    capsys.readouterr()

    (tree / "tests" / "test_alpha.py").write_text("def test_a():\n    assert False\n")

    assert _run(["verify"], tree) == 1
    out = capsys.readouterr().out
    assert canopus.LOSS_OF_LOCK in out
    detail = next(line for line in out.splitlines()
                  if line.startswith("  anchor ") and "records" in line)
    assert detail.rstrip() == f"  anchor   {anchor.resolve()} records {approved}"


def test_verify_explains_an_uncommitted_approval_rather_than_denying_the_line(
    tree: Path, anchor: Path, capsys
):
    """LOCK UNCONFIRMED has to say the true reason, and print the approval axis.

    An earlier revision re-derived its own sentence here, "<anchor> carries no
    canopus-anchor: line yet", which is plainly false in this scenario: the
    working copy carries one, `approve` wrote it, and the reason it does not
    count is that nobody committed it. The reason now comes from the single
    producer of the precedence decision.
    """
    gate = _init_gate_repo(anchor)
    _git(gate, "add", "-A")
    _git(gate, "commit", "-q", "-m", "no approval yet")

    assert _run(["approve", "tests/test_alpha.py", "--label", "demo",
                 "--anchor", str(anchor)], tree) == 0
    assert _freeze(tree, anchor) == 0
    assert f"canopus-anchor: {_root_of(tree)}" in anchor.read_text()
    capsys.readouterr()

    assert _run(["verify"], tree) == 0
    out = capsys.readouterr().out
    lock_line = next(line for line in out.splitlines()
                     if canopus.LOCK_UNCONFIRMED in line)
    assert "no approval is recorded in the committed state" in lock_line
    assert "carries no" not in lock_line
    assert "APPROVAL UNVERIFIED" in out


def test_verify_names_the_unbound_anchor_and_gives_the_reason_once(
    tree: Path, anchor: Path, capsys
):
    """The `verify` half of wire 2.2, and the reason is printed exactly once.

    The per-file report above this line is EMPTY: nothing in the contract moved,
    the anchor's repository did. So the detail line carries the path and the
    STATE, while the approval axis a few lines below carries the sentence. The
    same sentence twice in one report is how an operator learns to skim the
    second one.
    """
    gate = _init_gate_repo(anchor)

    assert _run(["approve", "tests/test_alpha.py", "--label", "demo",
                 "--anchor", str(anchor)], tree) == 0
    assert _freeze(tree, anchor) == 0
    capsys.readouterr()

    (gate / ".git").rename(gate / ".git-hidden")

    assert _run(["verify"], tree) == 1
    out = capsys.readouterr().out
    assert canopus.LOSS_OF_LOCK in out
    assert f"  anchor   {anchor.resolve()} [{canopus.ANCHOR_UNBOUND}]" in out
    assert out.count("the approval cannot be attributed") == 1


def test_freeze_contract_reports_how_much_is_already_green(tree, anchor, capsys):
    """The redness gate needs one red in the SET, so it does not scale to the moment.

    Measured during the wire 2 build: a mid-build retake froze a contract that was
    already 11 of 14 green and was accepted, because three were still red. The
    same gate a fully red contract passes at the start. Nothing said so, which is
    the part that is fixable cheaply: say it, at freeze time and in the ledger,
    and let the operator judge a retake differently from a first freeze.
    """
    _write_contract(tree)   # one failing, one passing

    assert _run(["freeze", "--label", "demo", "--anchor", str(anchor),
                 "--contract", "tests/contract/slice"], tree) == 0

    assert "1 of 2 already green" in capsys.readouterr().out
    entries = [json.loads(line) for line
               in (tree / ".canopus" / "history.jsonl").read_text().splitlines()]
    frozen = [e for e in entries if e["event"] == "freeze"]
    assert frozen and "1 of 2 already green" in frozen[-1]["reason"]


# ============================================================
# `approve`: the candidate hash a human commits
# ============================================================

def _git(repo: Path, *argv: str) -> None:
    subprocess.run(["git", "-C", str(repo), *argv], check=True,
                   capture_output=True, text=True)


def _init_gate_repo(anchor: Path) -> Path:
    """Turn the anchor's directory into a repository with a synthetic identity.

    The identity is invented: this file ships in a public repository and carries
    no real person or host.

    The seed commit is EMPTY, and both halves of that are load-bearing. It has to
    exist because `approve` and `freeze` refuse a repository with no commits: an
    identity is a digest over the root commits, so a repository that has none
    would acquire its identity at the exact moment a human commits the approval.
    It has to be empty because every caller here decides for itself whether the
    gate artifact is tracked, and `add -A` in the helper would commit that
    artifact behind their backs.
    """
    gate = anchor.parent
    for argv in (["init", "-q", "-b", "main"],
                 ["config", "user.email", "builder@example.invalid"],
                 ["config", "user.name", "Builder"],
                 ["commit", "-q", "--allow-empty", "-m", "seed"]):
        _git(gate, *argv)
    return gate


def _ledger(tree: Path) -> list:
    return [json.loads(line) for line
            in (tree / ".canopus" / "history.jsonl").read_text().splitlines()]


def _recorded(anchor: Path) -> str:
    """The last hash the artifact records, read whole and never by prefix.

    The LAST LINE CARRYING THE PREFIX, not the file's last line. The artifact is
    a human-authored document the tool appends one line to, so prose written
    under that line is ordinary; a helper that read the final line would then
    return a word from the prose and every assertion built on it would go quiet.
    """
    values = [
        line.strip()[len(canopus.ANCHOR_PREFIX):].strip()
        for line in anchor.read_text().splitlines()
        if line.strip().startswith(canopus.ANCHOR_PREFIX)
    ]
    assert values, f"{anchor} records no {canopus.ANCHOR_PREFIX} line"
    return values[-1]


def test_the_recorded_helper_reads_the_anchor_line_not_the_last_line(anchor: Path):
    """The helper every approve assertion below leans on, pinned itself."""
    anchor.write_text(
        f"# gate\n\ncanopus-anchor: {'a' * 64}\n\nApproved after the review.\n"
    )
    assert _recorded(anchor) == "a" * 64


def test_approve_writes_the_candidate_hash_and_no_freeze_state(tree: Path, anchor: Path):
    assert _run(["approve", "--label", "l", "--anchor", str(anchor),
                 "tests/test_alpha.py"], tree) == 0

    assert "canopus-anchor:" in anchor.read_text()
    assert not (tree / ".canopus" / "freeze.json").exists()


def test_the_approved_hash_is_the_hash_freeze_then_computes(tree: Path, anchor: Path):
    """If these two ever disagree, every freeze is refused and the tool is dead.

    The artifact is left exactly as `approve` wrote it, which is the real
    sequence an operator runs. Nothing needs scrubbing: the anchor PATH is inside
    the root hash and its CONTENTS are not, so the line approve appends cannot
    move the digest freeze then computes.
    """
    assert _run(["approve", "--label", "l", "--anchor", str(anchor),
                 "tests/test_alpha.py"], tree) == 0
    approved = _recorded(anchor)
    assert len(approved) == 64, "a truncated digest is not an approval"

    assert _run(["freeze", "--label", "l", "--anchor", str(anchor),
                 "tests/test_alpha.py"], tree) == 0
    assert _root_of(tree) == approved


def test_approve_refuses_an_artifact_that_already_records_a_hash(tree: Path, anchor: Path,
                                                                capsys):
    assert _run(["approve", "--label", "l", "--anchor", str(anchor),
                 "tests/test_alpha.py"], tree) == 0
    first = _recorded(anchor)
    capsys.readouterr()

    assert _run(["approve", "--label", "l", "--anchor", str(anchor),
                 "tests/test_alpha.py"], tree) == 1
    assert first in capsys.readouterr().err
    assert anchor.read_text().count("canopus-anchor:") == 1


def test_approve_replace_requires_a_reason(tree: Path, anchor: Path, capsys):
    assert _run(["approve", "--label", "l", "--anchor", str(anchor),
                 "tests/test_alpha.py"], tree) == 0

    assert _run(["approve", "--label", "l", "--anchor", str(anchor),
                 "--replace", "tests/test_alpha.py"], tree) == 1
    assert "--reason" in capsys.readouterr().err

    # And a declared --cause, end to end through the CLI. The frozen contract
    # pins `retake_cause_or_error` as a function; nothing in it reaches
    # `cmd_approve`, so without this the refusal could be perfectly correct and
    # never wired to the command an operator types. Same defect shape the
    # friction-counters slice found in its own SC-4, one layer over.
    assert _run(["approve", "--label", "l", "--anchor", str(anchor),
                 "--replace", "--reason", "the set changed",
                 "tests/test_alpha.py"], tree) == 1
    err = capsys.readouterr().err
    assert "--cause" in err
    assert "contract-strengthened" in err, (
        "the refusal does not name the vocabulary it wants")

    assert _run(["approve", "--label", "l", "--anchor", str(anchor),
                 "--replace", "--reason", "the set changed",
                 "--cause", "frozen-set-wrong",
                 "tests/test_alpha.py"], tree) == 0

    # And the cause reaches the LEDGER structurally, not only the argv. A flag
    # the command accepts and drops leaves `count_retakes` reading exactly the
    # unclassified records this slice exists to end.
    replaced = [e for e in _ledger(tree) if e["event"] == "anchor_replaced"]
    assert replaced and replaced[-1]["kind"] == "frozen-set-wrong", replaced


def test_a_cause_without_replace_is_refused_rather_than_silently_dropped(
        tree: Path, anchor: Path, capsys):
    """A first approval writes no `anchor_replaced`, so a cause has nowhere to go.

    Accepting it silently is the worse failure: the operator types the flag, sees
    exit 0, and has recorded nothing. Found at step 11 of the yield-axes slice by
    reading the branch rather than by a mutation, because a silent no-op breaks no
    assertion anywhere.
    """
    assert _run(["approve", "--label", "l", "--anchor", str(anchor),
                 "--cause", "contract-strengthened",
                 "tests/test_alpha.py"], tree) == 1
    err = capsys.readouterr().err
    assert "--replace" in err
    assert not [e for e in _ledger(tree) if e["event"] == "approve"], (
        "the refused approval still reached the ledger")


def test_approve_replace_appends_and_keeps_the_earlier_approval(tree: Path, anchor: Path):
    """A replacement appends. Overwriting would erase the trail the artifact is
    for, and leave `read_committed_anchor`'s last-line-wins rule nothing to be
    last among."""
    assert _run(["approve", "tests/test_alpha.py", "--label", "l",
                 "--anchor", str(anchor)], tree) == 0
    first = _recorded(anchor)

    (tree / "tests" / "test_beta.py").write_text("def test_b():\n    assert True\n")
    assert _run(["approve", "tests/test_alpha.py", "tests/test_beta.py",
                 "--label", "l", "--anchor", str(anchor),
                 "--replace", "--reason", "widened the approved set",
                 "--cause", "frozen-set-wrong"], tree) == 0

    text = anchor.read_text()
    second = _recorded(anchor)
    assert second != first
    assert f"canopus-anchor: {first}" in text
    assert f"canopus-anchor: {second}" in text

    assert [entry["event"] for entry in _ledger(tree)] == [
        "approve", "approve", "anchor_replaced"
    ]
    replaced = _ledger(tree)[-1]
    assert replaced["reason"] == "widened the approved set"
    assert replaced["root"] == second


def test_approve_refuses_a_line_it_wrote_that_nobody_committed(tree: Path, anchor: Path,
                                                              capsys):
    """The union of both readers, in the direction the committed half alone misses.

    Inside a repository, the line this command wrote a minute ago is absent from
    HEAD, so `read_committed_anchor` answers UNCOMMITTED. A guard built on the
    committed copy alone would let a second approve over a different set append
    silently, with no --replace and no reason anywhere on the record.
    """
    _init_gate_repo(anchor)
    _git(anchor.parent, "add", "-A")
    _git(anchor.parent, "commit", "-q", "-m", "a gate with no approval in it")

    assert _run(["approve", "tests/test_alpha.py", "--label", "l",
                 "--anchor", str(anchor)], tree) == 0
    capsys.readouterr()

    assert _run(["approve", "tests/test_alpha.py", "--label", "l",
                 "--anchor", str(anchor)], tree) == 1
    assert "already records" in capsys.readouterr().err


def test_the_approved_hash_matches_over_a_contract_baseline_too(tree: Path, anchor: Path,
                                                                capsys):
    """The baseline is inside the root hash, so it is where two constructions of
    the candidate would drift apart without one shared builder."""
    _write_contract(tree)   # one failing, one passing

    assert _run(["approve", "--label", "demo", "--anchor", str(anchor),
                 "--contract", "tests/contract/slice"], tree) == 0
    assert "1 of 2 already green before this approval" in capsys.readouterr().out
    approved = _recorded(anchor)

    assert _run(["freeze", "--label", "demo", "--anchor", str(anchor),
                 "--contract", "tests/contract/slice"], tree) == 0
    assert _root_of(tree) == approved
    manifest = json.loads((tree / ".canopus" / "freeze.json").read_text())
    assert manifest["baseline"] == {"tests/contract/slice/test_contract.py": 2}


def test_approve_requires_at_least_one_path(tree: Path, anchor: Path, capsys):
    """The same refusal `freeze` gives, because the check now lives in the builder
    they share and a sibling that lost it would approve an empty set."""
    assert _run(["approve", "--label", "l", "--anchor", str(anchor)], tree) == 1
    assert "at least one path" in capsys.readouterr().err
    assert "canopus-anchor:" not in anchor.read_text()


def test_approve_fails_closed_when_the_anchor_cannot_be_written(tree: Path, anchor: Path,
                                                                capsys):
    """No traceback, no ledger line, and a message about the right subsystem.

    The artifact write comes BEFORE append_history deliberately: a ledger that
    records an approval the artifact never received is worse than no ledger.

    The message half is the wire 2.2 repair. This fell through to `main`'s
    generic OSError handler and printed "the frozen contract could not be read,
    so it cannot be verified", which is false in both halves. The contract was
    read, and it was the artifact that could not be WRITTEN.
    """
    os.chmod(anchor, 0o444)
    try:
        assert _run(["approve", "tests/test_alpha.py", "--label", "l",
                     "--anchor", str(anchor)], tree) == 1
    finally:
        os.chmod(anchor, 0o644)

    err = capsys.readouterr().err
    assert "Traceback" not in err
    assert "could not be read" not in err
    assert "could NOT be written to" in err
    assert str(anchor) in err
    assert "canopus-anchor:" not in anchor.read_text()
    # The refusal IS on the ledger from 2026-08-02 (A2), and the assertion
    # changed from "nothing was written" to "the REFUSAL was written and nothing
    # else". Before A2 the ledger held 152 events and not one refusal, so every
    # time this gate refused, the event vanished and its yield could never be
    # counted. What must still be absent is an `approve` entry: a refused
    # approval must not read as an approval.
    events = [r["event"] for r in read_ledger(tree)]
    assert events == ["refuse_approve"], events


def test_approve_refuses_a_committed_approval_whose_working_copy_was_scrubbed(
    tree: Path, anchor: Path, capsys
):
    """The half of the union guard the working reader can never see.

    The sibling test above covers the other direction, where the line exists in
    the working copy and not in HEAD. This one is the direction the rule is
    actually FOR: the approval is COMMITTED, and the working copy no longer
    carries it. `read_anchor` answers ANCHOR_UNRECORDED, so a guard built on the
    working copy alone appends a second hash over a different path set, with no
    --replace, no reason, and nothing anywhere saying the approved set changed.

    Reaching this state needs no cleverness and no tampering story: an operator
    who committed an approval and then tidied the file by hand is in it. The
    scrub here is a bare rewrite rather than `git checkout --`, because checkout
    RESTORES the committed line and so never reaches the committed half at all.
    """
    _init_gate_repo(anchor)

    assert _run(["approve", "tests/test_alpha.py", "--label", "l",
                 "--anchor", str(anchor)], tree) == 0
    approved = _recorded(anchor)
    _git(anchor.parent, "add", anchor.name)
    _git(anchor.parent, "commit", "-q", "-m", "the approval")

    anchor.write_text("# gate artifact\n")   # scrubbed by hand, still committed
    assert "canopus-anchor:" not in anchor.read_text()
    capsys.readouterr()

    (tree / "tests" / "test_beta.py").write_text("def test_b():\n    assert True\n")
    assert _run(["approve", "tests/test_alpha.py", "tests/test_beta.py",
                 "--label", "l", "--anchor", str(anchor)], tree) == 1

    err = capsys.readouterr().err
    assert "already records" in err
    assert approved in err
    assert "canopus-anchor:" not in anchor.read_text()


def test_approve_refuses_while_a_freeze_is_active(tree: Path, anchor: Path, capsys):
    """Approving during a live freeze walks the operator into a red lock.

    Measured: approve set A, commit, freeze set A, verify reads LOCK HELD. Then
    approve set B while that freeze is still held and commit it, which is exactly
    what `approve`'s own closing line tells the operator to do, and verify reads
    LOSS OF LOCK with not one byte of the frozen contract moved.
    `freeze` already carries this guard; the sibling that writes the approved
    hash did not.
    """
    gate = _init_gate_repo(anchor)

    assert _run(["approve", "tests/test_alpha.py", "--label", "l",
                 "--anchor", str(anchor)], tree) == 0
    approved = _recorded(anchor)
    _git(gate, "add", anchor.name)
    _git(gate, "commit", "-q", "-m", "the approval")

    assert _freeze(tree, anchor) == 0
    assert _root_of(tree) == approved
    capsys.readouterr()
    assert _run(["verify"], tree) == 0
    assert canopus.LOCK_HELD in capsys.readouterr().out

    (tree / "tests" / "test_beta.py").write_text("def test_b():\n    assert True\n")
    assert _run(["approve", "tests/test_alpha.py", "tests/test_beta.py",
                 "--label", "l", "--anchor", str(anchor),
                 "--replace", "--reason", "widened"], tree) == 1
    err = capsys.readouterr().err
    assert "already active" in err
    assert "release" in err

    # The refusal is what keeps the lock green: no second candidate was appended,
    # so there is nothing for the operator to commit that would redden it. The
    # only line standing is the committed approval, and it is the frozen root.
    assert _recorded(anchor) == _root_of(tree)
    assert _run(["verify"], tree) == 0
    assert canopus.LOCK_HELD in capsys.readouterr().out


def test_approve_says_the_artifact_was_written_when_only_the_ledger_fails(
    tree: Path, anchor: Path, capsys
):
    """A half-landed approval must not be reported as a failed one.

    The artifact write comes first deliberately, and the order stays: the
    reverse leaves a ledger claiming an approval the artifact never received.
    What the order costs is this window, where the candidate IS on the artifact
    and the ledger entry is not. Calling that "the command failed" sends the
    operator into a retry that demands --replace --reason for an approval they
    were told had not happened.
    """
    ledger_dir = tree / ".canopus"
    ledger_dir.mkdir()
    os.chmod(ledger_dir, 0o500)
    try:
        assert _run(["approve", "tests/test_alpha.py", "--label", "l",
                     "--anchor", str(anchor)], tree) == 1
    finally:
        os.chmod(ledger_dir, 0o700)

    err = capsys.readouterr().err
    assert "Traceback" not in err
    assert "WAS written" in err
    assert _recorded(anchor) in err
    assert not (ledger_dir / "history.jsonl").exists()

    # The claim the message makes, checked against disk rather than trusted.
    assert len(_recorded(anchor)) == 64
    assert "`approve` ledger entry failed" in err


def test_a_partly_written_ledger_names_the_entry_that_did_land(
    tree: Path, anchor: Path, capsys, monkeypatch
):
    """Two ledger writes, so "the ledger entry failed" can be half true.

    When the replacement path runs, `approve` appends two entries. If the first
    lands and the second does not, a message reading "the ledger entry failed"
    tells the operator less than the tool knows, which is the same imprecision
    the surrounding branch exists to remove one layer up.
    """
    from scripts import canopus as canopus_cli

    assert _run(["approve", "tests/test_alpha.py", "--label", "l",
                 "--anchor", str(anchor)], tree) == 0
    capsys.readouterr()

    real = canopus_cli.append_history
    calls: list[str] = []

    def failing(root, event, **kwargs):
        calls.append(event)
        if len(calls) == 1:
            return real(root, event, **kwargs)
        raise OSError("ledger is full")

    monkeypatch.setattr(canopus_cli, "append_history", failing)
    assert _run(["approve", "tests/test_alpha.py", "--label", "l",
                 "--anchor", str(anchor), "--replace", "--reason", "set changed",
                 "--cause", "frozen-set-wrong"],
                tree) == 1

    err = capsys.readouterr().err
    assert "`anchor_replaced` ledger entry failed" in err
    assert "The `approve` entry did land." in err
    assert calls == ["approve", "anchor_replaced"]


def test_approve_refuses_an_all_green_contract(tree: Path, anchor: Path, capsys):
    """`freeze` carries a tested copy of this refusal, so removing the check from
    the builder they now SHARE stays green unless approve pins it too. A contract
    that is already entirely green asserts nothing about work not yet done, and
    approving one writes a candidate hash over a vacuous contract."""
    _write_contract(tree, red=False)

    assert _run(["approve", "--label", "demo", "--anchor", str(anchor),
                 "--contract", "tests/contract/slice"], tree) == 1
    assert "asserts nothing" in capsys.readouterr().err
    assert "canopus-anchor:" not in anchor.read_text()
    # The refusal IS on the ledger from 2026-08-02 (A2), and the assertion
    # changed from "nothing was written" to "the REFUSAL was written and nothing
    # else". Before A2 the ledger held 152 events and not one refusal, so every
    # time this gate refused, the event vanished and its yield could never be
    # counted. What must still be absent is an `approve` entry: a refused
    # approval must not read as an approval.
    events = [r["event"] for r in read_ledger(tree)]
    assert events == ["refuse_approve"], events


def test_approve_refuses_a_contract_directory_with_no_test_modules(
    tree: Path, anchor: Path, capsys
):
    """The builder's other removable guard, pinned from the approve side.

    A --contract naming nothing collectable can never be attested, so the
    approval would record a hash for a contract that no run can ever prove.
    """
    empty = tree / "tests" / "contract" / "empty"
    empty.mkdir(parents=True)
    (empty / "helper.py").write_text("VALUE = 1\n")

    assert _run(["approve", "--label", "demo", "--anchor", str(anchor),
                 "--contract", "tests/contract/empty"], tree) == 1
    assert "names no test modules" in capsys.readouterr().err
    assert "canopus-anchor:" not in anchor.read_text()


def test_approve_prints_the_candidate_root_for_the_operator_to_read(
    tree: Path, anchor: Path, capsys
):
    """The closing line says "read it, then commit". This is the line to read.

    Without it the operator's only route to the hash they are about to approve is
    to open the artifact and trust whatever is at the bottom, which is the eye
    comparison the whole tool exists to remove.
    """
    assert _run(["approve", "tests/test_alpha.py", "--label", "l",
                 "--anchor", str(anchor)], tree) == 0

    out = capsys.readouterr().out
    # The CANOPUS headline rather than "line one": the no-plugin-baseline notice
    # is printed while the candidate manifest is built, so it precedes it.
    first = next(line for line in out.splitlines() if "CANOPUS" in line)
    assert f"root {_recorded(anchor)}" in first
    assert "label: l" in first
    assert "1 file" in first


def test_every_reason_flag_defaults_to_the_empty_string():
    """`release` defaults --reason to ""; `approve` did not, so args.reason came
    through as None on the one command whose whole job is to put a reason on the
    record. Asserted at the parser, because `args.reason or ""` downstream
    launders the difference into the ledger and hides it.

    `freeze` was the third command in this list until it stopped writing the
    anchor. Its --reason existed only to explain a --replace-anchor, and both
    moved to `approve`, so the assertion below is that `freeze` carries NEITHER
    flag rather than that its --reason defaults well.
    """
    parser = canopus.build_parser()
    for argv in (["approve", "x", "--label", "l", "--anchor", "a"],
                 ["release", "--ship"]):
        assert parser.parse_args(argv).reason == "", argv[0]

    frozen = parser.parse_args(["freeze", "x", "--label", "l", "--anchor", "a"])
    assert not hasattr(frozen, "reason")
    assert not hasattr(frozen, "replace_anchor")


def test_approve_writes_the_empty_reason_through_to_the_ledger(tree: Path, anchor: Path):
    assert _run(["approve", "tests/test_alpha.py", "--label", "l",
                 "--anchor", str(anchor)], tree) == 0
    assert _ledger(tree)[-1]["reason"] == ""


# ============================================================
# `freeze`: verifies an approval rather than writing one
# ============================================================

def test_freeze_no_longer_writes_the_anchor(tree: Path, anchor: Path):
    """The write moved to approve. A tool that writes then checks its own line
    has verified nothing."""
    before = anchor.read_text()

    assert _run(["freeze", "--label", "l", "--anchor", str(anchor),
                 "tests/test_alpha.py"], tree) == 0

    assert anchor.read_text() == before


def test_freeze_refuses_a_committed_hash_that_disagrees(tree: Path, anchor: Path,
                                                        capsys):
    """The binding: what was approved is what may be frozen.

    The approval lives ONLY in the commit, and the working copy is scrubbed of it
    before the freeze. Measured rather than assumed: with the line left on disk
    this test passed against the wire 2 `cmd_freeze`, which refused any anchor
    whose WORKING copy already recorded a hash, so it would have been green
    before a line of this slice existed and asserted nothing about the binding.
    """
    gate = _init_gate_repo(anchor)
    anchor.write_text(f"canopus-anchor: {'e' * 64}\n", encoding="utf-8")
    _git(gate, "add", anchor.name)
    _git(gate, "commit", "-q", "-m", "approve")
    anchor.write_text("# gate\n", encoding="utf-8")

    assert _freeze(tree, anchor) == 1
    err = capsys.readouterr().err
    assert "e" * 64 in err
    assert not (tree / ".canopus" / "freeze.json").exists()


def test_the_freeze_refusal_names_both_hashes_under_their_own_labels(
    tree: Path, anchor: Path, capsys
):
    """A refusal that prints two bare digests cannot be acted on.

    The sibling above pins that the COMMITTED hash reaches the message. Stripping
    the `approved` and `computed` labels leaves both digests in the text and
    every other assertion in this file green, and the operator is handed two
    64-character strings with nothing saying which one they approved and which
    one this freeze would have taken. Measured: with the labels removed, the
    whole ordinary suite stayed green.
    """
    gate = _init_gate_repo(anchor)
    anchor.write_text(f"canopus-anchor: {'e' * 64}\n", encoding="utf-8")
    _git(gate, "add", anchor.name)
    _git(gate, "commit", "-q", "-m", "approve")
    anchor.write_text("# gate\n", encoding="utf-8")   # the approval lives only in HEAD

    assert _freeze(tree, anchor) == 1

    lines = [line.strip() for line in capsys.readouterr().err.splitlines()]
    assert f"approved  {'e' * 64}" in lines
    computed = next(line for line in lines if line.startswith("computed  "))
    assert len(computed.split()[1]) == 64, "a truncated digest is not a report"


def test_freeze_refuses_a_committed_hash_that_is_a_prefix_of_the_computed_root(
    tree: Path, anchor: Path, capsys
):
    """The comparison is over FULL digests, and the two values here agree from
    character 0 for 32 characters.

    A refusal written as `not computed.startswith(committed)` passes the sibling
    test above, whose committed value is 64 e's and shares no first character with
    any real digest. A builder with a shell can brute-force a short prefix by
    appending whitespace to a frozen file, so a truncated anchor that looks
    rigorous and is not is worse than no anchor at all.

    The working copy is scrubbed after the commit for the reason the sibling
    names: the approval has to be reachable only through git, or the refusal
    under test is not the one that fired.
    """
    gate = _init_gate_repo(anchor)

    assert _run(["approve", "tests/test_alpha.py", "--label", "l",
                 "--anchor", str(anchor)], tree) == 0
    approved = _recorded(anchor)
    assert len(approved) == 64

    truncated = approved[:32]
    anchor.write_text(f"# gate\n\ncanopus-anchor: {truncated}\n", encoding="utf-8")
    _git(gate, "add", anchor.name)
    _git(gate, "commit", "-q", "-m", "a truncated approval")
    anchor.write_text("# gate\n", encoding="utf-8")
    capsys.readouterr()

    assert _freeze(tree, anchor) == 1
    err = capsys.readouterr().err
    assert truncated in err
    assert not (tree / ".canopus" / "freeze.json").exists()


def test_freeze_takes_a_committed_approval_the_working_copy_no_longer_carries(
    tree: Path, anchor: Path, capsys
):
    """The positive guarantee of the whole split: the COMMIT alone is enough.

    Every other refusal test here binds the negative case. Narrowing the permit
    rule to the working copy alone, `manifest["root"] == working_hash`, passes
    this file and the frozen contract with it, which measured that the guarantee
    the repository governs had no test at all. Under that narrowing this scenario
    exits 1 with "the committed approval does not match what this freeze would
    take".

    So the approval is scrubbed from the working file after the commit and is
    reachable only through git: the freeze proceeds, takes exactly the approved
    root, prints neither amber line, and `verify` reads LOCK HELD off HEAD.
    """
    gate = _init_gate_repo(anchor)

    assert _run(["approve", "tests/test_alpha.py", "--label", "demo",
                 "--anchor", str(anchor)], tree) == 0
    approved = _recorded(anchor)
    assert len(approved) == 64, "a truncated digest is not an approval"
    _git(gate, "add", anchor.name)
    _git(gate, "commit", "-q", "-m", "the approval")

    anchor.write_text("# gate artifact\n")   # the approval now lives only in HEAD
    assert "canopus-anchor:" not in anchor.read_text()
    capsys.readouterr()

    assert _freeze(tree, anchor) == 0
    out = capsys.readouterr().out
    assert _root_of(tree) == approved
    assert "approval unverified" not in out
    assert "approval uncommitted" not in out

    assert _run(["verify"], tree) == 0
    verified = capsys.readouterr().out
    assert canopus.LOCK_HELD in verified
    assert canopus.APPROVED in verified


def test_freeze_proceeds_without_a_verifiable_approval_and_says_so(
    tree: Path, anchor: Path, capsys
):
    """An operator whose gate artifact is a file in a folder still gets a lock,
    and the amber line is what stops that lock reading like an approved one.

    The line is pinned here because the branch that prints it can be wrapped in
    `if False and ...` without failing anything else: the exit code, the
    manifest, and every sibling assertion survive it, and the operator silently
    loses the one sentence that separates a lock a human approved from one
    nobody did.
    """
    assert _run(["freeze", "--label", "l", "--anchor", str(anchor),
                 "tests/test_alpha.py"], tree) == 0
    out = capsys.readouterr().out
    assert "approval unverified" in out
    assert f"{NO_REPO}: this freeze was taken without a committed approval" in out
    assert "approval uncommitted" not in out


def test_the_freeze_ledger_entry_records_the_approval_posture(tree: Path, anchor: Path):
    """With no contract note, `reason` carries the git status token.

    The conflation of "reason" with "status" is deliberate and stays: this
    ledger's `reason` is a free-form "why this entry looks like this" string
    rather than a typed cause, and `verify_fail` already writes a lock-state
    token into the same field. Separating them needs a new key in
    `append_history`, which lives in the module the PreToolUse dispatcher loads
    on every write. Unpinned it was free to drift, so it is pinned.

    The other half, where a contract note wins the field, is pinned by
    `test_freeze_contract_reports_how_much_is_already_green`.
    """
    assert _freeze(tree, anchor) == 0
    entry = _ledger(tree)[-1]
    assert entry["event"] == "freeze"
    assert entry["reason"] == NO_REPO


def test_freeze_takes_a_red_lock_rather_than_none_when_only_the_commit_is_missing(
    tree: Path, anchor: Path, capsys
):
    """The re-baseline window, and why it is a permit rather than a refusal.

    A refused freeze writes NO manifest, and `freeze_gate` returns 0 in silence
    when no freeze is active. So refusing here hands the operator who edited a
    contract the one outcome worse than a red lock: no lock at all, and a suite
    that passes. Taking the freeze leaves an ACTIVE manifest whose committed
    approval disagrees, which reddens `verify` and every pytest session until a
    human commits the new approval.

    The permit is narrow: it needs the artifact to already record EXACTLY what
    this freeze would take. With no such candidate the sibling refusals above
    still fire, and the frozen contract pins the same behaviour from the lock
    axis.
    """
    gate = _init_gate_repo(anchor)
    assert _run(["approve", "tests/test_alpha.py", "--label", "l",
                 "--anchor", str(anchor)], tree) == 0
    first = _recorded(anchor)
    _git(gate, "add", anchor.name)
    _git(gate, "commit", "-q", "-m", "the approval")

    assert _freeze(tree, anchor) == 0
    assert _run(["release", "--window", "--reason", "re-baseline"], tree) == 0

    (tree / "tests" / "test_alpha.py").write_text("def test_a():\n    assert False\n")
    assert _run(["approve", "tests/test_alpha.py", "--label", "l",
                 "--anchor", str(anchor), "--replace", "--reason", "edited",
                 "--cause", "contract-strengthened"],
                tree) == 0
    second = _recorded(anchor)
    assert second != first
    capsys.readouterr()

    # Deliberately NOT committed: the candidate is on the artifact only.
    assert _freeze(tree, anchor) == 0
    out = capsys.readouterr().out
    assert "approval uncommitted" in out
    assert first in out and second in out

    assert (tree / ".canopus" / "freeze.json").exists()
    assert _run(["verify"], tree) == 1
    assert canopus.LOSS_OF_LOCK in capsys.readouterr().out


# ============================================================
# The null-stub probe: redness that means something
# ============================================================

def _write_vacuous_contract(tree: Path, real: bool = False) -> Path:
    """A contract whose tests all die on an absent import.

    With real=True one of them asserts a value a MagicMock cannot satisfy, so it
    stays red under the stub and the contract is not wholly vacuous.
    """
    directory = tree / "tests" / "contract" / "slice"
    directory.mkdir(parents=True, exist_ok=True)
    second = "assert answer() == 42" if real else "assert answer() is not None"
    (directory / "test_contract.py").write_text(
        'def test_vacuous():\n'
        '    """SC-1."""\n'
        "    from absent_thing import answer\n"
        "    assert answer() is not None\n"
        "\n\n"
        "def test_other():\n"
        "    from absent_thing import answer\n"
        f"    {second}\n"
    )
    return directory


def test_probe_lists_the_tests_that_assert_nothing(tree, capsys):
    _write_vacuous_contract(tree, real=True)

    _run(["probe", "tests/contract/slice"], tree)
    out = capsys.readouterr().out

    assert "asserts nothing" in out
    assert "test_vacuous" in out


def test_probe_labels_how_a_red_test_failed(tree, capsys):
    """The operator's first question is whether anything failed for a reason
    other than the code being absent.

    Its own contract rather than `_write_contract`, because that helper's red test
    now dies on an absent import and would be labelled `import` on every run: this
    test needs one red test of each kind side by side to show the label
    discriminating.
    """
    directory = tree / "tests" / "contract" / "slice"
    directory.mkdir(parents=True)
    (directory / "test_contract.py").write_text(
        "def test_asserts():\n"
        "    assert 1 == 2\n"
        "\n\n"
        "def test_imports():\n"
        "    from absent_thing import answer\n"
        "    assert answer() == 42\n"
    )

    _run(["probe", "tests/contract/slice"], tree)
    out = capsys.readouterr().out

    rows = {
        name: next(line for line in out.splitlines() if name in line)
        for name in ("test_asserts", "test_imports")
    }
    assert "assertion" in rows["test_asserts"]
    assert "import" in rows["test_imports"]


def test_freeze_refuses_a_wholly_vacuous_contract(tree, anchor, capsys):
    """The bite: wire 2 froze this contract, because every test was red."""
    _write_vacuous_contract(tree)

    assert _run(["freeze", "--label", "demo", "--anchor", str(anchor),
                 "--contract", "tests/contract/slice"], tree) == 1
    assert "asserts nothing" in capsys.readouterr().err
    assert not (tree / ".canopus" / "freeze.json").exists()


def test_freeze_takes_a_contract_that_still_asserts_something(tree, anchor):
    """The refusal must not swallow the ordinary mid-build contract."""
    _write_vacuous_contract(tree, real=True)

    assert _run(["freeze", "--label", "demo", "--anchor", str(anchor),
                 "--contract", "tests/contract/slice"], tree) == 0


def test_probe_does_not_call_an_already_green_test_vacuous(tree, capsys):
    """`vacuous` holds every test that passed under the stub, and a test that
    passed for REAL is in it too. Labelling that one "asserts nothing" is a false
    claim about a test asserting real behaviour, and it hides the already-green
    reading the operator is told to question."""
    directory = tree / "tests" / "contract" / "slice"
    directory.mkdir(parents=True)
    # The green case asserts against the standard library, so it passes for a
    # reason no environment can take away, and the stub never touches it.
    (directory / "test_contract.py").write_text(
        "def test_green_for_real():\n"
        "    import json\n"
        "    assert json.dumps({'a': 1}) == '{\"a\": 1}'\n"
        "\n\n"
        "def test_vacuous():\n"
        "    from absent_thing import answer\n"
        "    assert answer() is not None\n"
    )

    _run(["probe", "tests/contract/slice"], tree)
    out = capsys.readouterr().out

    green = next(line for line in out.splitlines() if "test_green_for_real" in line)
    assert "passed" in green
    assert "asserts nothing" not in green
    assert "asserts nothing" in next(
        line for line in out.splitlines() if "test_vacuous" in line
    )


def _write_vacuous_contract_plus(tree: Path, tail: str) -> Path:
    """A wholly vacuous contract with one extra test appended verbatim.

    Every test that is RED here dies on the same absent import and passes the
    moment a mock stands in for it, so the refusal is owed. What *tail* adds is
    a test that is neither red nor green, which is the shape the refusal's old
    `outcome != "passed"` filter could not survive.
    """
    directory = tree / "tests" / "contract" / "slice"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "test_contract.py").write_text(
        "import pytest\n"
        "\n\n"
        'def test_vacuous():\n'
        '    """SC-1."""\n'
        "    from absent_thing import answer\n"
        "    assert answer() is not None\n"
        "\n\n"
        "def test_other():\n"
        "    from absent_thing import answer\n"
        "    assert answer() is not None\n"
        "\n\n"
        f"{tail}"
    )
    return directory


def test_one_skipped_test_does_not_buy_a_vacuous_contract_a_freeze(
    tree, anchor, capsys,
):
    """Fail-open, measured: this contract froze at exit 0 before the fix.

    A skipped test is never in `vacuous`, so under `outcome != "passed"` it
    joined `cases` as a member that could not be matched, the subset failed, and
    the refusal went silent over a contract that asserts nothing at all.
    """
    _write_vacuous_contract_plus(
        tree,
        "@pytest.mark.skip(reason='parked until the module lands')\n"
        "def test_parked():\n    assert False\n",
    )

    assert _run(["freeze", "--label", "demo", "--anchor", str(anchor),
                 "--contract", "tests/contract/slice"], tree) == 1
    assert "asserts nothing" in capsys.readouterr().err
    assert not (tree / ".canopus" / "freeze.json").exists()


def test_one_xfail_test_does_not_buy_a_vacuous_contract_a_freeze(
    tree, anchor, capsys,
):
    """The same escape hatch under its other name.

    xunit1 writes an expected failure as a `skipped` child, so `xfail` reached
    the old filter exactly as `skip` did, and bought the same exit 0.
    """
    _write_vacuous_contract_plus(
        tree,
        "@pytest.mark.xfail(reason='not implemented yet')\n"
        "def test_expected(): \n    assert False\n",
    )

    assert _run(["freeze", "--label", "demo", "--anchor", str(anchor),
                 "--contract", "tests/contract/slice"], tree) == 1
    assert "asserts nothing" in capsys.readouterr().err
    assert not (tree / ".canopus" / "freeze.json").exists()


def test_freeze_refuses_the_from_none_bypass(tree, anchor, capsys):
    """The bypass this slice closes, at the CLI seam.

    This replaces test_freeze_says_so_when_vacuity_could_not_be_measured, which
    asserted the freeze was TAKEN here and only reported having measured nothing.
    That was true of the revision that read the child's failure text to decide
    what to stub: `from None` erased the text. The stub set comes from the AST
    now, so the contract's one test passes under both stubs, earns the vacuity
    label, and the freeze is refused.
    """
    directory = tree / "tests" / "contract" / "slice"
    directory.mkdir(parents=True)
    (directory / "test_contract.py").write_text(
        'def test_vacuous():\n'
        '    """SC-1."""\n'
        "    try:\n"
        "        from absent_thing import answer\n"
        "    except ImportError:\n"
        "        raise AssertionError('not implemented yet') from None\n"
        "    assert answer() is not None\n"
    )

    assert _run(["freeze", "--label", "demo", "--anchor", str(anchor),
                 "--contract", "tests/contract/slice"], tree) == 1
    assert "asserts nothing" in capsys.readouterr().err
    assert not (tree / ".canopus" / "freeze.json").exists()


def _write_conftest_fixture_contract(tree) -> Path:
    """Escape shape (a): the contract's only absent import is in its conftest.

    Building the subject in a fixture is ordinary pytest, and the AST reader
    globbed `test_*.py` only, so the claim set came back empty, nothing was
    stubbed, and the probe returned a verdict it had never taken. Measured
    through this CLI before the fix: `probe` exited 0 printing no vacuity word at
    all, and `freeze` exited 0 and wrote the manifest.
    """
    directory = tree / "tests" / "contract" / "slice"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "conftest.py").write_text(
        "import pytest\n"
        "\n\n"
        "@pytest.fixture\n"
        "def widget():\n"
        "    from absent_thing import Widget\n"
        "    return Widget()\n"
    )
    (directory / "test_contract.py").write_text(
        'def test_widget_exists(widget):\n'
        '    """SC-1."""\n'
        "    assert widget is not None\n"
    )
    return directory


def test_freeze_refuses_a_vacuous_test_built_in_a_conftest_fixture(
    tree, anchor, capsys,
):
    _write_conftest_fixture_contract(tree)

    assert _run(["freeze", "--label", "demo", "--anchor", str(anchor),
                 "--contract", "tests/contract/slice"], tree) == 1
    assert "asserts nothing" in capsys.readouterr().err
    assert not (tree / ".canopus" / "freeze.json").exists()


def test_probe_names_a_vacuous_test_built_in_a_conftest_fixture(tree, capsys):
    _write_conftest_fixture_contract(tree)

    assert _run(["probe", "tests/contract/slice"], tree) == 1
    out = capsys.readouterr().out
    assert "asserts nothing" in next(
        line for line in out.splitlines() if "test_widget_exists" in line
    )


def test_freeze_still_takes_a_real_contract_whose_fixture_is_in_a_conftest(
    tree, anchor,
):
    """The other direction, because a gate that refuses good contracts is routed
    around, and one that is routed around proves nothing while looking as though
    it does.

    The assertion is `len(...) == 0`, which is the container assertion the two
    stub value sets exist to separate: it passes under the set whose length is 0
    and fails under the one whose length is 7, so its outcome moves with the value
    and it asserts something after all. Reading the conftest makes that
    measurement possible; it must not make it an accusation.
    """
    directory = tree / "tests" / "contract" / "slice"
    directory.mkdir(parents=True)
    (directory / "conftest.py").write_text(
        "import pytest\n"
        "\n\n"
        "@pytest.fixture\n"
        "def widget():\n"
        "    from absent_thing import Widget\n"
        "    return Widget()\n"
    )
    (directory / "test_contract.py").write_text(
        'def test_widget_reports_zero(widget):\n'
        '    """SC-1."""\n'
        "    assert len(widget.items()) == 0\n"
    )

    assert _run(["freeze", "--label", "demo", "--anchor", str(anchor),
                 "--contract", "tests/contract/slice"], tree) == 0


def _write_importless_contract(tree) -> Path:
    """Escape shape (b): a red contract whose source names no module at all.

    Nothing is stubbed, so the probe measures nothing, and the empty set it
    returned was indistinguishable from a completed measurement that found
    nothing vacuous. Measured before the fix: `probe` exited 0 and `freeze` wrote
    the manifest.
    """
    directory = tree / "tests" / "contract" / "slice"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "test_contract.py").write_text(
        'def test_a():\n    """SC-1."""\n    assert 1 == 2\n')
    return directory


def test_freeze_refuses_a_contract_that_names_no_module(tree, anchor, capsys):
    _write_importless_contract(tree)

    assert _run(["freeze", "--label", "demo", "--anchor", str(anchor),
                 "--contract", "tests/contract/slice"], tree) == 1
    err = capsys.readouterr().err
    assert "NOT measured" in err
    assert not (tree / ".canopus" / "freeze.json").exists()


def test_approve_refuses_a_contract_that_names_no_module(tree, anchor, capsys):
    """`approve` is the surface a human commits from, so the refusal has to land
    here too rather than only at the freeze that follows it."""
    _write_importless_contract(tree)

    assert _run(["approve", "--label", "demo", "--anchor", str(anchor),
                 "--contract", "tests/contract/slice"], tree) == 1
    assert "NOT measured" in capsys.readouterr().err
    assert "canopus-anchor" not in anchor.read_text()


def test_probe_refuses_a_contract_that_names_no_module(tree, capsys):
    """And it is a refusal, not a traceback: the CLI turns ContractError into an
    operator-visible line on every one of the three surfaces."""
    _write_importless_contract(tree)

    assert _run(["probe", "tests/contract/slice"], tree) == 1
    out = capsys.readouterr().out
    assert "NOT measured" in out
    assert "vacuity UNKNOWN" in next(
        line for line in out.splitlines() if "test_a" in line
    )


def test_freeze_refuses_when_the_vacuity_probe_cannot_run(
    tree, anchor, monkeypatch, capsys,
):
    """A measurement that could not happen is a refusal, not a pass."""
    from scripts.utils.canopus_contract import ContractError

    def _explode(*args, **kwargs):
        raise ContractError("pytest wrote no JUnit report")

    monkeypatch.setattr(canopus, "run_null_stub", _explode)
    _write_contract(tree, red=True)

    assert _run(["freeze", "--label", "demo", "--anchor", str(anchor),
                 "--contract", "tests/contract/slice"], tree) == 1
    assert "could not be measured" in capsys.readouterr().err
    assert not (tree / ".canopus" / "freeze.json").exists()


def test_probe_refuses_when_the_vacuity_probe_cannot_run(
    tree, monkeypatch, capsys,
):
    """The same unmeasurable state on the surface the operator reads.

    `probe` runs the stub session unconditionally, so it meets this failure
    first. Two things have to hold and only one of them is the message. The
    per-test table reads `vacuous` twenty lines below the call and
    `vacuity_refusal` reads it again at the end, so an except branch that only
    printed left both reading an UNBOUND name and killed the command with an
    `UnboundLocalError` on exactly the failure it was added to report. An empty
    `vacuous` must also never be weighed as a verdict: a real `cases` set against
    an empty one finds no subset and returns [], which is the silent "measured,
    and nothing was vacuous" reading this refusal exists to remove.
    """
    from scripts.utils.canopus_contract import ContractError

    def _explode(*args, **kwargs):
        raise ContractError("pytest wrote no JUnit report")

    monkeypatch.setattr(canopus, "run_null_stub", _explode)
    _write_vacuous_contract(tree)

    assert _run(["probe", "tests/contract/slice"], tree) == 1
    out = capsys.readouterr().out
    assert "could not be measured" in out
    # The table still rendered: the failure is reported, not raised through it.
    assert "test_vacuous" in out


def test_probe_marks_every_red_row_unknown_when_the_probe_failed(
    tree, monkeypatch, capsys,
):
    """The table has to say it too, not only the trailing refusal.

    With `vacuous` empty every red row printed its ordinary failure line and
    nothing on it marked vacuity as unmeasured, so an operator who read to the
    end was told and one who skimmed the rows was not. The rows are the surface
    this command exists to be read from.
    """
    from scripts.utils.canopus_contract import ContractError

    def _explode(*args, **kwargs):
        raise ContractError("pytest wrote no JUnit report")

    monkeypatch.setattr(canopus, "run_null_stub", _explode)
    _write_vacuous_contract(tree, real=True)

    assert _run(["probe", "tests/contract/slice"], tree) == 1
    lines = capsys.readouterr().out.splitlines()
    rows = [
        next(line for line in lines if name in line)
        for name in ("test_vacuous", "test_other")
    ]
    assert all("vacuity UNKNOWN" in row for row in rows)


def test_probe_leaves_a_green_row_alone_when_the_probe_failed(
    tree, monkeypatch, capsys,
):
    """UNKNOWN belongs on the rows the verdict would have judged, and no others.

    Only RED tests are weighed by `vacuity_refusal`, so a green row's vacuity was
    never going to be reported either way; stamping it UNKNOWN would invent a
    missing measurement rather than name one.
    """
    from scripts.utils.canopus_contract import ContractError

    def _explode(*args, **kwargs):
        raise ContractError("pytest wrote no JUnit report")

    monkeypatch.setattr(canopus, "run_null_stub", _explode)
    directory = tree / "tests" / "contract" / "slice"
    directory.mkdir(parents=True)
    (directory / "test_contract.py").write_text(
        "def test_green_for_real():\n"
        "    import json\n"
        "    assert json.dumps({'a': 1}) == '{\"a\": 1}'\n"
        "\n\n"
        "def test_red():\n"
        "    from absent_thing import answer\n"
        "    assert answer() is not None\n"
    )

    assert _run(["probe", "tests/contract/slice"], tree) == 1
    lines = capsys.readouterr().out.splitlines()
    green = next(line for line in lines if "test_green_for_real" in line)
    red = next(line for line in lines if "test_red" in line)
    assert "vacuity UNKNOWN" not in green
    assert "vacuity UNKNOWN" in red


# ============================================================
# The probe is weighed against the population the caller measured
# ============================================================

def _population_spy(monkeypatch) -> list[dict]:
    """Capture the keyword arguments every `run_null_stub` call was given.

    Asserted at the CALL rather than by reading the source, because
    `expected_population` carries a default: a caller that stops passing it
    raises nothing, fails no type check, and silently sends `run_null_stub` off
    to run its OWN unstubbed baseline. The probe's lost-test guard then measures
    one pytest session while the caller applies the verdict to another, and
    nothing holds the two populations equal.
    """
    seen: list[dict] = []

    def _spy(paths, root, **kwargs):
        seen.append(kwargs)
        return set()

    monkeypatch.setattr(canopus, "run_null_stub", _spy)
    return seen


_REAL_POPULATION = [
    ("tests/contract/slice/test_contract.py", "test_a", "failure"),
    ("tests/contract/slice/test_contract.py", "test_b", "passed"),
]


def test_freeze_hands_the_probe_the_real_runs_population(tree, anchor, monkeypatch):
    """The freeze seam passes its OWN run's triples, not counts and not a shrug.

    The green `test_b` is in the expected value on purpose: per-file counts and
    the red-only subset both collapse it, so an assertion over the full triples
    is what tells the three apart.
    """
    seen = _population_spy(monkeypatch)
    _write_contract(tree)   # test_a asserts False, test_b passes

    assert _run(["freeze", "--label", "demo", "--anchor", str(anchor),
                 "--contract", "tests/contract/slice"], tree) == 0

    assert len(seen) == 1
    assert sorted(seen[0]["expected_population"]) == _REAL_POPULATION


def test_probe_hands_the_probe_the_real_runs_population(tree, monkeypatch):
    """The same pin on the other call site; two seams, two chances to forget."""
    seen = _population_spy(monkeypatch)
    _write_contract(tree)

    assert _run(["probe", "tests/contract/slice"], tree) == 0

    assert len(seen) == 1
    assert sorted(seen[0]["expected_population"]) == _REAL_POPULATION


def test_the_null_stub_does_not_run_once_the_contract_is_already_refused(
    tree, anchor, monkeypatch, capsys,
):
    """A whole second pytest session, paid for an answer thrown away.

    `run_null_stub` can only ADD to a refusal, so a contract that collected
    nothing has already earned its exit 1 from the first run.
    """
    directory = tree / "tests" / "contract" / "slice"
    directory.mkdir(parents=True)
    # Module-scope import: the file collects nothing, caught by the first run.
    (directory / "test_contract.py").write_text(
        "from absent_thing import answer\n\n\n"
        'def test_a():\n    """SC-1."""\n    assert answer() == 42\n'
    )
    calls = []

    def _record(*args, **kwargs):
        calls.append(args)
        return set()

    monkeypatch.setattr(canopus, "run_null_stub", _record)

    assert _run(["freeze", "--label", "demo", "--anchor", str(anchor),
                 "--contract", "tests/contract/slice"], tree) == 1

    assert calls == []
    assert "collected nothing" in capsys.readouterr().err


def test_the_null_stub_does_not_run_when_a_RED_contract_is_already_refused(
    tree, anchor, monkeypatch, capsys,
):
    """The half of that skip the single-file case cannot reach.

    The sibling above refuses a contract that is not red at all, so `red` alone
    would have skipped the session too and the `not reasons` half of the
    condition is never weighed. Two files separate them: one collects nothing and
    earns the refusal, the other is genuinely red. Only `not reasons` stops the
    session here, and it is a whole pytest run — three, now — spent on a verdict
    that cannot change an exit code already earned.
    """
    directory = tree / "tests" / "contract" / "slice"
    directory.mkdir(parents=True)
    # Module-scope import: this file collects nothing and earns the refusal.
    (directory / "test_broken.py").write_text(
        "from absent_thing import answer\n\n\n"
        'def test_a():\n    """SC-1."""\n    assert answer() == 42\n'
    )
    # And this one is red, so the contract as a whole is.
    (directory / "test_red.py").write_text("def test_b():\n    assert False\n")
    calls = []

    def _record(*args, **kwargs):
        calls.append(args)
        return set()

    monkeypatch.setattr(canopus, "run_null_stub", _record)

    assert _run(["freeze", "--label", "demo", "--anchor", str(anchor),
                 "--contract", "tests/contract/slice"], tree) == 1

    assert calls == []
    assert "collected nothing" in capsys.readouterr().err


# ============================================================
# The ledger guard, as a class rather than one function at a time
# ============================================================

def _ledger_raises(monkeypatch, message: str = "ledger is full"):
    """Make every append_history call in the CLI raise OSError.

    An injected error rather than a read-only directory, because `cmd_freeze`
    writes the manifest into the same `.canopus/` a chmod would close, so the
    chmod route never reaches the ledger line this guard is about.
    """
    def failing(root, event, **kwargs):
        raise OSError(message)

    monkeypatch.setattr(canopus, "append_history", failing)


def test_freeze_reports_the_active_lock_when_only_the_ledger_fails(
    tree, anchor, capsys, monkeypatch
):
    """The freeze IS taken by the time the ledger can fail, so a total-failure
    message is false of the state.

    Unguarded, the OSError fell through to `main`, which printed "the frozen
    contract could not be read, so it cannot be verified" over a live freeze and
    sent the operator into a re-run that now refuses with "a freeze is already
    active". The second consequence is quieter and worse: `freeze_windows` opens
    a window on this ledger entry, so with the entry missing `canopus pack`
    reports every commit made under this lock as made outside it.
    """
    _ledger_raises(monkeypatch)

    assert _freeze(tree, anchor) == 1

    err = capsys.readouterr().err
    assert "could not be read" not in err
    assert "IS ACTIVE" in err
    assert "`freeze` ledger entry failed" in err
    assert "pack" in err
    # The escape has to PARSE. This is the one branch whose entire point is that
    # the printed sentence is true of the state — an active, enforcing freeze
    # plus the command that clears it — and `release --reason` stopped parsing
    # the moment the kind became required, so the sentence sent the operator to
    # an exit 2.
    assert "release --window --reason" in err
    # Parsed, not merely matched: the string an operator would copy is fed to
    # the real parser, which exits 2 on a release that names no kind.
    printed = re.search(r"`release ([^`]*)`", err).group(1)
    canopus.build_parser().parse_args(["release", *shlex.split(printed)])

    # The claim the message makes, checked against disk and against the tool.
    assert (tree / ".canopus" / "freeze.json").exists()
    assert not (tree / ".canopus" / "history.jsonl").exists()
    assert _run(["status"], tree) == 0
    assert canopus.LOCK_UNCONFIRMED in capsys.readouterr().out


def test_a_release_the_ledger_could_not_record_is_refused(
    tree, anchor, capsys, monkeypatch
):
    """The sibling of the freeze guard, and the reason this is a class.

    Both release paths log BEFORE clearing, so a failed ledger write leaves the
    freeze standing. Clearing anyway would end a freeze with no line saying it
    ended, which is exactly the gap deleting the manifest by hand leaves.

    The render is taken BEFORE the ledger is broken, and the ordering carries
    the point. From 2026-08-03 the `--ship` arm meets the ship-evidence gate
    first, so without a render this test would pass on the wrong refusal: the
    right message about the wrong failure. Rendering first puts the ledger's own
    write failure back in front of the assertion.
    """
    assert _freeze(tree, anchor) == 0
    assert _run(["pack"], tree) == 0
    capsys.readouterr()
    _ledger_raises(monkeypatch)

    for argv in (["release", "--ship", "--reason", "done"],
                 ["release", "--force", "--window", "--reason", "damaged"]):
        assert _run(argv, tree) == 1, argv
        err = capsys.readouterr().err
        assert "could not be read" not in err, argv
        assert "NOTHING was released" in err, argv
        assert (tree / ".canopus" / "freeze.json").exists(), argv


def test_a_failed_verify_ledger_entry_does_not_contradict_the_lock_report(
    tree, anchor, capsys, monkeypatch
):
    """`verify` already printed the per-file report and already failed closed.

    Unguarded, the last sentence an operator read on a genuinely broken lock was
    "the frozen contract could not be read", which reads as though the state
    above it had never been established.
    """
    assert _freeze(tree, anchor) == 0
    (tree / "tests" / "test_alpha.py").write_text("def test_a():\n    assert False\n")
    capsys.readouterr()
    _ledger_raises(monkeypatch)

    assert _run(["verify"], tree) == 1

    captured = capsys.readouterr()
    assert canopus.LOSS_OF_LOCK in captured.out
    assert "could not be read" not in captured.err
    assert "`verify_fail` ledger entry failed" in captured.err


# ============================================================
# A skipped contract test is visible, and is not called vacuous
# ============================================================

def test_probe_shows_a_skipped_test_as_skipped_rather_than_vacuous(tree, capsys):
    """`pytest.importorskip` is an ordinary idiom, and nothing refuses a skipped
    contract test.

    The display filter was `outcome != "passed"` while `vacuity_refusal` filters
    on `outcome in RED_OUTCOMES`, so a skipped test landed in the vacuous branch:
    it is in `vacuous` because the stub supplies the module it skipped on, and
    the `continue` swallowed the only line that would have said it never ran. The
    one surface the operator is told to read reclassified it into the bucket they
    are invited to strike off by eye.
    """
    directory = tree / "tests" / "contract" / "slice"
    directory.mkdir(parents=True)
    (directory / "test_contract.py").write_text(
        "import pytest\n\n\n"
        "def test_parked():\n"
        "    answer = pytest.importorskip('absent_thing').answer\n"
        "    assert answer() is not None\n"
        "\n\n"
        "def test_red():\n"
        "    from absent_thing import answer\n"
        "    assert answer() == 42\n"
    )

    _run(["probe", "tests/contract/slice"], tree)
    out = capsys.readouterr().out

    parked = next(line for line in out.splitlines() if "test_parked" in line)
    assert "skipped" in parked
    assert "vacuous" not in parked
    assert "asserts nothing" not in parked
    # No invented failure mode either: a skipped test carries no failure child,
    # so the heuristic would have defaulted it to `other`.
    assert "other" not in parked


# ============================================================
# The --replace reason reaches the artifact, not only the ledger
# ============================================================

def test_the_replace_reason_is_written_to_the_artifact(tree: Path, anchor: Path):
    """`.canopus/history.jsonl` is gitignored and one command removes it.

    Without the reason on the artifact, an operator reading the diff a human
    commits sees two indistinguishable hash lines and no account of either.
    """
    from scripts.utils.canopus_freeze import ANCHOR_RECORDED, read_anchor

    assert _run(["approve", "tests/test_alpha.py", "--label", "l",
                 "--anchor", str(anchor)], tree) == 0
    (tree / "tests" / "test_beta.py").write_text("def test_b():\n    assert True\n")
    assert _run(["approve", "tests/test_alpha.py", "tests/test_beta.py",
                 "--label", "l", "--anchor", str(anchor),
                 "--replace", "--reason", "the beta case joined the set",
                 "--cause", "frozen-set-wrong"], tree) == 0

    text = anchor.read_text()
    assert "the beta case joined the set" in text
    assert _ledger(tree)[-1]["reason"] == "the beta case joined the set"

    # The reason is on its OWN line, and the parser is untouched by it. Both
    # halves matter: read_anchor takes everything after the prefix as the hash,
    # so a reason on that line would corrupt the value every later comparison
    # rests on.
    status, value = read_anchor(anchor)
    assert status == ANCHOR_RECORDED
    assert value == _recorded(anchor)
    assert len(value) == 64
    assert int(value, 16) >= 0
    assert not any(
        line.strip().startswith(canopus.ANCHOR_PREFIX)
        and "the beta case joined the set" in line
        for line in text.splitlines()
    )


def test_a_multi_line_reason_cannot_forge_a_second_anchor_line(
    tree: Path, anchor: Path
):
    """The reason is operator text written into the durable record, so it is
    collapsed to one line before it lands. A newline inside it would otherwise
    write a `canopus-anchor:` line this tool never computed, into the one file a
    human reads to see what was approved."""
    from scripts.utils.canopus_freeze import read_anchor

    assert _run(["approve", "tests/test_alpha.py", "--label", "l",
                 "--anchor", str(anchor)], tree) == 0
    approved = _recorded(anchor)
    forged = f"{canopus.ANCHOR_PREFIX} {'e' * 64}"

    assert _run(["approve", "tests/test_alpha.py", "--label", "l",
                 "--anchor", str(anchor), "--replace", "--reason",
                 f"widened\n{forged}", "--cause", "frozen-set-wrong"], tree) == 0

    # No line the operator could read as an approval except the ones approve
    # computed. `read_anchor` takes the LAST such line, so an injected one that
    # landed below the real one would BE the answer.
    lines = [line.strip() for line in anchor.read_text().splitlines()]
    assert forged not in lines
    assert [line for line in lines
            if line.startswith(canopus.ANCHOR_PREFIX)] == [
        f"{canopus.ANCHOR_PREFIX} {approved}"] * 2

    # The set did not change, so the honest answer is the same digest as before.
    _status, value = read_anchor(anchor)
    assert value == approved
    assert len(value) == 64


def _init_anchor_repo(anchor, *, commit: bool) -> None:
    """Make the anchor's directory a repository, optionally with one commit."""
    for argv in (["init", "-q", "-b", "main"],
                 ["config", "user.email", "builder@example.invalid"],
                 ["config", "user.name", "Builder"]):
        subprocess.run(["git", "-C", str(anchor.parent), *argv], check=True,
                       capture_output=True, text=True)
    if commit:
        subprocess.run(["git", "-C", str(anchor.parent), "add", "-A"], check=True,
                       capture_output=True, text=True)
        subprocess.run(["git", "-C", str(anchor.parent), "commit", "-q", "-m", "seed"],
                       check=True, capture_output=True, text=True)


def test_freeze_records_the_anchors_repository(tree, anchor):
    """The binding is built once, in _candidate_manifest, so approve and freeze
    compute the same root. Two copies of that construction is how the approved
    hash and the frozen hash come to differ over a default nobody noticed."""
    from scripts.utils.canopus_git import repo_identity

    _init_anchor_repo(anchor, commit=True)

    assert _freeze(tree, anchor) == 0

    manifest = json.loads((tree / ".canopus" / "freeze.json").read_text())
    _status, identity = repo_identity(anchor.parent)
    assert len(identity) == 64
    assert manifest["anchor_repo"] == {"in_repo": True, "identity": identity}


def test_freeze_records_no_binding_for_an_anchor_outside_a_repository(tree, anchor):
    """The supported plain-folder case, recorded explicitly rather than by
    omission: the freeze itself states that there was no repository to bind to."""
    assert _freeze(tree, anchor) == 0

    manifest = json.loads((tree / ".canopus" / "freeze.json").read_text())
    assert manifest["anchor_repo"] == {"in_repo": False, "identity": ""}


def test_freeze_refuses_an_anchor_in_a_repository_with_no_commits(tree, anchor, capsys):
    """The first commit into an empty repository is the approval act itself, so
    an identity recorded now would change at the exact moment a human approved
    and turn the freeze red for doing the right thing."""
    _init_anchor_repo(anchor, commit=False)

    assert _freeze(tree, anchor) == 1
    assert "has no commits" in capsys.readouterr().err
    assert not (tree / ".canopus" / "freeze.json").exists()


def test_the_repository_refusals_come_before_the_contract_runs(
    tree, anchor, capsys, monkeypatch
):
    """Fail fast, because both refusals are knowable before a single test runs.

    Behind the contract block this refusal cost a full pytest session over the
    contract, plus the null-stub session behind it, before telling the operator
    to go and commit something in another repository. The spy is on the pytest
    runner rather than on a clock: a timing assertion would be flaky, and what is
    actually being asserted is that the expensive thing never happened.
    """
    calls: list = []

    def _spy(paths, root, **kwargs):
        calls.append((tuple(paths), root))
        return ""

    monkeypatch.setattr(canopus, "run_pytest_report", _spy)
    _write_contract(tree)
    _init_anchor_repo(anchor, commit=False)

    assert _run(["freeze", "--label", "demo", "--anchor", str(anchor),
                 "--contract", "tests/contract/slice"], tree) == 1

    assert calls == []
    assert "has no commits" in capsys.readouterr().err


def test_freeze_refuses_when_git_cannot_be_consulted(tree, anchor, capsys, monkeypatch):
    """A freeze taken here would RECORD A LIE, so it is not taken.

    `in_repo` was `repo_status == REPO_PRESENT`, so REPO_UNKNOWN — no git binary
    reachable — wrote `in_repo: false`: the positive claim that the anchor was
    OUTSIDE any repository, which the tool has no evidence for. Every later
    `verify` then read BINDING_BROKEN, "the freeze was taken blind", and blamed
    the blinding rather than naming the cause.
    """
    from scripts.utils.canopus_freeze import REPO_UNKNOWN

    monkeypatch.setattr(canopus, "repo_identity", lambda _d: (REPO_UNKNOWN, ""))

    assert _freeze(tree, anchor) == 1

    err = capsys.readouterr().err
    assert "git could not be consulted" in err
    # The recovery, named. A refusal that does not say what to do next is a
    # refusal an operator routes around.
    assert "on PATH" in err
    assert not (tree / ".canopus" / "freeze.json").exists()


def test_approve_refuses_when_git_cannot_be_consulted(tree, anchor, capsys, monkeypatch):
    """Both commands, because a refusal on one of them is a refusal on neither:
    `approve` builds the candidate through the same builder, so an approval taken
    blind records the same unbound claim the freeze would."""
    from scripts.utils.canopus_freeze import REPO_UNKNOWN

    monkeypatch.setattr(canopus, "repo_identity", lambda _d: (REPO_UNKNOWN, ""))

    assert _run(["approve", "tests/test_alpha.py", "--label", "demo",
                 "--anchor", str(anchor)], tree) == 1
    assert "git could not be consulted" in capsys.readouterr().err
    assert canopus.ANCHOR_PREFIX not in anchor.read_text()


def test_freeze_refuses_a_waiver_the_approval_does_not_carry(tree, anchor, capsys):
    """`canopus pack` renders CONTRACT WAIVED from the ANCHOR, which only
    `approve` writes.

    So `approve` without the flag followed by `freeze --contract-satisfied`
    produced a waived freeze whose evidence page showed no waiver at all, and
    nothing refused the pairing. The waiver survived only in `.canopus/`, which
    is gitignored and which one `rm -rf` removes.
    """
    _write_contract(tree, red=False)
    assert _run(["approve", "--label", "demo", "--anchor", str(anchor),
                 "--contract", "tests/contract/slice",
                 "--contract-satisfied", "the slice implemented it"], tree) == 0
    # The approval is there; the WAIVER line is what is missing, so the artifact
    # is rewritten with the anchor line alone. That is the exact state `approve`
    # without the flag leaves, minus the hash mismatch that would refuse first
    # for a different reason.
    approved = _recorded(anchor)
    # The criteria section rides along: dropping it would refuse this freeze for
    # A12's empty-criteria rule before it ever reached the waiver check, which is
    # a different refusal and not the one under test.
    anchor.write_text("# gate artifact\n\n"
                      "## Phase 1 — Success criteria\n\n"
                      "- **SC-1** WHEN a scratch slice runs, THE SYSTEM SHALL "
                      "behave as the test says.\n\n"
                      f"canopus-anchor: {approved}\n")
    capsys.readouterr()

    assert _run(["freeze", "--label", "demo", "--anchor", str(anchor),
                 "--contract", "tests/contract/slice",
                 "--contract-satisfied", "the slice implemented it"], tree) == 1

    err = capsys.readouterr().err
    assert "records no waiver" in err
    assert not (tree / ".canopus" / "freeze.json").exists()


def test_a_red_contract_is_not_refused_for_a_missing_waiver(tree, anchor, capsys):
    """The refusal is bound to the waiver FIRING, never to the flag's presence.

    On a red contract the flag changes nothing and the command says so, so there
    is no waiver for the evidence page to omit. Refusing here would refuse a run
    that made no claim at all, which is how a guard written against a flag rather
    than against the act ends up denying honest work.
    """
    _write_contract(tree)   # test_a asserts False

    assert _run(["freeze", "--label", "demo", "--anchor", str(anchor),
                 "--contract", "tests/contract/slice",
                 "--contract-satisfied", "the slice implemented it"], tree) == 0
    assert "changed nothing" in capsys.readouterr().out


# ============================================================
# Every printed or documented `release` invocation names a kind
# ============================================================

# Python implicit string concatenation, joined so a command that wraps across two
# source lines is still one command to the scanner below. Without this the escape
# printed by canopus_gate.py — which breaks after `release ` — reads as a bare
# mention and the whole file passes vacuously.
_WRAPPED = re.compile(r'"\s*\n\s*f?"')
_INVOCATION = re.compile(r"release((?:\s+--[a-z-]+)+)")
_ESCAPE_SOURCES = (
    "scripts/canopus.py",
    "scripts/utils/canopus_gate.py",
    ".claude/hooks/_dispatch.py",
    "docs/EXTENDING.md",
)


def test_every_printed_release_invocation_names_a_kind():
    """A class test, because this defect arrived one printed sentence at a time.

    `release` has required `--window` or `--ship` since wire 2.2 Task 6, so any
    surface still printing `release --reason "<why>"` hands the operator a
    command that exits 2. Four of the five escapes were fixed when the kind
    became required and the fifth was not: the one `cmd_freeze` prints when the
    manifest was written and the ledger append then failed, which is the branch
    whose entire point is that the sentence is true of the state.

    Scanning the source rather than asserting five strings, so the sixth surface
    is covered before someone writes it.
    """
    found = 0
    for rel in _ESCAPE_SOURCES:
        text = _WRAPPED.sub("", (canopus.ENGINE_ROOT / rel).read_text(encoding="utf-8"))
        for match in _INVOCATION.finditer(text):
            flags = match.group(1).split()
            found += 1
            assert "--window" in flags or "--ship" in flags, f"{rel}: release {flags}"
    # A floor, so a regex that stops matching cannot pass this test in silence.
    assert found >= 6


def test_the_force_flag_says_the_escape_is_logged(capsys):
    """`release --help` is read while a damaged manifest denies every write.

    An operator there cannot tell a recorded escape from an unrecorded one unless
    the help says so, and that distinction is the entire reason `cmd_release`'s
    success message exists: a forced release is a normal, recorded event, while
    deleting freeze.json by hand leaves a gap in an append-only ledger.
    """
    with pytest.raises(SystemExit):
        canopus.build_parser().parse_args(["release", "--help"])

    assert "logged" in capsys.readouterr().out.lower()


# ============================================================
# `--contract-satisfied`: the named waiver for a RETAKE
# ============================================================
# A retake taken after the slice turned its last contract row green is refused
# by the redness rule, which is right about a first freeze and wrong about this
# one. The workaround it produced was passing the contract directory
# POSITIONALLY, which drops the baseline and with it the attestation's per-file
# subset check, the collected-nothing refusal, the vacuity re-proof, the ledger
# note and the pack's contract section. The flag keeps every one of those and
# waives exactly one reason.


def test_contract_satisfied_waives_the_green_refusal(tree, anchor, capsys):
    """The one reason it waives, and the baseline it keeps by waiving it there.

    The baseline assertion is the point of the flag rather than a bonus: the
    positional workaround it replaces froze the identical set with `baseline:
    {}`.
    """
    _write_contract(tree, red=False)
    # The approve half is not decoration: from wire 2.2 `freeze` refuses a
    # waiver the approval on the artifact does not carry, because `canopus pack`
    # renders CONTRACT WAIVED from that artifact and nowhere else.
    assert _run(["approve", "--label", "demo", "--anchor", str(anchor),
                 "--contract", "tests/contract/slice",
                 "--contract-satisfied", "the slice implemented it"], tree) == 0
    capsys.readouterr()

    assert _run(["freeze", "--label", "demo", "--anchor", str(anchor),
                 "--contract", "tests/contract/slice",
                 "--contract-satisfied", "the slice implemented it"], tree) == 0

    manifest = json.loads((tree / ".canopus" / "freeze.json").read_text())
    assert manifest["baseline"] == {"tests/contract/slice/test_contract.py": 2}
    assert "the slice implemented it" in capsys.readouterr().out


def test_contract_satisfied_waives_the_green_refusal_for_approve_too(tree, anchor):
    """Both commands, because a waiver on one of them is a waiver on neither.

    `approve` computes the candidate hash and `freeze` takes it; a flag that
    changes what one of them accepts, and not the other, produces a candidate
    that the freeze refuses or the reverse.
    """
    _write_contract(tree, red=False)

    assert _run(["approve", "--label", "demo", "--anchor", str(anchor),
                 "--contract", "tests/contract/slice",
                 "--contract-satisfied", "the slice implemented it"], tree) == 0
    assert len(_recorded(anchor)) == 64


def test_contract_satisfied_does_not_waive_a_collected_nothing_refusal(
    tree, anchor, capsys
):
    """The per-file zero-item rule is untouched, by construction not by wording.

    This is what a string filter over the returned list would have got wrong:
    the two reasons are produced by one function, and the only safe way to
    suppress one of them is at the site that produces it.
    """
    directory = tree / "tests" / "contract" / "slice"
    directory.mkdir(parents=True, exist_ok=True)
    # Collects nothing: the import is at module scope, so the module never
    # imports and pytest reports a collection error rather than any item.
    (directory / "test_contract.py").write_text(
        'from absent_thing import answer\n\n\ndef test_a():\n    """SC-1."""\n    assert answer()\n'
    )

    assert _run(["freeze", "--label", "demo", "--anchor", str(anchor),
                 "--contract", "tests/contract/slice",
                 "--contract-satisfied", "the slice implemented it"], tree) == 1
    err = capsys.readouterr().err
    assert "collected nothing" in err
    assert not (tree / ".canopus" / "freeze.json").exists()


def test_contract_satisfied_on_a_red_contract_is_a_no_op_that_says_so(
    tree, anchor, capsys
):
    """A flag whose no-op runs look identical to its live ones becomes a habit.

    So the red case is stated out loud. The freeze still succeeds, because the
    contract earned that on its own redness rather than on the waiver.
    """
    _write_contract(tree)   # test_a asserts False, test_b passes

    assert _run(["freeze", "--label", "demo", "--anchor", str(anchor),
                 "--contract", "tests/contract/slice",
                 "--contract-satisfied", "the slice implemented it"], tree) == 0
    out = capsys.readouterr().out
    assert "--contract-satisfied changed nothing" in out
    assert "this contract is RED" in out


def test_contract_satisfied_reaches_the_ledger(tree, anchor):
    """The waiver is a durable record, not a line that scrolls off the terminal.

    `reason` already carries the contract note, so both halves are asserted: a
    reader of `.canopus/history.jsonl` learns what the contract measured AND why
    a green one was accepted.
    """
    _write_contract(tree, red=False)
    assert _run(["approve", "--label", "demo", "--anchor", str(anchor),
                 "--contract", "tests/contract/slice",
                 "--contract-satisfied", "the slice implemented it"], tree) == 0

    assert _run(["freeze", "--label", "demo", "--anchor", str(anchor),
                 "--contract", "tests/contract/slice",
                 "--contract-satisfied", "the slice implemented it"], tree) == 0

    entry = _ledger(tree)[-1]
    assert entry["event"] == "freeze"
    assert "2 of 2 already green" in entry["reason"]
    assert "the slice implemented it" in entry["reason"]


def test_contract_satisfied_cannot_be_passed_without_a_reason(tree, anchor, capsys):
    """The reason is the VALUE, so argparse refuses a bare flag for us.

    Pinned because the alternative shape, `--contract-satisfied` as a store_true
    beside an optional `--reason`, is the one that ships a waiver nobody has to
    explain.
    """
    _write_contract(tree, red=False)

    with pytest.raises(SystemExit) as excinfo:
        _run(["freeze", "--label", "demo", "--anchor", str(anchor),
              "--contract", "tests/contract/slice", "--contract-satisfied"], tree)

    assert excinfo.value.code != 0
    assert "expected one argument" in capsys.readouterr().err


def test_contract_satisfied_rides_onto_the_anchor_artifact(tree, anchor):
    """The waiver needs a DURABLE record, and the ledger is not one.

    `.canopus/history.jsonl` is gitignored and `rm -rf .canopus` removes it, so a
    waiver recorded only there is a courtesy rather than a guarantee. It goes
    onto the artifact a human commits, beside the approval it belongs to.

    The second assertion is the one that keeps the first safe: the waiver sits on
    its own line under its own prefix, so `read_anchor` still reads a whole
    64-character digest off the anchor line and nothing of the prose above it.
    """
    from scripts.utils.canopus_freeze import SATISFIED_PREFIX, read_anchor_waiver

    _write_contract(tree, red=False)

    assert _run(["approve", "--label", "demo", "--anchor", str(anchor),
                 "--contract", "tests/contract/slice",
                 "--contract-satisfied", "the slice implemented it"], tree) == 0

    text = anchor.read_text()
    assert f"{SATISFIED_PREFIX} the slice implemented it" in text
    assert len(_recorded(anchor)) == 64
    assert read_anchor_waiver(anchor, _recorded(anchor)) == "the slice implemented it"


def test_pack_renders_the_waiver_marker(tree, anchor, capsys):
    """Say it where the operator reads the evidence, not only where it was typed.

    `pack` is the page the second human decision is taken on. A freeze that
    passed the redness rule on a stated reason rather than on its own redness is
    a weaker claim than one that earned it, and a page that omits the difference
    is reporting the stronger claim.
    """
    _write_contract(tree, red=False)
    satisfied = ["--contract-satisfied", "the slice implemented it"]

    assert _run(["approve", "--label", "demo", "--anchor", str(anchor),
                 "--contract", "tests/contract/slice", *satisfied], tree) == 0
    assert _run(["freeze", "--label", "demo", "--anchor", str(anchor),
                 "--contract", "tests/contract/slice", *satisfied], tree) == 0
    capsys.readouterr()

    assert _run(["pack"], tree) == 0
    out = capsys.readouterr().out
    assert "CONTRACT WAIVED" in out
    assert "the slice implemented it" in out


def test_pack_binds_the_waiver_marker_to_this_freezes_root(tree, anchor, capsys):
    """A waiver two retakes old must not be reported against today's freeze.

    So the marker is read by HASH rather than as "the last waiver in the file".
    Here the artifact carries a waiver beside a digest this freeze never took,
    which is what an anchor looks like after one waived retake and one honest
    one.
    """
    from scripts.utils.canopus_freeze import SATISFIED_PREFIX

    assert _freeze(tree, anchor) == 0
    with anchor.open("a", encoding="utf-8") as handle:
        handle.write(f"\n{SATISFIED_PREFIX} a waiver for another freeze\n"
                     f"canopus-anchor: {'c' * 64}\n")
    capsys.readouterr()

    assert _run(["pack"], tree) == 0
    out = capsys.readouterr().out
    assert "CONTRACT WAIVED" not in out
    assert "a waiver for another freeze" not in out


def test_contract_satisfied_skips_the_null_stub_session(tree, anchor, monkeypatch):
    """A pytest session whose answer cannot matter is not run.

    `vacuity_refusal` weighs RED tests only, so on the wholly green contract this
    flag exists for its `cases` set is empty and it returns [] by construction.
    The stub run was still spent, in full, to be discarded a line later.

    The red control is half the test: asserting only the absence of a call would
    pass just as well if the stub were skipped everywhere, which would silently
    retire the vacuity proof.
    """
    calls: list[tuple] = []

    def _spy(paths, root, **kwargs):
        calls.append((tuple(paths), root))
        return set()

    monkeypatch.setattr(canopus, "run_null_stub", _spy)

    _write_contract(tree, red=False)
    assert _run(["approve", "--label", "demo", "--anchor", str(anchor),
                 "--contract", "tests/contract/slice",
                 "--contract-satisfied", "the slice implemented it"], tree) == 0
    assert _run(["freeze", "--label", "demo", "--anchor", str(anchor),
                 "--contract", "tests/contract/slice",
                 "--contract-satisfied", "the slice implemented it"], tree) == 0
    assert calls == []

    assert _run(["release", "--window", "--reason", "next case"], tree) == 0
    _write_contract(tree)   # test_a asserts False, test_b passes
    assert _run(["freeze", "--label", "demo", "--anchor", str(anchor),
                 "--contract", "tests/contract/slice"], tree) == 0
    assert len(calls) == 1


def test_contract_satisfied_with_a_blank_reason_says_it_took_no_waiver(
    tree, anchor, capsys
):
    """Fail-closed is right; failing closed in silence is not.

    A reason of pure whitespace collapses to "", so no waiver is taken and the
    green contract is refused. Told only "no contract test failed", an operator
    who passed the flag concludes the flag is broken rather than that the reason
    was blank.
    """
    _write_contract(tree, red=False)

    assert _run(["freeze", "--label", "demo", "--anchor", str(anchor),
                 "--contract", "tests/contract/slice",
                 "--contract-satisfied", "   "], tree) == 1

    captured = capsys.readouterr()
    assert "blank reason" in captured.out
    assert "NO waiver was taken" in captured.out
    assert "no contract test failed" in captured.err
    assert not (tree / ".canopus" / "freeze.json").exists()


def test_status_prints_the_contract_baseline(tree, anchor, capsys):
    """`status` is where an operator confirms a retake restored the baseline.

    It reported every other manifest axis and dropped this one, so a freeze
    taken positionally (`baseline: {}`) and one taken with `--contract` printed
    byte-identical status. The renderer is shared with `pack` rather than
    copied, because two copies of it is how the two commands come to disagree
    about the same manifest.
    """
    _write_contract(tree)

    assert _run(["freeze", "--label", "demo", "--anchor", str(anchor),
                 "--contract", "tests/contract/slice"], tree) == 0
    capsys.readouterr()
    assert _run(["status"], tree) == 0

    out = capsys.readouterr().out
    assert "contract" in out
    assert "of 2" in out
    assert "tests/contract/slice/test_contract.py" in out


def test_status_prints_no_contract_section_without_a_baseline(tree, anchor, capsys):
    """Silence is the honest reading for a freeze taken without `--contract`.

    There is no per-file item count behind such a freeze, so a row here would
    be invented rather than measured.
    """
    assert _freeze(tree, anchor) == 0
    capsys.readouterr()
    assert _run(["status"], tree) == 0

    assert "\ncontract\n" not in capsys.readouterr().out


def test_release_refuses_without_a_kind(tree, anchor):
    """A release that does not name its kind is two different events wearing one
    name, and the freeze must survive the refusal untouched."""
    assert _freeze(tree, anchor) == 0

    with pytest.raises(SystemExit) as excinfo:
        _run(["release", "--reason", "no kind given"], tree)

    assert excinfo.value.code == 2
    assert (tree / ".canopus" / "freeze.json").exists()


def test_release_records_the_kind_it_was_given(tree, anchor):
    from scripts.utils.canopus_freeze import read_ledger

    assert _freeze(tree, anchor) == 0
    assert _run(["release", "--window", "--reason", "mid-build recipe change"], tree) == 0

    last = read_ledger(tree)[-1]
    assert last["event"] == "release"
    assert last["kind"] == "window"


# ============================================================
# The waiver is written by the ACT, not by the flag
# ============================================================
# `cmd_freeze` bound its refusal to the waiver having FIRED and `cmd_approve`
# bound its ARTIFACT WRITE to `args.contract_satisfied` being non-empty: a guard
# applied to one sibling and not the other, which is a defect this project has
# produced repeatedly. The consequence is worse than an inconsistency: `approve` said
# "--contract-satisfied changed nothing" and then wrote the waiver into the
# artifact a human commits, so `canopus pack` printed CONTRACT WAIVED over a
# contract that was red, or over no contract at all.


def test_approve_writes_no_waiver_for_a_red_contract(tree, anchor, capsys):
    """Measured end to end before the fix, on the artifact and in the ledger.

    The approval itself is legitimate here and still lands; it is the waiver
    line, and the claim it makes about how the freeze was earned, that has no
    basis. The command already tells the operator the flag changed nothing, and
    the record has to say the same thing.
    """
    from scripts.utils.canopus_freeze import SATISFIED_PREFIX

    _write_contract(tree)   # test_a asserts False: the contract is RED

    assert _run(["approve", "--label", "demo", "--anchor", str(anchor),
                 "--contract", "tests/contract/slice",
                 "--contract-satisfied", "habitually pasted"], tree) == 0

    assert "--contract-satisfied changed nothing" in capsys.readouterr().out
    text = anchor.read_text()
    assert SATISFIED_PREFIX not in text
    assert len(_recorded(anchor)) == 64
    assert "habitually pasted" not in _ledger(tree)[-1]["reason"]


def test_approve_writes_no_waiver_when_no_contract_ran(tree, anchor):
    """The quieter half of the same defect: the flag with no `--contract` at all.

    Nothing measured anything, so there is no refusal to waive and nothing said
    so out loud either. A waiver line for a contract that never ran is a claim
    with no run behind it.
    """
    from scripts.utils.canopus_freeze import SATISFIED_PREFIX

    assert _run(["approve", "tests/test_alpha.py", "--label", "demo",
                 "--anchor", str(anchor),
                 "--contract-satisfied", "habitually pasted"], tree) == 0

    assert SATISFIED_PREFIX not in anchor.read_text()
    assert "habitually pasted" not in _ledger(tree)[-1]["reason"]


def test_pack_reads_the_waiver_from_the_committed_copy(tree, anchor, capsys):
    """The waiver survives an edit to the working copy, like the approval does.

    Measured before the fix: `sed -i '/canopus-contract-satisfied:/d'` on the
    artifact took CONTRACT WAIVED off `canopus pack` from one occurrence to zero,
    while LOCK HELD and APPROVED were unchanged and HEAD still carried the waiver
    line. The lock and the approval are read from the repository; the waiver was
    read from the file beside it, and a page whose three claims come from two
    copies can be made to say something no committed record supports.
    """
    _init_gate_repo(anchor)
    _write_contract(tree, red=False)
    satisfied = ["--contract-satisfied", "the slice implemented it"]

    assert _run(["approve", "--label", "demo", "--anchor", str(anchor),
                 "--contract", "tests/contract/slice", *satisfied], tree) == 0
    _git(anchor.parent, "add", str(anchor))
    _git(anchor.parent, "commit", "-q", "-m", "the approval, with its waiver")
    assert _run(["freeze", "--label", "demo", "--anchor", str(anchor),
                 "--contract", "tests/contract/slice", *satisfied], tree) == 0

    scrubbed = "\n".join(
        line for line in anchor.read_text().splitlines()
        if not line.strip().startswith(canopus.SATISFIED_PREFIX)
    )
    anchor.write_text(scrubbed + "\n")
    capsys.readouterr()

    assert _run(["pack"], tree) == 0
    out = capsys.readouterr().out
    assert "CONTRACT WAIVED" in out
    assert "the slice implemented it" in out
    assert "LOCK HELD" in out


def test_status_and_verify_report_the_waiver_too(tree, anchor, capsys):
    """The command an operator is told to run themselves said the least.

    Every documented countermeasure to this tool's limits is "run `canopus
    verify` yourself", and `verify` and `status` were the two surfaces that never
    mentioned a waived contract at all. `pack` alone is not enough: it is the
    page for the second decision, not the one typed during the build.
    """
    _write_contract(tree, red=False)
    satisfied = ["--contract-satisfied", "the slice implemented it"]

    assert _run(["approve", "--label", "demo", "--anchor", str(anchor),
                 "--contract", "tests/contract/slice", *satisfied], tree) == 0
    assert _run(["freeze", "--label", "demo", "--anchor", str(anchor),
                 "--contract", "tests/contract/slice", *satisfied], tree) == 0
    capsys.readouterr()

    assert _run(["verify"], tree) == 0
    verified = capsys.readouterr().out
    assert "CONTRACT WAIVED" in verified
    assert "the slice implemented it" in verified

    assert _run(["status"], tree) == 0
    reported = capsys.readouterr().out
    assert "CONTRACT WAIVED" in reported
    assert "the slice implemented it" in reported


# ============================================================
# `status` says what the gate says
# ============================================================


def test_status_names_the_cause_of_a_red_lock_instead_of_the_wrong_one(
    tree, anchor, capsys
):
    """With the anchor's repository hidden, nothing on the tree has moved.

    `status` printed "run `canopus verify` for the per-file report" anyway, and
    that report lists nothing: the contract is intact and the binding is what
    broke. `loss_of_lock_sentences` was written to enumerate the four causes and
    was wired into the gate alone.
    """
    _init_gate_repo(anchor)
    assert _run(["approve", "tests/test_alpha.py", "--label", "demo",
                 "--anchor", str(anchor)], tree) == 0
    _git(anchor.parent, "add", str(anchor))
    _git(anchor.parent, "commit", "-q", "-m", "the approval")
    assert _run(["freeze", "tests/test_alpha.py", "--label", "demo",
                 "--anchor", str(anchor)], tree) == 0
    (anchor.parent / ".git").rename(anchor.parent / ".git-hidden")
    capsys.readouterr()

    assert _run(["status"], tree) == 0
    out = capsys.readouterr().out
    assert "LOSS OF LOCK" in out
    assert "The frozen contract moved" not in out
    assert "cannot be attributed" in out
    # The discriminator: the per-file report is offered only when a file really
    # moved. Nothing did, so an invitation to go and read it is the wrong
    # instruction, and it is what this command printed unconditionally.
    assert "per-file report" not in out


def test_status_names_an_open_release_window(tree, anchor, capsys):
    """The gate reports it at every session start; `status` said "no active
    freeze" and stopped there, which is true and is not the whole state."""
    assert _freeze(tree, anchor) == 0
    assert _run(["release", "--window", "--reason", "mid-build recipe change"],
                tree) == 0
    capsys.readouterr()

    assert _run(["status"], tree) == 0
    out = capsys.readouterr().out
    assert "release window open" in out
    assert "mid-build recipe change" in out


def test_status_names_a_vanished_manifest(tree, anchor, capsys):
    """The state that was quieter than the sanctioned one, on this surface too."""
    assert _freeze(tree, anchor) == 0
    (tree / ".canopus" / "freeze.json").unlink()
    capsys.readouterr()

    assert _run(["status"], tree) == 0
    out = capsys.readouterr().out
    assert "MANIFEST GONE" in out
    assert "--force" in out
