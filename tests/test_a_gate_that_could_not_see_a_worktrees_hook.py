"""`_pre_push_gate_armed` read `<repo>/.git/hooks/pre-push` as a path.

In a linked worktree `.git` is a FILE holding `gitdir: <path>`, and git resolves
hooks against the COMMON gitdir. The expression therefore named a directory that
does not exist, the predicate returned False forever, and every push from a
worktree was skipped with "the pre-push test gate is not installed" while the
gate was armed and would have run. Two of the three modes in `main()` push
exactly one repository, so that skip is a backup that produced no off-machine
copy at all -- announced, correctly for the wrong reason, as NOTHING PUSHED.

MEASURED 2026-08-30 in a scratch repo, before the fix: the hook armed at
`<main>/.git/hooks/pre-push` returned True for the main clone and False for the
worktree, while `git rev-parse --git-path hooks/pre-push` run inside that
worktree named the very file the predicate had just failed to find.

Nothing here runs `git push`, `git commit`, or `push-all.py`. The repositories
are `git init`ed under `tmp_path` and only `git worktree add` is invoked; the
predicate under test reads files off the disk.

`push-all.py` is loaded BY PATH rather than imported: it calls `ensure_venv()` at
module scope, so a plain import `os.execv`s the whole pytest process under any
interpreter that is not `.venv/bin/python`. Same load and same reason as
`tests/test_push_all_gate.py`.
"""
import importlib.util
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

_spec = importlib.util.spec_from_file_location(
    "push_all_worktree_gate", ROOT / "scripts" / "push-all.py")
push_all = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(push_all)

ARMED_HOOK = "#!/bin/sh\nexec python run-tests.py\n"


def _git(*args, cwd):
    return subprocess.run(["git", *args], cwd=str(cwd), check=True,
                          capture_output=True, text=True)


def _clone_with_armed_hook(tmp_path):
    """A repo with one commit and an ARMED engine pre-push hook. No remote."""
    main = tmp_path / "main"
    main.mkdir()
    _git("init", "-q", "-b", "main", ".", cwd=main)
    _git("config", "user.email", "bond@example.invalid", cwd=main)
    _git("config", "user.name", "James Bond", cwd=main)
    (main / "README.md").write_text("scratch\n", encoding="utf-8")
    _git("add", "-A", cwd=main)
    # The one commit this file makes, and it is made in tmp_path, never in the
    # workspace: `git worktree add` refuses to run against an unborn HEAD.
    _git("-c", "commit.gpgsign=false", "commit", "-q", "-m", "init", cwd=main)
    hooks = main / ".git" / "hooks"
    hooks.mkdir(parents=True, exist_ok=True)
    (hooks / "pre-push").write_text(ARMED_HOOK, encoding="utf-8")
    return main


def test_a_worktree_reads_as_armed_when_the_shared_hook_is_armed(tmp_path):
    main = _clone_with_armed_hook(tmp_path)
    wt = tmp_path / "wt"
    _git("worktree", "add", "-q", str(wt), "-b", "side", cwd=main)

    # the premise: this really is the gitfile shape, not a second .git directory
    assert (wt / ".git").is_file()
    # ...and git really does resolve the hook to the main clone's copy, so a push
    # from here WOULD run the suite. Asked of git, not asserted from memory.
    resolved = subprocess.run(
        ["git", "rev-parse", "--path-format=absolute", "--git-path",
         "hooks/pre-push"],
        cwd=str(wt), check=True, capture_output=True, text=True).stdout.strip()
    assert Path(resolved) == (main / ".git" / "hooks" / "pre-push").resolve()

    assert push_all._pre_push_gate_armed(main) is True
    assert push_all._pre_push_gate_armed(wt) is True


def test_a_worktree_whose_shared_hook_is_absent_still_reads_as_unarmed(tmp_path):
    """The other direction. A predicate that answered True for every worktree
    would pass this file's sibling test and disarm the gate, which is the worse
    failure of the two."""
    main = _clone_with_armed_hook(tmp_path)
    (main / ".git" / "hooks" / "pre-push").unlink()
    wt = tmp_path / "wt"
    _git("worktree", "add", "-q", str(wt), "-b", "side", cwd=main)

    assert push_all._pre_push_gate_armed(wt) is False


def test_a_worktree_hook_without_the_marker_reads_as_unarmed(tmp_path):
    """A hook that exists but runs no suite is not a gate. The stock git-lfs
    pre-push is exactly this shape."""
    main = _clone_with_armed_hook(tmp_path)
    (main / ".git" / "hooks" / "pre-push").write_text(
        "#!/bin/sh\nexec git lfs pre-push \"$@\"\n", encoding="utf-8")
    wt = tmp_path / "wt"
    _git("worktree", "add", "-q", str(wt), "-b", "side", cwd=main)

    assert push_all._pre_push_gate_armed(wt) is False


def test_the_data_marker_is_not_satisfied_by_the_engine_hook_in_a_worktree(tmp_path):
    """Resolving the shared hooks dir must not collapse the two markers: the
    engine hook runs the engine suite and carries neither the data overlay's
    marker nor any claim about the overlay's tests."""
    main = _clone_with_armed_hook(tmp_path)
    wt = tmp_path / "wt"
    _git("worktree", "add", "-q", str(wt), "-b", "side", cwd=main)

    assert push_all._pre_push_gate_armed(
        wt, marker=push_all.DATA_GATE_MARKER) is False
    assert push_all._pre_push_gate_armed(
        wt, marker=push_all.ENGINE_GATE_MARKER) is True


@pytest.mark.parametrize("payload", [
    "",                                  # empty gitfile
    "not a gitdir line at all\n",        # wrong grammar
    "gitdir:\n",                         # the key with no path
])
def test_an_unparseable_gitfile_resolves_to_nothing_and_fails_closed(
        tmp_path, payload):
    """Every shape this resolver cannot read has to read as NOT armed. A gate
    that guesses in the permissive direction is not a gate."""
    repo = tmp_path / "weird"
    repo.mkdir()
    (repo / ".git").write_text(payload, encoding="utf-8")

    assert push_all._git_hooks_dir(repo) is None
    assert push_all._pre_push_gate_armed(repo) is False


def test_a_gitfile_pointing_at_a_missing_gitdir_fails_closed(tmp_path):
    """This one parses, so the resolver names a path -- and the path does not
    exist, so the predicate that consumes it still has to say "not armed"
    rather than raise or assume."""
    repo = tmp_path / "dangling"
    repo.mkdir()
    (repo / ".git").write_text(
        f"gitdir: {tmp_path / 'gone'}\n", encoding="utf-8")

    assert push_all._pre_push_gate_armed(repo) is False


def test_a_directory_with_no_dot_git_at_all_fails_closed(tmp_path):
    bare = tmp_path / "nothing"
    bare.mkdir()

    assert push_all._git_hooks_dir(bare) is None
    assert push_all._pre_push_gate_armed(bare) is False


def test_a_submodule_shaped_gitdir_uses_its_own_hooks_not_a_parents(tmp_path):
    """A submodule's gitdir is a gitfile too, but it carries no `commondir` and
    holds its own `hooks/`. The absence of that file is the answer, not an error
    to fail on, and not a licence to walk up to some parent's hooks."""
    repo = tmp_path / "sub"
    repo.mkdir()
    gitdir = tmp_path / "parent" / ".git" / "modules" / "sub"
    (gitdir / "hooks").mkdir(parents=True)
    (gitdir / "hooks" / "pre-push").write_text(ARMED_HOOK, encoding="utf-8")
    (repo / ".git").write_text(f"gitdir: {gitdir}\n", encoding="utf-8")

    assert push_all._git_hooks_dir(repo) == gitdir / "hooks"
    assert push_all._pre_push_gate_armed(repo) is True


def test_an_ordinary_clone_is_unchanged(tmp_path):
    """The shape every real run takes. The worktree fix must not move it."""
    main = _clone_with_armed_hook(tmp_path)

    assert push_all._git_hooks_dir(main) == main / ".git" / "hooks"
    assert push_all._pre_push_gate_armed(main) is True
