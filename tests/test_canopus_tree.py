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


def test_flagged_paths_recognises_lowercase_and_capital_s_tags(monkeypatch):
    """`git ls-files -v`'s tag alphabet, pinned directly against `git_output`
    rather than a real index in every one of these states: `H` is an ordinary
    cached file and is never flagged; `S` alone is skip-worktree; `h` and `s`
    are BOTH lowercased -- assume-unchanged, whichever base letter it started
    from -- and lowercasing is what actually marks the bit, not the letter
    itself. Measured against a real index (four successive `update-index`
    calls on one file) before this was written, not taken from the manual
    page.
    """
    import scripts.utils.canopus_tree as canopus_tree

    raw = "H clean.py\x00h hidden.py\x00S worktree.py\x00s both.py\x00"

    def fake_git_output(root, *args):
        assert args == ("ls-files", "-v", "-z")
        return raw

    monkeypatch.setattr(canopus_tree, "git_output", fake_git_output)
    assert canopus_tree._flagged_paths(Path(".")) == [
        "hidden.py", "worktree.py", "both.py",
    ]


def test_flagged_paths_answers_none_on_a_git_failure(monkeypatch):
    """Fail-closed, matching every other reader in this module: a check that
    could not run must never be read as a check that found nothing."""
    import scripts.utils.canopus_tree as canopus_tree

    monkeypatch.setattr(canopus_tree, "git_output", lambda root, *a: None)
    assert canopus_tree._flagged_paths(Path(".")) is None


def test_an_assume_unchanged_edit_is_hashed_despite_a_clean_status(repo):
    """`git status --porcelain` is DEFINED to say nothing about a path
    carrying this bit -- that is the whole reason the bit exists -- so `dirty`
    built from status alone misses exactly the edit an attacker would reach
    for. Reproduced: set the bit, edit the tracked file, confirm status is
    genuinely silent while `tree_state` still moves.
    """
    from scripts.utils.canopus_tree import tree_state

    _git(repo, "update-index", "--assume-unchanged", "kept.py")
    before = tree_state(repo)
    assert "kept.py" in before["dirty"]  # flagged and unedited: hashed anyway

    (repo / "kept.py").write_text("x = 2\n", encoding="utf-8")
    status = _git(repo, "status", "--porcelain")
    assert status.stdout == "", "the whole point of the bit: status stays silent"

    after = tree_state(repo)
    assert after["dirty"]["kept.py"] != before["dirty"]["kept.py"]


def test_a_skip_worktree_edit_is_hashed_despite_a_clean_status(repo):
    """The same reproduction, for `--skip-worktree` rather than
    `--assume-unchanged`. Both bits hide a path from status; both are closed
    the same way, by reading the bytes directly instead of trusting status."""
    from scripts.utils.canopus_tree import tree_state

    _git(repo, "update-index", "--skip-worktree", "kept.py")
    before = tree_state(repo)
    assert "kept.py" in before["dirty"]

    (repo / "kept.py").write_text("x = 2\n", encoding="utf-8")
    status = _git(repo, "status", "--porcelain")
    assert status.stdout == ""

    after = tree_state(repo)
    assert after["dirty"]["kept.py"] != before["dirty"]["kept.py"]


def test_the_bit_set_after_a_clean_record_still_perishes_it(repo):
    """The reviewer's literal reproduction, ordered exactly as measured: a
    clean record taken BEFORE the bit is ever set, then the bit is set, then
    the file is broken, then nothing is run. Without the merge in
    `tree_state`, `git status` stays silent at both samples and two
    DIFFERENT tree states compare equal -- a green record over a corrupted
    implementation.
    """
    from scripts.utils.canopus_freeze import tree_drift
    from scripts.utils.canopus_tree import tree_state

    recorded = tree_state(repo)  # taken before the bit exists at all

    _git(repo, "update-index", "--assume-unchanged", "kept.py")
    (repo / "kept.py").write_text("x = broken\n", encoding="utf-8")

    current = tree_state(repo)
    drift = tree_drift(recorded, current)
    assert any("kept.py" in reason for reason in drift)


def test_the_bit_set_before_the_record_still_perishes_on_a_later_edit(repo):
    """The other ordering: the bit is ALREADY set when the record is taken
    (so the recorded state already carries the path, hashed), and the edit
    happens afterward. Both orderings have to close, not just the one in the
    reproduction, or the fix only half-answers the finding."""
    from scripts.utils.canopus_freeze import tree_drift
    from scripts.utils.canopus_tree import tree_state

    _git(repo, "update-index", "--assume-unchanged", "kept.py")
    recorded = tree_state(repo)  # the bit is already set here

    (repo / "kept.py").write_text("x = broken\n", encoding="utf-8")
    current = tree_state(repo)

    drift = tree_drift(recorded, current)
    assert any("kept.py" in reason for reason in drift)


def test_a_flagged_path_through_a_subdirectory_hashes_against_the_toplevel(repo):
    """`git ls-files` paths are relative to wherever git was invoked, unlike
    `git status --porcelain`'s toplevel-relative guarantee -- the same class
    of bug `test_tree_state_through_a_subdirectory_hashes_against_the_toplevel`
    pins for the porcelain half, reproduced here for the flagged half: joining
    a root-relative ls-files path onto the wrong base would hash a path that
    does not exist and report no drift for a file that changed.
    """
    from scripts.utils.canopus_tree import tree_state

    pkg = repo / "pkg"
    pkg.mkdir()
    (pkg / "a.py").write_text("x = 1\n", encoding="utf-8")
    _git(repo, "add", "pkg/a.py")
    _git(repo, "commit", "-q", "-m", "add pkg")
    _git(repo, "update-index", "--assume-unchanged", "pkg/a.py")

    (pkg / "a.py").write_text("x = 2\n", encoding="utf-8")
    dirty = tree_state(pkg)["dirty"]
    assert "pkg/a.py" in dirty
    assert dirty["pkg/a.py"] is not None


def test_tree_state_answers_none_when_ls_files_fails(repo, monkeypatch):
    """Fail-closed the same way the empty-toplevel guard is: a half of the
    tree this function cannot examine must never be silently reported as a
    tree with nothing wrong in that half."""
    import scripts.utils.canopus_tree as canopus_tree

    real_git_output = canopus_tree.git_output

    def fake_git_output(root, *args):
        if args and args[0] == "ls-files":
            return None
        return real_git_output(root, *args)

    monkeypatch.setattr(canopus_tree, "git_output", fake_git_output)
    assert canopus_tree.tree_state(repo) is None


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


def test_a_flagged_symlink_pointing_outside_the_repo_costs_only_its_own_path(
    repo, tmp_path_factory,
):
    """A tracked, flagged symlink pointing OUTSIDE the repository used to make
    `.relative_to(toplevel)` raise inside the flagged-path loop, and the whole
    function answered None for it -- fail-closed, but total: ONE symlink
    disabled the tool for the WHOLE repository. Reproduced exactly as
    measured: an assume-unchanged symlink pointing at a file outside the repo,
    sitting beside an ordinary tracked-file edit elsewhere in the tree. The
    edit is real; before this closed, the collapse hid it completely.
    """
    from scripts.utils.canopus_tree import tree_state

    outside = tmp_path_factory.mktemp("outside") / "target.txt"
    outside.write_text("external\n", encoding="utf-8")
    (repo / "escape.py").symlink_to(outside)
    _git(repo, "add", "escape.py")
    _git(repo, "commit", "-q", "-m", "add symlink")
    _git(repo, "update-index", "--assume-unchanged", "escape.py")

    (repo / "kept.py").write_text("x = 2\n", encoding="utf-8")  # ordinary edit
    state = tree_state(repo)

    assert state is not None, "one flagged symlink must not disable the whole tree"
    assert "kept.py" in state["dirty"], "the real edit elsewhere must still be visible"


def test_a_flagged_in_repo_symlink_keeps_its_own_reported_name(repo):
    """`(resolved_root / rel).resolve()` used to follow the symlink itself
    while computing the dirty-dict KEY, so a flagged alias was recorded under
    its TARGET's name rather than its own -- `alias.py` -> `real.py` hashed
    under the key `real.py`, silently merging two distinct tracked paths into
    one dirty entry and losing the alias's own identity from the record.
    """
    from scripts.utils.canopus_tree import tree_state

    (repo / "real.py").write_text("x = 1\n", encoding="utf-8")
    (repo / "alias.py").symlink_to(repo / "real.py")
    _git(repo, "add", "real.py", "alias.py")
    _git(repo, "commit", "-q", "-m", "add alias")
    _git(repo, "update-index", "--assume-unchanged", "alias.py")

    dirty = tree_state(repo)["dirty"]
    assert "alias.py" in dirty, "the flagged path must be keyed by its own name"
    assert "real.py" not in dirty, "real.py was never edited or flagged itself"


@pytest.fixture
def repo_with_submodule(tmp_path: Path) -> Path:
    """An outer repo with a local submodule `sub`, both initialised offline.

    `-c protocol.file.allow=always` is passed per-call rather than set
    globally: git 2.38+ refuses a local submodule clone by default
    (CVE-2022-39253), and this suite must not touch the operator's global git
    config to work around that.
    """
    inner = tmp_path / "inner"
    inner.mkdir()
    _git(inner, "init", "-q")
    _git(inner, "config", "user.email", "builder@example.invalid")
    _git(inner, "config", "user.name", "Builder")
    (inner / "f.py").write_text("v = 1\n", encoding="utf-8")
    _git(inner, "add", "f.py")
    _git(inner, "commit", "-q", "-m", "inner first")

    outer = tmp_path / "outer"
    outer.mkdir()
    _git(outer, "init", "-q")
    _git(outer, "config", "user.email", "builder@example.invalid")
    _git(outer, "config", "user.name", "Builder")
    _git(outer, "-c", "protocol.file.allow=always", "submodule", "add", "-q",
        str(inner), "sub")
    _git(outer, "commit", "-q", "-m", "add submodule")
    return outer


def test_a_submodules_second_different_edit_produces_no_further_drift(
    repo_with_submodule,
):
    """Pins the disclosed limit rather than approving of it: a submodule
    reports as ONE path, and that path hashes to None -- it is a directory,
    not a file -- whether clean or dirty. `tree_state` sees only that the
    submodule IS dirty, never WHAT it now contains, so a second, different
    edit inside it moves nothing an attestation can perish on: an alternate
    implementation could be swapped into the submodule after a record is
    taken, and the record would still apply. This is NOT a bug this slice
    fixes -- hashing submodule content would be a mechanism change, not a
    disclosure fix, and this suite does not make it; the test exists so the
    limit cannot silently regress into something worse, and so it cannot
    silently go undisclosed either.
    """
    from scripts.utils.canopus_freeze import tree_drift
    from scripts.utils.canopus_tree import tree_state

    outer = repo_with_submodule
    (outer / "sub" / "f.py").write_text("v = 2\n", encoding="utf-8")
    first = tree_state(outer)
    assert first["dirty"].get("sub") is None, (
        "a dirty submodule hashes to None: it is a directory, not a file"
    )

    (outer / "sub" / "f.py").write_text("v = 3\n", encoding="utf-8")
    second = tree_state(outer)

    assert tree_drift(first, second) == [], (
        "the documented ceiling: a SECOND, DIFFERENT edit inside a dirty "
        "submodule produces no further drift at all"
    )
