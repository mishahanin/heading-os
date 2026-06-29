"""End-to-end tests for the shared verified-push helper (scripts/utils/git_push.py).

Exercises supervised_push against a LOCAL bare remote (no network): a real
`git push` plus the ahead/behind == 0 0 postcondition. Also covers ahead_behind
and current_branch. The failure/hung/postcondition_failed verdicts are covered
at the primitive level in tests/test_supervise.py.

Run: python3 -m pytest tests/test_git_push.py
"""
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import scripts.utils.git_push as git_push
from scripts.utils.git_push import ahead_behind, current_branch, supervised_push


def _git(args, cwd):
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True,
                          text=True, check=True)


def _make_repo(tmp_path):
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


def test_supervised_push_succeeds_and_verifies(tmp_path):
    _remote, work = _make_repo(tmp_path)
    v = supervised_push(work, remote="origin", branch="main", stall_window=15)
    assert v["state"] == "ok", v
    assert v["postcondition_ok"] is True
    assert ahead_behind(work, "origin", "main") == (0, 0)


def test_current_branch(tmp_path):
    _remote, work = _make_repo(tmp_path)
    assert current_branch(work) == "main"


def test_ahead_behind_detects_unpushed_commit(tmp_path):
    _remote, work = _make_repo(tmp_path)
    supervised_push(work, branch="main", stall_window=15)
    (work / "g.txt").write_text("x", encoding="utf-8")
    _git(["add", "-A"], work)
    _git(["commit", "-m", "second"], work)
    # one local commit not yet pushed -> ahead by 1
    assert ahead_behind(work, "origin", "main") == (0, 1)
    # a second supervised push reconciles back to 0 0
    v = supervised_push(work, branch="main", stall_window=15)
    assert v["state"] == "ok", v
    assert ahead_behind(work, "origin", "main") == (0, 0)


def _pose_as_engine(monkeypatch, work, tmp_path):
    """Make ``work`` look like the split-topology engine clone to git_push."""
    monkeypatch.setattr(git_push, "get_workspace_root", lambda: work)
    monkeypatch.setattr(git_push, "get_data_root", lambda: tmp_path / "data")


def test_supervised_push_refuses_dirty_engine(monkeypatch, tmp_path):
    # The universal engine/data wall: a private-routed file in the engine clone makes
    # supervised_push refuse BEFORE pushing -- on ANY engine push path (push-all,
    # safe-push, future callers), with no skip flag. Regression for the 2026-06-22 leak.
    remote, work = _make_repo(tmp_path)
    _pose_as_engine(monkeypatch, work, tmp_path)
    leak = work / "crm" / "contacts" / "x.md"  # routes private
    leak.parent.mkdir(parents=True)
    leak.write_text("name: X\n", encoding="utf-8")
    v = supervised_push(work, branch="main", stall_window=15)
    assert v["state"] == "failed", v
    assert "crm/contacts/x.md" in v["flagged"]
    assert "data-class artifact" in v["reason"]
    assert v["exit_code"] is None  # synthetic verdict -- no push subprocess ran
    # It refused WITHOUT pushing: the bare remote never received a main branch.
    no_main = subprocess.run(
        ["git", "-C", str(remote), "show-ref", "--verify", "refs/heads/main"],
        capture_output=True,
    )
    assert no_main.returncode != 0


def test_supervised_push_allows_clean_engine(monkeypatch, tmp_path):
    # A clean engine clone (no private/corporate file) pushes normally -- the wall
    # must not break legitimate engine pushes.
    _remote, work = _make_repo(tmp_path)
    _pose_as_engine(monkeypatch, work, tmp_path)
    v = supervised_push(work, branch="main", stall_window=15)
    assert v["state"] == "ok", v
    assert ahead_behind(work, "origin", "main") == (0, 0)
