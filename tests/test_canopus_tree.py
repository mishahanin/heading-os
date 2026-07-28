"""The working tree's state, which is what an attestation now perishes on."""
import subprocess
from pathlib import Path

import pytest


def _git(directory: Path, *argv: str):
    """git in *directory*, with every GIT_* variable out of the child environment.

    Not tidiness. This suite runs inside this repository's pre-push hook, and git
    exports GIT_DIR and GIT_INDEX_FILE to a hook, so an unscrubbed helper would
    resolve the ENGINE's repository instead of the fixture's. The same scrub
    `tests/test_canopus_gate.py::_git` applies, for the same measured reason.
    """
    import os

    env = {key: value for key, value in os.environ.items()
           if not key.startswith("GIT_")}
    return subprocess.run(["git", "-C", str(directory), *argv], check=True,
                          capture_output=True, text=True, env=env)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "builder@example.invalid")
    _git(root, "config", "user.name", "Builder")
    (root / "kept.py").write_text("x = 1\n", encoding="utf-8")
    _git(root, "add", "kept.py")
    _git(root, "commit", "-q", "-m", "first")
    return root


def test_a_clean_tree_has_a_head_and_nothing_dirty(repo):
    from scripts.utils.canopus_tree import TREE_RECIPE, tree_state

    state = tree_state(repo)
    assert state["recipe"] == TREE_RECIPE
    assert len(state["head"]) == 40
    assert state["dirty"] == {}


def test_editing_a_tracked_file_moves_the_state(repo):
    from scripts.utils.canopus_tree import tree_state

    before = tree_state(repo)
    (repo / "kept.py").write_text("x = 2\n", encoding="utf-8")
    after = tree_state(repo)

    assert after["head"] == before["head"]
    assert list(after["dirty"]) == ["kept.py"]
    assert after["dirty"]["kept.py"] != before["dirty"].get("kept.py")


def test_an_untracked_file_of_any_kind_is_in_the_state(repo):
    """The whole point of leaving `sys.modules` behind: a YAML config and a
    subprocess-only script are as much of the tree as a module is."""
    from scripts.utils.canopus_tree import tree_state

    (repo / "config.yaml").write_text("flag: true\n", encoding="utf-8")
    (repo / "helper.sh").write_text("echo hi\n", encoding="utf-8")

    dirty = tree_state(repo)["dirty"]
    assert sorted(dirty) == ["config.yaml", "helper.sh"]


def test_an_untracked_file_inside_an_untracked_directory_is_named_in_full(repo):
    """Default porcelain reports `newdir/` and stops. A directory name carries no
    hash, so a state that recorded it would not move when the file inside it
    changed -- and a new module dropped into a new package is exactly the shape
    a builder reaches for. `--untracked-files=all` is what makes the entry a
    file."""
    from scripts.utils.canopus_tree import tree_state

    (repo / "newpkg").mkdir()
    (repo / "newpkg" / "mod.py").write_text("y = 1\n", encoding="utf-8")

    dirty = tree_state(repo)["dirty"]
    assert sorted(dirty) == ["newpkg/mod.py"]
    assert dirty["newpkg/mod.py"] is not None


def test_a_deleted_tracked_file_is_recorded_as_a_gap(repo):
    from scripts.utils.canopus_tree import tree_state

    (repo / "kept.py").unlink()
    assert tree_state(repo)["dirty"] == {"kept.py": None}


def test_a_commit_moves_the_head(repo):
    from scripts.utils.canopus_tree import tree_state

    before = tree_state(repo)
    (repo / "second.py").write_text("z = 1\n", encoding="utf-8")
    _git(repo, "add", "second.py")
    _git(repo, "commit", "-q", "-m", "second")
    after = tree_state(repo)

    assert after["head"] != before["head"]
    assert after["dirty"] == {}


def test_a_rename_records_both_ends(repo):
    """Porcelain -z ships a rename as TWO NUL-separated fields, the new path then
    the old one. A parser that reads only the first loses the old path, and the
    old path is the one whose disappearance a reader needs to see."""
    from scripts.utils.canopus_tree import tree_state

    _git(repo, "mv", "kept.py", "moved.py")
    dirty = tree_state(repo)["dirty"]
    assert sorted(dirty) == ["kept.py", "moved.py"]


def test_a_tree_that_is_not_a_git_working_copy_answers_none(tmp_path):
    """It answers rather than raising, and None is what `build_attestation`
    reads as "this run could not describe the tree it ran against"."""
    from scripts.utils.canopus_tree import tree_state

    plain = tmp_path / "plain"
    plain.mkdir()
    assert tree_state(plain) is None


def test_a_path_with_a_space_survives_the_parser(repo):
    """The -z form exists so a path never has to be quoted or escaped; a parser
    that split on whitespace would lose exactly these."""
    from scripts.utils.canopus_tree import tree_state

    (repo / "a file.py").write_text("q = 1\n", encoding="utf-8")
    assert "a file.py" in tree_state(repo)["dirty"]


def test_porcelain_paths_records_both_ends_of_a_copy():
    """A copy entry ships the same two-field shape a rename does: the new path
    then the old one. `_porcelain_paths` branches on `"R" in code or "C" in
    code`, so this pins the "C" half of that condition directly -- getting real
    git to emit a `C` status needs `--find-copies` plus a similarity threshold
    git decides at runtime, which is not a reliable trigger to hang a
    regression test on.
    """
    from scripts.utils.canopus_tree import _porcelain_paths

    raw = "C  new.py\x00old.py\x00"
    assert _porcelain_paths(raw) == ["new.py", "old.py"]


def test_porcelain_paths_survives_an_embedded_newline():
    """The -z form exists precisely so a path never needs escaping; a path
    containing a literal newline is real content, not a field separator."""
    from scripts.utils.canopus_tree import _porcelain_paths

    raw = "?? weird\nname.py\x00"
    assert _porcelain_paths(raw) == ["weird\nname.py"]


def test_tree_state_through_a_subdirectory_hashes_against_the_toplevel(repo):
    """Git porcelain paths are relative to the repository TOPLEVEL, never to
    whatever directory git was invoked from -- git-status(1) documents that
    explicitly for the porcelain format. Joining a porcelain path to a
    non-toplevel `root` reads bytes from a path that does not exist, so every
    entry hashes to None and two DIFFERENT tree states compare equal: a green
    attestation surviving a change to the thing under test.

    Reproduced exactly as measured: a repo with `pkg/a.py`, called through the
    subdirectory `pkg` rather than the repo root, edited twice.
    """
    from scripts.utils.canopus_freeze import tree_drift
    from scripts.utils.canopus_tree import tree_state

    pkg = repo / "pkg"
    pkg.mkdir()
    (pkg / "a.py").write_text("x = 1\n", encoding="utf-8")
    _git(repo, "add", "pkg/a.py")
    _git(repo, "commit", "-q", "-m", "add pkg")

    (pkg / "a.py").write_text("x = 2\n", encoding="utf-8")
    before = tree_state(pkg)
    assert before["dirty"].get("pkg/a.py") is not None, (
        "the hash should be readable through the subdirectory root, not None"
    )

    (pkg / "a.py").write_text("x = 3\n", encoding="utf-8")
    after = tree_state(pkg)

    assert before["dirty"] != after["dirty"]
    drift = tree_drift(before, after)
    assert any("pkg/a.py" in reason for reason in drift)


def test_a_toplevel_path_ending_in_a_space_is_not_truncated(tmp_path):
    """`Path(top.strip())` used to strip real trailing spaces from the
    repository path along with git's trailing newline. A repository whose
    toplevel directory name ends in a space then resolved to the WRONG
    directory: every file hashed to None, and two different tree states
    compared equal -- the exact silent-green failure this module exists to
    prevent, reintroduced one layer up by the fix that closed it below.

    Reproduced exactly as measured: a repo whose toplevel directory name ends
    in a space, with a tracked file edited between two `tree_state` calls.
    """
    from scripts.utils.canopus_tree import tree_state

    root = tmp_path / "repo "
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "builder@example.invalid")
    _git(root, "config", "user.name", "Builder")
    (root / "a.py").write_text("x = 1\n", encoding="utf-8")
    _git(root, "add", "a.py")
    _git(root, "commit", "-q", "-m", "first")

    (root / "a.py").write_text("x = 2\n", encoding="utf-8")
    dirty = tree_state(root)["dirty"]
    assert dirty.get("a.py") is not None, (
        "the hash should be readable through a toplevel path ending in a "
        "space, not None"
    )


def test_tree_state_answers_none_when_the_toplevel_is_empty(tmp_path, monkeypatch):
    """The empty-toplevel guard is what stops `Path("")` (`.` in disguise)
    from becoming the toplevel every porcelain path gets joined against.
    Pinned directly against `git_output`, monkeypatched in this module's
    namespace, rather than against a real git edge case: mutating the guard
    to `if top is None` alone (dropping the empty-after-newline-strip check)
    left this branch untested.
    """
    import scripts.utils.canopus_tree as canopus_tree

    def fake_git_output(root, *args):
        if args == ("rev-parse", "HEAD"):
            return "a" * 40 + "\n"
        if args == ("rev-parse", "--show-toplevel"):
            return "\n"
        raise AssertionError(f"unexpected git_output call: {args}")

    monkeypatch.setattr(canopus_tree, "git_output", fake_git_output)

    assert canopus_tree.tree_state(tmp_path) is None
