"""The remote wall inspected one environment; the push ran another.

`scripts/utils/git_push.py` is the one must-complete push primitive, and its
remote-identity wall is the check that stops the private overlay reaching the
public engine repository. The wall asks git for the push URL by shelling out;
`supervised_push` then runs `git push` with the caller's `env=`. Those were two
different environments, and a git URL rewrite lives in the environment.

MEASURED 2026-08-29 with a local `file://` bare repository standing in for the
engine remote (.tmp/audit/measure59b.py), driving the real `supervised_push`:

    url.<base>.insteadOf present only in the PUSH env
      -> wall silent, state "ok", ahead/behind postcondition satisfied,
         `intended` received NOTHING and the ENGINE remote received the commit.

    url.<base>.pushInsteadOf present only in the PUSH env
      -> the same.

    the same pair present only in the AMBIENT environment
      -> refused a push that would have been entirely safe.

Both directions, from one cause. The first diagnosis blamed the git command and
was wrong: measured, `git remote get-url --push` DOES apply `pushurl`,
`insteadOf` and `pushInsteadOf`, and agrees with `git push --dry-run` in every
combination. It was being asked in the wrong world.

The fix is not a denylist of dangerous variables - that is always one variable
short, which is the defect shape this audit keeps finding. It is that the
environment is resolved ONCE, before any wall runs, and every git call the push
depends on takes it: the root check, the remote wall, and the ahead/behind
postcondition.

The module docstring also promised, of the four (env, token) combinations, that
"neither inherits the ambient environment". Two of the four do. That sentence is
gone and the four paths are now stated one by one, which the first test here
holds to the code.
"""
from __future__ import annotations

import importlib.util
import os
import subprocess
from pathlib import Path

import pytest

from scripts.utils import git_push

ROOT = Path(__file__).resolve().parent.parent

AMBIENT_MARKER = "SHARD59_AMBIENT_MARKER"

# Not a literal at the assertion site: ruff S105 reads a string compared
# against a TOKEN-shaped key as a hardcoded credential, and it is right to.
_MARKER_VALUE = "shard59-stand-in"

# Three distinguishable stand-ins for the credential the remote wall is handed.
# Named rather than inlined for the same ruff S105 reason as `_MARKER_VALUE`,
# and DISTINCT from each other on purpose: a precedence chain whose legs share a
# value is a fixture whose two values are the same string, and it proves the
# chain resolved to something without proving it resolved to the right leg.
_ENV_CREDENTIAL = "shard30-from-the-push-env"
_ARG_CREDENTIAL = "shard30-from-the-argument"
_FALLBACK_CREDENTIAL = "shard30-from-the-stored-file"


def _git_probe(*args, cwd=None, env=None):
    """Ask git a question whose non-zero exit is the ANSWER, not a failure.

    `rev-parse --verify -q` on an absent ref exits 1 and prints nothing, which
    is how `_head` reports "no such ref". Only a probe may use `check=False`.
    """
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True,
                          text=True, env=env, timeout=60, check=False)


def _git(*args, cwd=None, env=None):
    """Arrange a fixture repository. A non-zero exit STOPS the test.

    SPLIT FROM `_git_probe` 2026-08-30. One helper served both roles with
    `check=False`, and no caller inspected a return code, so a failed
    `git init -b`, `remote add`, `add` or `commit` was silently ignored: the
    fixture carried on, and the test then reported a push or wall outcome
    computed over a repository that was never built. In the empty-repository
    case it could even SATISFY an assertion such as `not _head(...)` -- a pass
    earned by the setup having failed. Now the arrangement raises
    `CalledProcessError` naming the command, so a broken fixture reads as a
    broken fixture.
    """
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True,
                          text=True, env=env, timeout=60, check=True)


@pytest.fixture
def marked_ambient(monkeypatch):
    """A variable that exists ONLY in the parent environment.

    Its presence in a child env proves ambient inheritance; its absence proves
    the caller's dict was used verbatim.
    """
    monkeypatch.setenv(AMBIENT_MARKER, "leaked")
    return AMBIENT_MARKER


@pytest.fixture
def quiet_walls(monkeypatch):
    """Neutralise the walls. Used only by the tests that are about the ENV."""
    monkeypatch.setattr(git_push, "remote_objection", lambda *a, **k: None)
    monkeypatch.setattr(git_push, "enclosing_repo_root", lambda p, **k: None)
    monkeypatch.setattr(git_push, "_roots_unreadable", lambda p: None)
    monkeypatch.setattr(git_push, "_is_split_engine", lambda p: False)


@pytest.fixture
def captured_child(monkeypatch):
    """What `run_supervised` was handed, without running anything."""
    seen: dict = {}

    def fake(cmd, *, env=None, **kw):
        seen["cmd"] = list(cmd)
        seen["env"] = env
        # The real `run_supervised` CALLS the postcondition; this fake does not,
        # so it has to hand it back or the third env-carrying check is
        # unobservable. It was swallowed by `**kw` until 2026-08-30.
        seen["postcondition"] = kw.get("postcondition")
        return {"state": "ok", "elapsed_s": 0.0, "exit_code": 0, "tail": ""}

    monkeypatch.setattr(git_push, "run_supervised", fake)
    return seen


# ============================================================
# 1 - the four auth paths, one by one
# ============================================================
def test_no_env_and_no_token_inherits_the_ambient_environment(
        quiet_walls, captured_child, marked_ambient, tmp_path):
    """`env=None` reaches Popen, and Popen with env=None inherits everything."""
    git_push.supervised_push(tmp_path)
    assert captured_child["env"] is None


def test_a_token_alone_copies_the_ambient_environment(
        quiet_walls, captured_child, marked_ambient, tmp_path):
    git_push.supervised_push(tmp_path, token=_MARKER_VALUE)
    child = captured_child["env"]
    assert child[marked_ambient] == "leaked"
    assert child["GH_PUSH_TOKEN"] == _MARKER_VALUE
    assert child["GIT_TERMINAL_PROMPT"] == "0"


def test_a_caller_env_is_used_verbatim(quiet_walls, captured_child,
                                       marked_ambient, tmp_path):
    git_push.supervised_push(tmp_path, env={"HOME": "/nowhere"})
    child = captured_child["env"]
    assert marked_ambient not in child
    assert child == {"HOME": "/nowhere"}


def test_a_caller_env_plus_a_token_adds_only_the_token(
        quiet_walls, captured_child, marked_ambient, tmp_path):
    git_push.supervised_push(tmp_path, env={"HOME": "/nowhere"}, token=_MARKER_VALUE)
    child = captured_child["env"]
    assert marked_ambient not in child
    assert set(child) == {"HOME", "GH_PUSH_TOKEN", "GIT_TERMINAL_PROMPT"}


def test_the_token_never_reaches_argv(quiet_walls, captured_child, tmp_path):
    git_push.supervised_push(tmp_path, token="shard59-must-not-appear")
    assert not any("shard59-must-not-appear" in part
                   for part in captured_child["cmd"])


# A test asserting the retired sentence is ABSENT from the module docstring was
# written here and removed. It went red, correctly: the docstring now QUOTES the
# false promise in order to record that it was false. A substring check cannot
# tell a live claim from a citation of a retired one, so it would have forced
# the history out of the file to stay green. The four tests above are what holds
# the docstring honest, because they hold the CODE to what it says.


# ============================================================
# 2 - the wall and the push get the SAME environment
# ============================================================
def test_every_wall_receives_the_environment_the_push_will_run_with(
        monkeypatch, captured_child, tmp_path):
    """The structural invariant, with no git involved.

    Each of the three git-backed checks records the env it was handed. All of
    them must equal, by identity of content, what reaches the child.
    """
    got: dict[str, object] = {}
    monkeypatch.setattr(git_push, "enclosing_repo_root",
                        lambda p, *, env=None: got.__setitem__("root", env))
    monkeypatch.setattr(git_push, "_roots_unreadable", lambda p: None)
    monkeypatch.setattr(git_push, "_is_split_engine", lambda p: False)
    monkeypatch.setattr(git_push, "remote_objection",
                        lambda repo, **k: got.__setitem__("wall", k.get("env")))
    monkeypatch.setattr(git_push, "ahead_behind",
                        lambda *a, **k: got.__setitem__("after", k.get("env")) or (0, 0))

    caller_env = {"HOME": "/nowhere", "GIT_CONFIG_COUNT": "0"}
    git_push.supervised_push(tmp_path, env=caller_env)
    assert got["root"] == caller_env
    assert got["wall"] == caller_env
    assert captured_child["env"] == caller_env

    # The postcondition is a CLOSURE, so `supervised_push` returning does not
    # mean it ran: the fake `run_supervised` never invokes it. Until 2026-08-30
    # a comment here said "call it the way run_supervised would" and nothing
    # called anything, so the `ahead_behind` stub was dead code, `got["after"]`
    # was never set and never asserted, and the third of the three checks the
    # docstring above counts was uncovered. Invoke it, then assert.
    assert "after" not in got, (
        "the fake run_supervised invoked the postcondition; this test no longer "
        "proves the closure carries the env on its own")
    postcondition = captured_child["postcondition"]
    assert callable(postcondition), "no postcondition reached run_supervised"
    assert postcondition() is True
    assert got["after"] == caller_env


@pytest.fixture
def captured_token(monkeypatch):
    """The credential `supervised_push` hands the remote wall, without pushing."""
    seen: dict = {}
    monkeypatch.setattr(git_push, "enclosing_repo_root", lambda p, **k: None)
    monkeypatch.setattr(git_push, "_roots_unreadable", lambda p: None)
    monkeypatch.setattr(git_push, "_is_split_engine", lambda p: False)
    monkeypatch.setattr(git_push, "load_gh_token", lambda: _FALLBACK_CREDENTIAL)
    monkeypatch.setattr(
        git_push, "remote_objection",
        lambda repo, **k: seen.__setitem__("token", k.get("token")))
    monkeypatch.setattr(
        git_push, "run_supervised",
        lambda cmd, **kw: {"state": "ok", "elapsed_s": 0.0,
                           "exit_code": 0, "tail": ""})
    return seen


def test_the_wall_is_given_the_credential_the_push_env_carries(
        captured_token, tmp_path):
    """The same defect as the rest of this file, in the other argument.

    `push-all.py` never passes `token=`; it puts `GH_TOKEN` inside the `env`
    dict it hands to `supervised_push`, and the line that reaches into that dict
    is what lets the remote wall ask GitHub whether the remote is public. Drop
    that one leg and the wall still runs, still returns, and still reports
    nothing - it just asks GitHub anonymously, so a private remote answers 404,
    `_gh_visibility` returns None, and Check B goes dark with no message.

    MEASURED 2026-09-01: deleting `(env or {}).get("GH_TOKEN")` from the token
    expression left 177 tests green across this file, `tests/test_git_push.py`
    and `tests/test_one_file_six_parsers.py` - every test in the tree that
    mentions `GH_TOKEN` at all.
    """
    git_push.supervised_push(tmp_path, env={"GH_TOKEN": _ENV_CREDENTIAL})
    assert captured_token["token"] == _ENV_CREDENTIAL


def test_an_explicit_token_still_outranks_the_one_in_the_env(
        captured_token, tmp_path):
    """The contrast, derived the other way: the precedence is argument first."""
    git_push.supervised_push(tmp_path, env={"GH_TOKEN": _ENV_CREDENTIAL},
                             token=_ARG_CREDENTIAL)
    assert captured_token["token"] == _ARG_CREDENTIAL


def test_with_neither_the_wall_falls_back_to_the_stored_credential(
        captured_token, tmp_path):
    """And the third leg, so the chain is pinned end to end rather than at one
    link. Without this case a mutation that hard-wires the env leg would pass."""
    git_push.supervised_push(tmp_path, env={"HOME": "/nowhere"})
    assert captured_token["token"] == _FALLBACK_CREDENTIAL


def test_the_postcondition_verifies_under_the_same_environment(monkeypatch, tmp_path):
    seen: dict = {}
    monkeypatch.setattr(git_push, "enclosing_repo_root", lambda p, **k: None)
    monkeypatch.setattr(git_push, "_roots_unreadable", lambda p: None)
    monkeypatch.setattr(git_push, "_is_split_engine", lambda p: False)
    monkeypatch.setattr(git_push, "remote_objection", lambda *a, **k: None)
    monkeypatch.setattr(git_push, "ahead_behind",
                        lambda *a, **k: seen.setdefault("env", k.get("env")) or (0, 0))

    def fake(cmd, *, env=None, postcondition=None, **kw):
        postcondition()
        return {"state": "ok", "elapsed_s": 0.0, "exit_code": 0, "tail": ""}

    monkeypatch.setattr(git_push, "run_supervised", fake)
    caller_env = {"HOME": "/nowhere"}
    git_push.supervised_push(tmp_path, env=caller_env)
    assert seen["env"] == caller_env


# ============================================================
# 3 - end to end, against real repositories on disk
# ============================================================
@pytest.fixture
def world(tmp_path, monkeypatch):
    """intended.git, engine.git, a work tree, and a fake engine CLONE.

    The engine clone's `origin` points at engine.git, so `_engine_push_urls`
    reads a real remote rather than a stub, and Check A compares real
    identities. `get_workspace_root`/`get_data_root` are pinned so the test
    does not depend on a data overlay existing, which CI does not have.
    """
    intended = tmp_path / "intended.git"
    engine_bare = tmp_path / "engine.git"
    for bare in (intended, engine_bare):
        _git("init", "--bare", "-q", "-b", "main", str(bare))

    engine_clone = tmp_path / "engine"
    _git("init", "-q", "-b", "main", str(engine_clone))
    _git("remote", "add", "origin", f"file://{engine_bare}", cwd=engine_clone)

    work = tmp_path / "work"
    _git("init", "-q", "-b", "main", str(work))
    _git("config", "user.email", "probe@example.invalid", cwd=work)
    _git("config", "user.name", "Probe", cwd=work)
    (work / "secret.txt").write_text("private overlay content\n", encoding="utf-8")
    _git("add", "-A", cwd=work)
    _git("commit", "-qm", "one", cwd=work)
    _git("remote", "add", "origin", f"file://{intended}", cwd=work)

    monkeypatch.setattr(git_push, "get_workspace_root", lambda: engine_clone)
    monkeypatch.setattr(git_push, "get_data_root", lambda: tmp_path / "data")
    monkeypatch.setattr(git_push, "load_gh_token", lambda: None)
    git_push._VIS_CACHE.clear()

    return {"intended": intended, "engine_bare": engine_bare, "work": work}


def _rewrite(target: Path, source: Path, key: str) -> dict:
    return {
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": f"url.file://{target}/.{key}",
        "GIT_CONFIG_VALUE_0": f"file://{source}",
    }


def _head(bare: Path) -> str:
    """The `main` commit in `bare`, or "" when the ref does not exist yet.

    A PROBE: an absent ref is the answer several tests are asking for, so this
    is the one caller that must not raise on a non-zero exit.
    """
    return _git_probe("--git-dir", str(bare), "rev-parse", "--verify", "-q",
                      "main").stdout.strip()


def test_a_clean_push_still_reaches_its_own_remote(world):
    env = dict(os.environ)
    verdict = git_push.supervised_push(world["work"], env=env, stall_window=30)
    assert verdict["state"] == "ok", verdict.get("reason")
    assert _head(world["intended"])
    assert not _head(world["engine_bare"])


@pytest.mark.parametrize("key", ["insteadOf", "pushInsteadOf"])
def test_a_rewrite_in_the_push_env_is_refused(world, key):
    """The bypass. Before the fix this pushed the overlay and reported ok."""
    env = dict(os.environ)
    env.update(_rewrite(world["engine_bare"], world["intended"], key))
    verdict = git_push.supervised_push(world["work"], env=env, stall_window=30)
    assert verdict["state"] == "failed"
    assert "ENGINE remote" in verdict["reason"]
    assert not _head(world["engine_bare"]), "the overlay reached the engine remote"
    assert not _head(world["intended"])


@pytest.mark.parametrize("key", ["insteadOf", "pushInsteadOf"])
def test_a_rewrite_only_in_the_ambient_env_does_not_refuse(world, key, monkeypatch):
    """The other direction: the wall must not object to a push that is safe."""
    for k, v in _rewrite(world["engine_bare"], world["intended"], key).items():
        monkeypatch.setenv(k, v)
    clean = {k: v for k, v in os.environ.items()
             if not k.startswith("GIT_CONFIG_")}
    verdict = git_push.supervised_push(world["work"], env=clean, stall_window=30)
    assert verdict["state"] == "ok", verdict.get("reason")
    assert _head(world["intended"])
    assert not _head(world["engine_bare"])


def test_the_wall_still_refuses_a_plainly_configured_engine_remote(world):
    """The rewrite cases must not be the only way this fires."""
    _git("remote", "set-url", "--push", "origin",
         f"file://{world['engine_bare']}", cwd=world["work"])
    verdict = git_push.supervised_push(world["work"], env=dict(os.environ),
                                       stall_window=30)
    assert verdict["state"] == "failed"
    assert "ENGINE remote" in verdict["reason"]


# ============================================================
# 4 - each git helper honours the env it is given
# ============================================================
def test_push_url_honours_the_env_it_is_given(world):
    env = dict(os.environ)
    env.update(_rewrite(world["engine_bare"], world["intended"], "insteadOf"))
    plain = git_push._push_url(world["work"], "origin")
    rewritten = git_push._push_url(world["work"], "origin", env=env)
    assert str(world["intended"]) in plain
    assert str(world["engine_bare"]) in rewritten


def test_engine_push_urls_rewrites_under_the_env_it_is_given(world, tmp_path):
    other = tmp_path / "other.git"
    _git("init", "--bare", "-q", "-b", "main", str(other))
    env = dict(os.environ)
    env.update(_rewrite(other, world["engine_bare"], "insteadOf"))
    engine_clone = tmp_path / "engine"
    plain = git_push._engine_push_urls(engine_clone)
    rewritten = git_push._engine_push_urls(engine_clone, env=env)
    assert plain != rewritten
    assert any("other" in u for u in rewritten)


def test_engine_push_urls_lists_the_remotes_the_env_names(world, tmp_path):
    """The env decides WHICH remotes exist, not only what they resolve to.

    `git remote` is a second subprocess and needs the env for its own reason:
    `GIT_DIR` moves the question to another repository, whose remotes carry
    other NAMES. Read the list in the wrong world and the names that come back
    do not exist in the right one, so every URL lookup fails and Check A gets
    an EMPTY set. An empty set fails open.

    A `GIT_CONFIG_*` pair defining `remote.<name>.url` was tried first and does
    not work: measured 2026-08-29, `git remote` lists such a remote but
    `git remote get-url --push <name>` answers "No such remote", so it can
    never enter the set either way and the probe proved nothing.
    """
    decoy_bare = tmp_path / "decoy.git"
    decoy_clone = tmp_path / "decoy"
    _git("init", "--bare", "-q", "-b", "main", str(decoy_bare))
    _git("init", "-q", "-b", "main", str(decoy_clone))
    # A DIFFERENT remote name, which is the whole point: with the same name the
    # mutation is invisible, because the wrong list happens to spell the right
    # thing.
    _git("remote", "add", "upstream", f"file://{decoy_bare}", cwd=decoy_clone)

    engine_clone = tmp_path / "engine"
    env = dict(os.environ, GIT_DIR=str(decoy_clone / ".git"))
    plain = git_push._engine_push_urls(engine_clone)
    redirected = git_push._engine_push_urls(engine_clone, env=env)
    assert any("engine" in u for u in plain)
    assert redirected, "reading the list in the ambient world empties the set"
    assert any("decoy" in u for u in redirected)


def test_the_wall_reads_the_engines_identity_in_the_push_environment(world):
    """End to end for the same gap, one level up.

    The rewrite here moves the ENGINE's remote onto the URL this repository is
    about to push to, so Check A must refuse. `remote_objection` only sees it
    if `supervised_push` handed it the push env AND it handed that env on to
    `_engine_push_urls`.
    """
    env = dict(os.environ)
    env.update(_rewrite(world["intended"], world["engine_bare"], "insteadOf"))
    verdict = git_push.supervised_push(world["work"], env=env, stall_window=30)
    assert verdict["state"] == "failed", verdict.get("reason")
    assert "ENGINE remote" in verdict["reason"]
    assert not _head(world["intended"])


def test_enclosing_repo_root_honours_the_env_it_is_given(world, tmp_path):
    """`GIT_DIR` decides which repository the question is about."""
    env = dict(os.environ, GIT_DIR=str(world["engine_bare"]))
    assert git_push.enclosing_repo_root(world["work"]) == world["work"].resolve()
    assert git_push.enclosing_repo_root(world["work"], env=env) != \
        world["work"].resolve()


def test_ahead_behind_honours_the_env_it_is_given(world):
    """A real push happens FIRST, so `origin/main` exists and the ambient
    answer is a NUMBER.

    The earlier version of this test skipped the push, so `origin/main` did not
    resolve and the function returned None with or without the env. It was
    green over a case that could not tell the two apart, which is no test at
    all: the mutation that drops `env=` from the subprocess survived it.
    """
    env = dict(os.environ)
    assert git_push.supervised_push(world["work"], env=env,
                                    stall_window=30)["state"] == "ok"
    assert git_push.ahead_behind(world["work"], "origin", "main") == (0, 0)
    assert git_push.ahead_behind(world["work"], "origin", "main", env=env) == (0, 0)
    redirected = dict(os.environ, GIT_DIR=str(world["engine_bare"]))
    assert git_push.ahead_behind(world["work"], "origin", "main",
                                 env=redirected) is None


# ============================================================
# 5 - every refusal carries the same keys
# ============================================================
def test_the_remote_refusal_carries_the_flagged_key_its_siblings_carry(world):
    """A caller reading `verdict["flagged"]` uniformly got a KeyError on the
    single highest-stakes refusal in the function."""
    _git("remote", "set-url", "--push", "origin",
         f"file://{world['engine_bare']}", cwd=world["work"])
    verdict = git_push.supervised_push(world["work"], env=dict(os.environ),
                                       stall_window=30)
    assert verdict["state"] == "failed"
    assert verdict["flagged"] == []


def test_every_early_refusal_returns_the_same_key_set(monkeypatch, tmp_path):
    """Driven, not read: each refusal is provoked and its keys compared."""
    shapes = []

    # (a) not a repository root
    monkeypatch.setattr(git_push, "enclosing_repo_root",
                        lambda p, **k: Path("/somewhere/else"))
    monkeypatch.setattr(git_push, "_roots_unreadable", lambda p: None)
    monkeypatch.setattr(git_push, "_is_split_engine", lambda p: False)
    monkeypatch.setattr(git_push, "remote_objection", lambda *a, **k: None)
    shapes.append(git_push.supervised_push(tmp_path))

    # (b) roots unreadable on the engine clone
    monkeypatch.setattr(git_push, "enclosing_repo_root", lambda p, **k: None)
    monkeypatch.setattr(git_push, "_roots_unreadable", lambda p: "boom")
    shapes.append(git_push.supervised_push(tmp_path))

    # (c) the engine clone carries data-class artifacts
    monkeypatch.setattr(git_push, "_roots_unreadable", lambda p: None)
    monkeypatch.setattr(git_push, "_is_split_engine", lambda p: True)
    monkeypatch.setattr(git_push, "scan_engine_repo", lambda p: ["outputs/x.md"])
    shapes.append(git_push.supervised_push(tmp_path))

    # (d) the remote is the wrong one
    monkeypatch.setattr(git_push, "_is_split_engine", lambda p: False)
    monkeypatch.setattr(git_push, "remote_objection", lambda *a, **k: "no")
    shapes.append(git_push.supervised_push(tmp_path))

    assert len(shapes) == 4
    assert all(v["state"] == "failed" for v in shapes)
    keys = {frozenset(v) for v in shapes}
    assert len(keys) == 1, f"refusal shapes disagree: {[sorted(v) for v in shapes]}"
    assert "flagged" in next(iter(keys))


# ============================================================
# 6 - the same invariant, one layer up
# ============================================================
def _load_push_all():
    """push-all.py execs a venv guard at module scope; tests/conftest.py sets
    the flag that disarms it, and it is collected before this module."""
    spec = importlib.util.spec_from_file_location(
        "push_all_shard59", ROOT / "scripts" / "push-all.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_push_alls_precondition_asks_in_the_push_environment(tmp_path, monkeypatch):
    """`push-all` runs the wall a second time, as a precondition, before the
    chokepoint runs it again. A fix that lands in one of two copies leaves the
    earlier one reading a different world, and that copy is the one whose
    refusal stops the whole run."""
    push_all = _load_push_all()
    repo = tmp_path / "data"
    _git("init", "-q", "-b", "main", str(repo))
    _git("config", "user.email", "probe@example.invalid", cwd=repo)
    _git("config", "user.name", "Probe", cwd=repo)
    (repo / "note.md").write_text("x\n", encoding="utf-8")
    _git("add", "-A", cwd=repo)
    _git("commit", "-qm", "one", cwd=repo)

    seen: dict = {}

    def fake_objection(target, **kw):
        seen["env"] = kw.get("env")
        return "refusing, for the test"

    monkeypatch.setattr(push_all, "remote_objection", fake_objection)
    push_env = {"GH_TOKEN": "t", "SHARD59_ONLY_HERE": "1"}
    with pytest.raises(SystemExit):
        push_all.push_repo("DATA", repo, "msg", False, True, push_env)
    assert seen["env"] == push_env
