#!/usr/bin/env python3
"""The commit source: what becomes an index row, and what must never.

Commit messages are the largest body of written reasoning in this workspace and
nothing retrieved them before this: no FTS, no vector, no CodeGraph edge. This
module turns `git log` into rows the existing memory-index store already accepts.

Three properties carry real risk and are tested here rather than assumed:

1. **The air gap.** A commit that touches a `personal` path (or the `_secure/`
   prefix) must not enter ANY persistent index -- not its message, not its paths,
   not a partial row. A commit that touches one denied file and nine allowed ones
   is skipped WHOLE, because indexing the message of a personal change leaks the
   change.
2. **Backup noise is excluded.** 153 of 1,257 commits are `chore: workspace
   backup <date>`; on the data side that is a fifth of all history. They answer no
   question and their near-identical vectors crowd real hits.
3. **Both body variants exist and differ.** The spec calls for measuring the
   changed-path list in the body against leaving it out, so the flag must actually
   change the text that gets embedded.

Design fixtures build real git repositories in tmp_path. A mocked `git log` would
test the parser against my own idea of git's output, which is the assumption most
worth not making.
"""
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.utils.commit_source import iter_commits, BACKUP_SUBJECT_RE  # noqa: E402


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=True
    ).stdout


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "r"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "Test")
    return repo


def _commit(repo: Path, rel: str, subject: str, body: str = "") -> str:
    f = repo / rel
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(f"{subject}\n", encoding="utf-8")
    _git(repo, "add", "-A")
    msg = subject if not body else f"{subject}\n\n{body}"
    _git(repo, "commit", "-q", "-m", msg)
    return _git(repo, "rev-parse", "HEAD").strip()


# --- the air gap -----------------------------------------------------------

def test_a_commit_touching_a_personal_path_is_never_indexed(tmp_path):
    repo = _repo(tmp_path)
    _commit(repo, "docs/ok.md", "public change")
    secret = _commit(repo, "chronicle/personal/2026-08-21.md", "private note")
    shas = {c["sha"] for c in iter_commits(repo, repo_label="data")}
    assert secret not in shas


def test_a_mixed_commit_is_skipped_whole_not_partially(tmp_path):
    """One denied file among many denies the commit. Its message describes it."""
    repo = _repo(tmp_path)
    (repo / "docs").mkdir()
    (repo / "docs" / "a.md").write_text("a\n", encoding="utf-8")
    (repo / "chronicle" / "personal").mkdir(parents=True)
    (repo / "chronicle" / "personal" / "b.md").write_text("b\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "touches both")
    rows = list(iter_commits(repo, repo_label="data"))
    assert rows == []


def test_the_vault_prefix_is_denied_too(tmp_path):
    repo = _repo(tmp_path)
    vault = _commit(repo, "_secure/x.md", "vault change")
    assert vault not in {c["sha"] for c in iter_commits(repo, repo_label="data")}


def test_case_does_not_open_the_air_gap(tmp_path):
    repo = _repo(tmp_path)
    upper = _commit(repo, "chronicle/Personal/x.md", "capitalised personal")
    assert upper not in {c["sha"] for c in iter_commits(repo, repo_label="data")}


# --- backup noise ----------------------------------------------------------

def test_backup_commits_are_excluded(tmp_path):
    repo = _repo(tmp_path)
    real = _commit(repo, "a.md", "feat: a real change")
    noise = _commit(repo, "b.md", "chore: workspace backup 2026-08-21 18:33")
    shas = {c["sha"] for c in iter_commits(repo, repo_label="engine")}
    assert real in shas
    assert noise not in shas


def test_the_backup_pattern_is_anchored_not_a_substring_match():
    """`fix: undo the workspace backup regression` is a real commit, not noise."""
    assert BACKUP_SUBJECT_RE.match("chore: workspace backup 2026-08-21 18:33")
    assert not BACKUP_SUBJECT_RE.match("fix: undo the workspace backup regression")


# --- row shape -------------------------------------------------------------

def test_a_row_carries_what_the_store_needs(tmp_path):
    repo = _repo(tmp_path)
    sha = _commit(repo, "a.md", "feat: the subject", "The reason it was done.")
    (row,) = list(iter_commits(repo, repo_label="engine"))
    assert row["sha"] == sha
    assert row["id"] == f"commit:engine:{sha}"
    assert row["path"] == f"engine@{sha}"
    assert row["title"] == "feat: the subject"
    assert row["ntype"] == "commit"
    assert isinstance(row["mtime"], float) and row["mtime"] > 0
    assert "The reason it was done." in row["body"]


def test_both_body_variants_exist_and_differ(tmp_path):
    repo = _repo(tmp_path)
    _commit(repo, "scripts/action-queue.py", "feat: something")
    with_paths = list(iter_commits(repo, repo_label="engine", include_paths=True))[0]
    without = list(iter_commits(repo, repo_label="engine", include_paths=False))[0]
    assert "scripts/action-queue.py" in with_paths["body"]
    assert "scripts/action-queue.py" not in without["body"]
    assert with_paths["body"] != without["body"]


def test_a_merge_commit_with_no_changed_paths_still_yields_a_row(tmp_path):
    """An empty commit has no paths, so it cannot be denied -- and must survive."""
    repo = _repo(tmp_path)
    _commit(repo, "a.md", "first")
    _git(repo, "commit", "-q", "--allow-empty", "-m", "chore: empty on purpose")
    subjects = {c["title"] for c in iter_commits(repo, repo_label="engine")}
    assert "chore: empty on purpose" in subjects


# --- incremental -----------------------------------------------------------

def test_since_limits_the_walk_to_new_commits(tmp_path):
    repo = _repo(tmp_path)
    first = _commit(repo, "a.md", "first")
    second = _commit(repo, "b.md", "second")
    rows = list(iter_commits(repo, repo_label="engine", since=first))
    assert [r["sha"] for r in rows] == [second]


def test_an_unknown_since_falls_back_to_a_full_walk(tmp_path):
    """A rewritten history must rebuild, not silently index nothing."""
    repo = _repo(tmp_path)
    _commit(repo, "a.md", "first")
    _commit(repo, "b.md", "second")
    rows = list(iter_commits(repo, repo_label="engine", since="0" * 40))
    assert len(rows) == 2


def test_a_repo_with_no_commits_yields_nothing_and_does_not_raise(tmp_path):
    repo = _repo(tmp_path)
    assert list(iter_commits(repo, repo_label="engine")) == []


def test_a_path_that_is_not_a_git_repo_raises_plainly(tmp_path):
    with pytest.raises(ValueError, match="not a git repository"):
        list(iter_commits(tmp_path / "nope", repo_label="engine"))


# --- the caller's own deny lists -------------------------------------------
#
# Every air-gap case above rides on the two HARD-CODED denies (`_secure/` and
# the `personal` segment), which `air_gap.is_denied` applies whatever the caller
# passes. So `is_denied(p, deny_prefixes, deny_segments)` could be written
# `is_denied(p)` and all four of them stayed green - MEASURED 2026-09-01, that
# mutation survived the whole file. `memory-index.py` reads `deny_prefixes` and
# `deny_segments` out of `config/memory-index.yaml` and passes them here, so
# dropping them silently indexes every path the OPERATOR asked to be air-gapped
# while the two built-in ones go on working and the gate looks armed.

def test_a_caller_supplied_deny_prefix_reaches_the_air_gap(tmp_path):
    repo = _repo(tmp_path)
    keep = _commit(repo, "docs/ok.md", "public change")
    denied = _commit(repo, "vault-of-mine/x.md", "config-denied change")
    shas = {c["sha"] for c in iter_commits(repo, repo_label="engine",
                                           deny_prefixes=("vault-of-mine/",))}
    assert keep in shas, "the deny list swallowed an allowed commit"
    assert denied not in shas, (
        "the caller's deny_prefixes never reached is_denied; only the "
        "hard-coded _secure/ prefix is still being enforced")


def test_a_caller_supplied_deny_segment_reaches_the_air_gap(tmp_path):
    repo = _repo(tmp_path)
    keep = _commit(repo, "docs/ok.md", "public change")
    denied = _commit(repo, "threads/confidential/x.md", "config-denied change")
    shas = {c["sha"] for c in iter_commits(repo, repo_label="engine",
                                           deny_segments=("confidential",))}
    assert keep in shas
    assert denied not in shas, (
        "the caller's deny_segments never reached is_denied; only the "
        "hard-coded `personal` segment is still being enforced")


def test_a_denied_commit_is_counted_in_the_stats_the_caller_prints(tmp_path):
    """`stats` is the only way a caller learns this walk withheld anything.

    The refusal happens inside `iter_commits`, so `memory-index.py`, which counts
    its OWN denials, counts zero for a commit layer and prints "0 denied" beside
    the ones it did see. That reads as "nothing was withheld" - the module
    docstring says so - and the increment had no test: zeroing it survived.
    """
    repo = _repo(tmp_path)
    _commit(repo, "docs/ok.md", "public change")
    _commit(repo, "chronicle/personal/x.md", "private note")
    stats: dict = {}
    rows = list(iter_commits(repo, repo_label="data", stats=stats))
    assert len(rows) == 1
    assert stats.get("denied") == 1, (
        f"the walk refused a commit and reported {stats!r}")


def test_stats_stays_at_zero_when_nothing_was_withheld(tmp_path):
    """Anchor: a counter that always says 1 is as useless as one that says 0."""
    repo = _repo(tmp_path)
    _commit(repo, "docs/ok.md", "public change")
    stats: dict = {}
    assert len(list(iter_commits(repo, repo_label="engine", stats=stats))) == 1
    assert stats.get("denied", 0) == 0, stats


# --- separators the fields can legally contain -----------------------------
#
# The module's own comment records both of these as MEASURED on 2026-08-30 and
# names `maxsplit=3` and `-z` as the fixes. Neither had a test: removing
# `maxsplit=3` survived the whole file on 2026-09-01. A commit object may hold
# any byte but NUL, so git emits `\x1f` and `\x1e` inside `%s` and `%b`
# verbatim, and both bytes are reachable from ordinary tooling - a pasted
# terminal capture, a diff of a fixture file, any generated changelog.

def test_a_field_separator_inside_a_body_does_not_truncate_it(tmp_path):
    """Without `maxsplit=3` everything past the byte is silently dropped."""
    repo = _repo(tmp_path)
    _commit(repo, "a.md", "feat: subject", "before \x1f after")
    (row,) = list(iter_commits(repo, repo_label="engine"))
    assert "after" in row["body"], (
        f"the body was cut at the field separator: {row['body']!r}")


def test_a_record_separator_inside_a_body_loses_no_commit(tmp_path):
    """The `\\x1e` case, which additionally split one record in two.

    The tail fragment then failed the four-field count and was skipped, so the
    loss was an ADJACENT commit, not only the mangled one. Both subjects have to
    survive the walk.
    """
    repo = _repo(tmp_path)
    _commit(repo, "a.md", "feat: the neighbour")
    _commit(repo, "b.md", "feat: the carrier", "before \x1e after")
    subjects = [c["title"] for c in iter_commits(repo, repo_label="engine")]
    assert subjects == ["feat: the carrier", "feat: the neighbour"], subjects


def test_a_carriage_return_in_a_filename_is_not_translated_away(tmp_path):
    """`text=True` turns on universal newlines, which rewrites CR to LF.

    `subprocess` has no `newline=` knob to switch that off, so the fix is to
    read bytes and decode here. MEASURED in the module comment on 2026-08-30:
    two files differing only by that byte came back as one record under
    `text=True`. The consequence is an indexed `Files:` line naming a path that
    exists nowhere on disk.
    """
    repo = _repo(tmp_path)
    (repo / "cr\rname.md").write_text("x\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "feat: a carriage return in a name")
    (row,) = list(iter_commits(repo, repo_label="engine", include_paths=True))
    assert "cr\rname.md" in row["changed"], row["changed"]
    assert "cr\nname.md" not in row["changed"], (
        "the CR was translated to LF; the indexed path names nothing on disk")


def test_a_control_character_in_a_denied_path_does_not_open_the_air_gap(tmp_path):
    """The bypass `_changed_paths`' docstring records, asserted rather than described.

    git quotes and C-escapes a path holding a control character whatever
    `core.quotePath` says, so without `-z` the path arrives as the literal
    `"_secure/leak\\na.md"` - quotes included - and the leading quote defeats the
    `startswith` prefix match in `is_denied`. The vault path then goes into the
    row and the gate reports nothing withheld.
    """
    repo = _repo(tmp_path)
    (repo / "_secure").mkdir()
    (repo / "_secure" / "leak\na.md").write_text("x\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "vault change with a newline in the path")
    assert list(iter_commits(repo, repo_label="data")) == [], (
        "a vault commit was indexed because its path arrived quoted")
