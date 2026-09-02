#!/usr/bin/env python3
"""Two tools that ask git a question, and what they do when git is not there.

`scripts/utils/repo_files.py` and `scripts/check-path-references.py` each shell
out to git. Both had a documented fail-soft path for git REFUSING -- a non-zero
return code -- and neither had one for git being absent, because
`subprocess.run` raises `FileNotFoundError` before a process is ever started and
no return code exists to read. `repo_files.py` went further and asserted the
opposite in a comment: "Anything else (128: not a git repository, git missing)
means the question went unanswered". The sentence saying the case was handled is
why it was not.

MEASURED 2026-09-02 with `PATH=/nonexistent`, before the fix:
`ignored_paths_or_none`, `ignored_paths`, `not_ignored`, `tracked_paths` and
`check-path-references.tracked_markdown` all died on `FileNotFoundError: [Errno
2] No such file or directory: 'git'`.

Git-absent is simulated by pointing PATH at an empty directory, so the real
`subprocess.run` call is the thing that fails -- the function under test is not
patched out. Each refusal is paired with an ANCHOR case on the same corpus with
the real PATH, without which a guard that returned "cannot answer" for
everything would pass this file.
"""
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.utils.repo_files import (  # noqa: E402
    ignored_paths,
    ignored_paths_or_none,
    not_ignored,
    tracked_paths,
)
from scripts.utils.workspace import get_workspace_root  # noqa: E402

_SRC = get_workspace_root() / "scripts" / "check-path-references.py"
_spec = importlib.util.spec_from_file_location("check_path_references_gitless", _SRC)
cpr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cpr)

ROOT = get_workspace_root()


@pytest.fixture
def no_git(tmp_path, monkeypatch):
    """PATH with nothing on it, so the real `subprocess.run` cannot find git."""
    empty = tmp_path / "empty-bin"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    # The premise of every test below. If some day a git turns up anyway, these
    # tests would pass by measuring the ordinary path and saying nothing.
    with pytest.raises(FileNotFoundError):
        subprocess.run(["git", "--version"], capture_output=True)
    return empty


# --- scripts/utils/repo_files.py ---------------------------------------------

def test_ignored_paths_or_none_answers_none_when_git_is_absent(no_git):
    """"git could not say", the value this module was written to distinguish."""
    assert ignored_paths_or_none([str(ROOT / "scripts" / "memlog.py")], ROOT) is None


def test_ignored_paths_raises_its_own_refusal_not_a_filenotfounderror(no_git):
    """The named RuntimeError, so the sweep's message says what went wrong.

    A `FileNotFoundError: 'git'` surfacing out of a tree sweep names a file the
    operator will go looking for and reads as a missing input, not a missing
    tool.
    """
    with pytest.raises(RuntimeError, match="git check-ignore failed"):
        ignored_paths([str(ROOT / "scripts" / "memlog.py")], ROOT)


def test_every_public_reader_in_repo_files_refuses_the_same_way(no_git):
    """All four reach git through one call site; none may raise OSError.

    This is the shape of the original defect: the guard existed for one
    function's idea of failure and the other three inherited nothing.
    """
    for name, call in (
        ("ignored_paths", lambda: ignored_paths([str(ROOT / "scripts" / "memlog.py")], ROOT)),
        ("not_ignored", lambda: not_ignored([str(ROOT / "scripts" / "memlog.py")], ROOT)),
        ("tracked_paths", lambda: tracked_paths(["scripts/memlog.py"], ROOT)),
    ):
        with pytest.raises(RuntimeError, match="git check-ignore failed") as caught:
            call()
        assert not isinstance(caught.value, OSError), (
            f"{name} let the OS error through instead of naming the refusal"
        )


def test_anchor_repo_files_still_reads_the_tree_with_git_on_path():
    """Without this, a guard that answered "cannot ask" always would pass."""
    assert ignored_paths_or_none([str(ROOT / "scripts" / "memlog.py")], ROOT) == set()
    found = tracked_paths(["scripts/memlog.py"], ROOT)
    assert found == [ROOT / "scripts" / "memlog.py"], found


# --- scripts/check-path-references.py -----------------------------------------

def test_tracked_markdown_warns_and_returns_empty_when_git_is_absent(no_git, capsys):
    assert cpr.tracked_markdown(ROOT) == []
    err = capsys.readouterr().err
    assert "could not run `git ls-files`" in err, err
    assert "no Markdown was scanned" in err, err


def test_the_scanner_refuses_rather_than_passing_when_git_is_absent(no_git, capsys):
    """An empty corpus must not read as a clean tree.

    `main()` already refuses a zero-file corpus with exit 2; the point here is
    that the refusal is now REACHED instead of being pre-empted by a traceback.
    """
    result = cpr.scan_scoped(ROOT)
    assert result.files_read == 0
    assert cpr.refuse("no tracked Markdown was read", as_json=False) == 2


def test_anchor_tracked_markdown_still_lists_the_corpus_with_git_on_path():
    corpus = cpr.tracked_markdown(ROOT)
    assert len(corpus) >= 250, (
        f"only {len(corpus)} tracked Markdown file(s); the corpus collapsed and "
        "the anchor above it is measuring nothing"
    )
