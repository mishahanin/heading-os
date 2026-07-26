"""Tests for the Canopus CLI (wire 1)."""
import json
import os
import subprocess
from pathlib import Path

import pytest

import scripts.canopus as canopus
from scripts.canopus import main
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
    path = tmp_path / "outside" / "gate-artifact.md"
    path.parent.mkdir(parents=True)
    path.write_text("# gate artifact\n")
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
    assert "root " in out.splitlines()[0]


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
    assert _run(["release", "--reason", "re-baseline"], tree) == 0
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
    _freeze(tree, anchor)
    assert _run(["release", "--reason", "wire 1 shipped"], tree) == 0
    assert not (tree / ".canopus" / "freeze.json").exists()
    events = [
        json.loads(line)["event"]
        for line in (tree / ".canopus" / "history.jsonl").read_text().strip().splitlines()
    ]
    assert events == ["freeze", "release"]


def test_release_without_an_active_freeze_fails(tree, capsys):
    assert _run(["release"], tree) == 1
    assert "no active freeze" in capsys.readouterr().err


def test_force_release_clears_a_corrupt_manifest_and_logs_it(tree, anchor):
    _freeze(tree, anchor)
    (tree / ".canopus" / "freeze.json").write_text("{ not json")
    assert _run(["release", "--force", "--reason", "encoding false alarm"], tree) == 0
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
    for argv in (["status"], ["verify"], ["release", "--force", "--reason", "x"]):
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

    record = cf.build_attestation(
        root_digest=root_digest,
        frozen_tests={"tests/test_alpha.py": {
            "collected": passed + skipped, "passed": passed,
            "failed": 0 if qualified else 1, "skipped": skipped,
            "deselected": deselected,
        }},
        exit_status=0,
        attested_at="2026-07-25T10:42:11+00:00",
    )
    cf.write_attestation(tree, record)
    return record


def test_verify_reports_attested_when_the_run_matches(tree, anchor, capsys):
    _freeze(tree, anchor)
    root = _root_of(tree)
    anchor.write_text(f"# gate\n\ncanopus-anchor: {root}\n")
    _attest(tree, root)
    assert _run(["verify"], tree) == 0
    out = capsys.readouterr().out
    assert "LOCK HELD" in out
    assert "NOT ATTESTED" not in out
    assert "ATTESTED" in out
    assert "3 frozen tests passed" in out


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
    _freeze(tree, anchor)
    root = _root_of(tree)
    anchor.write_text(f"# gate\n\ncanopus-anchor: {root}\n")
    _attest(tree, root)
    (tree / "tests" / "test_alpha.py").write_text("def test_a():\n    assert False\n")
    assert _run(["verify"], tree) == 1
    out = capsys.readouterr().out
    assert "LOSS OF LOCK" in out
    # The edit moved the root, so the earlier attestation stops applying without
    # anyone having to remember to delete it.
    assert "NOT ATTESTED" in out


def test_status_carries_the_attestation_line(tree, anchor, capsys):
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
    directory = tree / "tests" / "contract" / "slice"
    directory.mkdir(parents=True, exist_ok=True)
    body = ("def test_a():\n    assert False\n\n\ndef test_b():\n    assert True\n"
            if red else
            "def test_a():\n    assert True\n\n\ndef test_b():\n    assert True\n")
    (directory / "test_contract.py").write_text(body)
    return directory


def test_freeze_contract_records_the_baseline(tree, anchor):
    _write_contract(tree)

    assert _run(["freeze", "--label", "demo", "--anchor", str(anchor),
                 "--contract", "tests/contract/slice"], tree) == 0

    manifest = json.loads((tree / ".canopus" / "freeze.json").read_text())
    assert manifest["baseline"] == {"tests/contract/slice/test_contract.py": 2}


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
    assert "continuity" in out
    assert "staleness" in out


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
    assert _run(["approve", "--label", "l", "--anchor", str(anchor),
                 "--replace", "--reason", "the set changed",
                 "tests/test_alpha.py"], tree) == 0


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
                 "--replace", "--reason", "widened the approved set"], tree) == 0

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
    """No traceback, and no ledger line claiming an approval that never landed.

    The artifact write comes BEFORE append_history deliberately: a ledger that
    records an approval the artifact never received is worse than no ledger.
    """
    os.chmod(anchor, 0o444)
    try:
        assert _run(["approve", "tests/test_alpha.py", "--label", "l",
                     "--anchor", str(anchor)], tree) == 1
    finally:
        os.chmod(anchor, 0o644)

    assert "Traceback" not in capsys.readouterr().err
    assert "canopus-anchor:" not in anchor.read_text()
    assert not (tree / ".canopus" / "history.jsonl").exists()


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
                 "--anchor", str(anchor), "--replace", "--reason", "set changed"],
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
    assert not (tree / ".canopus" / "history.jsonl").exists()


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
    first = out.splitlines()[0]
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
                 ["release"]):
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
    assert _run(["release", "--reason", "re-baseline"], tree) == 0

    (tree / "tests" / "test_alpha.py").write_text("def test_a():\n    assert False\n")
    assert _run(["approve", "tests/test_alpha.py", "--label", "l",
                 "--anchor", str(anchor), "--replace", "--reason", "edited"],
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
        "def test_vacuous():\n"
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
    other than the code being absent."""
    _write_contract(tree)   # test_a asserts False, test_b passes

    _run(["probe", "tests/contract/slice"], tree)
    out = capsys.readouterr().out

    line = next(part for part in out.splitlines() if "test_a" in part)
    assert "assertion" in line


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
        "def test_vacuous():\n"
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


def test_freeze_says_so_when_vacuity_could_not_be_measured(tree, anchor, capsys):
    """The narrowing bypass, reported rather than refused.

    `from None` suppresses the chained ModuleNotFoundError, so the report names
    no absent module, nothing is stubbed, and the refusal cannot fire over a
    contract every test of which asserts nothing. This freeze is TAKEN, because
    a red contract naming no absent module is also the ordinary shape of tests
    failing on assertions against code that already exists. What it must not do
    is stay silent about having measured nothing.
    """
    directory = tree / "tests" / "contract" / "slice"
    directory.mkdir(parents=True)
    (directory / "test_contract.py").write_text(
        "def test_vacuous():\n"
        "    try:\n"
        "        from absent_thing import answer\n"
        "    except ImportError:\n"
        "        raise AssertionError('not implemented yet') from None\n"
        "    assert answer() is not None\n"
    )

    assert _run(["freeze", "--label", "demo", "--anchor", str(anchor),
                 "--contract", "tests/contract/slice"], tree) == 0
    assert "NOT measured" in capsys.readouterr().out
    assert (tree / ".canopus" / "freeze.json").exists()


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
        "def test_a():\n    assert answer() == 42\n"
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
    """
    assert _freeze(tree, anchor) == 0
    capsys.readouterr()
    _ledger_raises(monkeypatch)

    for argv in (["release", "--reason", "done"],
                 ["release", "--force", "--reason", "damaged"]):
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
                 "--replace", "--reason", "the beta case joined the set"], tree) == 0

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
                 f"widened\n{forged}"], tree) == 0

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
