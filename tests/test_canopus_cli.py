"""Tests for the Canopus CLI (wire 1)."""
import json
import os
import subprocess
from pathlib import Path

import pytest

import scripts.canopus as canopus
from scripts.canopus import main


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


def test_freeze_prints_the_root_hash_and_records_it(tree, anchor, capsys):
    assert _freeze(tree, anchor) == 0
    out = capsys.readouterr().out
    assert "root " in out.splitlines()[0]
    assert "Recorded in" in out
    assert f"canopus-anchor: {_root_of(tree)}" in anchor.read_text()


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

    In wire 1 the re-freeze succeeded and `verify` caught it afterwards. It is
    now refused outright, because the artifact still records the approved hash.
    Deliberately widening a frozen set is the --replace-anchor path, which
    carries a reason and a ledger entry.
    """
    assert _freeze(tree, anchor) == 0
    approved = _root_of(tree)
    assert _run(["release", "--reason", "re-baseline"], tree) == 0

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


def test_freeze_writes_the_anchor_line_itself(tree, anchor):
    assert _freeze(tree, anchor) == 0

    assert f"canopus-anchor: {_root_of(tree)}" in anchor.read_text()
    assert _run(["verify"], tree) == 0


def test_freeze_refuses_an_anchor_that_already_carries_a_line(tree, anchor, capsys):
    anchor.write_text("canopus-anchor: " + "b" * 64 + "\n")
    before = anchor.read_bytes()

    assert _freeze(tree, anchor) == 1
    assert "already records" in capsys.readouterr().err
    assert anchor.read_bytes() == before
    assert not (tree / ".canopus" / "freeze.json").exists()


def test_replace_anchor_appends_and_logs_the_reason(tree, anchor):
    assert _freeze(tree, anchor) == 0
    first = _root_of(tree)
    assert _run(["release", "--reason", "segment done"], tree) == 0

    (tree / "tests" / "test_beta.py").write_text("def test_b():\n    assert True\n")
    assert _run(["freeze", "tests/test_alpha.py", "tests/test_beta.py",
                 "--label", "demo", "--anchor", str(anchor),
                 "--replace-anchor", "--reason", "widened the frozen set"], tree) == 0

    text = anchor.read_text()
    assert f"canopus-anchor: {first}" in text
    assert f"canopus-anchor: {_root_of(tree)}" in text
    assert _run(["verify"], tree) == 0

    ledger = (tree / ".canopus" / "history.jsonl").read_text().splitlines()
    replaced = [json.loads(line) for line in ledger
                if json.loads(line)["event"] == "anchor_replaced"]
    assert replaced and replaced[-1]["reason"] == "widened the frozen set"


def test_replace_anchor_requires_a_reason(tree, anchor, capsys):
    anchor.write_text("canopus-anchor: " + "b" * 64 + "\n")
    assert _run(["freeze", "tests/test_alpha.py", "--label", "demo",
                 "--anchor", str(anchor), "--replace-anchor"], tree) == 1
    assert "--reason" in capsys.readouterr().err


def test_pack_exits_nonzero_with_no_freeze(tree, capsys):
    assert _run(["pack"], tree) == 1
    assert "no active freeze" in capsys.readouterr().err


def test_pack_reports_both_axes_and_the_uncovered_list(tree, anchor, capsys):
    assert _freeze(tree, anchor) == 0
    assert _run(["pack"], tree) == 0
    out = capsys.readouterr().out
    assert "LOCK HELD" in out
    assert "NOT ATTESTED" in out          # no run has attested this freeze yet
    assert "not covered" in out
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
    """
    import subprocess

    gate = anchor.parent
    for argv in (["init", "-q", "-b", "main"],
                 ["config", "user.email", "builder@example.invalid"],
                 ["config", "user.name", "Builder"]):
        subprocess.run(["git", "-C", str(gate), *argv], check=True,
                       capture_output=True, text=True)

    assert _freeze(tree, anchor) == 0
    approved = _root_of(tree)
    subprocess.run(["git", "-C", str(gate), "add", "-A"], check=True,
                   capture_output=True, text=True)
    subprocess.run(["git", "-C", str(gate), "commit", "-q", "-m", "approve"],
                   check=True, capture_output=True, text=True)
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
    """
    assert _freeze(tree, anchor) == 0
    approved = _root_of(tree)
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
    working copy carries one, the tool wrote it, and the reason it does not count
    is that nobody committed it. The reason now comes from the single producer of
    the precedence decision.
    """
    import subprocess

    gate = anchor.parent
    for argv in (["init", "-q", "-b", "main"],
                 ["config", "user.email", "builder@example.invalid"],
                 ["config", "user.name", "Builder"]):
        subprocess.run(["git", "-C", str(gate), *argv], check=True,
                       capture_output=True, text=True)
    subprocess.run(["git", "-C", str(gate), "add", "-A"], check=True,
                   capture_output=True, text=True)
    subprocess.run(["git", "-C", str(gate), "commit", "-q", "-m", "no approval yet"],
                   check=True, capture_output=True, text=True)

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
    """
    gate = anchor.parent
    for argv in (["init", "-q", "-b", "main"],
                 ["config", "user.email", "builder@example.invalid"],
                 ["config", "user.name", "Builder"]):
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

    The working copy is scrubbed between the two calls, and the scrub is NOT part
    of the invariant: it only steps around the refusal `cmd_freeze` still carries
    on a recorded working line, which moves to the committed copy in the next
    slice. The anchor PATH is inside the root hash and its CONTENTS are not, so
    scrubbing cannot change the digest either side computes.
    """
    assert _run(["approve", "--label", "l", "--anchor", str(anchor),
                 "tests/test_alpha.py"], tree) == 0
    approved = _recorded(anchor)
    assert len(approved) == 64, "a truncated digest is not an approval"

    anchor.write_text("# gate artifact\n")

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

    anchor.write_text("# gate artifact\n")   # see the note two tests above

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

    # The scrub steps around the refusal cmd_freeze still carries on a recorded
    # WORKING line, which moves to the committed copy in the next slice. The
    # committed approval is what the lock reads, and it survives the scrub.
    anchor.write_text("# gate artifact\n")
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
    # only line standing is the one cmd_freeze wrote back, and it is the frozen
    # root.
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
    """`freeze` and `release` both default --reason to ""; `approve` did not, so
    args.reason came through as None on the one command whose whole job is to put
    a reason on the record. Asserted at the parser, because `args.reason or ""`
    downstream launders the difference into the ledger and hides it."""
    parser = canopus.build_parser()
    for argv in (["freeze", "x", "--label", "l", "--anchor", "a"],
                 ["approve", "x", "--label", "l", "--anchor", "a"],
                 ["release"]):
        assert parser.parse_args(argv).reason == "", argv[0]


def test_approve_writes_the_empty_reason_through_to_the_ledger(tree: Path, anchor: Path):
    assert _run(["approve", "tests/test_alpha.py", "--label", "l",
                 "--anchor", str(anchor)], tree) == 0
    assert _ledger(tree)[-1]["reason"] == ""
