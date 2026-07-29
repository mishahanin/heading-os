"""Contract for: a refusal about one repository must not cancel the other.

Frozen at Fix 1, before `RepoNotPushable` exists. Retired at ship time by
promoting whatever the ordinary suite does not already hold into
`tests/test_push_all_gate.py` and `tests/test_push_all_orchestration.py`.

`push-all.py` is loaded BY PATH rather than imported, and that is not a style
choice: it calls `ensure_venv()` at module scope, so a plain import `os.execv`s
the whole pytest process under any interpreter that is not `.venv/bin/python`.
The guard is set once in `tests/conftest.py`; see `tests/test_push_all_gate.py`
for the same load and the same reason.

That load has a measured consequence for the probe, recorded here because the
gate artifact records it too: the AST of this file names no first-party module,
so the null-stub vacuity rule has no claim set on this contract and its verdict
is UNKNOWN rather than clean. Nothing here is proved non-vacuous by the
instrument, and the human read at Fix 1 is the whole of the control.
"""
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent.parent

_spec = importlib.util.spec_from_file_location(
    "push_all_contract", ROOT / "scripts" / "push-all.py")
push_all = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(push_all)


def _repo_on_branch(tmp_path, branch):
    """A git repo with one commit, checked out on *branch*."""
    repo = tmp_path / "repo"
    repo.mkdir()
    for args in (["git", "init", "-q"],
                 ["git", "config", "user.email", "builder@example.invalid"],
                 ["git", "config", "user.name", "Builder"]):
        push_all.run(args, repo)
    (repo / "a.txt").write_text("a\n", encoding="utf-8")
    push_all.run(["git", "add", "."], repo)
    push_all.run(["git", "commit", "-q", "-m", "one"], repo)
    if branch != "main":
        push_all.run(["git", "checkout", "-q", "-b", branch], repo)
    else:
        push_all.run(["git", "branch", "-M", "main"], repo)
    return repo


def _arm(repo):
    """Write a pre-push hook that satisfies _pre_push_gate_armed."""
    hooks = repo / ".git" / "hooks"
    hooks.mkdir(parents=True, exist_ok=True)
    (hooks / "pre-push").write_text(
        "#!/bin/sh\nexec .venv/bin/python scripts/run-tests.py\n", encoding="utf-8")


# ============================================================
# SC-2, SC-3: the per-repository refusal is raised, not exited
# ============================================================

def test_sc2_a_branch_that_is_not_main_raises_rather_than_exiting(tmp_path):
    """sys.exit here is what silently cancelled the DATA backup. The type says
    this repository cannot be pushed, never that the run must stop."""
    repo = _repo_on_branch(tmp_path, "feat/x")

    with pytest.raises(push_all.RepoNotPushable) as caught:
        push_all.push_repo("R", repo, "m", False, False, {})
    assert "feat/x" in str(caught.value)


def test_sc7_the_branch_check_is_reached_under_dry_run(tmp_path):
    """The dry-run return sat ABOVE the branch check, so a dry run reported no
    skip at all. A dry run that hides the one thing this change surfaces lies."""
    repo = _repo_on_branch(tmp_path, "feat/x")

    with pytest.raises(push_all.RepoNotPushable):
        push_all.push_repo("R", repo, "m", False, True, {})


def test_sc3_an_unarmed_suite_gate_raises_and_names_its_installer(tmp_path):
    repo = _repo_on_branch(tmp_path, "main")

    with pytest.raises(push_all.RepoNotPushable) as caught:
        push_all.push_repo("ENGINE", repo, "m", False, True, {},
                           is_engine=True, test_gate=True)
    assert "install-git-hooks" in str(caught.value)


def test_sc4_the_suite_gate_is_keyed_on_test_gate_not_on_is_engine(tmp_path):
    """The two flags are separate on purpose and this is the test that says why.

    `is_engine` turns on the engine LEAK scans; `test_gate` turns on the suite
    precondition. `main()` checked the suite gate ABOVE its single-repo branch,
    so it covered the pre-cutover mode too, and that mode pushes this same
    engine clone with `is_engine` deliberately OFF because its data files are
    tracked legitimately there. One flag serving both would have narrowed a
    security check from two modes to one while looking like a pure move.
    """
    repo = _repo_on_branch(tmp_path, "main")

    assert push_all.push_repo("repo", repo, "m", False, True, {},
                              is_engine=True) is None
    with pytest.raises(push_all.RepoNotPushable):
        push_all.push_repo("repo", repo, "m", False, True, {}, test_gate=True)


def test_the_suite_gate_is_not_a_precondition_of_the_data_overlay(tmp_path):
    """A do-not-break guard rather than new behaviour. The DATA overlay has no
    pre-push gate and never needed one; requiring it there would refuse every
    data backup on every machine."""
    repo = _repo_on_branch(tmp_path, "main")

    assert push_all.push_repo("DATA", repo, "m", False, True, {}) is None


# ============================================================
# SC-1, SC-5, SC-6: what main() attempts, in what order
# ============================================================

def _wire(tmp_path, monkeypatch, behaviour):
    """main() with both roots faked and push_repo recorded. Returns the call log.

    Each entry is (name, kwargs), so a test can assert on order and on which
    call sites carry test_gate without caring about paths.
    """
    engine = tmp_path / "engine"
    data = tmp_path / "data"
    engine.mkdir()
    data.mkdir()
    calls = []

    def fake_push_repo(name, repo, message, do_commit, dry_run, push_env, **kw):
        calls.append((name, kw))
        if name in behaviour:
            raise push_all.RepoNotPushable(behaviour[name])

    monkeypatch.setattr(push_all, "push_repo", fake_push_repo)
    monkeypatch.setattr(push_all, "get_workspace_root", lambda: engine)
    monkeypatch.setattr(push_all, "get_data_root", lambda: data)
    monkeypatch.setattr(push_all, "is_exec_workspace", lambda: False)
    monkeypatch.setattr(push_all, "gh_token", lambda: "t")
    monkeypatch.setattr("sys.argv", ["push-all.py"])
    return calls


def _code():
    """main()'s exit code, treating a clean return as 0."""
    try:
        push_all.main()
    except SystemExit as exc:
        return exc.code if exc.code is not None else 0
    return 0


def test_sc1_both_repositories_pushed_exits_zero(tmp_path, monkeypatch):
    calls = _wire(tmp_path, monkeypatch, {})

    assert _code() == 0
    assert {name for name, _kw in calls} == {"ENGINE", "DATA"}


def test_sc5_data_is_attempted_before_the_engine(tmp_path, monkeypatch):
    """Measured, not aesthetic: the engine's pre-push hook runs the full suite
    inside the push and took 320 seconds on this machine. The data overlay is
    the only half that cannot be reconstructed, so it does not queue behind a
    several-minute gate that may fail, stall, or be interrupted."""
    calls = _wire(tmp_path, monkeypatch, {})
    _code()

    assert calls[0][0] == "DATA"


def test_sc2_a_skipped_engine_does_not_stop_the_data_push(tmp_path, monkeypatch):
    """THE defect this whole slice exists to close."""
    calls = _wire(tmp_path, monkeypatch,
                  {"ENGINE": "branch is 'feat/x', expected 'main'"})

    assert _code() == 3
    assert "DATA" in {name for name, _kw in calls}


def test_sc2_the_summary_names_the_skipped_repo_and_the_reason(
        tmp_path, monkeypatch, capsys):
    _wire(tmp_path, monkeypatch, {"ENGINE": "branch is 'feat/x', expected 'main'"})
    _code()

    out = capsys.readouterr().out
    assert "ENGINE" in out
    assert "feat/x" in out


def test_a_skipped_data_overlay_also_exits_three(tmp_path, monkeypatch):
    """Symmetry is not decoration: the rule is about repositories, not about the
    engine. A rule that only ever fires one way is a special case in disguise."""
    _wire(tmp_path, monkeypatch, {"DATA": "branch is 'wip', expected 'main'"})

    assert _code() == 3


def test_sc4_the_suite_gate_is_required_at_both_engine_pushing_call_sites(
        tmp_path, monkeypatch):
    """The two-repo ENGINE call and the pre-cutover single-repo call both reach
    the engine remote, so both must carry test_gate. Neither DATA call may."""
    calls = _wire(tmp_path, monkeypatch, {})
    _code()
    by_name = dict(calls)

    assert by_name["ENGINE"].get("test_gate") is True
    assert "test_gate" not in by_name["DATA"]


def test_sc6_a_stop_the_world_exit_is_never_absorbed(tmp_path, monkeypatch):
    """RepoNotPushable is the ONLY thing main() catches. A SystemExit from a
    security refusal has to travel, or this change would have quietly converted
    every unbypassable wall into a skip."""
    _wire(tmp_path, monkeypatch, {})

    def exploding(name, *args, **kwargs):
        raise SystemExit(2)

    monkeypatch.setattr(push_all, "push_repo", exploding)
    assert _code() == 2


# The Global Constraint that eight sys.exit sites must not change is NOT pinned
# here, deliberately. The version first drafted counted `sys.exit` occurrences in
# the source and asserted ten, which holds only by arithmetic coincidence (eight
# kept plus two new exit-3 paths) and would break for reasons that have nothing
# to do with security. A test coupled to a count rather than to behaviour is the
# shape development-standards.md names as wrong. The invariant that matters is
# behavioural and is pinned above: main() absorbs RepoNotPushable and nothing
# else, so no security refusal can become a skip. The eight sites keep their own
# coverage in tests/test_push_all_gate.py, and the constraint is checked by the
# reviewer reading the diff.
