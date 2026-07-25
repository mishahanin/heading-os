"""Tests for the Canopus CLI (wire 1)."""
import json
from pathlib import Path

import pytest

from scripts.canopus import main


@pytest.fixture
def tree(tmp_path: Path, monkeypatch) -> Path:
    root = tmp_path / "tree"
    (root / "tests").mkdir(parents=True)
    (root / "tests" / "test_alpha.py").write_text("def test_a():\n    assert True\n")
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


def _freeze(tree, anchor=None):
    argv = ["freeze", "tests/test_alpha.py", "--label", "demo"]
    if anchor is not None:
        argv += ["--anchor", str(anchor)]
    return _run(argv, tree)


def test_freeze_prints_the_root_hash_and_the_paste_line(tree, anchor, capsys):
    # The paste line only exists when there is an anchor to paste it into.
    assert _freeze(tree, anchor=anchor) == 0
    out = capsys.readouterr().out
    assert "root " in out.splitlines()[0]
    assert f"canopus-anchor: {_root_of(tree)}" in out
    assert (tree / ".canopus" / "freeze.json").exists()


def test_freeze_without_an_anchor_says_the_lock_is_uncheckable(tree, capsys):
    assert _freeze(tree) == 0
    out = capsys.readouterr().out
    assert "canopus-anchor:" not in out
    assert "LOCK UNCONFIRMED" in out


def test_freeze_refuses_when_one_is_already_active(tree, capsys):
    _freeze(tree)
    assert _freeze(tree) == 1
    assert "already active" in capsys.readouterr().err


def test_freeze_refuses_a_missing_path(tree, capsys):
    assert _run(["freeze", "tests/nope.py", "--label", "demo"], tree) == 1
    assert "does not exist" in capsys.readouterr().err


def test_freeze_refuses_an_anchor_inside_the_tree(tree, capsys):
    inside = tree / "gate.md"
    inside.write_text("# nope\n")
    assert _freeze(tree, anchor=inside) == 1
    assert "inside the working tree" in capsys.readouterr().err


def test_verify_without_an_anchor_is_unconfirmed(tree, capsys):
    _freeze(tree)
    assert _run(["verify"], tree) == 0
    assert "LOCK UNCONFIRMED" in capsys.readouterr().out


def test_verify_with_an_unrecorded_anchor_is_unconfirmed(tree, anchor, capsys):
    _freeze(tree, anchor=anchor)
    assert _run(["verify"], tree) == 0
    assert "LOCK UNCONFIRMED" in capsys.readouterr().out


def test_verify_with_an_agreeing_anchor_holds(tree, anchor, capsys):
    _freeze(tree, anchor=anchor)
    anchor.write_text(f"# gate\n\ncanopus-anchor: {_root_of(tree)}\n")
    assert _run(["verify"], tree) == 0
    assert "LOCK HELD" in capsys.readouterr().out


def test_verify_with_a_disagreeing_anchor_is_loss_of_lock(tree, anchor, capsys):
    _freeze(tree, anchor=anchor)
    anchor.write_text("# gate\n\ncanopus-anchor: " + "0" * 64 + "\n")
    assert _run(["verify"], tree) == 1
    assert "LOSS OF LOCK" in capsys.readouterr().out


def test_verify_with_a_vanished_anchor_is_loss_of_lock(tree, anchor, capsys):
    _freeze(tree, anchor=anchor)
    anchor.unlink()
    assert _run(["verify"], tree) == 1
    assert "LOSS OF LOCK" in capsys.readouterr().out


def test_verify_reports_the_changed_file(tree, capsys):
    _freeze(tree)
    (tree / "tests" / "test_alpha.py").write_text("def test_a():\n    assert False\n")
    assert _run(["verify"], tree) == 1
    out = capsys.readouterr().out
    assert "LOSS OF LOCK" in out
    assert "tests/test_alpha.py" in out


def test_verify_without_an_active_freeze_fails(tree, capsys):
    assert _run(["verify"], tree) == 1
    assert "no active freeze" in capsys.readouterr().err


def test_verify_on_a_corrupt_manifest_fails(tree, capsys):
    _freeze(tree)
    (tree / ".canopus" / "freeze.json").write_text("{ not json")
    assert _run(["verify"], tree) == 1
    assert "unreadable" in capsys.readouterr().err


def test_release_records_the_event_and_clears_the_manifest(tree):
    _freeze(tree)
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


def test_force_release_clears_a_corrupt_manifest_and_logs_it(tree):
    _freeze(tree)
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
    _freeze(tree, anchor=anchor)
    assert _run(["status"], tree) == 0
    out = capsys.readouterr().out
    assert "demo" in out
    assert "1 file" in out
    # The manifest stores the RESOLVED anchor path. Comparing the raw fixture
    # path breaks wherever the temp root is a symlink (macOS /var -> /private/var).
    assert str(anchor.resolve()) in out


def test_freeze_accepts_paths_relative_to_root_from_any_cwd(tmp_path, monkeypatch):
    """--root exists so the frozen tree need not be the cwd."""
    root = tmp_path / "elsewhere"
    (root / "tests").mkdir(parents=True)
    (root / "tests" / "test_alpha.py").write_text("def test_a():\n    assert True\n")
    monkeypatch.chdir(tmp_path)
    assert main(["--root", str(root), "freeze", "tests/test_alpha.py", "--label", "demo"]) == 0
    assert (root / ".canopus" / "freeze.json").exists()


def test_freeze_resolves_a_relative_anchor_against_root_not_cwd(tmp_path, monkeypatch):
    """A relative --anchor is anchored to --root, exactly like the positional paths."""
    root = tmp_path / "root-tree"
    (root / "tests").mkdir(parents=True)
    (root / "tests" / "test_alpha.py").write_text("def test_a():\n    assert True\n")

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
    root = tmp_path / "root-tree"
    (root / "tests").mkdir(parents=True)
    (root / "tests" / "test_alpha.py").write_text("def test_a():\n    assert True\n")

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


def test_verify_anchor_override_inside_the_working_tree_is_refused(tree, capsys):
    """The verify override must be refused the same way a freeze-time anchor is."""
    _freeze(tree)
    inside = tree / "gate.md"
    inside.write_text("# nope\n")
    assert _run(["verify", "--anchor", str(inside)], tree) == 1
    assert "inside the working tree" in capsys.readouterr().err
