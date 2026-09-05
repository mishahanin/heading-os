"""The pre-push gate narrows only when it knows the range, and says so either way.

THE DEFECT THIS EXISTS FOR. Until 2026-09-04 the engine's pre-push hook ran all
24805 tests on every push to carry a one-file change. Nothing decided that.

THE SIZE IS 24805, AND THE FIRST NUMBER WRITTEN HERE WAS 18591. That figure came
from a run launched with `-x`, which stops at the first failures and counts only
what finished before the stop, so it was a partial run reported as a total. The
real figure is one clean full run over the merged tree at `ca9457d` on
2026-09-04: 24805 passed, 2 skipped, exit 0, 979 s at one-minute load 26 with two
worktrees competing for the box. The lesson is worth more than the correction: an
aborted run's count is not a corpus size, and a bare number with no run behind it
cannot be told apart from one that has a run behind it. Hence the date and the
revision beside every count in this change.

Running everything is what you do when you cannot
answer "what did this change reach", and day mode answered it. The narrowing is
worth roughly eight minutes a push, and it is worth exactly nothing if it is
wrong once.

TWO WAYS TO GET IT WRONG, and this file is built around both.

THE RANGE. `origin/main..HEAD` is the obvious expression and it is a guess: it
names a remote-tracking ref that may be an hour stale, may have been fetched
before someone else pushed, and on a new branch names a different history
entirely. Git hands a pre-push hook the authoritative answer on STDIN, one
`<local ref> <local sha> <remote ref> <remote sha>` per ref, and the gate uses
that and nothing else.

THE FALLBACK. A gate that widens to the full suite in silence is
indistinguishable from a gate that never narrowed, and nobody notices the day it
stops working. So every decision carries a reason, both directions are asserted
here, and each widening condition has its own test rather than a shared one:
a single "returns full on bad input" case is satisfied by a function that returns
full on everything, which is the shape that would quietly undo this whole change.

STDIN IS ALSO GIT-LFS'S. The hook's slot belongs to `git lfs pre-push`, which
reads the same ref lines to decide which objects to upload. A pipe is consumed by
whoever reads it first, so a gate that reads stdin and forgets to replay it
pushes LFS pointers with no object behind them, and the next fresh clone's `git
lfs pull` fails. `test_git_lfs_still_receives_the_ref_lines` drives the real hook
through a real `git push` with a recording `git-lfs` shim on PATH.
"""
from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.utils.prepush_gate import Decision, decide, is_null_sha  # noqa: E402

# One string, not `ROOT / ".githooks" / "pre-push"`. The reason is written out in
# tests/test_install_git_hooks.py: day mode reads a test's string constants to find
# the files it drives as a subprocess, and the two-part form spells nothing it can
# match. This gate is the thing that would then run all 24805 tests to carry a
# change to itself.
HOOK = ROOT / ".githooks/pre-push"


def _git(repo: Path, *args: str, check: bool = True) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True, check=False,
        env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
             "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"},
    )
    if check and result.returncode != 0:
        raise AssertionError(f"git {args}: {result.stderr}")
    return result.stdout.strip()


@dataclass
class Scratch:
    """A scratch repository, and the two shas a push of it would carry."""

    path: Path
    base: str
    head: str


@pytest.fixture
def repo(tmp_path: Path) -> Scratch:
    """A scratch repository with two commits and two test files.

    `tests/test_thing.py` imports the file the second commit changes;
    `tests/test_other.py` imports nothing of the sort. The second file is the
    anchor for the narrowing direction: without it, "select everything" would
    satisfy every assertion below about what IS selected.
    """
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    (repo / "tests").mkdir()
    (repo / "scripts" / "__init__.py").write_text("", encoding="utf-8")
    (repo / "scripts" / "thing.py").write_text("VALUE = 1\n", encoding="utf-8")
    (repo / "scripts" / "unrelated.py").write_text("OTHER = 1\n", encoding="utf-8")
    (repo / "tests" / "test_thing.py").write_text(
        "import scripts.thing\n\n\ndef test_it():\n    assert scripts.thing.VALUE\n",
        encoding="utf-8",
    )
    (repo / "tests" / "test_other.py").write_text(
        "import scripts.unrelated\n\n\ndef test_it():\n"
        "    assert scripts.unrelated.OTHER\n",
        encoding="utf-8",
    )
    _git(repo.parent, "init", "-q", "-b", "main", str(repo))
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")
    base = _git(repo, "rev-parse", "HEAD")

    (repo / "scripts" / "thing.py").write_text("VALUE = 2\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "change thing")
    head = _git(repo, "rev-parse", "HEAD")

    return Scratch(path=repo, base=base, head=head)


def _refs(repo: Scratch, *, local: str | None = None, remote: str | None = None) -> str:
    return (f"refs/heads/main {local or repo.head} "
            f"refs/heads/main {remote or repo.base}\n")


# ============================================================
# The narrowing direction: it really does narrow, and correctly
# ============================================================


def test_a_push_of_new_commits_narrows_to_the_tests_the_range_reaches(repo):
    decision = decide(repo.path, _refs(repo))

    assert decision.full is False, decision.reason
    assert decision.tests == ["tests/test_thing.py"], decision.tests
    assert "tests/test_other.py" not in decision.tests, (
        "the gate selected a test no route reaches from the pushed range; a "
        "selector that returns everything is not a selector"
    )
    assert decision.changed == ["scripts/thing.py"], decision.changed


def test_a_narrowed_decision_still_states_its_reason(repo):
    """A silent narrow is as unauditable as a silent widen, from the other side."""
    decision = decide(repo.path, _refs(repo))

    assert decision.reason, "a narrowed run printed no reason at all"
    assert repo.base[:12] in decision.reason, decision.reason
    assert repo.head[:12] in decision.reason, decision.reason


def test_the_range_comes_from_stdin_and_not_from_a_remote_tracking_ref(repo):
    """The whole point. A stale `origin/main` must not change the answer.

    `origin/main` is pointed at the tip, which is what a fetch-behind-a-push
    looks like from the inside. If the gate consulted it, the range would be
    empty and `tests/test_thing.py` would drop out of the selection.
    """
    _git(repo.path, "update-ref", "refs/remotes/origin/main", repo.head)

    decision = decide(repo.path, _refs(repo))

    assert decision.tests == ["tests/test_thing.py"], (
        "the selection moved when a remote-tracking ref moved, so the gate is "
        "reading something other than git's ref lines"
    )


# ============================================================
# Every widening condition, one test each
# ============================================================


def test_a_remote_sha_of_all_zeros_runs_everything(repo):
    """A branch the remote has never seen: nothing has covered its base."""
    decision = decide(repo.path, _refs(repo, remote="0" * 40))

    assert decision.full is True
    assert decision.tests == []
    assert "never seen" in decision.reason, decision.reason


def test_a_local_sha_of_all_zeros_runs_everything(repo):
    """A ref deletion carries no commits, so there is no range to select against."""
    decision = decide(repo.path, _refs(repo, local="0" * 40))

    assert decision.full is True
    assert "deleted" in decision.reason, decision.reason


def test_a_sha256_null_sha_is_recognised_too():
    """64 zeros, for the day this repository is not sha1. Length is not the test."""
    assert is_null_sha("0" * 64)
    assert is_null_sha("0" * 40)
    assert not is_null_sha("0" * 39 + "1")
    assert not is_null_sha("")


def test_a_force_push_runs_everything(repo):
    """The remote sha is not an ancestor, so the range is not what this push adds.

    Built by rewinding to the base and committing something else, which is what
    an amend or a rebase leaves behind.
    """
    _git(repo.path, "reset", "-q", "--hard", repo.base)
    (repo.path / "scripts" / "thing.py").write_text("VALUE = 3\n", encoding="utf-8")
    _git(repo.path, "add", "-A")
    _git(repo.path, "commit", "-qm", "diverged")
    diverged = _git(repo.path, "rev-parse", "HEAD")

    decision = decide(repo.path, _refs(repo, local=diverged, remote=repo.head))

    assert decision.full is True
    assert "not an ancestor" in decision.reason, decision.reason


def test_empty_stdin_runs_everything(repo):
    """Invoked by hand, or by something that is not git."""
    decision = decide(repo.path, "")

    assert decision.full is True
    assert "no ref lines" in decision.reason, decision.reason


def test_an_unparseable_stdin_line_runs_everything(repo):
    """Three fields, not four. Whatever wrote this, it was not git."""
    decision = decide(repo.path, "refs/heads/main deadbeef refs/heads/main\n")

    assert decision.full is True
    assert "not git" in decision.reason, decision.reason


def test_a_dirty_working_tree_runs_everything(repo):
    """pytest imports the files on disk, so the disk has to be what is pushed."""
    (repo.path / "scripts" / "thing.py").write_text("VALUE = 99\n", encoding="utf-8")

    decision = decide(repo.path, _refs(repo))

    assert decision.full is True
    assert "uncommitted" in decision.reason, decision.reason


def test_an_untracked_file_counts_as_a_dirty_tree(repo):
    """`git status --porcelain` reports it, and the tree sweeps read it.

    `scripts/utils/repo_files.py::tracked_paths` globs the filesystem rather than
    reading the git index, so an untracked, un-ignored file is inside the corpus
    a hundred sweeps read. A tree carrying one is not the pushed commit.
    """
    (repo.path / "scripts" / "stray.py").write_text("STRAY = 1\n", encoding="utf-8")

    decision = decide(repo.path, _refs(repo))

    assert decision.full is True
    assert "uncommitted" in decision.reason, decision.reason


def test_pushing_a_commit_that_is_not_head_runs_everything(repo):
    """`git push origin <sha>:main` from a tree standing somewhere else."""
    decision = decide(repo.path, _refs(repo, local=repo.base, remote=repo.base))

    assert decision.full is True
    assert "not HEAD" in decision.reason, decision.reason


def test_a_root_that_git_cannot_answer_for_runs_everything(tmp_path):
    """No repository at all: every git call fails and none of them may be fatal."""
    decision = decide(tmp_path, "refs/heads/main a1 refs/heads/main b2\n")

    assert decision.full is True
    assert decision.reason, "a git failure widened without saying why"


def test_day_mode_raising_runs_everything(repo, monkeypatch):
    """The selector's own failure mode. `DayModeError` must not reach the caller."""
    import scripts.utils.prepush_gate as gate
    from scripts.utils.day_mode import DayModeError

    def _boom(*_a, **_k):
        raise DayModeError("index unavailable")

    monkeypatch.setattr(gate, "build_index", _boom)

    decision = decide(repo.path, _refs(repo))

    assert decision.full is True
    assert "index unavailable" in decision.reason, decision.reason


def test_an_unanticipated_exception_runs_everything(repo, monkeypatch):
    """"Anything at all you did not anticipate" is a condition, not a hope.

    A bare `except Exception` is normally a finding. Here it IS the control: the
    one outcome that must be impossible is a crashed selector letting a push
    through, so this asserts the handler catches a type nobody wrote a branch for.
    """
    import scripts.utils.prepush_gate as gate

    def _boom(*_a, **_k):
        raise ZeroDivisionError("nobody saw this coming")

    monkeypatch.setattr(gate, "build_index", _boom)

    decision = decide(repo.path, _refs(repo))

    assert decision.full is True
    assert "ZeroDivisionError" in decision.reason, decision.reason


def test_a_changed_file_no_route_reaches_runs_everything(repo):
    """Day mode's own "could not decide" is doubt, and doubt widens.

    `docs/notes.md` is reached by no import, no literal and no sweep in this
    scratch tree, so day mode reports it undecided rather than selecting for it.
    """
    (repo.path / "docs").mkdir()
    (repo.path / "docs" / "notes.md").write_text("something\n", encoding="utf-8")
    _git(repo.path, "add", "-A")
    _git(repo.path, "commit", "-qm", "add a doc nothing reaches")
    head = _git(repo.path, "rev-parse", "HEAD")

    decision = decide(repo.path, _refs(repo, local=head, remote=repo.head))

    assert decision.full is True
    assert "could not decide" in decision.reason, decision.reason
    assert "docs/notes.md" in decision.reason, decision.reason


# ============================================================
# The command the decision turns into
# ============================================================


def _run_tests_module():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "run_tests_gate", ROOT / "scripts" / "run-tests.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_a_narrowed_command_keeps_the_marker_and_the_workers():
    """Narrowing changes the FILE LIST and nothing else about the run."""
    module = _run_tests_module()

    cmd = module.build_command(acceptance=False, tests=["tests/test_a.py"])

    assert "not acceptance" in cmd, cmd
    assert "-n" in cmd and "auto" in cmd, cmd
    assert cmd[-1] == "tests/test_a.py", cmd


def test_a_bare_invocation_still_runs_the_whole_suite():
    """The paired direction. Only the pre-push path narrows; everything else does not."""
    module = _run_tests_module()

    cmd = module.build_command(acceptance=False)

    assert not any(part.endswith(".py") and "tests/" in part for part in cmd), (
        f"a bare `python scripts/run-tests.py` carried a file list: {cmd}"
    )


def test_the_pre_push_flag_narrows_and_its_absence_does_not(monkeypatch, repo, capsys):
    """`main()` is the surface the hook reads. Drive it, not `build_command`."""
    import types

    module = _run_tests_module()
    seen: dict = {}
    monkeypatch.setattr(
        module, "subprocess",
        types.SimpleNamespace(
            run=lambda cmd, **kw: (seen.__setitem__("cmd", list(cmd)),
                                   types.SimpleNamespace(returncode=0))[1]))
    monkeypatch.setattr(module, "decide",
                        lambda root, text: Decision(full=False, reason="r",
                                                    tests=["tests/test_thing.py"]))
    monkeypatch.setattr(module.sys.stdin, "isatty", lambda: True, raising=False)

    monkeypatch.setattr("sys.argv", ["run-tests.py", "--pre-push"])
    assert module.main() == 0
    assert "tests/test_thing.py" in seen["cmd"], seen["cmd"]

    monkeypatch.setattr("sys.argv", ["run-tests.py"])
    assert module.main() == 0
    assert "tests/test_thing.py" not in seen["cmd"], seen["cmd"]


def test_a_widened_run_says_so_on_stdout(monkeypatch, capsys):
    """The line a human reads when a push takes eight minutes instead of one."""
    module = _run_tests_module()
    monkeypatch.setattr(module, "decide",
                        lambda root, text: Decision(full=True, reason="a stale ref"))
    monkeypatch.setattr(module.sys.stdin, "isatty", lambda: True, raising=False)

    assert module.pre_push_selection(ROOT) is None
    out = capsys.readouterr().out
    assert "FULL SUITE" in out and "a stale ref" in out, out


def test_a_tty_stdin_is_never_read(monkeypatch):
    """A gate that blocks on a terminal is a gate that gets bypassed."""
    module = _run_tests_module()

    def _explode():
        raise AssertionError("stdin was read from a terminal")

    monkeypatch.setattr(module.sys.stdin, "isatty", lambda: True, raising=False)
    monkeypatch.setattr(module.sys.stdin, "read", _explode, raising=False)

    assert module.read_ref_lines() == ""


# ============================================================
# The hook, driven by a real push
# ============================================================


@pytest.fixture
def pushable(tmp_path: Path):
    """A repo with a bare remote, the real hook installed, and two shims on PATH.

    The shims are the assertion surface: `run-tests.py` records the stdin the
    gate was handed, `git-lfs` records the stdin the hand-off was handed. Both
    must hold the same ref line, which is the property a single `cat` would break.
    """
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
    work = tmp_path / "work"
    work.mkdir()
    (work / "scripts").mkdir()
    _git(tmp_path, "init", "-q", "-b", "main", str(work))
    _git(work, "remote", "add", "origin", str(remote))

    gate_log = tmp_path / "gate-stdin.txt"
    (work / "scripts" / "run-tests.py").write_text(
        "#!/bin/sh\ncat > " + str(gate_log) + "\nexit 0\n", encoding="utf-8")
    (work / "scripts" / "run-tests.py").chmod(0o755)

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    lfs_log = tmp_path / "lfs-stdin.txt"
    (bin_dir / "git-lfs").write_text(
        "#!/bin/sh\ncat > " + str(lfs_log) + "\nexit 0\n", encoding="utf-8")
    (bin_dir / "git-lfs").chmod(0o755)

    hooks = Path(_git(work, "rev-parse", "--absolute-git-dir")) / "hooks"
    hooks.mkdir(parents=True, exist_ok=True)
    body = HOOK.read_text(encoding="utf-8").replace(
        '"$PY" "$ROOT/scripts/run-tests.py"', '"$ROOT/scripts/run-tests.py"')
    (hooks / "pre-push").write_text(body, encoding="utf-8")
    (hooks / "pre-push").chmod(0o755)

    (work / "file.txt").write_text("one\n", encoding="utf-8")
    _git(work, "add", "-A")
    _git(work, "commit", "-qm", "one")
    return work, remote, bin_dir, gate_log, lfs_log


def test_git_lfs_still_receives_the_ref_lines(pushable):
    """The hand-off the brief says not to break, driven by a real `git push`.

    Both consumers see the same line. A hook that read stdin for the gate and
    then `exec`ed git-lfs would leave the second file empty, and LFS objects
    would stop uploading with nothing said.
    """
    work, _remote, bin_dir, gate_log, lfs_log = pushable

    result = subprocess.run(
        ["git", "-C", str(work), "push", "-q", "origin", "main"],
        capture_output=True, text=True,
        env={**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}",
             "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
             "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"},
    )

    assert result.returncode == 0, result.stderr
    gate_saw = gate_log.read_text(encoding="utf-8")
    lfs_saw = lfs_log.read_text(encoding="utf-8")
    head = _git(work, "rev-parse", "HEAD")

    assert head in gate_saw, f"the gate got no ref line: {gate_saw!r}"
    assert head in lfs_saw, (
        f"git-lfs got no ref line: {lfs_saw!r}. The gate consumed stdin and did "
        f"not replay it, so LFS objects would stop uploading."
    )
    assert gate_saw == lfs_saw, (gate_saw, lfs_saw)


def test_a_failing_gate_blocks_the_push(pushable):
    """The property every other line here is in service of."""
    work, remote, bin_dir, _gate_log, _lfs_log = pushable
    (work / "scripts" / "run-tests.py").write_text(
        "#!/bin/sh\ncat > /dev/null\nexit 1\n", encoding="utf-8")
    (work / "scripts" / "run-tests.py").chmod(0o755)

    result = subprocess.run(
        ["git", "-C", str(work), "push", "-q", "origin", "main"],
        capture_output=True, text=True,
        env={**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"},
    )

    assert result.returncode != 0, "a red gate let the push through"
    assert _git(remote, "rev-parse", "--verify", "-q", "refs/heads/main",
                check=False) == "", "the ref landed on the remote anyway"


def test_the_hook_asks_run_tests_for_the_narrowed_mode():
    """The flag is what connects the hook to the gate; without it nothing narrows."""
    body = HOOK.read_text(encoding="utf-8")

    assert "--pre-push" in body, (
        "the pre-push hook no longer asks run-tests.py for the narrowed mode, so "
        "every push runs the whole suite again"
    )
    assert "run-tests.py" in body, (
        "scripts/push-all.py and install-git-hooks.py both detect the armed gate "
        "by this literal (ENGINE_GATE_MARKER)"
    )
