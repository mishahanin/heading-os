"""The push walls inspected the present state and the push shipped the past.

`scripts/push-all.py` decided what to inspect from the working tree, the index,
and a two-endpoint `origin/main..HEAD` diff, and then read the bytes back off
the disk. A push does not send the disk. It sends the objects the commits carry,
including the version of a file that an intermediate commit introduced and a
later edit removed.

MEASURED 2026-08-29 on a real repository with a real bare remote
(`.tmp/audit/measure61.py`), before the fix:

  secret committed with `--no-verify`, then wiped from the working tree
    -> the file WAS listed, the scanner read the cleaned bytes off the disk,
       "No secrets detected.", exit 0, and the push shipped the commit.
  commit A adds the secret, commit B removes it, both unpushed
    -> the two-endpoint diff nets to nothing, the file was not even listed,
       exit 0, and the push shipped commit A.
  control, the same secret in the working tree
    -> refused, so the harness was measuring something.

The first is the exact scenario `content_scan`'s docstring claimed to cover: "a
bypassed commit is still caught before anything leaves the machine". All three
walls shared the blind spot, because all three asked the same wrong question.

Two controls are load-bearing in this file and are not padding. A clean repo
must still PASS, or a wall that refuses everything would score full marks here.
And a secret in the working tree must still be REFUSED, or the history pass
could have quietly replaced the coverage it was meant to add.
"""
from __future__ import annotations

import ast
import base64
import importlib.util
import inspect
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.utils.engine_guard import (  # noqa: E402
    engine_text_files,
    engine_text_rels,
    scan_engine_repo,
)
from scripts.utils import push_history  # noqa: E402
from scripts.utils.push_history import (  # noqa: E402
    HistoryBlob,
    HistoryUnavailable,
    generations,
    read_blob,
    unpushed_blobs,
    unpushed_paths,
)

# Every child this file spawns is `git` in a scratch tree, and `git` has never
# read HEADING_OS_DATA. Pinning it away from the operator's live overlay costs
# these tests nothing and removes them from the reachability ratchet in
# tests/conftest.py. See the `scratch_data_root` fixture for the measurement.
pytestmark = pytest.mark.usefixtures("scratch_data_root")

# push-all.py calls ensure_venv() at MODULE scope; tests/conftest.py sets the
# guard that stops it re-execing pytest. Same note as tests/test_push_all_gate.py.
_spec = importlib.util.spec_from_file_location(
    "push_all_shard61", ROOT / "scripts" / "push-all.py")
push_all = importlib.util.module_from_spec(_spec)
sys.modules["push_all_shard61"] = push_all
_spec.loader.exec_module(push_all)


# Assembled at import, never written whole. A literal of this shape is a
# secret-shaped string in a tracked file, and this workspace's own commit gate
# refuses those on sight -- correctly, since it cannot tell a planted one from a
# live one. `test_the_planted_value_is_one_the_scanner_matches` proves the
# assembly still trips the vocabulary, because most assertions below are
# ABSENCES and an absence is satisfied by a token that was never a token.
#
# Named TRIPWIRE, not SECRET. `detect-secrets` has a "Secret Keyword" rule that
# fires on the NAME of an assignment, so `SECRET = "..."` refused this commit
# whatever the right-hand side was. Adding `pragma: allowlist secret` would have
# cleared it by switching the gate off over this line, and a gate switched off
# for convenience is the defect this whole audit keeps finding.
TRIPWIRE = "sk-ant-api03-" + ("Z" * 40) + "-" + ("q" * 12)


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], check=check,
                          capture_output=True, text=True)


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    """A clone with a real bare remote, one pushed commit, on `origin/main`.

    A real remote and not a stub: every function under test asks git what is
    reachable from HEAD and not from `origin/main`, and a fabricated ref would
    make the answer come from the fixture rather than from git.
    """
    bare = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", "-q", str(bare)], check=True)
    work = tmp_path / "work"
    subprocess.run(["git", "clone", "-q", str(bare), str(work)], check=True)
    _git(work, "config", "user.email", "t@example.invalid")
    _git(work, "config", "user.name", "Shard Sixty One")
    (work / "seed.md").write_text("seed\n", encoding="utf-8")
    _git(work, "add", "-A")
    _git(work, "commit", "-q", "-m", "seed")
    _git(work, "push", "-q", "origin", "HEAD:main")
    _git(work, "fetch", "-q", "origin")
    return work


def _commit(repo: Path, rel: str, body: str | None, message: str) -> None:
    """Write (or delete, when body is None) `rel` and commit it."""
    target = repo / rel
    if body is None:
        target.unlink()
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "--no-verify", "-m", message)


# ============================================================
# The planted value
# ============================================================


def test_the_planted_value_is_one_the_scanner_matches(tmp_path: Path) -> None:
    """Assembling the token must not have made it stop being a token."""
    probe = tmp_path / "probe.md"
    probe.write_text(f"key: {TRIPWIRE}\n", encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "secret-scanner.py"), str(probe)],
        capture_output=True, text=True)
    assert proc.returncode == 1, (
        "the assembled value is not something the workspace vocabulary matches, "
        "so every absence assertion in this file would pass vacuously")


# ============================================================
# push_history.unpushed_blobs
# ============================================================


def test_nothing_unpushed_means_no_blobs(repo: Path) -> None:
    assert unpushed_blobs(repo) == []


def test_a_committed_file_is_listed(repo: Path) -> None:
    _commit(repo, "a.md", "hello\n", "add a")
    assert [b.rel for b in unpushed_blobs(repo)] == ["a.md"]


def test_a_worktree_edit_is_not_history(repo: Path) -> None:
    """The two answers are different questions; this one must not absorb the other."""
    (repo / "a.md").write_text("uncommitted\n", encoding="utf-8")
    assert unpushed_blobs(repo) == []


def test_both_versions_are_listed_when_two_commits_touch_one_file(repo: Path) -> None:
    _commit(repo, "a.md", "first\n", "one")
    _commit(repo, "a.md", "second\n", "two")
    blobs = unpushed_blobs(repo)
    assert [b.rel for b in blobs] == ["a.md", "a.md"]
    assert len({b.sha for b in blobs}) == 2


def test_a_version_deleted_by_a_later_commit_is_still_listed(repo: Path) -> None:
    """THE DEFECT, at the enumeration layer.

    `git diff origin/main..HEAD` compares two endpoints, so a file added and then
    removed nets to nothing and does not appear at all. The push still carries
    the commit that added it.
    """
    _commit(repo, "gone.md", "was here\n", "add")
    _commit(repo, "gone.md", None, "remove")
    two_endpoint = _git(repo, "diff", "--name-only", "origin/main..HEAD").stdout
    assert "gone.md" not in two_endpoint, (
        "the premise of this test is that the endpoint diff misses it")
    assert "gone.md" in unpushed_paths(repo)


def test_a_parentless_commit_in_the_range_is_still_walked(repo: Path) -> None:
    """`--root`, which had no witness at all until 2026-09-01.

    `git diff-tree` on a commit with no parent emits NOTHING unless `--root` is
    given: there is no other tree to diff against. So a range containing a
    parentless commit reports not "fewer paths" but ZERO, and all three push
    walls then inspect an empty list and pass.

    This is not a hypothetical shape. The workspace's standing plan for making
    the engine repository public is to squash or rewrite its history first, "so
    the initial-import commit cannot ship any pre-cleanup content" - and a
    rewrite produces exactly this: a brand new root commit that `origin/main`
    cannot reach. The one push where the walls matter most is the one where they
    would have seen nothing.

    MEASURED 2026-09-01 by deleting `--root` from the `diff-tree` argument list:
    `rev-list origin/main..HEAD` still counted the commit, `unpushed_paths`
    returned `[]` where it returns `['leaked.md']` today, and all 66 tests in
    this file stayed green.
    """
    _git(repo, "checkout", "-q", "--orphan", "rewritten")
    _git(repo, "rm", "-rq", "--cached", ".")
    for path in repo.iterdir():
        if path.name != ".git":
            path.unlink()
    _commit(repo, "leaked.md", "planted\n", "squashed import")

    assert len(_git(repo, "rev-list", "origin/main..HEAD").stdout.split()) == 1, (
        "the premise is a single parentless commit in the unpushed range")
    assert unpushed_paths(repo) == ["leaked.md"]


def test_a_blob_already_on_the_base_is_not_listed(repo: Path) -> None:
    _commit(repo, "a.md", "hello\n", "add a")
    _git(repo, "push", "-q", "origin", "HEAD:main")
    _git(repo, "fetch", "-q", "origin")
    assert unpushed_blobs(repo) == []


def test_identical_pairs_are_deduplicated(repo: Path) -> None:
    """Two commits restoring the same bytes give one (rel, sha), not two."""
    _commit(repo, "a.md", "same\n", "one")
    _commit(repo, "a.md", "different\n", "two")
    _commit(repo, "a.md", "same\n", "three")
    blobs = unpushed_blobs(repo)
    assert len(blobs) == 2
    assert len(blobs) == len(set(blobs))


def test_two_paths_with_identical_bytes_are_both_reported(repo: Path) -> None:
    """MEASURED HOLE, 2026-08-29, in the first version of this module.

    `git rev-list --objects` prints each OBJECT once, with ONE of its paths.
    Byte-identical files at `docs/note.md` and `outputs/operations/note.md`
    produced a single line naming only the first, so the routing wall -- which
    judges by path -- would never have seen the private one. The enumeration
    walks changes, not reachability, precisely because of this.
    """
    (repo / "docs").mkdir()
    (repo / "outputs" / "operations").mkdir(parents=True)
    (repo / "docs" / "note.md").write_text("same bytes\n", encoding="utf-8")
    (repo / "outputs" / "operations" / "note.md").write_text(
        "same bytes\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "--no-verify", "-m", "two paths, one blob")

    paths = unpushed_paths(repo)
    assert "docs/note.md" in paths
    assert "outputs/operations/note.md" in paths


def test_a_merge_commit_does_not_hide_a_path(repo: Path) -> None:
    """A `git log --name-only` over a merge shows nothing without `-m`."""
    start = _git(repo, "rev-parse", "HEAD").stdout.strip()
    _commit(repo, "trunk.md", "trunk\n", "on trunk")
    _git(repo, "checkout", "-q", "-b", "side", start)
    _commit(repo, "outputs/operations/side.md", "branch\n", "on the side")
    _git(repo, "checkout", "-q", "-")
    _git(repo, "merge", "-q", "--no-ff", "side", "-m", "merge")

    paths = unpushed_paths(repo)
    assert "trunk.md" in paths
    assert "outputs/operations/side.md" in paths


def test_content_created_by_a_conflict_resolution_is_reported(repo: Path) -> None:
    """The `-m` flag, measured.

    Without it `git diff-tree` prints NOTHING for a merge commit, and the test
    above passes anyway because the side branch's own commit is in the range. A
    conflict resolution is different: the resolved bytes exist in the merge
    commit and in neither parent, so they are the one thing only `-m` can see.
    A dropped `-m` survived a mutation run against the earlier merge test, which
    is how this case was found.
    """
    start = _git(repo, "rev-parse", "HEAD").stdout.strip()
    _commit(repo, "conf.md", "ours\n", "ours")
    _git(repo, "checkout", "-q", "-b", "side", start)
    _commit(repo, "conf.md", "theirs\n", "theirs")
    _git(repo, "checkout", "-q", "-")
    merge = _git(repo, "merge", "--no-ff", "side", "-m", "merge", check=False)
    assert merge.returncode != 0, "the fixture must actually conflict"
    (repo / "conf.md").write_text("resolved only in the merge\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "--no-verify", "-m", "resolve")

    resolved = {read_blob(repo, b.sha) for b in unpushed_blobs(repo)}
    assert b"resolved only in the merge\n" in resolved, (
        "the bytes that exist only in the merge commit were never enumerated")


def test_a_deletion_contributes_no_phantom_blob(repo: Path) -> None:
    """A delete record carries an all-zero destination; it is not an object."""
    _commit(repo, "gone.md", "was here\n", "add")
    _commit(repo, "gone.md", None, "remove")
    for blob in unpushed_blobs(repo):
        assert set(blob.sha) != {"0"}
        assert read_blob(repo, blob.sha) is not None


def test_only_blobs_are_returned_never_trees_or_commits(repo: Path) -> None:
    """`rev-list --objects` prints trees too, and a tree has a path."""
    _commit(repo, "deep/nested/a.md", "hello\n", "add")
    rels = [b.rel for b in unpushed_blobs(repo)]
    assert rels == ["deep/nested/a.md"], (
        f"a tree object leaked into the blob list: {rels}")


def test_a_non_ascii_path_survives_into_read_blob(repo: Path) -> None:
    """This is a bilingual workspace; a Cyrillic filename is ordinary here."""
    _commit(repo, "докум.md", "текст\n", "add")
    blobs = unpushed_blobs(repo)
    assert [b.rel for b in blobs] == ["докум.md"]
    assert read_blob(repo, blobs[0].sha).decode("utf-8") == "текст\n"


def test_a_directory_with_no_base_reports_nothing_rather_than_raising(
        tmp_path: Path) -> None:
    """No refs is a real emptiness, not a failure to look.

    RENAMED 2026-08-30. The name used to read "raises rather than reporting
    nothing" while the only assertion in the body was `== []` -- the opposite
    claim, with no `pytest.raises` anywhere in the function. `has_base` answers
    False for a directory with no refs, so returning `[]` is the CORRECT answer
    and the assertion was the right side; the name and docstring were the wrong
    one. The fail-closed half the old name promised now has its own test below,
    where the raise can actually be observed.
    """
    not_a_repo = tmp_path / "bare-dir"
    not_a_repo.mkdir()
    assert unpushed_blobs(not_a_repo) == []


def test_a_walk_that_fails_after_the_base_resolved_raises(
        repo: Path, monkeypatch) -> None:
    """Fail closed. "I could not look" must not read as "there is nothing".

    The case the sibling above cannot reach: a base that DOES resolve and a git
    call that then fails. `has_base` is satisfied, so an empty return here would
    be a wall reading a broken git as a clean history. Only `rev-list` is made
    to fail, so the two `rev-parse` probes still answer truthfully and the test
    lands on the branch it names rather than on the no-base early return.
    """
    real_git = push_history._git

    def failing(root: Path, *args: str):
        if args and args[0] == "rev-list":
            return subprocess.CompletedProcess([], 128, b"", b"fatal: bad revision")
        return real_git(root, *args)

    monkeypatch.setattr(push_history, "_git", failing)
    with pytest.raises(HistoryUnavailable):
        unpushed_blobs(repo)


def test_a_missing_blob_raises_rather_than_returning_empty_bytes(repo: Path) -> None:
    """An empty file and an unreadable object are different states."""
    with pytest.raises(HistoryUnavailable):
        read_blob(repo, "0" * 40)


def test_an_empty_blob_reads_as_empty_and_does_not_raise(repo: Path) -> None:
    _commit(repo, "empty.md", "", "add empty")
    blobs = unpushed_blobs(repo)
    assert read_blob(repo, blobs[0].sha) == b""


# ============================================================
# push_history.generations
# ============================================================


def test_distinct_paths_form_one_generation() -> None:
    blobs = [HistoryBlob("a.md", "1"), HistoryBlob("b.md", "2")]
    assert generations(blobs) == [blobs]


def test_a_repeated_path_forces_a_second_generation() -> None:
    blobs = [HistoryBlob("a.md", "1"), HistoryBlob("a.md", "2")]
    groups = generations(blobs)
    assert len(groups) == 2


@pytest.mark.parametrize("versions", [1, 2, 3, 5])
def test_the_generation_count_is_the_deepest_path_not_the_blob_count(
        versions: int) -> None:
    """Grouping by commit would have made this the commit count, for no gain."""
    blobs = [HistoryBlob("a.md", str(i)) for i in range(versions)]
    blobs += [HistoryBlob(f"other{i}.md", f"x{i}") for i in range(9)]
    assert len(generations(blobs)) == versions


def test_no_generation_ever_holds_one_path_twice() -> None:
    """The invariant the scratch layout depends on: one path, one file on disk."""
    blobs = [HistoryBlob("a.md", str(i)) for i in range(4)]
    blobs += [HistoryBlob("b.md", str(i)) for i in range(2)]
    for group in generations(blobs):
        rels = [b.rel for b in group]
        assert len(rels) == len(set(rels))


def test_every_blob_lands_in_exactly_one_generation() -> None:
    """A grouping that drops a blob is a gate that skips a version."""
    blobs = [HistoryBlob("a.md", str(i)) for i in range(3)]
    blobs += [HistoryBlob("b.md", str(i)) for i in range(3)]
    placed = [b for group in generations(blobs) for b in group]
    assert sorted(placed, key=lambda b: (b.rel, b.sha)) == sorted(
        blobs, key=lambda b: (b.rel, b.sha))


# ============================================================
# engine_guard: the path-only half of the filter
# ============================================================


def test_engine_text_rels_keeps_a_path_that_is_not_on_disk() -> None:
    """The whole reason the split exists: a deleted version still ships."""
    assert engine_text_rels(["scripts/never-existed-shard61.py"]) == [
        "scripts/never-existed-shard61.py"]


def test_engine_text_files_drops_the_same_path(tmp_path: Path) -> None:
    assert engine_text_files(tmp_path, ["scripts/never-existed-shard61.py"]) == []


def test_both_halves_drop_a_private_routed_path(tmp_path: Path) -> None:
    private = "outputs/operations/whatever.md"
    assert engine_text_rels([private]) == []
    assert engine_text_files(tmp_path, [private]) == []


def test_both_halves_drop_a_binary_suffix(tmp_path: Path) -> None:
    assert engine_text_rels(["docs/logo.png"]) == []
    assert engine_text_files(tmp_path, ["docs/logo.png"]) == []


def test_scan_engine_repo_without_extras_is_unchanged(repo: Path) -> None:
    """Eighteen callers pass no extras; none of them may change behaviour."""
    _commit(repo, "scripts/ok.py", "print(1)\n", "add code")
    assert scan_engine_repo(repo) == []


def test_scan_engine_repo_flags_an_extra_path_that_is_not_on_disk(repo: Path) -> None:
    flagged = scan_engine_repo(repo, extra_paths=["outputs/operations/leak.md"])
    assert "outputs/operations/leak.md" in flagged


# ============================================================
# The secret wall, end to end
# ============================================================


def _refuses(fn, *args) -> tuple[bool, str]:
    """Run a wall. Returns (refused, the SystemExit code rendered as a string).

    The second element is `str(exc.code)` -- `"2"` for every refusal this file
    asserts on -- and NOT captured output. The docstring said "captured
    stdout+stderr is not available here" until 2026-08-30, which invited a
    reader to treat the second element as disposable and drop the `code == "2"`
    assertions that distinguish a refusal from an arbitrary exit. Output is
    captured separately, with `capsys`.

    The walls call `sys.exit`, which is the contract the push path relies on, so
    the test asserts on the exception rather than on a return value. An empty
    string is returned when nothing was raised, so `code` is only meaningful
    when `refused` is True.
    """
    try:
        fn(*args)
    except SystemExit as exc:
        return True, str(exc.code)
    return False, ""


def test_a_clean_repository_still_passes(repo: Path, capsys) -> None:
    """CONTROL. A wall that refused everything would score full marks below."""
    _commit(repo, "note.md", "nothing to see\n", "clean")
    refused, _ = _refuses(push_all.content_scan, repo)
    assert not refused


def test_a_secret_in_the_working_tree_is_still_refused(repo: Path, capsys) -> None:
    """CONTROL. The coverage that already existed must not have been replaced."""
    (repo / "secret.md").write_text(f"key: {TRIPWIRE}\n", encoding="utf-8")
    refused, code = _refuses(push_all.content_scan, repo)
    assert refused and code == "2"
    assert "a file about to be pushed" in capsys.readouterr().out


def test_a_secret_committed_then_wiped_from_the_worktree_is_refused(
        repo: Path, capsys) -> None:
    """THE DEFECT. Measured passing before the fix; see this module's docstring."""
    _commit(repo, "secret.md", f"key: {TRIPWIRE}\n", "oops")
    (repo / "secret.md").write_text("key: [removed]\n", encoding="utf-8")
    refused, code = _refuses(push_all.content_scan, repo)
    assert refused and code == "2"
    assert "a commit about to be pushed" in capsys.readouterr().out


def test_a_secret_added_and_removed_across_two_commits_is_refused(
        repo: Path, capsys) -> None:
    """THE DEFECT, second shape: the endpoint diff nets to nothing."""
    _commit(repo, "secret.md", f"key: {TRIPWIRE}\n", "add")
    _commit(repo, "secret.md", None, "remove")
    refused, code = _refuses(push_all.content_scan, repo)
    assert refused and code == "2"


def test_the_refusal_names_a_commit_not_a_file(repo: Path, capsys) -> None:
    """The two remedies differ, so the wording has to.

    Telling the operator to edit a file when the leak lives in a commit sends
    them to do something that changes nothing.
    """
    _commit(repo, "secret.md", f"key: {TRIPWIRE}\n", "oops")
    (repo / "secret.md").write_text("clean\n", encoding="utf-8")
    _refuses(push_all.content_scan, repo)
    out = capsys.readouterr().out
    assert "the history has to be rewritten" in out
    assert "secret-like CONTENT in a file about to be pushed" not in out


def test_the_scanner_skip_list_still_applies_to_history(
        repo: Path, capsys) -> None:
    """A commit that edits the scanner's own source must not refuse the backup.

    `secret-scanner.py`, `secret_patterns.py` and `.env.example` hold secret
    patterns by definition and the scanner skips them by REPO-RELATIVE path. The
    history pass lays blobs out in a scratch tree, so without pinning
    `WORKSPACE_ROOT` to that tree the skip would not resolve and every commit
    touching those three files would block the push. This workspace edits them.

    The file is DELETED by a second commit so only the history pass sees it. In
    the working tree of a temporary fixture repo the path resolves under neither
    workspace root, so the skip legitimately does not apply there and the wall
    legitimately refuses -- a fact about the fixture, not about the pin. Driving
    `history_content_scan` alone is what isolates the property.
    """
    _commit(repo, "scripts/utils/secret_patterns.py",
            f'EXAMPLE = "{TRIPWIRE}"\n', "edit the pattern module")
    _commit(repo, "scripts/utils/secret_patterns.py", None, "and remove it")
    assert "scripts/utils/secret_patterns.py" in unpushed_paths(repo)
    refused, _ = _refuses(push_all.history_content_scan, repo)
    assert not refused, capsys.readouterr().out


def test_the_skip_list_does_not_blanket_every_path_in_the_scratch_tree(
        repo: Path, capsys) -> None:
    """The pin above must not have turned the whole history pass into a no-op.

    Same shape as the test before it -- committed then deleted, so only history
    sees it -- but at an ordinary path. If moving `WORKSPACE_ROOT` had made the
    scanner skip everything, the test above would pass for the wrong reason and
    this one would fail.
    """
    _commit(repo, "scripts/ordinary.py", f'KEY = "{TRIPWIRE}"\n', "add")
    _commit(repo, "scripts/ordinary.py", None, "remove")
    refused, code = _refuses(push_all.history_content_scan, repo)
    assert refused and code == "2"


def test_a_refusal_from_history_is_recorded_in_the_real_denial_log(
        repo: Path, tmp_path: Path, monkeypatch) -> None:
    """The scratch tree is deleted seconds later; the record must not be in it.

    `denial_log_path` derives from `get_workspace_root()`, which the history pass
    moves. Without the `WORKSPACE_LOG_DIR` pin the wall would refuse and keep no
    evidence of having refused.
    """
    # WORKSPACE_LOG_DIR is deliberately NOT set. Setting it was the first
    # version of this test and it measured nothing: the child inherits the
    # parent environment, so the variable arrived whether or not the wall passed
    # it, and the mutation that deletes the pin SURVIVED. Moving WORKSPACE_ROOT
    # instead makes the DERIVED default point somewhere observable, which is the
    # only arrangement in which the pin can be seen doing work.
    monkeypatch.delenv("WORKSPACE_LOG_DIR", raising=False)
    fake_workspace = tmp_path / "fake-workspace"
    fake_workspace.mkdir()
    monkeypatch.setenv("WORKSPACE_ROOT", str(fake_workspace))
    _commit(repo, "secret.md", f"key: {TRIPWIRE}\n", "oops")
    (repo / "secret.md").write_text("clean\n", encoding="utf-8")
    _refuses(push_all.content_scan, repo)

    log = fake_workspace / ".logs" / "denials" / "denials.jsonl"
    assert log.is_file(), "the refusal left no record outside the scratch tree"
    records = [json.loads(line) for line in
               log.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert any(r.get("context", "").endswith(":history") for r in records), (
        f"no record names the history pass: {records}")


def test_the_scratch_tree_is_gone_afterwards(repo: Path, monkeypatch) -> None:
    """A directory of extracted secrets must not outlive the scan."""
    seen: list[Path] = []
    real_run = push_all._run_scanner

    def spy(paths, cwd, context, extra_env=None):
        seen.append(Path(cwd))
        return real_run(paths, cwd, context, extra_env)

    monkeypatch.setattr(push_all, "_run_scanner", spy)
    _commit(repo, "a.md", "harmless\n", "add")
    push_all.history_content_scan(repo)
    assert seen, "the history pass never ran the scanner"
    for path in seen:
        assert not path.exists(), f"scratch tree survived: {path}"


# Every wall that asks git what the push will send, paired with the enumerator
# it asks through. All three must fail CLOSED, and until 2026-09-01 only the
# first had a witness: replacing the `sys.exit(2)` in `engine_clean_scan` or in
# `engine_content_scan` with a silent fall-through left all 67 tests in this
# file GREEN, measured one refusal at a time. That is a wall failing open in the
# one state where it cannot see - the exact failure `HistoryUnavailable`'s own
# docstring says it exists as an exception rather than an empty list to prevent.
#
# Named as (wall, enumerator) pairs derived from what each function calls, so a
# fourth wall added later shows up as a missing entry rather than as silence.
_HISTORY_WALLS = [
    ("history_content_scan", "unpushed_blobs"),
    ("engine_clean_scan", "unpushed_paths"),
    ("engine_content_scan", "unpushed_blobs"),
]


@pytest.mark.parametrize("wall,enumerator", _HISTORY_WALLS,
                         ids=[w for w, _ in _HISTORY_WALLS])
def test_every_history_wall_refuses_when_git_cannot_answer(
        repo: Path, monkeypatch, capsys, wall, enumerator) -> None:
    """Fail closed on the tooling error, exactly as the enumeration does."""
    def boom(*_a, **_k):
        raise HistoryUnavailable("git said no")

    class _CleanDenylist:
        degraded = False
        tokens = {"never-appears-shard30"}

        def scan_text(self, _text):
            return iter(())

    # `engine_content_scan` refuses on a degraded denylist BEFORE it asks git
    # anything, and a scratch clone is not a DATA overlay, so without this stub
    # the case would exit 2 for the wrong reason and pass while proving nothing.
    # The other two walls never call it. Same stub the two content-wall tests
    # below already install.
    monkeypatch.setattr(push_all, "build_denylist", lambda _root: _CleanDenylist())

    fn = getattr(push_all, wall)
    # `engine_content_scan` takes (repo, data_root); the other two take (repo).
    # Read the arity off the live signature rather than hard-coding it, so a
    # wall that gains a parameter fails here as a TypeError naming the wall
    # instead of quietly dropping out of the table.
    # POSITIONAL parameters only. A keyword-only parameter with a default is
    # not part of the arity this line means, and counting one made this pass
    # three positionals to `engine_content_scan` the day it gained
    # `*, will_commit` (2026-09-05) and fail with an arity error about a
    # signature that was fine.
    args = tuple(repo for name, param in inspect.signature(fn).parameters.items()
                 if param.kind in (param.POSITIONAL_ONLY,
                                   param.POSITIONAL_OR_KEYWORD))
    monkeypatch.setattr(push_all, enumerator, boom)
    refused, code = _refuses(fn, *args)
    assert refused and code == "2", (
        f"{wall} carried on after git could not report the unpushed history")
    assert "cannot read the unpushed history" in capsys.readouterr().out


def test_the_list_of_history_walls_is_the_live_one() -> None:
    """Derived from the module, so a fourth wall cannot be added unwitnessed.

    The parametrized cases above can only speak for the walls named in them. This
    asks `scripts/push-all.py` which of its functions call the history
    enumerators at all, and requires the answer to be exactly the set that has a
    fail-closed case. Set equality rather than a count: a count is satisfied by
    naming the wrong three.
    """
    tree = ast.parse((ROOT / "scripts" / "push-all.py").read_text(encoding="utf-8"))
    live = {}
    for fn in (n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)):
        called = {c.func.id for c in ast.walk(fn)
                  if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)}
        used = called & {"unpushed_blobs", "unpushed_paths"}
        if used:
            live[fn.name] = used
    assert set(live) == {w for w, _ in _HISTORY_WALLS}, (
        f"the set of walls reading the unpushed history changed: {sorted(live)}. "
        f"Add a fail-closed case for the new one before listing it here.")
    for wall, enumerator in _HISTORY_WALLS:
        assert enumerator in live[wall], (
            f"{wall} no longer calls {enumerator}, so its case above patches a "
            f"name it never reads and proves nothing")


# ============================================================
# The routing wall, end to end
# ============================================================


def test_a_private_routed_file_deleted_in_a_later_commit_is_refused(
        repo: Path, capsys) -> None:
    """The 2026-06-22 leak in the shape it would take today.

    Commit the private-routed file, notice, `git rm` it, commit again, push.
    Both commits reach the remote and the working tree has nothing to report.
    """
    _commit(repo, "outputs/operations/leak.md", "private\n", "add")
    _commit(repo, "outputs/operations/leak.md", None, "remove")
    assert scan_engine_repo(repo) == [], (
        "the premise is that the working-tree scan sees nothing")
    refused, code = _refuses(push_all.engine_clean_scan, repo)
    assert refused and code == "2"
    out = capsys.readouterr().out
    assert "unpushed history" in out
    assert "Rewrite the range" in out


def test_a_clean_engine_tree_and_history_still_passes(repo: Path) -> None:
    """CONTROL for the routing wall."""
    _commit(repo, "scripts/ok.py", "print(1)\n", "add code")
    refused, _ = _refuses(push_all.engine_clean_scan, repo)
    assert not refused


def test_the_routing_wall_labels_which_world_flagged_a_path(
        repo: Path, capsys) -> None:
    """One path can be flagged twice for two reasons with two remedies."""
    _commit(repo, "outputs/operations/live.md", "private\n", "add")
    _refuses(push_all.engine_clean_scan, repo)
    out = capsys.readouterr().out
    assert "[working tree]" in out


# ============================================================
# The real-entity wall reads history too
# ============================================================


def test_the_content_wall_reads_a_version_a_later_commit_deleted(
        repo: Path, monkeypatch, capsys) -> None:
    """The third wall shared the blind spot; fixing two of three is the defect
    this audit keeps finding."""
    # An invented name, and named `entity` rather than `token`: the linter reads
    # `token = "..."` as a credential literal, which this is not.
    entity = "Nikolai Vetrov-Shard61"

    class FakeDenylist:
        degraded = False
        tokens = {entity}

        def scan_text(self, text):
            for lineno, line in enumerate(text.splitlines(), 1):
                if entity in line:
                    yield lineno, entity, "person"

    monkeypatch.setattr(push_all, "build_denylist", lambda _root: FakeDenylist())
    _commit(repo, "docs/note.md", f"see {entity}\n", "add")
    _commit(repo, "docs/note.md", "see a placeholder\n", "genericise")

    refused, code = _refuses(push_all.engine_content_scan, repo, repo)
    assert refused and code == "2"
    out = capsys.readouterr().out
    assert "@" in out, "the finding does not name the blob it came from"
    assert "the commit range has to be rewritten" in out


def test_the_content_wall_still_passes_a_clean_history(
        repo: Path, monkeypatch) -> None:
    """CONTROL for the third wall."""
    class FakeDenylist:
        degraded = False
        tokens = {"never-appears-shard61"}

        def scan_text(self, _text):
            return iter(())

    monkeypatch.setattr(push_all, "build_denylist", lambda _root: FakeDenylist())
    _commit(repo, "docs/note.md", "ordinary prose\n", "add")
    refused, _ = _refuses(push_all.engine_content_scan, repo, repo)
    assert not refused


# ============================================================
# The shape of the module, so a later reader cannot undo it quietly
# ============================================================


def test_no_wall_reads_only_the_present(repo: Path, monkeypatch) -> None:
    """Each of the three walls must reach the history enumeration.

    Asked of the CALL, not of the import. A test that only checks that
    `push-all` imports `unpushed_blobs` passes while every wall ignores it --
    that exact gap survived three mutations in the previous shard.

    HERMETICITY, fixed 2026-08-30. This test used to drive the three walls
    against `Path(".")` -- the checkout pytest happens to run in -- with
    `monkeypatch.setattr(push_all, "repo_carried_paths", ...)` standing in for
    isolation. That patch was INERT: `engine_clean_scan` reaches the working
    tree through `engine_guard.scan_engine_repo`, which resolves
    `repo_carried_paths` from `engine_guard`'s OWN globals, so rebinding the
    name in `push_all` changed nothing. MEASURED 2026-08-30
    (`.tmp/pkgb_probe_wall.py`): with that exact patch applied and the cwd
    pointed at a scratch tree carrying `outputs/operations/leak.md`, the wall
    still refused with exit 2. The test passed only because this checkout
    happens to be clean, so it had never once exercised a wall against a tree
    it controlled. It now runs against the `repo` fixture, and the sibling
    below pins that the repository argument -- not the process cwd -- is what
    the walls read.
    """
    calls: list[str] = []

    def spy_blobs(repo_arg, *a, **k):
        calls.append("blobs")
        return []

    def spy_paths(repo_arg, *a, **k):
        calls.append("paths")
        return []

    monkeypatch.setattr(push_all, "unpushed_blobs", spy_blobs)
    monkeypatch.setattr(push_all, "unpushed_paths", spy_paths)
    monkeypatch.setattr(push_all, "_push_delta_files", lambda _r, **_k: set())

    class FakeDenylist:
        degraded = False
        tokens = {"x"}

        def scan_text(self, _t):
            return iter(())

    monkeypatch.setattr(push_all, "build_denylist", lambda _root: FakeDenylist())

    push_all.content_scan(repo)
    assert calls, "content_scan never asked about the history"
    calls.clear()
    push_all.engine_clean_scan(repo)
    assert calls, "engine_clean_scan never asked about the history"
    calls.clear()
    push_all.engine_content_scan(repo, repo)
    assert calls, "engine_content_scan never asked about the history"


def test_the_routing_wall_judges_the_repository_it_is_handed_not_the_cwd(
        repo: Path, tmp_path: Path, monkeypatch) -> None:
    """The negative case the hermeticity fix above needs to be worth anything.

    Both halves run with the process cwd pointed at a DIFFERENT tree, so a wall
    that reads `Path.cwd()` instead of its argument gets the wrong answer in one
    direction or the other and this test says so.

      clean repo handed in, dirty cwd  -> must NOT refuse
      dirty repo handed in, clean cwd  -> must refuse, exit 2

    The planted violation lives under `tmp_path`; nothing is written into the
    real checkout.
    """
    dirty = tmp_path / "dirty"
    (dirty / "outputs" / "operations").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(dirty)], check=True)
    (dirty / "outputs" / "operations" / "leak.md").write_text(
        "private\n", encoding="utf-8")
    monkeypatch.setattr(push_all, "unpushed_paths", lambda *a, **k: [])

    monkeypatch.chdir(dirty)
    refused, _ = _refuses(push_all.engine_clean_scan, repo)
    assert not refused, (
        "the wall refused over the cwd's leak while judging a clean repository")

    monkeypatch.chdir(repo)
    refused, code = _refuses(push_all.engine_clean_scan, dirty)
    assert refused and code == "2", (
        "the wall cleared a planted private-routed artifact in the repository "
        "it was handed")


def test_a_committed_path_that_escapes_the_scratch_tree_is_refused(
        repo: Path, monkeypatch, capsys) -> None:
    """The one place this file writes an attacker-influenced name.

    git tree entries cannot hold `..`, so the guard is belt and braces -- but a
    guard with no test is a guard nobody knows is there.

    REWRITTEN 2026-08-30. This was two raw substring greps over
    `scripts/push-all.py` (`"is_relative_to(root.resolve())"` and `"a committed
    path escapes"`), which is the technique the sibling
    `test_the_history_pass_has_no_environment_opt_out` documents as a defect in
    its own docstring: a substring cannot tell a live read from a mention of
    one, and the SECOND string asserted was the guard's own error message, so
    deleting the check while leaving the message behind kept the test green.
    The enumeration is stubbed instead, which is the only way to hand the layout
    code a path git itself would never produce.
    """
    escape = "../outside-the-scratch-tree.md"
    monkeypatch.setattr(push_all, "unpushed_blobs",
                        lambda _r: [HistoryBlob(escape, "0" * 39 + "1")])
    monkeypatch.setattr(push_all, "read_blob", lambda _r, _sha: b"ordinary\n")
    ran: list[str] = []
    monkeypatch.setattr(push_all, "_run_scanner",
                        lambda *a, **k: ran.append("scanned") or
                        subprocess.CompletedProcess([], 0, "", ""))

    refused, code = _refuses(push_all.history_content_scan, repo)
    assert refused and code == "2"
    assert "a committed path escapes" in capsys.readouterr().out
    assert not ran, "the escaping path reached the scanner instead of being refused"
    assert not (repo.parent / "outside-the-scratch-tree.md").exists()


def test_an_ordinary_committed_path_still_reaches_the_scanner(
        repo: Path, monkeypatch) -> None:
    """CONTROL for the escape guard: it must not refuse every layout.

    Without this, a guard that rejected `..` and everything else would score
    full marks on the test above.
    """
    monkeypatch.setattr(push_all, "unpushed_blobs",
                        lambda _r: [HistoryBlob("docs/ordinary.md", "0" * 39 + "1")])
    monkeypatch.setattr(push_all, "read_blob", lambda _r, _sha: b"ordinary\n")
    laid: list[str] = []

    def spy(paths, cwd, context, extra_env=None):
        laid.extend(paths)
        return subprocess.CompletedProcess([], 0, "", "")

    monkeypatch.setattr(push_all, "_run_scanner", spy)
    refused, _ = _refuses(push_all.history_content_scan, repo)
    assert not refused
    assert laid == ["docs/ordinary.md"]


def test_a_symlink_blob_lands_as_an_ordinary_file(repo: Path, monkeypatch) -> None:
    """A committed symlink must not become a link inside the scratch tree.

    Materialising one would let a commit point the scanner at a path outside the
    tree it is supposed to be reading. This workspace creates no symlinks; a
    repository handed to the wall is not obliged to share that habit.
    """
    (repo / "target.md").write_text("real\n", encoding="utf-8")
    subprocess.run(["ln", "-s", "target.md", str(repo / "link.md")], check=True)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "--no-verify", "-m", "add a link")

    # Inspected INSIDE the spy. The scratch tree is a TemporaryDirectory and is
    # gone by the time `history_content_scan` returns, so a check afterwards
    # measures nothing but its own FileNotFoundError.
    seen: list[tuple[bool, bytes]] = []

    def spy(paths, cwd, context, extra_env=None):
        for rel in paths:
            if Path(rel).name == "link.md":
                laid = Path(cwd) / rel
                seen.append((laid.is_symlink(), laid.read_bytes()))
        return subprocess.CompletedProcess([], 0, "", "")

    monkeypatch.setattr(push_all, "_run_scanner", spy)
    push_all.history_content_scan(repo)

    assert seen, "the committed symlink was never laid out"
    for is_link, data in seen:
        assert not is_link
        assert data == b"target.md"


def test_the_planted_secret_is_not_written_whole_into_this_file() -> None:
    """The commit gate refuses a secret-shaped literal, and it is right to.

    Recorded as a test so a later reader who "tidies" the assembly back into one
    string learns why it was split before the pre-commit hook tells them.
    """
    source = Path(__file__).read_text(encoding="utf-8")
    assert TRIPWIRE not in source
    # base64 keeps the needle out of a grep of this file while still naming it.
    assert base64.b64decode("c2stYW50LWFwaTAz").decode() in source


# Every spelling of "read something out of the environment" that Python offers
# through the two names this file can see. WIDENED 2026-08-30: the walker below
# used to recognise exactly `os.getenv` and `os.environ.get`, so the four
# ordinary spellings in `_ENV_READ_SPELLINGS` -- a subscript, an aliased import,
# `setdefault`, `pop` -- each added a live opt-out surface that the assertion's
# own message claimed could not exist.
_ENV_CALLEES = frozenset({
    "os.getenv", "getenv",
    "os.environ.get", "environ.get",
    "os.environ.setdefault", "environ.setdefault",
    "os.environ.pop", "environ.pop",
    "os.environb.get", "environb.get",
})
_ENV_SUBSCRIPT_BASES = frozenset({"os.environ", "environ", "os.environb", "environb"})
# What a read whose key is not a literal reports instead of raising. The old
# walker called `ast.literal_eval` on the first argument, so `os.getenv(prefix +
# name)` made the test ERROR with a traceback rather than fail with the message
# that explains what is wrong.
_NON_LITERAL = "<non-literal>"


def _env_reads(fn: ast.AST) -> list[str]:
    """Every environment key `fn` reads, in source order. `_NON_LITERAL` for a
    key the parser cannot resolve, which is a finding rather than an error."""
    found: list[tuple[int, int, str]] = []
    for node in ast.walk(fn):
        key = None
        if isinstance(node, ast.Call) and node.args:
            if ast.unparse(node.func) in _ENV_CALLEES:
                key = node.args[0]
        elif (isinstance(node, ast.Subscript)
                and ast.unparse(node.value) in _ENV_SUBSCRIPT_BASES):
            key = node.slice
        if key is None:
            continue
        try:
            name = ast.literal_eval(key)
        except (ValueError, TypeError, SyntaxError):
            name = _NON_LITERAL
        found.append((node.lineno, node.col_offset, name))
    return [name for _, _, name in sorted(found)]


@pytest.mark.parametrize("body,expected", [
    ("os.getenv('A')", ["A"]),
    ("os.environ.get('A')", ["A"]),
    ("os.environ['A']", ["A"]),
    ("environ['A']", ["A"]),
    ("getenv('A')", ["A"]),
    ("os.environ.setdefault('A', '1')", ["A"]),
    ("os.environ.pop('A', None)", ["A"]),
    ("os.getenv(prefix + 'A')", [_NON_LITERAL]),
    ("os.path.join('A', 'B')", []),
    ("d['A']", []),
])
def test_the_environment_walker_sees_each_spelling_of_a_read(
        body: str, expected: list[str]) -> None:
    """The negative case for the guard below. NEW 2026-08-30.

    A detector with no case that makes it fire measures nothing, and this one
    had none: the assertion below was green over a `history_content_scan` that
    read nothing, so a walker that recognised NOTHING would have scored full
    marks. Both directions are pinned -- the six reads must be seen, and the two
    non-reads must not be.
    """
    tree = ast.parse(f"def probe():\n    {body}\n")
    assert _env_reads(tree) == expected


def test_the_history_pass_has_no_environment_opt_out() -> None:
    """No skip flag. An unbypassable wall with an opt-out is a bypassable wall.

    Asked of the parsed function, not of a substring of the file. The first
    version of this test grepped the source and went red on the word `SKIP_PATHS`
    inside a comment, which is the whole failure mode of a substring assertion:
    it cannot tell a live read from a mention of one.

    SCOPE, stated rather than implied: `ast.walk` covers `history_content_scan`
    and the nested definitions inside it, and nothing else. A read moved into a
    module-level helper that this function calls is outside what this test can
    see -- `test_the_helpers_the_history_pass_calls_have_no_opt_out_either`
    below covers the helpers that exist today.
    """
    tree = ast.parse((ROOT / "scripts" / "push-all.py").read_text(encoding="utf-8"))
    fn = next(node for node in ast.walk(tree)
              if isinstance(node, ast.FunctionDef)
              and node.name == "history_content_scan")
    read_names = _env_reads(fn)
    assert read_names == ["WORKSPACE_LOG_DIR"], (
        f"the history pass reads {read_names}; the only environment value it may "
        "read is the log-directory pin, and anything else is an opt-out surface")


def test_the_helpers_the_history_pass_calls_have_no_opt_out_either() -> None:
    """The hole the scoping note above names, closed for the helpers on the path.

    `history_content_scan` delegates the layout and the scanner launch, so a
    skip flag read one frame down would be just as much an opt-out and the test
    above cannot see it. `_run_scanner` legitimately reads nothing by name (it
    copies `os.environ` wholesale to build the child environment), so the
    allowance is empty rather than a list.
    """
    tree = ast.parse((ROOT / "scripts" / "push-all.py").read_text(encoding="utf-8"))
    by_name = {node.name: node for node in ast.walk(tree)
               if isinstance(node, ast.FunctionDef)}
    for helper in ("_run_scanner", "_refuse_on_scanner"):
        assert helper in by_name, f"{helper} was renamed; this guard now measures nothing"
        assert _env_reads(by_name[helper]) == [], (
            f"{helper} reads the environment by name: {_env_reads(by_name[helper])}")


def test_os_is_imported_where_the_log_pin_needs_it() -> None:
    """Guards the one runtime name the log pin depends on."""
    assert hasattr(push_all, "os") and push_all.os is os
