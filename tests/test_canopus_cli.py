"""Tests for the Canopus CLI (wire 1)."""
import json
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


def test_freeze_prints_the_root_hash_and_the_paste_line(tree, anchor, capsys):
    assert _freeze(tree, anchor) == 0
    out = capsys.readouterr().out
    assert "root " in out.splitlines()[0]
    assert f"canopus-anchor: {_root_of(tree)}" in out
    assert (tree / ".canopus" / "freeze.json").exists()


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


def test_re_baselining_a_contract_is_visible_because_the_anchor_disagrees(
    tree, anchor, capsys
):
    """The hole a required --anchor closes: release, edit, re-freeze.

    Anchorless that sequence lands on amber LOCK UNCONFIRMED and exit 0 — a
    passing gate over a contract nobody approved. With the anchor still holding
    the approved hash, the re-freeze fails loudly instead.
    """
    _freeze(tree, anchor)
    approved = _root_of(tree)
    anchor.write_text(f"canopus-anchor: {approved}\n")
    assert _run(["release", "--reason", "re-baseline"], tree) == 0

    (tree / "tests" / "test_alpha.py").write_text("def test_a():\n    assert False\n")
    assert _freeze(tree, anchor) == 0
    assert _root_of(tree) != approved
    capsys.readouterr()

    assert _run(["verify"], tree) == 1
    out = capsys.readouterr().out
    assert "LOSS OF LOCK" in out
    assert approved in out


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
