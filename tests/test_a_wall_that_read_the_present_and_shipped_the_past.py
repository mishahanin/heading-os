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
from scripts.utils.push_history import (  # noqa: E402
    HistoryBlob,
    HistoryUnavailable,
    generations,
    read_blob,
    unpushed_blobs,
    unpushed_paths,
)

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


def test_a_broken_git_environment_raises_rather_than_reporting_nothing(
        tmp_path: Path) -> None:
    """Fail closed. "I could not look" must not read as "there is nothing"."""
    not_a_repo = tmp_path / "bare-dir"
    not_a_repo.mkdir()
    # `has_base` answers False here, so the function returns [] rather than
    # raising -- and that is the CORRECT answer for a directory with no refs.
    # The raise is reserved for a base that resolves and a walk that then fails.
    assert unpushed_blobs(not_a_repo) == []


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
    """Run a wall. Returns (refused, captured stdout+stderr is not available here).

    The walls call `sys.exit`, which is the contract the push path relies on, so
    the test asserts on the exception rather than on a return value.
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


def test_the_history_pass_refuses_when_git_cannot_answer(
        repo: Path, monkeypatch, capsys) -> None:
    """Fail closed on the tooling error, exactly as the enumeration does."""
    def boom(*_a, **_k):
        raise HistoryUnavailable("git said no")

    monkeypatch.setattr(push_all, "unpushed_blobs", boom)
    refused, code = _refuses(push_all.history_content_scan, repo)
    assert refused and code == "2"
    assert "cannot read the unpushed history" in capsys.readouterr().out


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


def test_no_wall_reads_only_the_present(monkeypatch) -> None:
    """Each of the three walls must reach the history enumeration.

    Asked of the CALL, not of the import. A test that only checks that
    `push-all` imports `unpushed_blobs` passes while every wall ignores it --
    that exact gap survived three mutations in the previous shard.
    """
    calls: list[str] = []

    def spy_blobs(repo, *a, **k):
        calls.append("blobs")
        return []

    def spy_paths(repo, *a, **k):
        calls.append("paths")
        return []

    monkeypatch.setattr(push_all, "unpushed_blobs", spy_blobs)
    monkeypatch.setattr(push_all, "unpushed_paths", spy_paths)
    monkeypatch.setattr(push_all, "_push_delta_files", lambda _r: set())
    monkeypatch.setattr(push_all, "repo_carried_paths", lambda _r: [])

    class FakeDenylist:
        degraded = False
        tokens = {"x"}

        def scan_text(self, _t):
            return iter(())

    monkeypatch.setattr(push_all, "build_denylist", lambda _root: FakeDenylist())

    push_all.content_scan(Path("."))
    assert calls, "content_scan never asked about the history"
    calls.clear()
    push_all.engine_clean_scan(Path("."))
    assert calls, "engine_clean_scan never asked about the history"
    calls.clear()
    push_all.engine_content_scan(Path("."), Path("."))
    assert calls, "engine_content_scan never asked about the history"


def test_the_scratch_layout_never_writes_outside_its_own_tree() -> None:
    """The one place this file writes an attacker-influenced name.

    git tree entries cannot hold `..`, so the guard is belt and braces -- but a
    guard with no test is a guard nobody knows is there.
    """
    source = (ROOT / "scripts" / "push-all.py").read_text(encoding="utf-8")
    assert "is_relative_to(root.resolve())" in source
    assert "a committed path escapes" in source


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


def test_the_history_pass_has_no_environment_opt_out() -> None:
    """No skip flag. An unbypassable wall with an opt-out is a bypassable wall.

    Asked of the parsed function, not of a substring of the file. The first
    version of this test grepped the source and went red on the word `SKIP_PATHS`
    inside a comment, which is the whole failure mode of a substring assertion:
    it cannot tell a live read from a mention of one.
    """
    tree = ast.parse((ROOT / "scripts" / "push-all.py").read_text(encoding="utf-8"))
    fn = next(node for node in ast.walk(tree)
              if isinstance(node, ast.FunctionDef)
              and node.name == "history_content_scan")
    read_names = []
    for node in ast.walk(fn):
        if isinstance(node, ast.Call):
            callee = ast.unparse(node.func)
            if callee in {"os.getenv", "os.environ.get"} and node.args:
                read_names.append(ast.literal_eval(node.args[0]))
    assert read_names == ["WORKSPACE_LOG_DIR"], (
        f"the history pass reads {read_names}; the only environment value it may "
        "read is the log-directory pin, and anything else is an opt-out surface")


def test_os_is_imported_where_the_log_pin_needs_it() -> None:
    """Guards the one runtime name the log pin depends on."""
    assert hasattr(push_all, "os") and push_all.os is os
