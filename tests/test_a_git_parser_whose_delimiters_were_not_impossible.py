"""A commit walker that lost text to its own separators, and let a vault path past.

`commit_source` split git's output on `\\x1f` and `\\x1e` under a comment
claiming "these two bytes are the only delimiters git will not find inside the
fields it is emitting". A commit object may hold any byte except NUL, so both
appear in real messages and git emits them verbatim: a `\\x1f` in a body made
`split(_FS)` yield five parts and `parts[3]` kept only the fragment before it,
and a `\\x1e` split one commit's record in two.

The sharper one is the air gap, and it is not what the audit predicted.
`_run` sets `core.quotePath=false` under a comment saying that removes the
quoting which defeats `is_denied`'s `startswith` prefix match. It does not:
`quotePath` governs NON-ASCII bytes only, and git quotes and C-escapes a path
holding a CONTROL character regardless. So `_secure/leak\\na.md` arrived as the
literal `"_secure/leak\\na.md"`, quotes included, `is_denied` returned False on
it, and the vault path was indexed into `embed_text` while the walk reported
nothing withheld.

`-z` closes all three: NUL is the byte git cannot emit inside a field, and with
it paths come out verbatim and unquoted.

These tests build their own git repos under `tmp_path`. Nothing reads the
workspace's own history, and nothing reaches the network.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils.air_gap import is_denied
from scripts.utils.commit_source import _changed_paths, iter_commits

US = "\x1f"  # ASCII unit separator, the field delimiter
RS = "\x1e"  # ASCII record separator


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=True
    )
    return proc.stdout


@pytest.fixture
def repo(tmp_path):
    """A throwaway git repo with a pinned identity and no ambient config."""
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "james.bond@example.invalid")
    _git(root, "config", "user.name", "James Bond")
    _git(root, "config", "commit.gpgsign", "false")
    return root


def _commit(repo: Path, message: str, *, write=None) -> None:
    if write is not None:
        path, content = write
        target = repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", message)
    else:
        _git(repo, "commit", "-q", "--allow-empty", "-m", message)


def _walk(repo: Path, **kwargs) -> list[dict]:
    return list(iter_commits(repo, repo_label="engine", **kwargs))


# --------------------------------------------------------------------------
# separators inside a message
# --------------------------------------------------------------------------

def test_a_unit_separator_in_a_body_does_not_truncate_it(repo):
    """The defect: everything past the separator vanished from the index."""
    _commit(repo, f"subject line\n\nbefore {US} after the separator")

    rows = _walk(repo)

    assert len(rows) == 1
    assert rows[0]["body"] == f"before {US} after the separator"
    assert "after the separator" in rows[0]["embed_text"]


def test_a_record_separator_in_a_body_neither_truncates_nor_splits(repo):
    """`\\x1e` additionally severed one commit's record into two."""
    _commit(repo, "first subject\n\nplain body")
    _commit(repo, f"second subject\n\nbefore {RS} after")
    _commit(repo, "third subject\n\nanother plain body")

    rows = _walk(repo)

    assert len(rows) == 3, "a separator in one body dropped an adjacent commit"
    bodies = {r["title"]: r["body"] for r in rows}
    assert bodies["second subject"] == f"before {RS} after"
    assert bodies["first subject"] == "plain body"
    assert bodies["third subject"] == "another plain body"


def test_a_separator_in_a_subject_loses_no_commit(repo):
    """A shifted field used to make `float(at)` raise and kill the whole walk."""
    _commit(repo, "clean one\n\nbody one")
    _commit(repo, f"broken {US} subject\n\nbody two")
    _commit(repo, "clean two\n\nbody three")

    rows = _walk(repo)

    assert len(rows) == 3
    assert all(r["sha"] for r in rows)
    assert all(isinstance(r["mtime"], float) for r in rows)


def test_ordinary_messages_are_unchanged(repo):
    """The maxsplit and the `-z` must not alter the common case."""
    _commit(repo, "feat: add a thing\n\nfirst line\nsecond line")

    rows = _walk(repo)

    assert rows[0]["title"] == "feat: add a thing"
    assert rows[0]["body"] == "first line\nsecond line"


def test_a_backup_commit_is_still_skipped(repo):
    _commit(repo, "chore: workspace backup")
    _commit(repo, "feat: real work")

    assert [r["title"] for r in _walk(repo)] == ["feat: real work"]


# --------------------------------------------------------------------------
# the air gap
# --------------------------------------------------------------------------

CONTROL_NAME = "_secure/leak\na.md"


def test_git_still_quotes_a_control_character_path(repo):
    """Pin the premise. `core.quotePath=false` does NOT cover this class.

    Without this the test below could pass because git stopped quoting, rather
    than because the parser stopped being fooled.
    """
    _commit(repo, "vault file", write=(CONTROL_NAME, "secret"))

    raw = _git(repo, "-c", "core.quotePath=false", "log", "--name-only", "--format=%H")

    assert '"_secure/leak\\na.md"' in raw


def test_a_control_character_path_reaches_is_denied_unquoted(repo):
    """The defect: the quoted form returned False from `is_denied`."""
    _commit(repo, "vault file", write=(CONTROL_NAME, "secret"))

    paths = _changed_paths(repo, ["HEAD"])
    found = [p for entries in paths.values() for p in entries]

    assert found == [CONTROL_NAME]
    assert is_denied(found[0]) is True
    # The shape that used to arrive, pinned so the direction of the fix is clear.
    assert is_denied('"_secure/leak\\na.md"') is False


def test_a_vault_commit_with_a_control_character_name_is_refused(repo):
    """End to end: the commit must not be yielded, and the refusal must count."""
    _commit(repo, "ordinary work", write=("docs/notes.md", "public"))
    _commit(repo, "vault file", write=(CONTROL_NAME, "secret"))

    stats: dict = {}
    rows = _walk(repo, include_paths=True, stats=stats)

    titles = [r["title"] for r in rows]
    assert "vault file" not in titles
    assert "ordinary work" in titles
    assert stats.get("denied") == 1


def test_an_ordinary_vault_path_is_still_refused(repo):
    """The plain case the gate always caught, so the fix did not trade one away."""
    _commit(repo, "plain vault file", write=("_secure/plain.md", "secret"))

    stats: dict = {}
    rows = _walk(repo, include_paths=True, stats=stats)

    assert rows == []
    assert stats.get("denied") == 1


def test_a_caller_supplied_deny_still_applies(repo):
    _commit(repo, "config change", write=("private/creds.md", "x"))

    stats: dict = {}
    rows = _walk(repo, include_paths=True, deny_prefixes=("private/",), stats=stats)

    assert rows == []
    assert stats.get("denied") == 1


def test_a_path_is_not_trimmed_of_its_own_whitespace(repo):
    """`.strip()` on a path produces one that matches neither disk nor a prefix."""
    name = "docs/trailing .md"
    _commit(repo, "spaced filename", write=(name, "x"))

    found = [p for entries in _changed_paths(repo, ["HEAD"]).values() for p in entries]

    assert found == [name]


def test_several_files_in_one_commit_are_all_listed(repo):
    _commit(repo, "two files", write=("a.md", "x"))
    (repo / "b.md").write_text("y", encoding="utf-8")
    (repo / "c.md").write_text("z", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "two more")

    rows = _walk(repo, include_paths=True)
    by_title = {r["title"]: sorted(r["changed"]) for r in rows}

    assert by_title["two more"] == ["b.md", "c.md"]
    assert by_title["two files"] == ["a.md"]


def test_a_non_ascii_path_still_survives_the_walk(repo):
    """The class `core.quotePath=false` was added for; `-z` must keep it working."""
    name = "docs/план.md"
    _commit(repo, "cyrillic filename", write=(name, "x"))

    found = [p for entries in _changed_paths(repo, ["HEAD"]).values() for p in entries]

    assert found == [name]
