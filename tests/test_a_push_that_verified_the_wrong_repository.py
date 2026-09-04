"""Shard 49: a push helper that answered about a repository it was never given.

Every git call in `scripts/utils/git_push.py` is `git -C <path> ...`, and git
walks UP from that path to the enclosing repository. Nothing in the module ever
checked that the path it was handed was a repository ROOT.

MEASURED 2026-08-28 against a bare engine clone, `ahead_behind` with default
arguments:

    <root>                    -> (0, 20)
    <root>/examples           -> (0, 20)
    <root>/scripts/utils      -> (0, 20)

The same three numbers, because all three questions were answered about the
enclosing repository. `supervised_push` ends with
`ahead_behind(repo, remote, branch) == (0, 0)` as its postcondition, so a
subdirectory handed to it pushes its PARENT and then verifies its PARENT's ref.
The run reports a verified push of a repository it was never given.

Six callers reach `supervised_push` - `safe-push.py`, `push-all.py`,
`publish-service.py`, `promote-knowledge.py`, `create-data-repo.py`,
`offboard-exec.py` - each passing a path it believes is a root, and none of them
checked either. The guard is at the chokepoint, which is what the comment above
the leak wall in that function already claims for itself.

The reachable case is not hypothetical. On a clone with no private overlay
`get_data_root()` resolves to `<engine>/examples`, a demo DIRECTORY inside the
engine clone. MEASURED on such a clone: `safe-push --repo all` sent that
directory through the whole pipeline, `_is_split_engine` answered False so the
leak wall never scanned it, `git -C` resolved it to the ENGINE's own remote, and
the remote wall refused with

    examples pushes to the ENGINE remote (github.com/mishahanin/heading-os),
    which is the public code repository. Refusing: this would publish private
    content.

The refusal is right and the stated cause is wrong. There is no private content
in `examples/`, and no data overlay on that machine at all. An operator reading
that goes hunting for a leak that does not exist, which is the failure
`.claude/rules/scope-claims.md` exists to prevent: a tool saying more than its
method established.

The guard only ever refuses on POSITIVE evidence. `enclosing_repo_root` returns
None for a bare repository, for a path that is no repository, and when git
cannot be run; None means unknown, and an unknown still reaches git so git can
fail on its own. A linked git worktree is its own toplevel and passes.

Tests: this file. See also tests/test_git_push.py, which owns the happy path,
the leak wall and the remote wall.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.utils.git_push import (  # noqa: E402
    ahead_behind,
    enclosing_repo_root,
    supervised_push,
)


@pytest.fixture(autouse=True)
def _supervisor_log_in_tmp_path(tmp_path, monkeypatch):
    """Real pushes in this file keep their supervisor log under `tmp_path`.

    `run_supervised` returns `verdict["log_path"]` so a human can open it after
    a push that went wrong. It therefore does not remove the log, and production
    must keep that. Under pytest nobody opens it and nothing removed it:
    MEASURED 2026-09-04 over a full `-n auto` run, 20 surviving
    `/tmp/supervise-*.log`, from the four test files that reach this call.

    Patched at `git_push.run_supervised`, not at each `supervised_push(...)`
    call site below. Two reasons and the second is the one that matters: most
    of those calls are refused by a wall before any child starts, so a
    `log_dir=` on each would be noise on the many to reach the few; and a call
    site is a place to forget, while a test added to this file tomorrow
    inherits this without knowing it exists.

    A test that installs its own `run_supervised` fake replaces this wrapper
    outright, which is correct: a fake spawns nothing and so leaks nothing.
    """
    from scripts.utils import git_push as _gp

    real = _gp.run_supervised

    def pinned(*a, **kw):
        # `is None`, NOT `setdefault`. `supervised_push` forwards `log_dir`
        # unconditionally, so the key is always PRESENT and always None unless
        # a caller set it; `setdefault` saw the key and did nothing, and the
        # first version of this fixture pinned nothing at all. Caught by the
        # leak guard itself, which still named all 20 logs after the "fix".
        if kw.get("log_dir") is None:
            kw["log_dir"] = str(tmp_path)
        return real(*a, **kw)

    monkeypatch.setattr(_gp, "run_supervised", pinned)


def _load(stem: str, name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{stem}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


sp = _load("safe-push", "safe_push_s49")


@pytest.fixture(autouse=True)
def _reach_safe_push_main(unguard_main_clone):
    """`safe-push.main()` opens with `require_main_clone(__file__)`, which exits
    2 from a worktree before the CLI-level refusals below are reached.
    Neutralised on THIS loaded module, for the duration of one test.

    Not a silenced guard: it is owned by
    `tests/test_guarded_entry_points_refuse_from_a_worktree.py`, which pins
    through the AST that the call is the first statement of `main()` and is
    passed `__file__`, and by `tests/test_clone_guard.py`, which pins that it
    fires. This file owns the behaviour behind it.
    """
    unguard_main_clone(sp)


def _git(args, cwd):
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True,
                          text=True, check=True)


def _make_repo(tmp_path):
    """A work tree with a LOCAL bare remote. No network anywhere in this file."""
    remote = tmp_path / "remote.git"
    work = tmp_path / "work"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(remote)],
                   check=True, capture_output=True)
    subprocess.run(["git", "init", "-b", "main", str(work)],
                   check=True, capture_output=True)
    _git(["config", "user.email", "t@example.com"], work)
    _git(["config", "user.name", "Test"], work)
    (work / "f.txt").write_text("hi", encoding="utf-8")
    _git(["add", "-A"], work)
    _git(["commit", "-m", "init"], work)
    _git(["remote", "add", "origin", str(remote)], work)
    return remote, work


def _remote_head(remote: Path) -> str | None:
    out = subprocess.run(["git", "-C", str(remote), "rev-parse", "main"],
                         capture_output=True, text=True)
    return out.stdout.strip() if out.returncode == 0 else None


# ==========================================================================
# 1 - the question nothing asked
# ==========================================================================

def test_a_repository_root_is_its_own_root(tmp_path):
    _remote, work = _make_repo(tmp_path)
    assert enclosing_repo_root(work) == work.resolve()


def test_a_subdirectory_resolves_to_the_repository_above_it(tmp_path):
    """This IS the defect, stated as the fact it rests on."""
    _remote, work = _make_repo(tmp_path)
    sub = work / "nested" / "deeper"
    sub.mkdir(parents=True)
    assert enclosing_repo_root(sub) == work.resolve()


@pytest.fixture
def outside_any_work_tree(tmp_path, monkeypatch):
    """A directory git cannot resolve to any enclosing repository.

    Both "not a repository" tests below used a bare `tmp_path / "plain"` and
    assumed pytest's basetemp lies outside every git work tree. It need not.
    Point `TMPDIR` inside this checkout and git discovery correctly walks up
    from `plain` and finds THIS repository: `enclosing_repo_root` then returns
    a root, and `supervised_push` returns the very refusal the second test
    says must not happen. Measured 2026-08-30 with
    `TMPDIR="$PWD/.tmp/..." pytest ...`: both tests failed, with no defect in
    the guard.

    `GIT_CEILING_DIRECTORIES` stops git's upward walk before it leaves
    `tmp_path`, so the fact under test ("this path is in no repository") is
    established by the fixture rather than borrowed from the machine. The
    assertion below measures that it worked instead of trusting it.
    """
    plain = tmp_path / "plain"
    plain.mkdir()
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path.resolve()))
    probe = subprocess.run(["git", "rev-parse", "--show-toplevel"],
                           cwd=plain, capture_output=True, text=True)
    assert probe.returncode != 0, (
        "the ceiling did not hold: git still resolves a work tree above "
        f"{plain} ({probe.stdout.strip()!r})")
    return plain


def test_a_path_that_is_no_repository_is_unknown(outside_any_work_tree):
    """None means "could not establish", never "this is a root". The guard
    refuses only on positive evidence, so unknown still reaches git."""
    assert enclosing_repo_root(outside_any_work_tree) is None


def test_a_missing_path_is_unknown(tmp_path):
    assert enclosing_repo_root(tmp_path / "gone") is None


def test_a_bare_repository_is_unknown(tmp_path):
    """A bare repository has no work tree, so `--show-toplevel` fails. Treating
    that as "not a root" would refuse a push nobody asked this to refuse."""
    bare = tmp_path / "bare.git"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(bare)],
                   check=True, capture_output=True)
    assert enclosing_repo_root(bare) is None


def test_a_linked_worktree_is_its_own_root(tmp_path):
    """The engine's own CI simulation runs from a linked worktree. If this
    answered with the main clone, the guard would refuse every push from one."""
    _remote, work = _make_repo(tmp_path)
    wt = tmp_path / "wt"
    _git(["worktree", "add", "-b", "side", str(wt)], work)
    assert enclosing_repo_root(wt) == wt.resolve()


def test_a_relative_path_to_a_root_is_not_refused(tmp_path, monkeypatch):
    """The caller side of the comparison, and the one that can produce a FALSE
    refusal rather than a missed one.

    `enclosing_repo_root` returns an absolute path, so it is only comparable to
    an absolute one. Two of the six callers hand this function whatever they
    were given: `promote-knowledge.py` passes `str(repo)` and `offboard-exec.py`
    passes `cwd`. Drop the `.resolve()` on the caller side and a relative path
    to a genuine root compares unequal to its own absolute form, and the push is
    refused for being a subdirectory of itself. A mutation that did exactly that
    survived every other test in this file, which is what asked the question.
    """
    remote, work = _make_repo(tmp_path)
    monkeypatch.chdir(tmp_path)

    v = supervised_push(Path("work"), remote="origin", branch="main",
                        stall_window=15)

    assert v["state"] == "ok", v
    assert _remote_head(remote) is not None


def test_two_defensive_branches_that_no_input_can_reach():
    """Stated rather than tested, because there is nothing left to test.

    Two mutations inside `enclosing_repo_root` survive by being equivalent, and
    both are recorded here so the next mutation run finds the answer instead of
    chasing them again.

    `Path(top).resolve()` -> `Path(top)`. MEASURED 2026-08-28: `git rev-parse
    --show-toplevel` already returns an absolute path with symlinks resolved.
    Invoked through a symlink to a repository it returned the physical path,
    identical to what `.resolve()` produces. The call is kept because a
    non-canonical answer would cause a false refusal, which is the expensive
    direction, and it costs one syscall.

    `if not top: return None` -> `return Path(path).resolve()`. Both make the
    guard pass: the caller's test is `root is not None and root != repo`, and
    `repo.resolve()` fails the second half exactly as `None` fails the first.
    The branch is a guard against `Path("")`, which is `.` and would resolve to
    the working directory rather than to anything git said.
    """
    from scripts.utils import git_push as _gp
    src = Path(_gp.__file__).read_text(encoding="utf-8")
    assert "return Path(top).resolve()" in src
    assert "if not top:" in src


def test_ahead_behind_answers_about_the_enclosing_repository(tmp_path):
    """The measurement behind the whole shard, pinned so it cannot be forgotten:
    the postcondition helper cannot tell a subdirectory from a root, which is
    why the guard has to sit above it rather than inside it."""
    _remote, work = _make_repo(tmp_path)
    supervised_push(work, branch="main", stall_window=15)
    (work / "g.txt").write_text("more", encoding="utf-8")
    _git(["add", "-A"], work)
    _git(["commit", "-m", "second"], work)
    sub = work / "nested"
    sub.mkdir()
    assert ahead_behind(work, "origin", "main") == (0, 1)
    assert ahead_behind(sub, "origin", "main") == (0, 1)


# ==========================================================================
# 2 - the chokepoint refuses, and pushes nothing
# ==========================================================================

def _reason_beyond_the_path_passed_in(reason: str, passed: Path) -> str:
    """The part of a refusal reason that the INPUT path cannot account for.

    A subdirectory's path contains its root's path as a prefix, so
    `str(root) in reason` and `root.name in reason` are both satisfied by the
    reason merely echoing back the path it was handed. MEASURED 2026-09-01:
    deleting `{root}` from the refusal message left both assertions green,
    because `{repo}` opens the sentence and `{repo}` is `<root>/nested`. Slicing
    past that echo is what makes "the refusal NAMES the enclosing repository" a
    claim the test actually measures.
    """
    marker = str(passed)
    assert marker in reason, f"the refusal no longer echoes the path it refused: {reason}"
    return reason.split(marker, 1)[1]


def test_a_subdirectory_is_refused(tmp_path):
    _remote, work = _make_repo(tmp_path)
    sub = work / "nested"
    sub.mkdir()

    v = supervised_push(sub, remote="origin", branch="main", stall_window=15)

    assert v["state"] == "failed", v
    assert "not a git repository root" in v["reason"]
    assert str(work.resolve()) in _reason_beyond_the_path_passed_in(v["reason"], sub)


def test_the_refusal_pushes_nothing(tmp_path):
    """The point of refusing BEFORE the subprocess rather than reading the
    verdict afterwards."""
    remote, work = _make_repo(tmp_path)
    assert _remote_head(remote) is None, "the bare remote starts empty"
    sub = work / "nested"
    sub.mkdir()

    supervised_push(sub, remote="origin", branch="main", stall_window=15)

    assert _remote_head(remote) is None, "the parent repository was pushed"


def test_the_refusal_says_which_repository_would_have_been_pushed(tmp_path):
    """A reason naming only "not a root" leaves the operator guessing which
    repository was about to move."""
    _remote, work = _make_repo(tmp_path)
    sub = work / "nested"
    sub.mkdir()

    reason = supervised_push(sub, stall_window=15)["reason"]
    beyond = _reason_beyond_the_path_passed_in(reason, sub)

    assert work.name in beyond
    assert "postcondition" in reason


def test_the_refusal_carries_the_verdict_shape_callers_read(tmp_path):
    """Six callers read this dict. A refusal that omits a key they index into
    turns a clean refusal into an unhandled KeyError."""
    _remote, work = _make_repo(tmp_path)
    sub = work / "nested"
    sub.mkdir()

    v = supervised_push(sub, stall_window=15)

    for key in ("state", "reason", "elapsed_s", "exit_code", "tail"):
        assert key in v, key
    assert v["exit_code"] is None
    assert v["elapsed_s"] == 0.0


def test_a_real_root_still_pushes(tmp_path):
    """The anchor. A guard that refused everything would pass every test above
    and stop all six callers from working."""
    remote, work = _make_repo(tmp_path)

    v = supervised_push(work, remote="origin", branch="main", stall_window=15)

    assert v["state"] == "ok", v
    assert _remote_head(remote) is not None


def test_a_path_that_is_no_repository_is_not_refused_by_this_guard(outside_any_work_tree):
    """Unknown is not refused here. git fails on its own, and the verdict must
    say so rather than blame a repository root that was never established."""
    plain = outside_any_work_tree

    v = supervised_push(plain, remote="origin", branch="main", stall_window=15)

    assert v["state"] != "ok"
    assert "not a git repository root" not in v["reason"]


# ==========================================================================
# 3 - the wall that refused for the wrong reason
# ==========================================================================

def test_a_subdirectory_of_the_engine_is_refused_for_the_right_reason(tmp_path, monkeypatch):
    """Before the guard, `<engine>/examples` reached the remote wall and was
    refused with "this would publish private content" - true of neither the
    directory nor the machine. `_is_split_engine` had already answered False for
    it, so the leak wall never even ran.
    """
    _remote, work = _make_repo(tmp_path)
    examples = work / "examples"
    examples.mkdir()
    monkeypatch.setattr("scripts.utils.git_push.get_workspace_root", lambda: work)
    monkeypatch.setattr("scripts.utils.git_push.get_data_root", lambda: examples)

    v = supervised_push(examples, remote="origin", branch="main", stall_window=15)

    assert v["state"] == "failed"
    assert "not a git repository root" in v["reason"]
    assert "publish private content" not in v["reason"]


# ==========================================================================
# 4 - safe-push, which is where an operator meets this
# ==========================================================================

def test_no_data_overlay_names_the_real_cause(tmp_path, monkeypatch):
    _remote, work = _make_repo(tmp_path)
    examples = work / "examples"
    examples.mkdir()
    monkeypatch.setattr(sp, "get_workspace_root", lambda: work)
    monkeypatch.setattr(sp, "get_data_root", lambda: examples)

    why = sp._no_data_overlay()

    assert why is not None
    assert "no private data overlay" in why
    assert str(examples) in why


def test_a_separate_data_repository_raises_no_objection(tmp_path, monkeypatch):
    """The other half. Reporting "no overlay" on a machine that has one would
    stop the operator's real backup."""
    _r1, engine = _make_repo(tmp_path / "a")
    _r2, data = _make_repo(tmp_path / "b")
    monkeypatch.setattr(sp, "get_workspace_root", lambda: engine)
    monkeypatch.setattr(sp, "get_data_root", lambda: data)

    assert sp._no_data_overlay() is None


def test_a_data_root_that_collapsed_onto_the_engine_is_named(tmp_path, monkeypatch):
    _remote, work = _make_repo(tmp_path)
    monkeypatch.setattr(sp, "get_workspace_root", lambda: work)
    monkeypatch.setattr(sp, "get_data_root", lambda: work)

    why = sp._no_data_overlay()

    assert why is not None
    assert "no separate overlay" in why


def test_an_unresolvable_data_root_is_reported_not_swallowed(tmp_path, monkeypatch):
    _remote, work = _make_repo(tmp_path)
    monkeypatch.setattr(sp, "get_workspace_root", lambda: work)

    def _boom():
        raise RuntimeError("HEADING_OS_DATA points nowhere")

    monkeypatch.setattr(sp, "get_data_root", _boom)

    why = sp._no_data_overlay()

    assert why is not None
    assert "points nowhere" in why


@pytest.fixture
def _no_overlay_cli(tmp_path, monkeypatch):
    _remote, work = _make_repo(tmp_path)
    examples = work / "examples"
    examples.mkdir()
    monkeypatch.setattr(sp, "get_workspace_root", lambda: work)
    monkeypatch.setattr(sp, "get_data_root", lambda: examples)
    monkeypatch.setattr(sp, "load_gh_token", lambda: "t0ken")

    def _never(*a, **k):
        raise AssertionError("a push was attempted for a repository that is not one")

    monkeypatch.setattr(sp, "supervised_push", _never)
    return work


def test_the_cli_stops_before_attempting_a_push(_no_overlay_cli, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["safe-push", "--repo", "data"])
    assert sp.main() == 3
    assert "nothing to push for 'data'" in capsys.readouterr().err


def test_the_cli_stops_for_repo_all_too(_no_overlay_cli, monkeypatch, capsys):
    """`--repo all` is the documented usage that reached the wrong refusal, and
    it must stop BEFORE pushing the engine so the run is not half done."""
    monkeypatch.setattr(sys, "argv", ["safe-push", "--repo", "all"])
    assert sp.main() == 3
    assert "nothing to push for 'data'" in capsys.readouterr().err


def test_the_json_verdict_is_always_a_list(_no_overlay_cli, monkeypatch, capsys):
    """It printed a bare object on the auth path and a list everywhere else, so
    `json.loads(out)[0]["state"]` crashed on exactly the path a consumer most
    needs to read."""
    monkeypatch.setattr(sys, "argv", ["safe-push", "--repo", "data", "--json"])
    assert sp.main() == 3
    payload = json.loads(capsys.readouterr().out)
    assert isinstance(payload, list)
    assert payload[0]["state"] == "no_data_repo"


def test_the_auth_error_json_is_a_list_too(tmp_path, monkeypatch, capsys):
    _remote, work = _make_repo(tmp_path)
    monkeypatch.setattr(sp, "get_workspace_root", lambda: work)
    monkeypatch.setattr(sp, "load_gh_token", lambda: None)
    monkeypatch.setattr(sys, "argv", ["safe-push", "--repo", "engine", "--json"])

    assert sp.main() == 3
    payload = json.loads(capsys.readouterr().out)
    assert isinstance(payload, list)
    assert payload[0]["state"] == "auth_error"


def test_pushing_only_the_engine_never_asks_about_the_overlay(tmp_path, monkeypatch):
    """`--repo engine` is the advertised standalone usage. A data-root check on
    that path would reintroduce the very failure the lazy `_repo_path` fixed."""
    _remote, work = _make_repo(tmp_path)
    monkeypatch.setattr(sp, "get_workspace_root", lambda: work)
    monkeypatch.setattr(sp, "load_gh_token", lambda: "t0ken")

    def _boom():
        raise AssertionError("get_data_root was called for --repo engine")

    monkeypatch.setattr(sp, "get_data_root", _boom)
    monkeypatch.setattr(sp, "supervised_push",
                        lambda *a, **k: {"state": "ok", "reason": "", "elapsed_s": 0.1,
                                         "exit_code": 0, "tail": ""})
    monkeypatch.setattr(sys, "argv", ["safe-push", "--repo", "engine"])

    assert sp.main() == 0
