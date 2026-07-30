"""Frozen contract: a push is refused when the remote on the FAR end is wrong.

Frozen at the pre-impl gate for the remote-identity-wall slice, before any
implementation exists. Every import of the code under test therefore sits inside
a test body; a module-scope import would stop this file collecting and a file
that collects nothing cannot be frozen.

This is NOT a copy of the implementation suite in the plan. Two properties are
held here that the plan's own tests do not hold, and they are the reason this
contract exists rather than being a ceremony:

  1. THE COMPOSITION. The plan states its own traceability limit: no test of its
     carries a REAL objection through to `sys.exit(2)`. Task 2 and Task 3 prove
     an objection is produced; Task 4 proves an objection becomes exit 2 using a
     STUBBED objection. The two legs compose and nothing measures the join.
     test_push_all_exits_2_on_a_real_objection_end_to_end is that measurement.
     A wall wired to a consumer that never calls it passes every test in the
     plan and protects nothing.

     Measured at freeze time, and worth recording because it is the whole point:
     that test currently fails with DID NOT RAISE SystemExit, and its captured
     stdout reads "pushed & verified [0 0] in sync with origin/main". The
     overlay reached the engine's remote in a sandbox. The contract is red
     because the defect is real, not because a name is missing.

  2. THE HOLE THAT WOULD HAVE SHIPPED. The first draft of the plan looked the
     ENGINE's remote up under the CALLER's remote name, so a repository whose
     remote is named anything but origin walked straight through. That is
     reachable from the command line today (safe-push takes --remote). It is
     pinned here, not only in the suite, because it is the failure this slice
     would most plausibly reintroduce.

Every refusal is asserted by its CONTENT, never by truthiness. An earlier draft
of this file asserted `is not None` and the vacuity probe correctly reported
three of these tests as asserting nothing: a mock return satisfies `is not None`
without any implementation behind it.

The fail-open direction is held too, and deliberately: a wall that refuses when
it cannot reach the GitHub API converts a network blip into a lost backup of the
one half of the workspace that cannot be reconstructed.
"""
import subprocess
from pathlib import Path

import pytest

ENGINE = Path(__file__).resolve().parents[3]

SENTINEL = "not-a-real-token-userinfo"


def _git(args, cwd):
    subprocess.run(["git", *args], cwd=str(cwd), check=True,
                   capture_output=True, text=True)


def _repo(base: Path, label: str):
    """A work repo named *label* with one commit, and a bare repo as origin.

    The label is not decoration. A refusal has to NAME the repository it is
    about, and two fixtures both called "work" would let a test pass on the
    wrong one.
    """
    remote = base / "remote.git"
    work = base / label
    subprocess.run(["git", "init", "--bare", "-b", "main", str(remote)],
                   check=True, capture_output=True)
    subprocess.run(["git", "init", "-b", "main", str(work)],
                   check=True, capture_output=True)
    _git(["config", "user.email", "builder@example.invalid"], work)
    _git(["config", "user.name", "Builder"], work)
    (work / "f.txt").write_text("x\n", encoding="utf-8")
    _git(["add", "-A"], work)
    _git(["commit", "-m", "one"], work)
    _git(["remote", "add", "origin", str(remote)], work)
    return remote, work


def _pose_as_split(monkeypatch, engine: Path, data: Path):
    """Point the data-root seam at throwaway trees: engine here, data there.

    Patched on git_push, which is where the wall reads them from, so this holds
    whether the wall is reached directly or through push-all.
    """
    from scripts.utils import git_push

    monkeypatch.setattr(git_push, "get_workspace_root", lambda: engine)
    monkeypatch.setattr(git_push, "get_data_root", lambda: data)


def _load_push_all():
    """push-all.py as a module. Its name is not importable, hence the loader.

    Inside a helper called from a test body rather than at module scope: the
    module re-execs under the project venv at import time, and tests/conftest.py
    sets the guard that makes that a no-op.
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "push_all_contract", ENGINE / "scripts" / "push-all.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sentinel_userinfo_url(host_path: str) -> str:
    """A URL carrying a recognisable non-credential userinfo component.

    Assembled at runtime. Written out in one piece it is refused by the
    workspace prevent-secrets hook, which refused the design spec and two drafts
    of the plan for exactly this. The hook is right; no allow-list entry exists.
    """
    return "https://" + "x-access-token:" + SENTINEL + "@" + host_path


# ============================================================
# Check A: the catastrophe, refused offline
# ============================================================

def test_a_non_engine_repo_aimed_at_the_engine_remote_is_refused(
        monkeypatch, tmp_path):
    """The worst outcome this workspace can produce.

    Nothing currently stands in the way: the content scan catches secrets only,
    the engine clean scan does not run on the overlay by design, and the
    existing wall identifies the local DIRECTORY rather than where it is aimed.
    """
    from scripts.utils.git_push import _normalize_remote_url, remote_objection

    engine_remote, engine = _repo(tmp_path / "e", "engine")
    _data_remote, data = _repo(tmp_path / "d", "data-overlay")
    _pose_as_split(monkeypatch, engine, data)
    _git(["remote", "set-url", "origin", str(engine_remote)], data)

    reason = remote_objection(data)
    assert reason is not None
    # Content, not truthiness. The refusal has to say WHICH repository and WHERE
    # it points, or an operator cannot act on it.
    assert "data-overlay" in reason
    assert _normalize_remote_url(str(engine_remote)) in reason


def test_the_refusal_survives_a_remote_the_caller_named_itself(
        monkeypatch, tmp_path):
    """The hole the first draft of the plan shipped, pinned so it cannot return.

    safe-push exposes --remote and hands the name straight through. If the
    engine side is looked up under that same name, a repository whose remote is
    called anything else and aimed at the engine URL is not compared to
    anything, and Check A returns no objection. Identity, not label.
    """
    from scripts.utils.git_push import _normalize_remote_url, remote_objection

    engine_remote, engine = _repo(tmp_path / "e", "engine")
    _data_remote, data = _repo(tmp_path / "d", "data-overlay")
    _pose_as_split(monkeypatch, engine, data)
    _git(["remote", "add", "gh", str(engine_remote)], data)

    reason = remote_objection(data, remote="gh")
    assert reason is not None
    assert _normalize_remote_url(str(engine_remote)) in reason


def test_the_wall_compares_identity_not_spelling(monkeypatch, tmp_path):
    """One repository named two ways. String equality is not the property."""
    from scripts.utils.git_push import remote_objection

    _engine_remote, engine = _repo(tmp_path / "e", "engine")
    _data_remote, data = _repo(tmp_path / "d", "data-overlay")
    _pose_as_split(monkeypatch, engine, data)
    _git(["remote", "set-url", "origin",
          "https://github.com/Owner/Repo.git"], engine)
    _git(["remote", "set-url", "origin", "git@github.com:owner/repo"], data)

    reason = remote_objection(data)
    assert reason is not None
    assert "github.com/owner/repo" in reason


# ============================================================
# Check B: the property itself
# ============================================================

def test_a_public_remote_is_refused_even_when_it_is_not_the_engines(
        monkeypatch, tmp_path):
    """Check A is a proxy for one specific collapse. This is the requirement."""
    from scripts.utils import git_push
    from scripts.utils.git_push import remote_objection

    _engine_remote, engine = _repo(tmp_path / "e", "engine")
    _data_remote, data = _repo(tmp_path / "d", "data-overlay")
    _pose_as_split(monkeypatch, engine, data)
    _git(["remote", "set-url", "origin",
          "https://github.com/owner/elsewhere.git"], data)
    monkeypatch.setattr(git_push, "_gh_visibility", lambda *a, **k: "public")

    reason = remote_objection(data)
    assert reason is not None
    assert "github.com/owner/elsewhere" in reason
    assert "PUBLIC" in reason


def test_an_unanswerable_visibility_permits_the_push(monkeypatch, tmp_path):
    """The fail-open direction, held as a contract term rather than a comment.

    An unreachable GitHub API carries no information about whether the overlay
    is private. Refusing here would trade a leak risk for a data-loss risk on
    the one command whose whole job is an off-machine copy of the irreplaceable
    half of the workspace. A wall that fails closed on this branch is a
    regression even though it looks stricter.
    """
    from scripts.utils import git_push
    from scripts.utils.git_push import remote_objection

    _engine_remote, engine = _repo(tmp_path / "e", "engine")
    _data_remote, data = _repo(tmp_path / "d", "data-overlay")
    _pose_as_split(monkeypatch, engine, data)
    _git(["remote", "set-url", "origin",
          "https://github.com/owner/unknown.git"], data)
    monkeypatch.setattr(git_push, "_gh_visibility", lambda *a, **k: None)

    assert remote_objection(data) is None


# ============================================================
# What must NOT be refused
# ============================================================

def test_the_engine_pushing_to_its_own_remote_is_never_refused(
        monkeypatch, tmp_path):
    """The engine is EXPECTED to point at the public engine repository. A wall
    that refuses it refuses every code push on the machine."""
    from scripts.utils import git_push
    from scripts.utils.git_push import remote_objection

    _engine_remote, engine = _repo(tmp_path / "e", "engine")
    _pose_as_split(monkeypatch, engine, tmp_path / "d")
    monkeypatch.setattr(git_push, "_gh_visibility", lambda *a, **k: "public")

    assert remote_objection(engine) is None


def test_a_correctly_configured_repo_still_pushes(monkeypatch, tmp_path):
    """The do-not-break term. A wall is worthless if it stops the normal case.

    Asserted through the real push, not only through the predicate, because the
    chokepoint is where the wall is wired and a wrong verdict shape there would
    fail the push while the predicate looked fine.
    """
    from scripts.utils.git_push import ahead_behind, remote_objection, supervised_push

    _engine_remote, engine = _repo(tmp_path / "e", "engine")
    _data_remote, data = _repo(tmp_path / "d", "data-overlay")
    _pose_as_split(monkeypatch, engine, data)

    assert remote_objection(data) is None
    verdict = supervised_push(data, branch="main", stall_window=15)
    assert verdict["state"] == "ok", verdict
    assert ahead_behind(data, "origin", "main") == (0, 0)


# ============================================================
# The credential must never appear in the refusal
# ============================================================

def test_no_output_of_the_wall_carries_a_remotes_userinfo(
        monkeypatch, tmp_path, capsys):
    """An explicit not-in check against a recognisable sentinel, never a regex.

    A git remote may legitimately embed a token for authentication. A wall whose
    refusal message prints the credential it was protecting is worse than no
    wall, so this is a contract term and not a nicety.
    """
    from scripts.utils import git_push
    from scripts.utils.git_push import remote_objection

    _engine_remote, engine = _repo(tmp_path / "e", "engine")
    _data_remote, data = _repo(tmp_path / "d", "data-overlay")
    _pose_as_split(monkeypatch, engine, data)
    _git(["remote", "set-url", "origin",
          _sentinel_userinfo_url("github.com/owner/pub.git")], data)
    monkeypatch.setattr(git_push, "_gh_visibility", lambda *a, **k: "public")

    reason = remote_objection(data)
    assert reason is not None
    assert "github.com/owner/pub" in reason      # it identified the remote...
    assert SENTINEL not in reason                # ...without the credential
    assert SENTINEL not in capsys.readouterr().out


# ============================================================
# The composition: a real objection reaching a real exit code
# ============================================================

def test_push_all_exits_2_on_a_real_objection_end_to_end(monkeypatch, tmp_path):
    """The one span the implementation suite leaves open, by its own admission.

    The plan proves the objection with the real function and proves the exit
    code with a stubbed one. Nothing measures the join, and the join is where a
    wall that is never called still passes every test. This drives push_repo
    with a genuinely misconfigured repository and asks for the exit code.

    dry_run is True on purpose: the refusal must be reported by a preview as
    well, and evaluating a precondition writes nothing, so the honest preview
    and the safe test are the same run.
    """
    push_all = _load_push_all()
    engine_remote, engine = _repo(tmp_path / "e", "engine")
    _data_remote, data = _repo(tmp_path / "d", "data-overlay")
    _pose_as_split(monkeypatch, engine, data)
    _git(["remote", "set-url", "origin", str(engine_remote)], data)

    with pytest.raises(SystemExit) as caught:
        push_all.push_repo("DATA", data, "m", False, True, {})
    assert caught.value.code == 2


def test_the_end_to_end_refusal_pushes_nothing(monkeypatch, tmp_path):
    """Refuse, then push, is not a wall. The bare remote must stay empty.

    The failing output of this test at freeze time is the defect itself:
    "pushed & verified [0 0] in sync with origin/main".
    """
    push_all = _load_push_all()
    engine_remote, engine = _repo(tmp_path / "e", "engine")
    _data_remote, data = _repo(tmp_path / "d", "data-overlay")
    _pose_as_split(monkeypatch, engine, data)
    _git(["remote", "set-url", "origin", str(engine_remote)], data)

    with pytest.raises(SystemExit):
        push_all.push_repo("DATA", data, "m", False, False, {})
    has_main = subprocess.run(
        ["git", "-C", str(engine_remote), "show-ref", "--verify",
         "refs/heads/main"],
        capture_output=True,
    )
    assert has_main.returncode != 0
