"""The offboard that revoked access to two repos that no longer exist.

`revoke_github_access` deleted direct collaborator grants on
`heading-os-corporate`, `31c-crm-{slug}` and `31c-workspace-{slug}`. The last
two are pre-cutover names, and the repo an exec's data actually lives in --
`repo_name_for(slug)` -- was never in the list. So the tool 404'd on two names
that do not exist, printed "not a direct collaborator" for all three, and left
the departing executive's access to their own data untouched.

Underneath that sat a second layer. The collaborators DELETE removes a direct
grant and nothing else, so an exec reaching the repos through an organisation
membership or a team is not a collaborator at all: every DELETE answers 404,
which the old code reported as a skip. `check_residual_access` found that but
was deliberately read-only, and the removal went on a manual checklist. The
operator overruled that on 2026-08-25: an executive who has left 31C must not
still reach the data, and a checklist line is a reminder, not a revocation.

So the run now removes team memberships and the org membership, and then ASKS
GITHUB whether each repo is still reachable. That question is the only honest
proof: a DELETE's exit status says nothing about the routes it does not touch.
A repo that is still reachable makes the run exit 1 and says which.

Tests: this file.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SOURCE = ROOT / "scripts" / "offboard-exec.py"


@pytest.fixture(scope="module")
def ob():
    spec = importlib.util.spec_from_file_location("offboard_exec_t", str(SOURCE))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["offboard_exec_t"] = mod
    spec.loader.exec_module(mod)
    return mod


class _Result:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _router(table, default=None):
    """Build a `run_cmd` stub that answers by matching argv fragments.

    `table` is a list of `(fragment, method, result)`. `method` is the `-X`
    value, or "GET" for a call with no `-X`. First match wins.
    """
    calls = []

    def run_cmd(cmd, cwd=None, check=True):
        calls.append(list(cmd))
        method = cmd[cmd.index("-X") + 1] if "-X" in cmd else "GET"
        joined = " ".join(cmd)
        for fragment, want_method, result in table:
            if fragment in joined and want_method == method:
                return result
        return default if default is not None else _Result(1, stderr="404 Not Found")

    run_cmd.calls = calls
    return run_cmd


# ==========================================================================
# 1 - the repo list that named two retired repos and missed the live one
# ==========================================================================

def test_the_exec_data_repo_is_in_the_list(ob, monkeypatch):
    """The whole finding: the repo holding the exec's data was never checked."""
    monkeypatch.setattr(ob, "repo_name_for", lambda slug: f"heading-os-data-{slug}")
    repos = ob.exec_repos("jane-doe")
    assert any(r.endswith("/heading-os-data-jane-doe") for r in repos)


def test_the_corporate_repo_is_still_in_the_list(ob):
    assert any(r.endswith("/heading-os-corporate") for r in ob.exec_repos("jane-doe"))


def test_the_retired_names_are_still_checked(ob):
    """Cheap insurance: an exec provisioned before the cutover may still be on them."""
    repos = " ".join(ob.exec_repos("jane-doe"))
    assert "31c-crm-jane-doe" in repos
    assert "31c-workspace-jane-doe" in repos


def test_the_repo_name_comes_from_the_roster_not_a_convention(ob, monkeypatch):
    """A roster row naming a different repo must be honoured, not guessed past."""
    monkeypatch.setattr(ob, "repo_name_for", lambda slug: "an-entirely-custom-name")
    assert any(r.endswith("/an-entirely-custom-name") for r in ob.exec_repos("jane-doe"))


def test_the_list_has_no_duplicates(ob, monkeypatch):
    monkeypatch.setattr(ob, "repo_name_for", lambda slug: "heading-os-corporate")
    repos = ob.exec_repos("jane-doe")
    assert len(repos) == len(set(repos))


def test_every_entry_is_org_qualified(ob):
    for repo in ob.exec_repos("jane-doe"):
        assert repo.startswith(f"{ob.github_org()}/")


def test_the_public_engine_repo_is_absent(ob):
    """No grant to remove and no private data behind it."""
    assert not any(r.endswith("/heading-os") for r in ob.exec_repos("jane-doe"))


# ==========================================================================
# 2 - the question that establishes access, rather than assuming it
# ==========================================================================

def test_a_reachable_repo_reports_true(ob, monkeypatch):
    monkeypatch.setattr(ob, "run_cmd", _router([("collaborators/jd", "GET", _Result(0))]))
    assert ob.has_repo_access("org/repo", "jd") is True


def test_a_404_reports_false(ob, monkeypatch):
    monkeypatch.setattr(ob, "run_cmd",
                        _router([("collaborators/jd", "GET",
                                  _Result(1, stderr="gh: 404 Not Found"))]))
    assert ob.has_repo_access("org/repo", "jd") is False


def test_any_other_failure_reports_unknown(ob, monkeypatch):
    """Not False. An unreachable API is not evidence that access is gone."""
    monkeypatch.setattr(ob, "run_cmd",
                        _router([("collaborators/jd", "GET",
                                  _Result(1, stderr="500 Internal Server Error"))]))
    assert ob.has_repo_access("org/repo", "jd") is None


def test_a_missing_gh_binary_reports_unknown(ob, monkeypatch):
    def _boom(*a, **k):
        raise FileNotFoundError("gh")
    monkeypatch.setattr(ob, "run_cmd", _boom)
    assert ob.has_repo_access("org/repo", "jd") is None


def test_the_check_is_a_get_not_a_delete(ob, monkeypatch):
    """A verification that mutates is not a verification."""
    stub = _router([("collaborators/jd", "GET", _Result(0))])
    monkeypatch.setattr(ob, "run_cmd", stub)
    ob.has_repo_access("org/repo", "jd")
    assert all("-X" not in cmd for cmd in stub.calls)


# ==========================================================================
# 3 - the revocation that now proves itself
# ==========================================================================

def test_a_team_granted_exec_is_reported_as_still_having_access(ob, monkeypatch, capsys):
    """The dangerous case verbatim: every DELETE 404s and access remains.

    Before the verification pass this run printed three yellow skips and
    returned True, which the verdict read as a clean revocation.
    """
    monkeypatch.setattr(ob, "repo_name_for", lambda s: f"heading-os-data-{s}")
    monkeypatch.setattr(ob, "run_cmd", _router([
        ("collaborators/jd", "DELETE", _Result(1, stderr="gh: 404 Not Found")),
        ("collaborators/jd", "GET", _Result(0)),          # still reachable
    ]))
    _ok, still = ob.revoke_github_access("jane-doe", {"github_user": "jd"})
    assert still, "a fully-retained access reported nothing outstanding"
    assert len(still) == len(ob.exec_repos("jane-doe"))
    assert "STILL HAS ACCESS" in capsys.readouterr().out


def test_a_clean_revocation_reports_nothing_outstanding(ob, monkeypatch):
    monkeypatch.setattr(ob, "repo_name_for", lambda s: f"heading-os-data-{s}")
    monkeypatch.setattr(ob, "run_cmd", _router([
        ("collaborators/jd", "DELETE", _Result(0)),
        ("collaborators/jd", "GET", _Result(1, stderr="404 Not Found")),
    ]))
    ok, still = ob.revoke_github_access("jane-doe", {"github_user": "jd"})
    assert ok is True and still == []


def test_an_unanswerable_check_is_not_a_pass(ob, monkeypatch, capsys):
    monkeypatch.setattr(ob, "repo_name_for", lambda s: f"heading-os-data-{s}")
    monkeypatch.setattr(ob, "run_cmd", _router([
        ("collaborators/jd", "DELETE", _Result(0)),
        ("collaborators/jd", "GET", _Result(1, stderr="503 Service Unavailable")),
    ]))
    ok, _still = ob.revoke_github_access("jane-doe", {"github_user": "jd"})
    assert ok is False, "an unknown answer was treated as revoked"
    assert "UNKNOWN" in capsys.readouterr().out


def test_the_username_comes_from_the_registry_field(ob, monkeypatch):
    """The field is `github_user`. `emergency-revoke.py` read `github_username`."""
    stub = _router([("collaborators", "DELETE", _Result(0)),
                    ("collaborators", "GET", _Result(1, stderr="404"))])
    monkeypatch.setattr(ob, "repo_name_for", lambda s: f"heading-os-data-{s}")
    monkeypatch.setattr(ob, "run_cmd", stub)
    ob.revoke_github_access("jane-doe", {"github_user": "octo-jane"})
    assert any("collaborators/octo-jane" in " ".join(c) for c in stub.calls)


def test_a_registry_without_a_username_falls_back_to_the_slug(ob, monkeypatch):
    stub = _router([("collaborators", "DELETE", _Result(0)),
                    ("collaborators", "GET", _Result(1, stderr="404"))])
    monkeypatch.setattr(ob, "repo_name_for", lambda s: f"heading-os-data-{s}")
    monkeypatch.setattr(ob, "run_cmd", stub)
    ob.revoke_github_access("jane-doe", {})
    assert any("collaborators/jane-doe" in " ".join(c) for c in stub.calls)


# ==========================================================================
# 4 - the removal that used to be a checklist item
# ==========================================================================

def _residual_router(*, in_team=True, in_org=True, after_clean=True):
    """A stub that models an org with one team, and tracks the DELETEs."""
    state = {"team": in_team, "org": in_org}
    calls = []

    def run_cmd(cmd, cwd=None, check=True):
        calls.append(list(cmd))
        joined = " ".join(cmd)
        method = cmd[cmd.index("-X") + 1] if "-X" in cmd else "GET"
        if joined.endswith("/teams --jq .[].slug") or "teams --jq" in joined:
            return _Result(0, stdout="engineering\n")
        if "teams/engineering/memberships/" in joined:
            if method == "DELETE":
                if after_clean:
                    state["team"] = False
                return _Result(0)
            return _Result(0) if state["team"] else _Result(1, stderr="404")
        if "memberships/" in joined:                      # org membership
            if method == "DELETE":
                if after_clean:
                    state["org"] = False
                return _Result(0)
            return _Result(0) if state["org"] else _Result(1, stderr="404")
        return _Result(1, stderr="404")

    run_cmd.calls = calls
    run_cmd.state = state
    return run_cmd


def test_a_team_membership_is_removed_not_written_down(ob, monkeypatch):
    stub = _residual_router()
    monkeypatch.setattr(ob, "run_cmd", stub)
    ob.remove_residual_access("jane-doe", {"github_user": "jd"})
    assert any("teams/engineering/memberships/jd" in " ".join(c) and "DELETE" in c
               for c in stub.calls), "the team membership was only reported"


def test_the_org_membership_is_removed(ob, monkeypatch):
    stub = _residual_router()
    monkeypatch.setattr(ob, "run_cmd", stub)
    ob.remove_residual_access("jane-doe", {"github_user": "jd"})
    assert any(c[-3].endswith(f"orgs/{ob.github_org()}/memberships/jd") and "DELETE" in c
               for c in stub.calls if len(c) >= 3), "the org membership survived"


def test_teams_are_removed_before_the_org(ob, monkeypatch):
    """Removing the org takes the teams with it; doing teams first names them."""
    stub = _residual_router()
    monkeypatch.setattr(ob, "run_cmd", stub)
    ob.remove_residual_access("jane-doe", {"github_user": "jd"})
    deletes = [" ".join(c) for c in stub.calls if "DELETE" in c]
    team_at = next(i for i, d in enumerate(deletes) if "teams/" in d)
    org_at = next(i for i, d in enumerate(deletes) if "teams/" not in d)
    assert team_at < org_at


def test_a_clean_removal_leaves_nothing_residual(ob, monkeypatch):
    monkeypatch.setattr(ob, "run_cmd", _residual_router())
    assert ob.remove_residual_access("jane-doe", {"github_user": "jd"}) == []


def test_a_removal_that_did_not_take_is_still_reported(ob, monkeypatch):
    """An org OWNER cannot be removed by API; the run must not claim success."""
    monkeypatch.setattr(ob, "run_cmd", _residual_router(after_clean=False))
    residual = ob.remove_residual_access("jane-doe", {"github_user": "jd"})
    assert residual, "access that survived the removal was reported as clear"


def test_an_owner_removal_failure_names_the_manual_step(ob, monkeypatch, capsys):
    def run_cmd(cmd, cwd=None, check=True):
        joined = " ".join(cmd)
        if "teams --jq" in joined:
            return _Result(0, stdout="")
        if "-X" in cmd:
            return _Result(1, stderr="403 Cannot remove the last owner")
        return _Result(0)
    monkeypatch.setattr(ob, "run_cmd", run_cmd)
    ob.remove_residual_access("jane-doe", {"github_user": "jd"})
    assert "OWNER" in capsys.readouterr().out


def test_a_missing_gh_binary_is_reported_not_swallowed(ob, monkeypatch):
    def _boom(*a, **k):
        raise FileNotFoundError("gh")
    monkeypatch.setattr(ob, "run_cmd", _boom)
    residual = ob.remove_residual_access("jane-doe", {"github_user": "jd"})
    assert residual and "COULD NOT BE REMOVED" in residual[0]


def test_nobody_with_no_access_is_touched(ob, monkeypatch):
    """A person already clear must not have DELETEs fired at them."""
    stub = _residual_router(in_team=False, in_org=False)
    monkeypatch.setattr(ob, "run_cmd", stub)
    ob.remove_residual_access("jane-doe", {"github_user": "jd"})
    team_deletes = [c for c in stub.calls if "DELETE" in c and "teams/" in " ".join(c)]
    assert not team_deletes


# ==========================================================================
# 5 - the verdict that must fail on retained access
# ==========================================================================

def test_a_reachable_repo_fails_the_run(ob):
    complete, reasons = ob.offboard_verdict(True, True, [], ["org/heading-os-data-jd"])
    assert complete is False
    assert any("STILL REACH" in r for r in reasons)


def test_the_reason_names_the_repo(ob):
    _c, reasons = ob.offboard_verdict(True, True, [], ["org/secret-repo"])
    assert any("org/secret-repo" in r for r in reasons)


def test_a_fully_clean_run_still_passes(ob):
    assert ob.offboard_verdict(True, True, [], []) == (True, [])


def test_the_new_argument_is_optional_for_existing_callers(ob):
    assert ob.offboard_verdict(True, True, []) == (True, [])


def test_residual_and_reachable_are_reported_separately(ob):
    """They measure different things; folding them together hides the worse one."""
    _c, reasons = ob.offboard_verdict(True, True, ["org membership"], ["org/repo"])
    assert len(reasons) == 2


def test_a_failed_delete_still_fails_the_run(ob):
    complete, _r = ob.offboard_verdict(False, True, [], [])
    assert complete is False


def test_unpreserved_contacts_still_fail_the_run(ob):
    complete, _r = ob.offboard_verdict(True, False, [], [])
    assert complete is False


# ==========================================================================
# 6 - the gate that makes the wider action safe
# ==========================================================================

def test_the_safety_gate_names_the_org_removal(ob, monkeypatch, capsys):
    """The action got wider; the confirmation must say so before it is typed."""
    monkeypatch.setattr("builtins.input", lambda _prompt: "jane-doe")
    assert ob.safety_gate("jane-doe") is True
    out = capsys.readouterr().out
    assert "ORGANISATION" in out
    assert "every repo in the org" in out


def test_a_wrong_confirmation_still_aborts(ob, monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _prompt: "someone-else")
    assert ob.safety_gate("jane-doe") is False


def test_the_checklist_no_longer_calls_org_removal_manual(ob, capsys):
    """It is done by the script now; listing it as a to-do invites a double run."""
    ob.print_manual_checklist("jane-doe", {"github_user": "jd", "email": "j@e.com"})
    out = capsys.readouterr().out
    assert "[ ] Remove org membership" not in out
    assert "now removed by this script" in out
