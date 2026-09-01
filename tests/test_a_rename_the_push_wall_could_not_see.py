"""A staged rename that both push-time content walls returned the empty set for.

`push-all.py` calls itself the UNBYPASSABLE wall: whatever a commit hook lets
through, the push-time content scan reads the bytes again before anything leaves
the machine. `_push_delta_files` is what tells that scan WHICH files to read.

It asked git three questions, all with `--diff-filter=ACM` and none with
`--no-renames`. Rename detection is git's default, so a `git mv` plus an edit is
ONE `R` entry, and `ACM` drops it: the destination path appears in no leg at all.

MEASURED 2026-08-29 in a scratch repo with a real bare remote. A staged rename of
a 200-line file carrying one new line came back from `git diff --cached
--name-only --diff-filter=ACM` as the EMPTY LIST, and as `['docs_b.md']` the
moment `--no-renames` was added. With the empty set, `content_scan` skips the
scanner entirely (`if files:` is False), `engine_content_scan` opens no file, and
`push_repo` goes on to commit and push. The only thing left standing is the
pre-commit framework hook, which is bypassable, and backstopping exactly that is
this wall's stated reason to exist.

`scripts/utils/push_history.py` already passes `--no-renames` to `git diff-tree`,
and says why. The two halves of the same wall disagreed about renames, and the
half that ran first was the one that was wrong.
"""
from __future__ import annotations

import ast
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PUSH_ALL = ROOT / "scripts" / "push-all.py"


@pytest.fixture(scope="module")
def push_all():
    spec = importlib.util.spec_from_file_location("push_all_probe", PUSH_ALL)
    module = importlib.util.module_from_spec(spec)
    sys.modules["push_all_probe"] = module
    spec.loader.exec_module(module)
    return module


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=str(repo),
                          capture_output=True, text=True)


@pytest.fixture()
def repo_with_remote(tmp_path):
    """A real clone of a real bare remote, so `origin/main..HEAD` resolves.

    A bare `git init` would leave `origin/main` undefined and the first of the
    three legs would error out, which is a different code path from the one
    under test.
    """
    bare, work = tmp_path / "remote.git", tmp_path / "work"
    subprocess.run(["git", "init", "--bare", "-q", str(bare)], check=True)
    subprocess.run(["git", "clone", "-q", str(bare), str(work)],
                   check=True, capture_output=True)
    _git(work, "config", "user.email", "t@example.invalid")
    _git(work, "config", "user.name", "Test")
    # Long enough that git's similarity index calls the move a rename. A short
    # file is reported as delete-plus-add, which the old code handled fine, so a
    # fixture built that way would pass against the defect.
    body = "\n".join(f"line {i} of ordinary documentation text" for i in range(200))
    (work / "docs_a.md").write_text(body + "\n", encoding="utf-8")
    _git(work, "add", "-A")
    _git(work, "commit", "-qm", "first")
    _git(work, "branch", "-M", "main")
    _git(work, "push", "-q", "origin", "main")
    return work, body


def test_git_really_calls_it_a_rename(repo_with_remote):
    """The fixture's own control. If git stopped detecting the rename, every
    assertion below would pass for the wrong reason."""
    work, body = repo_with_remote
    _git(work, "mv", "docs_a.md", "docs_b.md")
    (work / "docs_b.md").write_text(body + "\nthe new line\n", encoding="utf-8")
    _git(work, "add", "-A")
    assert _git(work, "status", "--porcelain").stdout.startswith("R ")


def test_a_staged_rename_reaches_the_wall(push_all, repo_with_remote):
    """The defect, in one assertion: this used to be the empty set."""
    work, body = repo_with_remote
    _git(work, "mv", "docs_a.md", "docs_b.md")
    (work / "docs_b.md").write_text(body + "\nthe new line\n", encoding="utf-8")
    _git(work, "add", "-A")
    assert "docs_b.md" in push_all._push_delta_files(work)


def test_a_committed_rename_reaches_the_wall(push_all, repo_with_remote):
    """The other side of the same move. The history leg already passed
    `--no-renames`, so this one was covered; asserting it keeps the two legs
    from drifting apart again."""
    work, body = repo_with_remote
    _git(work, "mv", "docs_a.md", "docs_b.md")
    (work / "docs_b.md").write_text(body + "\nthe new line\n", encoding="utf-8")
    _git(work, "add", "-A")
    _git(work, "commit", "-qm", "renamed")
    assert "docs_b.md" in push_all._push_delta_files(work)


def test_an_unstaged_rename_reaches_the_wall(push_all, repo_with_remote):
    """A rename done by hand, leaving the index alone, still reaches the wall.

    Which LEG carries it was measured 2026-09-01, because the name this test
    used to have ("the third leg, the plain working tree") named the wrong one.
    Git cannot report an unstaged rename here at all: rename detection needs
    both paths tracked, and the destination of an unstaged rename is untracked
    by definition. So the worktree diff sees only `D docs_a.md`, and it is
    `git ls-files --others` that carries `docs_b.md`.

    MEASURED, two mutations of `_push_delta_files`:
      - untracked leg deleted        -> this test FAILS (`assert 'docs_b.md' in set()`)
      - unstaged diff leg deleted    -> this test PASSES
    The name is corrected rather than the assertion: the property is real and
    worth pinning, it just belongs to the untracked leg. The unstaged diff leg
    gets its own test below, which it did not have.
    """
    work, body = repo_with_remote
    (work / "docs_b.md").write_text(body + "\nthe new line\n", encoding="utf-8")
    (work / "docs_a.md").unlink()
    assert "docs_b.md" in push_all._push_delta_files(work)


def test_an_unstaged_edit_to_a_tracked_file_reaches_the_wall(push_all,
                                                              repo_with_remote):
    """The third leg, for real: `git diff` with neither `--cached` nor a range.

    It had NO test anywhere. MEASURED 2026-09-01 by deleting the leg from
    `_push_delta_files` and running every test file in the repo that touches
    that function -- `test_a_credential_hidden_behind_a_quoted_path`,
    `test_handoff_redaction`, `test_push_all_gate`,
    `test_two_walls_that_looked_at_the_wrong_moment`,
    `test_two_secret_walls_that_split_a_filename_in_half`,
    `test_a_wall_that_read_the_present_and_shipped_the_past` and this one:
    258 passed, nothing red.

    The leg is live, not dead code, and this is the case that proves it.
    `engine_content_scan` runs at step 0 of `push_repo`, deliberately BEFORE the
    commit, and `push_repo` then commits with `git add -A`. So a tracked file
    the operator edited and never staged IS about to be pushed, and at the
    moment the wall looks it is in neither the committed delta nor the index nor
    the untracked list. This leg is the only thing that sees it.
    """
    work, body = repo_with_remote
    (work / "docs_a.md").write_text(body + "\nan unstaged edit\n",
                                    encoding="utf-8")

    assert _git(work, "status", "--porcelain").stdout.startswith(" M "), (
        "the fixture staged the edit; then this measures the --cached leg "
        "instead of the one it is named for")
    assert "docs_a.md" in push_all._push_delta_files(work)


def test_an_ordinary_edit_still_reaches_the_wall(push_all, repo_with_remote):
    """The other direction. A change that turns off rename handling entirely
    would satisfy nothing above and break everything the wall normally does."""
    work, body = repo_with_remote
    (work / "docs_a.md").write_text(body + "\nan ordinary edit\n", encoding="utf-8")
    _git(work, "add", "-A")
    assert "docs_a.md" in push_all._push_delta_files(work)


def test_a_clean_tree_gives_the_wall_nothing(push_all, repo_with_remote):
    work, _body = repo_with_remote
    assert push_all._push_delta_files(work) == set()


# ============================================================
# Both halves of the wall ask git the same way
# ============================================================

def git_diff_calls_without_no_renames(source: str) -> list[int]:
    """Line numbers of `git diff`/`diff-tree` argv lists that omit --no-renames.

    AST over the literal lists, not a substring sweep: the two halves of this
    wall are meant to agree about renames, and they silently did not. Pure, so
    both directions are measurable on synthetic input.
    """
    hits = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.List):
            continue
        words = [e.value for e in node.elts
                 if isinstance(e, ast.Constant) and isinstance(e.value, str)]
        if not words or words[0] != "git":
            continue
        if not ({"diff", "diff-tree"} & set(words)):
            continue
        if "--no-renames" not in words:
            hits.append(node.lineno)
    return hits


LOOSE = [
    'x = ["git", "diff", "--name-only"]',
    'x = ["git", "diff-tree", "-r", "HEAD"]',
]
TIGHT = [
    'x = ["git", "diff", "--no-renames", "--name-only"]',
    'x = ["git", "diff-tree", "--no-renames", "-r", "HEAD"]',
    'x = ["git", "status", "--porcelain"]',   # not a diff
    'x = ["diff", "a", "b"]',                 # not git
]


@pytest.mark.parametrize("snippet", LOOSE)
def test_the_rule_sees_a_rename_blind_diff(snippet):
    assert git_diff_calls_without_no_renames(snippet)


@pytest.mark.parametrize("snippet", TIGHT)
def test_the_rule_leaves_everything_else_alone(snippet):
    assert git_diff_calls_without_no_renames(snippet) == []


@pytest.mark.parametrize("rel", ["scripts/push-all.py",
                                 "scripts/utils/push_history.py"])
def test_neither_half_of_the_wall_is_rename_blind(rel):
    lines = git_diff_calls_without_no_renames(
        (ROOT / rel).read_text(encoding="utf-8"))
    assert not lines, (
        f"{rel} asks git for a diff without --no-renames at line(s) {lines}; "
        f"a `git mv` plus an edit is one R entry and --diff-filter=ACM drops it")
