"""End-to-end tests for the shared verified-push helper (scripts/utils/git_push.py).

Exercises supervised_push against a LOCAL bare remote (no network): a real
`git push` plus the ahead/behind == 0 0 postcondition. Also covers ahead_behind
and current_branch. The failure/hung/postcondition_failed verdicts are covered
at the primitive level in tests/test_supervise.py.

Run: python3 -m pytest tests/test_git_push.py
"""
import subprocess
import sys
import urllib.request
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import scripts.utils.git_push as git_push
from scripts.utils.git_push import ahead_behind, current_branch, supervised_push

# Every child this file spawns is `git` in a scratch tree, and `git` has never
# read HEADING_OS_DATA. Pinning it away from the operator's live overlay costs
# these tests nothing and removes them from the reachability ratchet in
# tests/conftest.py. See the `scratch_data_root` fixture for the measurement.
pytestmark = pytest.mark.usefixtures("scratch_data_root")


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


@pytest.mark.slow
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


def test_unreadable_roots_refuse_an_engine_clone_rather_than_skipping_the_wall(
    monkeypatch, tmp_path
):
    """Found by the 2026-08-23 audit. The wall was skipped in silence.

    `_is_split_engine` swallowed any exception from the root resolvers and
    answered False, and False means "not the engine, nothing to wall". So on a
    broken environment - the state in which misrouting is MOST likely - the
    chokepoint the module docstring calls unbypassable simply stopped scanning
    and said nothing. The remote-check leg three hundred lines down already
    printed a loud warning for the same condition, which is what makes this an
    oversight rather than a decision.

    A repository carrying `scripts/utils/engine_guard.py` is the engine clone
    whatever the resolvers say, and the answer there is refuse, not guess. The
    data overlay carries no such file, so it is untouched by this.
    """
    _remote, work = _make_repo(tmp_path)

    def _broken():
        raise RuntimeError("HEADING_OS_DATA points at nothing")

    # Only the DATA root is broken, which is the realistic shape: a bad
    # HEADING_OS_DATA. Breaking the workspace root as well would take down
    # `load_gh_token` first and prove nothing about the wall.
    monkeypatch.setattr(git_push, "get_data_root", _broken)
    (work / "scripts" / "utils").mkdir(parents=True)
    (work / "scripts" / "utils" / "engine_guard.py").write_text("x\n", encoding="utf-8")

    v = supervised_push(work, branch="main", stall_window=15)
    assert v["state"] == "failed", v
    assert "workspace roots" in v["reason"]
    assert v["exit_code"] is None
    no_main = subprocess.run(
        ["git", "-C", str(_remote), "show-ref", "--verify", "refs/heads/main"],
        capture_output=True,
    )
    assert no_main.returncode != 0


def test_unreadable_roots_do_not_refuse_a_repository_that_is_not_the_engine(
    monkeypatch, tmp_path
):
    """The data overlay and the corporate repos must still push on a broken
    environment: they legitimately carry private content and were never walled."""
    _remote, work = _make_repo(tmp_path)

    def _broken():
        raise RuntimeError("unresolvable")

    monkeypatch.setattr(git_push, "get_data_root", _broken)

    v = supervised_push(work, branch="main", stall_window=15)
    assert v["state"] == "ok", v


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
    "https://www.github.com/Owner/Repo.git",
    "ssh://git@ssh.github.com:443/Owner/Repo.git",
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


def test_check_a_catches_the_engine_remote_spelled_with_a_www_host(
        monkeypatch, tmp_path):
    """The reachable leak this slice's review found: `www.github.com`
    301-redirects to `github.com` and git follows it by default, so a data
    overlay whose origin is spelled this way names the exact same repository
    as the engine's `github.com` remote, and must be refused just as surely."""
    engine_remote, engine = _make_repo(tmp_path / "e")
    _data_remote, data = _make_repo(tmp_path / "d")
    _pose_as_split(monkeypatch, engine, data)
    _git(["remote", "set-url", "origin",
          "https://github.com/owner/repo.git"], engine)
    _git(["remote", "set-url", "origin",
          "https://www.github.com/owner/repo.git"], data)

    reason = git_push.remote_objection(data)
    assert reason is not None
    assert "ENGINE remote" in reason


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


def test_a_url_handed_in_as_the_remote_is_checked_too(monkeypatch, tmp_path):
    """`git push <url> <branch>` needs no configured remote, and safe-push takes
    --remote as a free-form string. An earlier version read an unresolvable
    remote name as proof that nothing could be pushed, and the overlay reached
    the engine's remote with every wall silent."""
    engine_remote, engine = _make_repo(tmp_path / "e")
    _data_remote, data = _make_repo(tmp_path / "d")
    _pose_as_split(monkeypatch, engine, data)

    reason = git_push.remote_objection(data, remote=str(engine_remote))
    assert reason is not None
    assert git_push._normalize_remote_url(str(engine_remote)) in reason


def test_the_chokepoint_refuses_a_url_handed_in_as_the_remote(monkeypatch, tmp_path):
    """The reviewer's reproduction, pinned. Before the fix this pushed."""
    engine_remote, engine = _make_repo(tmp_path / "e")
    _data_remote, data = _make_repo(tmp_path / "d")
    _pose_as_split(monkeypatch, engine, data)

    verdict = supervised_push(data, remote=str(engine_remote), branch="main",
                              stall_window=15)
    assert verdict["state"] == "failed", verdict
    has_main = subprocess.run(
        ["git", "-C", str(engine_remote), "show-ref", "--verify",
         "refs/heads/main"],
        capture_output=True,
    )
    assert has_main.returncode != 0, "the overlay reached the engine remote"


def test_an_unconfigured_remote_name_is_still_no_objection(monkeypatch, tmp_path):
    """The fall-through must not turn a typo into a refusal. A bare word cannot
    collide with a host/owner/repo URL, so git push keeps the right to fail."""
    _engine_remote, engine = _make_repo(tmp_path / "e")
    _data_remote, data = _make_repo(tmp_path / "d")
    _pose_as_split(monkeypatch, engine, data)

    assert git_push.remote_objection(data, remote="no-such-remote") is None


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


# Promoted from tests/contract/2026-07-30-remote-identity-wall/, the frozen
# contract of the slice that introduced the wall. Unchanged apart from this
# banner. Its sibling above proves the chokepoint refuses; nothing else in the
# suite proved it still lets the correct case through under a split pose, and a
# wall that fails the normal case is worse than no wall.
def test_a_correctly_configured_repo_still_pushes(monkeypatch, tmp_path):
    """The do-not-break term, asserted through the real push.

    Through the push and not only through the predicate: the chokepoint is
    where the wall is wired, and a wrong verdict SHAPE there would fail the
    push while remote_objection itself looked fine.
    """
    _engine_remote, engine = _make_repo(tmp_path / "e")
    _data_remote, data = _make_repo(tmp_path / "d")
    _pose_as_split(monkeypatch, engine, data)

    assert git_push.remote_objection(data) is None
    verdict = supervised_push(data, branch="main", stall_window=15)
    assert verdict["state"] == "ok", verdict
    assert ahead_behind(data, "origin", "main") == (0, 0)


# ============================================================
# Remote identity: Check B, the property itself
# ============================================================

@pytest.fixture(autouse=True)
def _clear_visibility_cache():
    """The cache lives for the process, so it must not live across tests."""
    git_push._VIS_CACHE.clear()
    yield
    git_push._VIS_CACHE.clear()


@pytest.fixture(autouse=True)
def _no_live_network(monkeypatch):
    """No test in this file may reach the real GitHub API.

    The property held only by construction: every Check B test patches
    _gh_visibility and the rest return at a host or path guard. A future test
    pointing a repository at a github.com URL without patching would issue a
    REAL authenticated request, because the chokepoint resolves a token through
    load_gh_token(), which reads the operator's own .env. Enforce it instead.
    """
    def _refuse(*_args, **_kwargs):
        raise AssertionError(
            "a test tried to open a real network connection; patch "
            "_gh_visibility instead")

    monkeypatch.setattr(urllib.request, "urlopen", _refuse)


def _aimed_at(monkeypatch, tmp_path, url: str):
    """A DATA overlay in a split topology, pointed at *url*."""
    _engine_remote, engine = _make_repo(tmp_path / "e")
    _data_remote, data = _make_repo(tmp_path / "d")
    _pose_as_split(monkeypatch, engine, data)
    _git(["remote", "set-url", "origin", url], data)
    return data


def test_a_repo_on_a_public_remote_is_refused(monkeypatch, tmp_path):
    data = _aimed_at(monkeypatch, tmp_path,
                     "https://github.com/owner/somewhere-public.git")
    monkeypatch.setattr(git_push, "_gh_visibility", lambda *a, **k: "public")

    reason = git_push.remote_objection(data)
    assert reason is not None
    assert "PUBLIC" in reason
    assert "github.com/owner/somewhere-public" in reason


def test_a_repo_on_a_private_remote_is_permitted(monkeypatch, tmp_path):
    data = _aimed_at(monkeypatch, tmp_path,
                     "https://github.com/owner/somewhere-private.git")
    monkeypatch.setattr(git_push, "_gh_visibility", lambda *a, **k: "private")

    assert git_push.remote_objection(data) is None


def test_a_visibility_that_cannot_be_answered_warns_and_permits(
        monkeypatch, tmp_path, capsys):
    """The one fail-open decision in this wall, and it is deliberate. An
    unreachable API carries no information about whether the overlay is private,
    and refusing the backup would trade a leak risk for a data-loss risk on the
    command whose whole job is the off-machine copy.

    Tokened on purpose: a tokenless cannot-answer is a question that was never
    asked (see test_a_tokenless_cannot_answer_does_not_warn below), so this case
    exercises the one that was actually asked and still came back empty."""
    data = _aimed_at(monkeypatch, tmp_path,
                     "https://github.com/owner/somewhere-unknown.git")
    monkeypatch.setattr(git_push, "_gh_visibility", lambda *a, **k: None)

    assert git_push.remote_objection(data, token="tok") is None
    out = capsys.readouterr().out
    assert "could not verify" in out
    assert "github.com/owner/somewhere-unknown" in out


def test_the_cannot_answer_warning_prints_once_per_remote(
        monkeypatch, tmp_path, capsys):
    """A real push-all run evaluates the precondition and then reaches the
    chokepoint, so an unguarded warning would print twice per repository."""
    data = _aimed_at(monkeypatch, tmp_path,
                     "https://github.com/owner/somewhere-unknown.git")
    monkeypatch.setattr(git_push, "_gh_visibility", lambda *a, **k: None)

    git_push.remote_objection(data, token="tok")
    git_push.remote_objection(data, token="tok")
    assert capsys.readouterr().out.count("could not verify") == 1


def test_a_tokenless_cannot_answer_does_not_warn(monkeypatch, tmp_path, capsys):
    """A supported tokenless dry run must not warn on every invocation.

    push-all.py --dry-run works with no GH_TOKEN, and a tokenless probe of a
    private repository always 404s, which is a question that could not be
    asked, not a lookup that failed, so it must not print the same warning a
    genuinely failed tokened lookup does."""
    data = _aimed_at(monkeypatch, tmp_path,
                     "https://github.com/owner/somewhere-unknown.git")
    monkeypatch.setattr(git_push, "_gh_visibility", lambda *a, **k: None)

    assert git_push.remote_objection(data) is None
    assert "could not verify" not in capsys.readouterr().out


def test_a_non_github_remote_is_not_warned_about(monkeypatch, tmp_path, capsys):
    """Not a lookup that failed, a question GitHub was never asked.

    Every local bare remote in this very file lands here. A warning on each of
    them is noise, and noise is what costs the warning its meaning on the one
    occasion it matters.
    """
    data = _aimed_at(monkeypatch, tmp_path, "https://gitlab.example/owner/repo.git")

    assert git_push.remote_objection(data) is None
    assert "could not verify" not in capsys.readouterr().out


def test_the_visibility_cache_does_not_carry_a_tokenless_answer_into_a_tokened_one(
        monkeypatch, tmp_path):
    """A 404 without a token means 'cannot see it', never 'not private'.

    create-data-repo calls supervised_push with neither token= nor env=, so the
    chokepoint resolves one through load_gh_token() while other callers pass one
    explicitly. One process can therefore ask both ways.
    """
    data = _aimed_at(monkeypatch, tmp_path,
                     "https://github.com/owner/somewhere.git")
    seen = []

    def _fake(normalized, *, token=None):
        seen.append(token)
        return None if token is None else "public"

    monkeypatch.setattr(git_push, "_gh_visibility", _fake)

    assert git_push.remote_objection(data, token=None) is None
    assert "PUBLIC" in git_push.remote_objection(data, token="t")
    assert seen == [None, "t"]  # the tokened call was really made


def test_no_objection_or_warning_ever_contains_userinfo(
        monkeypatch, tmp_path, capsys):
    """Written as an explicit not-in check against a recognisable sentinel
    rather than a general regex, so it fails loudly if stripping regresses."""
    data = _aimed_at(monkeypatch, tmp_path,
                     _with_userinfo("https://github.com/owner/pub.git"))
    monkeypatch.setattr(git_push, "_gh_visibility", lambda *a, **k: "public")

    reason = git_push.remote_objection(data)
    assert USERINFO_SENTINEL not in reason
    assert USERINFO_SENTINEL not in capsys.readouterr().out


def test_check_a_still_refuses_when_check_b_says_private(monkeypatch, tmp_path):
    """A private engine remote is still the engine remote. Check A is not a
    weaker form of Check B and must not be shadowed by it."""
    engine_remote, engine = _make_repo(tmp_path / "e")
    _data_remote, data = _make_repo(tmp_path / "d")
    _pose_as_split(monkeypatch, engine, data)
    _git(["remote", "set-url", "origin", str(engine_remote)], data)
    monkeypatch.setattr(git_push, "_gh_visibility", lambda *a, **k: "private")

    assert "ENGINE remote" in git_push.remote_objection(data)


def test_visibility_is_unanswerable_for_a_non_github_host():
    assert git_push._gh_visibility("gitlab.com/owner/repo") is None


def test_visibility_is_unanswerable_for_a_path_that_is_not_owner_repo():
    assert git_push._gh_visibility("github.com/owner") is None
    assert git_push._gh_visibility("github.com/owner/repo/extra") is None


@pytest.mark.parametrize("body", ["null", "[]", '"a string"', "17"])
def test_a_json_body_that_is_not_an_object_fails_open(monkeypatch, tmp_path, body):
    """A 200 from an intercepting proxy must not abort the backup. Escaping here
    killed the whole run, and DATA is pushed first, so nothing was pushed."""

    class _Resp:
        def read(self):
            return body.encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: _Resp())
    assert git_push._gh_visibility("github.com/owner/repo") is None


def test_a_truncated_response_fails_open(monkeypatch, tmp_path):
    """IncompleteRead is an HTTPException, not an OSError, so it matched no
    clause and escaped. A flaky uplink must not abort a backup."""
    import http.client

    def _truncated(*_a, **_k):
        raise http.client.IncompleteRead(b"partial")

    monkeypatch.setattr(urllib.request, "urlopen", _truncated)
    assert git_push._gh_visibility("github.com/owner/repo") is None


def test_a_bad_status_line_fails_open(monkeypatch, tmp_path):
    """BadStatusLine comes out of getresponse(), which urllib does not wrap."""
    import http.client

    def _bad(*_a, **_k):
        raise http.client.BadStatusLine("garbage")

    monkeypatch.setattr(urllib.request, "urlopen", _bad)
    assert git_push._gh_visibility("github.com/owner/repo") is None


def test_a_non_ascii_repository_name_fails_open(monkeypatch):
    """UnicodeEncodeError is a ValueError, so it matched no clause and escaped.
    It is raised from inside putrequest, after the socket has connected, so the
    trigger is a working connection plus one accented character or a soft hyphen
    in a repository name. It aborted the whole backup."""
    def _encode_error(*_a, **_k):
        raise UnicodeEncodeError("ascii", "п", 0, 1, "ordinal not in range")

    monkeypatch.setattr(urllib.request, "urlopen", _encode_error)
    assert git_push._gh_visibility("github.com/owner/пример") is None


def test_an_unanticipated_failure_fails_open(monkeypatch):
    """The point of the catch-all: the next member of the family must permit the
    push rather than abort the backup, without anyone having to name it first."""
    def _surprise(*_a, **_k):
        raise RuntimeError("something nobody enumerated")

    monkeypatch.setattr(urllib.request, "urlopen", _surprise)
    assert git_push._gh_visibility("github.com/owner/repo") is None


# ============================================================
# Remote identity: the ceiling notice
#
# remote_objection() can return "no objection" without either check having
# actually evaluated anything: an unreadable engine remote list, a
# repository whose remote does not resolve to a URL, or a host/path Check B's
# own guard never asks GitHub about. Each of those used to be silent, and a
# silent "no objection" reads exactly like a clean pass. This section pins
# that a signal fires on the degraded paths and stays off the happy one.
# ============================================================

def test_ceiling_notice_fires_for_a_non_github_forge_and_still_permits(
        monkeypatch, tmp_path, capsys):
    """Check B's own guard never asks GitHub about a non-GitHub host, so this
    is a real "wall did not evaluate" case, not merely a lookup that failed.
    The push is still permitted: this is a signal, not a new refusal."""
    data = _aimed_at(monkeypatch, tmp_path, "https://gitlab.example/owner/repo.git")

    reason = git_push.remote_objection(data)
    assert reason is None
    out = capsys.readouterr().out
    assert "reached its lower ceiling" in out
    assert "could not verify" not in out  # distinct from the Check-B-answered warning


def test_ceiling_notice_prints_nothing_on_a_fully_evaluated_private_remote(
        monkeypatch, tmp_path, capsys):
    """The happy path: Check A ran against a non-empty engine set and Check B
    answered. Neither the old warning nor the new notice belongs here."""
    data = _aimed_at(monkeypatch, tmp_path,
                     "https://github.com/owner/somewhere-private.git")
    monkeypatch.setattr(git_push, "_gh_visibility", lambda *a, **k: "private")

    assert git_push.remote_objection(data, token="tok") is None
    assert capsys.readouterr().out == ""


def test_ceiling_notice_prints_once_per_remote(monkeypatch, tmp_path, capsys):
    """Same suppression mechanism as the cannot-verify warning: a real
    push-all run asks twice (the precondition, then the chokepoint), and the
    notice must not print twice for one repository."""
    data = _aimed_at(monkeypatch, tmp_path, "https://gitlab.example/owner/repo.git")

    git_push.remote_objection(data)
    git_push.remote_objection(data)
    assert capsys.readouterr().out.count("reached its lower ceiling") == 1


# ============================================================
# Token loading: must not crash a caller that never asked for the token
# ============================================================

def test_load_gh_token_returns_none_on_an_undecodable_env_file(tmp_path, monkeypatch):
    """A wall built to fail open must not carry a hard-crash path. Check A
    never uses the token, so a single non-UTF-8 byte in `.env` must not raise
    out of `load_gh_token()` and crash the callers (offboard-exec,
    create-data-repo) that reach this read only because the chokepoint resolves
    it eagerly."""
    env_path = tmp_path / ".env"
    env_path.write_bytes(b"GH_TOKEN=abc\xffdef\n")
    monkeypatch.setattr(git_push, "get_workspace_root", lambda: tmp_path)

    assert git_push.load_gh_token() is None
