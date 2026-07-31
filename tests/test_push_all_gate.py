"""push-all.py enforces the single authoritative test gate AND the unbypassable
engine/data leak wall.

The regression suite is run by the engine's versioned pre-push hook (one gate,
on every push to engine). push-all no longer runs it a second time itself; it
refuses to push when that hook is not armed, so the gate can never be silently
skipped on an un-provisioned clone. These tests cover that enforcement predicate
plus engine_clean_scan() -- the pure-code routing wall that no `--no-verify` can
get past.
"""
import importlib.util
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

# push-all.py calls ensure_venv() at MODULE scope, so loading it here would
# os.execv the whole pytest process under any interpreter that is not
# .venv/bin/python. The guard against that is set once in tests/conftest.py,
# which is collected before this module; see the comment there, and
# tests/test_venv_relaunch_guard.py for the test that measures it.
_spec = importlib.util.spec_from_file_location("push_all", ROOT / "scripts" / "push-all.py")
push_all = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(push_all)


def _git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True)


def _init_repo(tmp_path) -> Path:
    repo = tmp_path / "engine"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    return repo


def _write(repo, rel, body="x"):
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")
    return p


def test_engine_clean_scan_passes_on_clean_tree(tmp_path):
    repo = _init_repo(tmp_path)
    _write(repo, "scripts/foo.py", "print(1)\n")
    _git(repo, "add", "-A")
    # No exit -> returns None cleanly.
    assert push_all.engine_clean_scan(repo) is None


def test_engine_clean_scan_refuses_on_data_artifact(tmp_path, capsys):
    repo = _init_repo(tmp_path)
    _write(repo, "crm/contacts/john.md", "name: John\n")  # routes private
    _git(repo, "add", "-A")
    with pytest.raises(SystemExit) as exc:
        push_all.engine_clean_scan(repo)
    assert exc.value.code == 2
    assert "crm/contacts/john.md" in capsys.readouterr().out


def test_engine_clean_scan_refuses_on_untracked_data(tmp_path, capsys):
    # A private file not yet staged is still caught -- `git add -A` would sweep it in.
    repo = _init_repo(tmp_path)
    _write(repo, "outputs/operations/leak.md", "plan\n")
    with pytest.raises(SystemExit) as exc:
        push_all.engine_clean_scan(repo)
    assert exc.value.code == 2
    assert "outputs/operations/leak.md" in capsys.readouterr().out


def _make_hook(tmp_path, body: str):
    hooks = tmp_path / ".git" / "hooks"
    hooks.mkdir(parents=True, exist_ok=True)
    (hooks / "pre-push").write_text(body, encoding="utf-8")
    return tmp_path


def test_gate_armed_true_when_hook_runs_tests(tmp_path):
    repo = _make_hook(tmp_path, "#!/usr/bin/env bash\nexec python scripts/run-tests.py\n")
    assert push_all._pre_push_gate_armed(repo) is True


def test_gate_not_armed_when_hook_missing(tmp_path):
    (tmp_path / ".git" / "hooks").mkdir(parents=True)
    assert push_all._pre_push_gate_armed(tmp_path) is False


def test_gate_not_armed_when_hook_does_not_run_tests(tmp_path):
    repo = _make_hook(tmp_path, "#!/usr/bin/env bash\necho noop\n")
    assert push_all._pre_push_gate_armed(repo) is False


# ============================================================
# RepoNotPushable: a refusal about one repo, not about the run
#
# Promoted from tests/contract/2026-07-30-backup-per-repo-refusal/, the frozen
# contract of the slice that introduced the type. These five were written before
# RepoNotPushable existed and are unchanged apart from this banner.
# ============================================================

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


def test_a_branch_that_is_not_main_raises_rather_than_exiting(tmp_path):
    """sys.exit here is what silently cancelled the DATA backup. The type says
    this repository cannot be pushed, never that the run must stop."""
    repo = _repo_on_branch(tmp_path, "feat/x")

    with pytest.raises(push_all.RepoNotPushable) as caught:
        push_all.push_repo("R", repo, "m", False, False, {})
    assert "feat/x" in str(caught.value)


def test_the_branch_check_is_reached_under_dry_run(tmp_path):
    """The dry-run return sat ABOVE the branch check, so a dry run reported no
    skip at all. A dry run that hides the one thing this change surfaces lies."""
    repo = _repo_on_branch(tmp_path, "feat/x")

    with pytest.raises(push_all.RepoNotPushable):
        push_all.push_repo("R", repo, "m", False, True, {})


def test_an_unarmed_suite_gate_raises_and_names_its_installer(tmp_path):
    repo = _repo_on_branch(tmp_path, "main")

    with pytest.raises(push_all.RepoNotPushable) as caught:
        push_all.push_repo("ENGINE", repo, "m", False, True, {},
                           is_engine=True, test_gate=True)
    assert "install-git-hooks" in str(caught.value)


def test_the_suite_gate_is_keyed_on_test_gate_not_on_is_engine(tmp_path):
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
# The remote-identity wall as a push-all precondition
#
# Stop-the-world, not per-repository. A refusal about a branch says this repo
# cannot be pushed and nothing about the others. A misconfigured remote says
# the configuration is wrong in a way the operator must see before anything
# else leaves the machine, so every other repository in the run is suspect for
# the same reason.
# ============================================================

def test_a_remote_objection_exits_2_rather_than_raising(tmp_path, monkeypatch, capsys):
    repo = _repo_on_branch(tmp_path, "main")
    monkeypatch.setattr(push_all, "remote_objection",
                        lambda *a, **k: "R pushes to the ENGINE remote (x/y).")

    with pytest.raises(SystemExit) as exc:
        push_all.push_repo("R", repo, "m", False, False, {})
    assert exc.value.code == 2
    assert "ENGINE remote" in capsys.readouterr().out


def test_the_remote_objection_is_not_a_reponotpushable(tmp_path, monkeypatch):
    """_attempt absorbs RepoNotPushable and lets everything else fly. This
    refusal must be in the second group, so the type is asserted directly."""
    repo = _repo_on_branch(tmp_path, "main")
    monkeypatch.setattr(push_all, "remote_objection", lambda *a, **k: "nope")

    with pytest.raises(SystemExit):
        push_all.push_repo("R", repo, "m", False, False, {})


def test_a_remote_objection_beats_a_branch_skip(tmp_path, monkeypatch):
    """Ordering, and it is a security decision rather than a style one. A repo
    on a feature branch raises RepoNotPushable, which _attempt absorbs. If the
    branch check ran first, a misconfigured remote on that repo would never be
    reported at all."""
    repo = _repo_on_branch(tmp_path, "feat/x")
    monkeypatch.setattr(push_all, "remote_objection", lambda *a, **k: "nope")

    with pytest.raises(SystemExit) as exc:
        push_all.push_repo("R", repo, "m", False, False, {})
    assert exc.value.code == 2


def test_the_refusal_is_reported_under_dry_run_and_writes_nothing(
        tmp_path, monkeypatch):
    """A preview that hides a refusal lies. Evaluating a precondition writes
    nothing, so a dry run can afford to be honest here too."""
    repo = _repo_on_branch(tmp_path, "main")
    before = push_all.run(["git", "rev-parse", "HEAD"], repo).stdout.strip()
    (repo / "b.txt").write_text("b\n", encoding="utf-8")
    monkeypatch.setattr(push_all, "remote_objection", lambda *a, **k: "nope")

    with pytest.raises(SystemExit) as exc:
        push_all.push_repo("R", repo, "m", True, True, {})
    assert exc.value.code == 2
    after = push_all.run(["git", "rev-parse", "HEAD"], repo).stdout.strip()
    assert after == before  # no commit was made
    assert (repo / "b.txt").exists()  # and the working tree is untouched


def test_a_repo_with_no_remote_at_all_raises_no_objection(tmp_path):
    """Every other test in this file builds a remoteless repo and expects the
    old behaviour, so the real un-stubbed function must stay silent on one.
    This asserts that directly rather than leaving it implicit."""
    repo = _repo_on_branch(tmp_path, "main")

    assert push_all.remote_objection(repo) is None
    assert push_all.push_repo("DATA", repo, "m", False, True, {}) is None


# ============================================================
# The composition: a REAL objection reaching a real exit code
#
# Promoted from tests/contract/2026-07-30-remote-identity-wall/, the frozen
# contract of the slice that introduced the wall. Unchanged apart from this
# banner. Every other refusal test above stubs remote_objection, so the two
# legs -- an objection is produced, an objection becomes exit 2 -- are each
# proved and their join is not. A wall wired to a consumer that never calls it
# passes all of them and protects nothing. These two measure the join.
# ============================================================

def _split_repo_aimed_at_the_engine(monkeypatch, tmp_path):
    """A DATA overlay whose origin is the ENGINE's bare remote, posed split.

    The pose is patched on git_push because that is where the wall reads the
    two roots from, and push_all imported the function itself rather than the
    module, so the roots must be moved where the function looks for them.
    """
    import scripts.utils.git_push as git_push

    def _make(base, label):
        remote, work = base / "remote.git", base / label
        subprocess.run(["git", "init", "--bare", "-b", "main", str(remote)],
                       check=True, capture_output=True)
        subprocess.run(["git", "init", "-b", "main", str(work)],
                       check=True, capture_output=True)
        _git(work, "config", "user.email", "builder@example.invalid")
        _git(work, "config", "user.name", "Builder")
        (work / "f.txt").write_text("x\n", encoding="utf-8")
        _git(work, "add", "-A")
        _git(work, "commit", "-m", "one")
        _git(work, "remote", "add", "origin", str(remote))
        return remote, work

    engine_remote, engine = _make(tmp_path / "e", "engine")
    _data_remote, data = _make(tmp_path / "d", "data-overlay")
    monkeypatch.setattr(git_push, "get_workspace_root", lambda: engine)
    monkeypatch.setattr(git_push, "get_data_root", lambda: data)
    _git(data, "remote", "set-url", "origin", str(engine_remote))
    return engine_remote, data


def test_push_all_exits_2_on_a_real_objection_end_to_end(monkeypatch, tmp_path):
    """dry_run is True on purpose: the refusal must be reported by a preview as
    well, and evaluating a precondition writes nothing, so the honest preview
    and the safe test are the same run."""
    _engine_remote, data = _split_repo_aimed_at_the_engine(monkeypatch, tmp_path)

    with pytest.raises(SystemExit) as caught:
        push_all.push_repo("DATA", data, "m", False, True, {})
    assert caught.value.code == 2


def test_the_end_to_end_refusal_pushes_nothing(monkeypatch, tmp_path):
    """Refuse, then push, is not a wall. The bare remote must stay empty.

    The failing output of this test at freeze time was the defect itself:
    "pushed & verified [0 0] in sync with origin/main". The overlay reached the
    engine's remote in a sandbox.
    """
    engine_remote, data = _split_repo_aimed_at_the_engine(monkeypatch, tmp_path)

    with pytest.raises(SystemExit):
        push_all.push_repo("DATA", data, "m", False, False, {})
    has_main = subprocess.run(
        ["git", "-C", str(engine_remote), "show-ref", "--verify",
         "refs/heads/main"],
        capture_output=True,
    )
    assert has_main.returncode != 0
