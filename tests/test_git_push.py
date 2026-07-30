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

import pytest

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


# ============================================================
# Remote identity: normalization
#
# A push has two ends. Everything below is about the far end: not "does this
# tree carry the wrong content" but "does this remote accept the wrong content".
# ============================================================

# Recognisable in an assertion and deliberately not token-shaped.
USERINFO_SENTINEL = "not-a-real-token-userinfo"

# Assembled rather than written out. A tracked file carrying a scheme, a user,
# a value and a host as one contiguous string is refused by the workspace
# prevent-secrets hook, and both the design spec and the plan for this slice hit
# that refusal while being written. The hook is right; the example is built at
# runtime instead.
_USERINFO = "x-access-token:" + USERINFO_SENTINEL


def _with_userinfo(url: str) -> str:
    """Insert the sentinel userinfo component into *url*."""
    scheme, _, rest = url.partition("://")
    return scheme + "://" + _USERINFO + "@" + rest


@pytest.mark.parametrize("url", [
    "https://github.com/Owner/Repo.git",
    "https://github.com/Owner/Repo",
    "https://github.com/Owner/Repo/",
    "https://github.com/owner/repo.git",
    "git@github.com:Owner/Repo.git",
    "git@github.com:owner/repo",
    "ssh://git@github.com/Owner/Repo.git",
    "https://github.com:443/Owner/Repo.git",
    _with_userinfo("https://github.com/Owner/Repo.git"),
])
def test_every_url_form_of_one_repository_normalizes_equal(url):
    assert git_push._normalize_remote_url(url) == "github.com/owner/repo"


def test_normalization_strips_userinfo_so_no_reason_can_leak_it():
    url = _with_userinfo("https://github.com/owner/repo.git")
    assert USERINFO_SENTINEL not in git_push._normalize_remote_url(url)


def test_different_repositories_do_not_normalize_equal():
    n = git_push._normalize_remote_url
    assert n("https://github.com/owner/repo") != n("https://github.com/owner/other")
    assert n("https://github.com/owner/repo") != n("https://gitlab.com/owner/repo")


def test_push_url_reads_the_configured_remote(tmp_path):
    remote, work = _make_repo(tmp_path)
    assert git_push._push_url(work, "origin") == str(remote)


def test_push_url_honours_pushurl_because_that_is_where_a_push_goes(tmp_path):
    _remote, work = _make_repo(tmp_path)
    _git(["config", "remote.origin.pushurl",
          "https://github.com/owner/elsewhere.git"], work)
    assert git_push._normalize_remote_url(git_push._push_url(work, "origin")) == \
        "github.com/owner/elsewhere"


def test_push_url_is_none_when_the_remote_does_not_exist(tmp_path):
    _remote, work = _make_repo(tmp_path)
    assert git_push._push_url(work, "upstream") is None


# ============================================================
# Remote identity: Check A, offline and unconditional
# ============================================================

def _pose_as_split(monkeypatch, engine: Path, data: Path):
    """Make the workspace look like the split topology: engine here, data there."""
    monkeypatch.setattr(git_push, "get_workspace_root", lambda: engine)
    monkeypatch.setattr(git_push, "get_data_root", lambda: data)


def test_a_data_repo_aimed_at_the_engine_remote_is_refused(monkeypatch, tmp_path):
    """The worst outcome this workspace can produce, refused offline.

    Nothing else stands in the way: content_scan catches secrets only, the
    engine clean scan does not run on DATA by design, and the existing wall
    identifies the local directory rather than where it is aimed.
    """
    engine_remote, engine = _make_repo(tmp_path / "e")
    _data_remote, data = _make_repo(tmp_path / "d")
    _pose_as_split(monkeypatch, engine, data)
    _git(["remote", "set-url", "origin", str(engine_remote)], data)

    reason = git_push.remote_objection(data)
    assert reason is not None
    assert "ENGINE remote" in reason


def test_the_refusal_names_the_repository_and_the_remote(monkeypatch, tmp_path):
    engine_remote, engine = _make_repo(tmp_path / "e")
    _data_remote, data = _make_repo(tmp_path / "d")
    _pose_as_split(monkeypatch, engine, data)
    _git(["remote", "set-url", "origin", str(engine_remote)], data)

    reason = git_push.remote_objection(data)
    assert data.name in reason
    assert git_push._normalize_remote_url(str(engine_remote)) in reason


def test_check_a_holds_across_url_form(monkeypatch, tmp_path):
    """Same repository, different spelling. Identity is not string equality."""
    _engine_remote, engine = _make_repo(tmp_path / "e")
    _data_remote, data = _make_repo(tmp_path / "d")
    _pose_as_split(monkeypatch, engine, data)
    _git(["remote", "set-url", "origin",
          "https://github.com/Owner/Repo.git"], engine)
    _git(["remote", "set-url", "origin",
          "git@github.com:owner/repo"], data)

    assert git_push.remote_objection(data) is not None


def test_the_engine_pushing_to_its_own_remote_is_not_refused(monkeypatch, tmp_path):
    """The engine is EXPECTED to point at the public engine repository."""
    _engine_remote, engine = _make_repo(tmp_path / "e")
    _pose_as_split(monkeypatch, engine, tmp_path / "d")

    assert git_push.remote_objection(engine) is None


def test_a_pre_cutover_single_repo_is_not_refused(monkeypatch, tmp_path):
    """With one repository there is one remote and nothing to compare it to.
    Comparing it to itself would refuse every backup on such a workspace."""
    _remote, work = _make_repo(tmp_path)
    _pose_as_split(monkeypatch, work, work)

    assert git_push.remote_objection(work) is None


def test_a_repo_with_no_such_remote_raises_no_objection(monkeypatch, tmp_path):
    """git push will fail on its own; that is not this wall's refusal to make.
    This also keeps every existing push_repo test green, since those build
    repositories with no remote at all."""
    _engine_remote, engine = _make_repo(tmp_path / "e")
    _data_remote, data = _make_repo(tmp_path / "d")
    _pose_as_split(monkeypatch, engine, data)

    assert git_push.remote_objection(data, remote="upstream") is None


def test_check_a_ignores_what_the_caller_named_its_remote(monkeypatch, tmp_path):
    """The engine's push URLs are the engine's property, not the caller's.

    safe-push takes --remote from the command line and hands it straight to
    supervised_push. If the engine side were looked up under that same name, a
    data overlay whose remote is called anything but 'origin' would slip past
    Check A entirely.
    """
    engine_remote, engine = _make_repo(tmp_path / "e")
    _data_remote, data = _make_repo(tmp_path / "d")
    _pose_as_split(monkeypatch, engine, data)
    _git(["remote", "add", "gh", str(engine_remote)], data)

    assert git_push.remote_objection(data, remote="gh") is not None


def test_check_a_reads_every_engine_remote_not_just_origin(monkeypatch, tmp_path):
    """An engine that pushes under a second remote name is still the engine."""
    _engine_remote, engine = _make_repo(tmp_path / "e")
    _data_remote, data = _make_repo(tmp_path / "d")
    _pose_as_split(monkeypatch, engine, data)
    _git(["remote", "add", "upstream", "https://github.com/owner/pub.git"], engine)
    _git(["remote", "set-url", "origin", "https://github.com/owner/pub.git"], data)

    assert git_push.remote_objection(data) is not None


def test_supervised_push_refuses_a_data_repo_aimed_at_the_engine_remote(
        monkeypatch, tmp_path):
    """The chokepoint copy. push-all is not the only caller, so a check placed
    only there would protect one path and leave the primitive open."""
    engine_remote, engine = _make_repo(tmp_path / "e")
    _data_remote, data = _make_repo(tmp_path / "d")
    _pose_as_split(monkeypatch, engine, data)
    _git(["remote", "set-url", "origin", str(engine_remote)], data)

    v = supervised_push(data, branch="main", stall_window=15)
    assert v["state"] == "failed", v
    assert "ENGINE remote" in v["reason"]
    assert v["exit_code"] is None  # synthetic verdict, no push subprocess ran
    # It refused WITHOUT pushing: the engine's bare remote never received main.
    no_main = subprocess.run(
        ["git", "-C", str(engine_remote), "show-ref", "--verify", "refs/heads/main"],
        capture_output=True,
    )
    assert no_main.returncode != 0
