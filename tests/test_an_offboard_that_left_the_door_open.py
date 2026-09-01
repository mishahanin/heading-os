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
# 4b - "I could not ask" must never render as "they are not a member"
#
# `check_residual_access` is what `remove_residual_access` returns from Step 1d,
# so it IS the re-verification, and its own comments say a 403 (a token without
# `read:org`), a 429 or a 5xx used to read exactly like a 404. The fix landed;
# nothing measured it. MEASURED 2026-09-01, over the 35 tests this file had: all
# FOUR "COULD NOT BE CHECKED" branches could be deleted and the file stayed
# green, which is a departing executive's retained org access reported as clear.
# ==========================================================================

def _check_router(*, org=None, teams=None, team_member=None):
    """A stub for `check_residual_access`'s three GETs. Defaults are all-clear.

    The org call is `orgs/{org}/memberships/{user}` and the team call is
    `orgs/{org}/teams/{team}/memberships/{user}`; both contain `memberships/`,
    so the team route is matched FIRST on `/teams/`.
    """
    org = org if org is not None else _Result(1, stderr="gh: 404 Not Found")
    teams = teams if teams is not None else _Result(0, stdout="engineering\n")
    team_member = (team_member if team_member is not None
                   else _Result(1, stderr="gh: 404 Not Found"))

    def run_cmd(cmd, cwd=None, check=True):
        joined = " ".join(cmd)
        if "/teams" in joined and "/memberships/" not in joined:
            return teams
        if "/teams/" in joined and "/memberships/" in joined:
            return team_member
        if "/memberships/" in joined:
            return org
        raise AssertionError(f"unexpected call: {joined}")

    return run_cmd


_UNANSWERED = [
    "gh: 403 Forbidden (token is missing the read:org scope)",
    "gh: 429 Too Many Requests",
    "gh: 502 Bad Gateway",
]


@pytest.mark.parametrize("stderr", _UNANSWERED)
def test_an_unqueryable_org_membership_is_reported_not_read_as_absent(
        ob, monkeypatch, stderr):
    monkeypatch.setattr(ob, "run_cmd", _check_router(org=_Result(1, stderr=stderr)))
    residual = ob.check_residual_access("jane-doe", {"github_user": "jd"})
    assert any("org membership" in r and "COULD NOT BE CHECKED" in r for r in residual), (
        f"{stderr!r} was read as 'not an org member'; that is a departing exec's "
        f"org-wide access reported as clear. Got: {residual}")


@pytest.mark.parametrize("stderr", _UNANSWERED)
def test_an_unqueryable_team_membership_is_reported_not_read_as_absent(
        ob, monkeypatch, stderr):
    monkeypatch.setattr(ob, "run_cmd",
                        _check_router(team_member=_Result(1, stderr=stderr)))
    residual = ob.check_residual_access("jane-doe", {"github_user": "jd"})
    assert any("team membership" in r and "COULD NOT BE CHECKED" in r for r in residual), (
        f"{stderr!r} was read as 'not on the team'. Got: {residual}")


@pytest.mark.parametrize("stderr", _UNANSWERED)
def test_a_team_list_that_could_not_be_fetched_is_reported(ob, monkeypatch, stderr):
    """Zero teams enumerated and 'no teams' must not render the same."""
    monkeypatch.setattr(ob, "run_cmd", _check_router(teams=_Result(1, stderr=stderr)))
    residual = ob.check_residual_access("jane-doe", {"github_user": "jd"})
    assert any("team memberships COULD NOT BE CHECKED" in r for r in residual), (
        f"an unlistable org read as an org with no teams. Got: {residual}")


def test_a_missing_gh_binary_is_reported_by_the_check_too(ob, monkeypatch):
    """The sibling of test_a_missing_gh_binary_is_reported_not_swallowed, on the
    read-only half. Without it the whole `except FileNotFoundError` branch of
    `check_residual_access` could be replaced with `pass`."""
    def _boom(*a, **k):
        raise FileNotFoundError("gh")

    monkeypatch.setattr(ob, "run_cmd", _boom)
    residual = ob.check_residual_access("jane-doe", {"github_user": "jd"})
    assert any("COULD NOT BE CHECKED" in r for r in residual), (
        f"gh being absent reported no residual access at all. Got: {residual}")


def test_a_genuine_404_on_every_route_reports_nothing_residual(ob, monkeypatch):
    """The other direction, so the four tests above are not satisfied by a
    function that simply always reports something."""
    monkeypatch.setattr(ob, "run_cmd", _check_router())
    assert ob.check_residual_access("jane-doe", {"github_user": "jd"}) == []


def test_a_present_membership_is_reported_as_present_not_as_unqueryable(ob, monkeypatch):
    """A real 200 must read as access held, with no 'could not check' hedge."""
    monkeypatch.setattr(ob, "run_cmd", _check_router(org=_Result(0)))
    residual = ob.check_residual_access("jane-doe", {"github_user": "jd"})
    assert any("org membership" in r for r in residual)
    assert not any("COULD NOT BE CHECKED" in r for r in residual), residual


def test_an_org_membership_the_run_could_not_query_is_named_in_step_1c(
        ob, monkeypatch, capsys):
    """Step 1c must say it did not attempt the org removal, as its team half does.

    The team loop prints '[error] Could not check membership of {org}/{team}' on
    any non-404 answer and then skips the DELETE. The ORG half had no such
    branch: a 403 (a token without `admin:org`), a 429 or a 5xx skipped the
    DELETE and printed NOTHING, about the widest grant in the whole offboard -
    org membership reaches every repo in the org, which is the reason
    `safety_gate` warns about it by name. Step 1d does still catch it, so the
    verdict was never wrong; what the operator could not tell was whether the
    removal had been ATTEMPTED, which is the difference between "retry it" and
    "escalate to a human with owner rights".
    """
    monkeypatch.setattr(ob, "run_cmd",
                        _check_router(org=_Result(1, stderr="gh: 403 Forbidden")))
    ob.remove_residual_access("jane-doe", {"github_user": "jd"})
    step_1c = capsys.readouterr().out.split("Step 1d")[0]
    assert "org membership" in step_1c.lower(), (
        "Step 1c printed nothing at all about the org membership it could not "
        "query:\n" + step_1c)


def test_an_org_membership_that_is_genuinely_absent_prints_no_error(ob, monkeypatch, capsys):
    """The other direction: a 404 is an answer, not a failure to ask."""
    monkeypatch.setattr(ob, "run_cmd", _check_router())
    ob.remove_residual_access("jane-doe", {"github_user": "jd"})
    step_1c = capsys.readouterr().out.split("Step 1d")[0]
    assert "could not check org membership" not in step_1c.lower(), step_1c


def test_an_unqueryable_route_reaches_the_verdict_through_step_1d(ob, monkeypatch):
    """End to end: what Step 1d returns is what decides the run."""
    monkeypatch.setattr(ob, "run_cmd",
                        _check_router(org=_Result(1, stderr="gh: 403 Forbidden")))
    residual = ob.remove_residual_access("jane-doe", {"github_user": "jd"})
    assert residual, "a route the run could not query was reported as clear"
    complete, reasons = ob.offboard_verdict(True, True, residual, [])
    assert complete is False
    assert any("COULD NOT BE CHECKED" in r for r in reasons), reasons


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
