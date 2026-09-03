#!/usr/bin/env python3
"""Shard 10-p4: an offboard that looked in a repository nobody uses any more.

`exec_repos` was corrected on 2026-08-25: two of the three repo names it used
had been retired, so a departing exec's real data overlay was never in the
revocation list at all. The module docstring records that repair. Two functions
in the same file were left behind and the docstring covers for them:

  - `archive_workspace_repo` archived `31c-workspace-{slug}` only. On the
    current model that name does not exist, so the exec's real overlay was never
    archived, and -- with no 404 branch, unlike its CRM sibling -- the step
    printed a red `[error]` about a repo that legitimately is not there, every
    single run. A permanent false alarm trains the operator to ignore the line
    that would matter. Its result was also discarded at the call site, so a
    genuine failure could not stop the run printing "Offboarding complete".
  - `preserve_crm_contacts` and `reassign_contacts` read `31c-crm-{slug}` at a
    top-level `contacts/`. On the current model contacts live inside the data
    overlay at `crm/contacts` -- the path `aggregate-crm.py` reads. The
    retirement is TWO levels deep, so swapping only the repo name still finds
    nothing.

The third is a blind spot with a double edge. The team-membership probe treated
every non-zero exit as "not a member", so a 403 from a token without `read:org`
read exactly like a clean result. The removal step skipped the DELETE on the
same 403, and Step 1d's re-check is literally the same function -- so the
verification was blind in the identical place and the verdict went green on a
team that was never even attempted.

Then: `ops-radar-notify` read a CRASHED radar (nonzero exit, empty stdout) as
"nothing due" and exited 0, so the daily unattended timer reported health it
never measured; `odin_pagerank`'s docstring listed an edge case that commit
76c63fd had already repaired; a comment advertised a "most specific first"
precedence over an unordered set; and `cmd_ack` did an unlocked
read-modify-write with a live signal sweep inside the window.

One finding is REFUTED with proof: `ollama_hosts_in_use` cannot return an empty
list, so the exit-code hole the report describes is unreachable.

Run: .venv/bin/python -m pytest tests/test_an_offboard_that_archived_the_wrong_repo.py
"""
from __future__ import annotations

import ast
import importlib.util
import json
import subprocess
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _load(rel: str, name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ofb = _load("scripts/offboard-exec.py", "offboard_exec_10p4")
orn = _load("scripts/ops-radar-notify.py", "ops_radar_notify_10p4")
opr = _load("scripts/ops-radar.py", "ops_radar_10p4")
opg = _load("scripts/odin_pagerank.py", "odin_pagerank_10p4")

# The org this file's fixtures pretend to be offboarding from.
#
# Pinned, because `github_org()` resolves through `load_github_org()`, which
# reads the OPERATOR'S PRIVATE DATA OVERLAY on every call and returns `""` when
# there is not one. `main()` then refuses with "the GitHub org could not be
# resolved" - correctly - so three of this file's tests passed only on the
# author's machine. MEASURED 2026-09-01: with `HEADING_OS_DATA` pointed at an
# empty directory, exactly the state of a fresh public clone and of CI, this
# file went 43 passed / 3 failed; against the live overlay, 46 passed. A test
# whose verdict is decided by data that is not in this repository is not a test
# of this repository, and the engine repo is public.
#
# It is also a stronger assertion: at `""` the archive rows were checking for
# `"/heading-os-data-marlow-carter"`, with a bare leading slash where the org
# belongs, so a function that dropped the org entirely would still have passed.
TEST_ORG = "example-org"


def _cp(returncode=0, stdout="", stderr=""):
    return types.SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


# ============================================================
# Finding 1 -- the archive that missed the only repo that exists
# ============================================================

@pytest.fixture
def gh(monkeypatch):
    """Record every `gh`/`git` argv and answer from a scripted table."""
    calls = []
    table = {}

    def run_cmd(cmd, check=True, **kw):
        calls.append(list(cmd))
        joined = " ".join(cmd)
        for pattern, result in table.items():
            if all(p in joined for p in pattern):
                if isinstance(result, Exception):
                    raise result
                return result
        return _cp(0)

    monkeypatch.setattr(ofb, "run_cmd", run_cmd)
    monkeypatch.setattr(ofb, "repo_name_for", lambda slug: f"heading-os-data-{slug}")
    monkeypatch.setattr(ofb, "github_org", lambda: TEST_ORG)
    return types.SimpleNamespace(calls=calls, table=table)


def test_the_org_is_pinned_and_not_read_from_the_operators_overlay(gh):
    """The fixture's own floor.

    `github_org()` is patched on the MODULE, so every function in it that calls
    the bare name resolves to `TEST_ORG` rather than to whatever
    `HEADING_OS_DATA` happens to point at. If a future refactor imports the
    resolver directly instead, this row goes red before the three `main()` tests
    start passing for the wrong reason again.
    """
    assert ofb.github_org() == TEST_ORG
    ofb.archive_workspace_repo("marlow-carter")
    assert all(name.startswith(f"{TEST_ORG}/") for name in _archived(gh.calls)), (
        f"an archive target was built from some other org: {_archived(gh.calls)}")


def _archived(calls):
    return [c[3] for c in calls if c[:3] == ["gh", "repo", "archive"]]


def test_the_current_data_overlay_is_the_repo_that_gets_archived(gh, capsys):
    assert ofb.archive_workspace_repo("marlow-carter") is True
    assert f"{ofb.github_org()}/heading-os-data-marlow-carter" in _archived(gh.calls)


def test_the_retired_name_is_still_attempted(gh, capsys):
    """Same reasoning `exec_repos` records: a 404 on a retired name costs one
    request; missing a repo the exec still holds costs their access to it."""
    ofb.archive_workspace_repo("marlow-carter")
    assert f"{ofb.github_org()}/31c-workspace-marlow-carter" in _archived(gh.calls)


def test_a_missing_retired_name_is_a_skip_not_a_red_error(gh, capsys):
    """The permanent false alarm: no 404 branch at all, so every current-model
    exec produced a red `[error]` about a repo that is not supposed to exist."""
    gh.table[("archive", "31c-workspace-marlow-carter")] = _cp(
        1, stderr="HTTP 404: Not Found")
    assert ofb.archive_workspace_repo("marlow-carter") is True
    out = capsys.readouterr().out
    assert "[skip]" in out
    assert "[error]" not in out


def test_a_real_archive_failure_is_reported_as_a_failure(gh, capsys):
    gh.table[("archive", "heading-os-data-marlow-carter")] = _cp(
        1, stderr="HTTP 403: Forbidden")
    assert ofb.archive_workspace_repo("marlow-carter") is False
    assert "[error]" in capsys.readouterr().out


def test_neither_name_existing_warns_without_failing_the_run(gh, capsys):
    """A re-run of an offboard whose repos were already deleted must not be
    permanently red."""
    gh.table[("archive",)] = _cp(1, stderr="HTTP 404: Not Found")
    assert ofb.archive_workspace_repo("marlow-carter") is True
    assert "Neither name exists" in capsys.readouterr().out


def test_a_missing_gh_binary_is_a_failure_not_a_crash(gh):
    gh.table[("archive",)] = FileNotFoundError("gh not found")
    assert ofb.archive_workspace_repo("marlow-carter") is False


def test_one_name_archiving_is_not_reported_as_neither_existing(gh, capsys):
    """The negative case ON the `all(...)` line.

    `test_neither_name_existing_warns_without_failing_the_run` 404s BOTH names,
    where `all` and `any` agree, so it cannot tell them apart. Measured
    2026-09-01: `all(r is None ...)` rewritten as `any(r is None ...)` left the
    file green at 47 passed - and on the CURRENT model that mutation fires on
    every single offboard, because the retired name legitimately 404s while the
    real overlay archives fine. The run would then print "Neither name exists;
    nothing was archived" immediately after archiving the exec's data overlay,
    which is the same permanent-false-alarm defect this function was fixed for,
    with the colours swapped.
    """
    gh.table[("archive", "31c-workspace-marlow-carter")] = _cp(
        1, stderr="HTTP 404: Not Found")
    assert ofb.archive_workspace_repo("marlow-carter") is True
    out = capsys.readouterr().out
    assert "Neither name exists" not in out, out
    assert "Archived" in out


def test_a_404_on_the_real_overlay_is_a_skip_and_not_a_failure(gh, capsys):
    """The `"404" in result.stderr` branch of `_archive_repo`, on the name that
    matters. The existing skip row uses the RETIRED name, so a mutation that
    kept the 404 branch for one name only would not show there."""
    gh.table[("archive", "heading-os-data-marlow-carter")] = _cp(
        1, stderr="HTTP 404: Not Found")
    assert ofb.archive_workspace_repo("marlow-carter") is True
    assert "[error]" not in capsys.readouterr().out


def test_the_per_exec_crm_repo_still_uses_the_retired_name_only(gh, capsys):
    """Correct, and deliberate: the current model has no separate CRM repo, so
    a 404 there is the expected answer."""
    gh.table[("archive",)] = _cp(1, stderr="HTTP 404: Not Found")
    assert ofb.archive_per_exec_crm_repo("marlow-carter") is True
    assert _archived(gh.calls) == [f"{ofb.github_org()}/31c-crm-marlow-carter"]


def test_a_failed_archive_reaches_the_verdict(gh):
    """It used to be discarded at the call site, so it could not stop the run
    printing "Offboarding complete"."""
    complete, reasons = ofb.offboard_verdict(True, True, [], [], archived=False)
    assert complete is False
    assert any("could not be archived" in r for r in reasons)


@pytest.fixture
def main_env(monkeypatch, tmp_path, unguard_main_clone):
    """`main()` with every external step stubbed, so only the wiring is under
    test. The verdict plumbing is the point: a failed archive has to travel from
    the archive step to the exit code.

    `offboard-exec.main()` opens with `require_main_clone(__file__)`, which
    exits 2 from a worktree before any of that wiring runs, so the guard is
    neutralised on this loaded module for the duration of each test that uses
    this fixture. That leaves it measured, not silenced:
    `tests/test_guarded_entry_points_refuse_from_a_worktree.py` pins through the
    AST that the call is the first statement of `main()` and is passed
    `__file__`, and `tests/test_clone_guard.py` pins that it fires. Those files
    own the control; this one owns the behaviour behind it.
    """
    unguard_main_clone(ofb)
    monkeypatch.setattr(sys, "argv", ["offboard-exec.py", "--exec", "marlow-carter"])
    for name, value in (
        # Pinned first, and for the reason `TEST_ORG` records: unpinned, `main()`
        # refuses outright on any clone without the operator's private overlay,
        # and these three tests were green only on one machine.
        ("github_org", lambda: TEST_ORG),
        ("validate_admin", lambda: None),
        ("get_exec_info", lambda slug: {"github_user": "marlow"}),
        ("safety_gate", lambda slug: True),
        ("revoke_github_access", lambda slug, info: (True, [])),
        ("check_residual_access", lambda slug, info: []),
        ("has_repo_access", lambda repo, user: False),
        ("preserve_crm_contacts", lambda slug: True),
        ("reassign_contacts", lambda slug, to: None),
        ("archive_per_exec_crm_repo", lambda slug: True),
        ("update_exec_registry", lambda slug: None),
        # `**_` since 2026-08-27: the audit log now takes the verdict, because
        # it used to write "GitHub access revoked" whatever had happened.
        ("log_offboarding", lambda slug, info, to, **_: None),
        ("print_manual_checklist", lambda slug, info: None),
    ):
        monkeypatch.setattr(ofb, name, value)
    return monkeypatch


def test_a_failed_archive_makes_the_whole_run_exit_nonzero(main_env, capsys):
    """The wiring, not the helper. The archive results were DISCARDED at the
    call site, so even a correct `archive_workspace_repo` could not stop the run
    printing "Offboarding complete"."""
    main_env.setattr(ofb, "archive_workspace_repo", lambda slug: False)
    assert ofb.main() == 1
    out = capsys.readouterr().out
    assert "INCOMPLETE" in out
    assert "could not be archived" in out


def test_a_clean_run_still_completes(main_env, capsys):
    main_env.setattr(ofb, "archive_workspace_repo", lambda slug: True)
    assert ofb.main() == 0
    assert "Offboarding complete" in capsys.readouterr().out


def test_a_failed_crm_archive_also_reaches_the_exit_code(main_env, capsys):
    main_env.setattr(ofb, "archive_workspace_repo", lambda slug: True)
    main_env.setattr(ofb, "archive_per_exec_crm_repo", lambda slug: False)
    assert ofb.main() == 1
    assert "could not be archived" in capsys.readouterr().out


def test_the_verdict_default_keeps_the_old_call_shape_working():
    complete, reasons = ofb.offboard_verdict(True, True, [], [])
    assert (complete, reasons) == (True, [])


# ============================================================
# Finding 2 -- contacts read from a repo nobody uses
# ============================================================

@pytest.fixture
def fleet(tmp_path, monkeypatch, gh):
    """A workspace root whose PARENT holds the sibling exec repos."""
    engine = tmp_path / "engine"
    engine.mkdir()
    monkeypatch.setattr(ofb, "get_workspace_root", lambda: engine)
    monkeypatch.setattr(ofb, "get_outputs_dir", lambda: tmp_path / "data" / "outputs")
    monkeypatch.setattr(ofb, "get_crm_contacts_dir",
                        lambda: tmp_path / "data" / "crm" / "contacts")
    return tmp_path


def _seed(parent: Path, repo: str, subpath: str, names=("alpha.md",)):
    d = parent / repo / subpath
    d.mkdir(parents=True)
    for n in names:
        (d / n).write_text("---\nowner: marlow-carter\n---\n\nnotes\n", encoding="utf-8")
    return d


def test_contacts_are_found_in_the_current_overlay_layout(fleet, gh, capsys):
    _seed(fleet, "heading-os-data-marlow-carter", "crm/contacts")
    src, everywhere = ofb._find_exec_contacts("marlow-carter")
    assert src == fleet / "heading-os-data-marlow-carter" / "crm" / "contacts"
    assert everywhere is True


def test_the_retirement_is_two_levels_deep(fleet, gh):
    """Swapping only the repo name still finds nothing: the SUBDIRECTORY moved
    too, from a top-level `contacts/` to `crm/contacts`."""
    (fleet / "heading-os-data-marlow-carter" / "contacts").mkdir(parents=True)
    src, _ = ofb._find_exec_contacts("marlow-carter")
    assert src is None, "a top-level contacts/ in the new repo is not the new layout"


def test_a_file_where_the_contacts_directory_should_be_is_not_accepted(fleet, gh):
    """`is_dir()`, not `exists()`. A plain file at that path would be handed on
    as if it were a directory of contacts."""
    d = fleet / "heading-os-data-marlow-carter" / "crm"
    d.mkdir(parents=True)
    (d / "contacts").write_text("not a directory", encoding="utf-8")
    (fleet / "31c-crm-marlow-carter").mkdir()
    src, everywhere = ofb._find_exec_contacts("marlow-carter")
    assert src is None
    assert everywhere is True


def test_a_pre_cutover_exec_is_still_found(fleet, gh):
    _seed(fleet, "31c-crm-marlow-carter", "contacts")
    src, _ = ofb._find_exec_contacts("marlow-carter")
    assert src == fleet / "31c-crm-marlow-carter" / "contacts"


def test_the_current_layout_wins_when_both_exist(fleet, gh):
    _seed(fleet, "heading-os-data-marlow-carter", "crm/contacts")
    _seed(fleet, "31c-crm-marlow-carter", "contacts")
    src, _ = ofb._find_exec_contacts("marlow-carter")
    assert "heading-os-data-marlow-carter" in str(src)


def test_preserving_copies_the_contacts_it_found(fleet, gh, capsys):
    _seed(fleet, "heading-os-data-marlow-carter", "crm/contacts",
          ("alpha.md", "beta.md"))
    assert ofb.preserve_crm_contacts("marlow-carter") is True
    dst = fleet / "data" / "outputs" / "operations" / "offboarding" / "marlow-carter-crm-final"
    assert sorted(p.name for p in dst.iterdir()) == ["alpha.md", "beta.md"]


def test_an_unreachable_repo_is_not_reported_as_preserved(fleet, gh, capsys):
    """"There are no contacts" and "I could not look" are different answers, and
    only the first may pass the verdict -- this runs immediately before the
    archive step makes the repo read-only."""
    gh.table[("clone",)] = subprocess.CalledProcessError(1, "gh")
    assert ofb.preserve_crm_contacts("marlow-carter") is False
    assert "nothing was checked" in capsys.readouterr().out


def test_no_gh_binary_while_cloning_is_reported_rather_than_crashing(
        fleet, gh, capsys):
    """The `FileNotFoundError` half of the clone handler, unmeasured.

    Its own comment says catching only `CalledProcessError` "let that crash the
    run partway through an offboard, leaving contacts unpreserved and the
    registry untouched with no rollback". Measured 2026-09-01: narrowing that
    `except` back to `(subprocess.CalledProcessError,)` left the file green at
    47 passed - only the `CalledProcessError` spelling had a fixture. The two
    are not interchangeable: `gh` missing from PATH raises the one that was
    never tested.
    """
    gh.table[("clone",)] = FileNotFoundError("gh not found")
    src, everywhere = ofb._find_exec_contacts("marlow-carter")
    assert src is None
    assert everywhere is False, (
        "no repo was reached, so this must not report that it looked everywhere")
    assert "Could not clone" in capsys.readouterr().out
    assert ofb.preserve_crm_contacts("marlow-carter") is False


def test_an_unanswerable_org_removal_is_announced(gh, capsys):
    """The removal step's ORG arm, matching its team arm.

    `test_the_removal_step_says_so_when_it_cannot_ask` covers the TEAM loop. The
    org branch beside it prints its own line and had no test: an unanswered org
    query skipped the DELETE and said nothing, so Step 1c rendered an empty
    section for the widest grant in the offboard.
    """
    _teams_env(gh, _cp(1, stderr="HTTP 404: Not Found"),
               org=_cp(1, stderr="HTTP 403: Resource not accessible by token"))
    ofb.remove_residual_access("marlow-carter", {})
    out = capsys.readouterr().out
    assert "Could not check org membership" in out, out
    assert "NOT attempted" in out


def test_a_clean_org_404_is_not_announced_as_unremovable(gh, capsys):
    """Anchor: printing that line on every 404 would make it noise on every
    clean offboard, which is the failure mode the archive step was fixed for."""
    _teams_env(gh, _cp(1, stderr="HTTP 404: Not Found"),
               org=_cp(1, stderr="HTTP 404: Not Found"))
    ofb.remove_residual_access("marlow-carter", {})
    assert "Could not check org membership" not in capsys.readouterr().out


def test_a_reachable_repo_with_no_contacts_is_an_honest_success(fleet, gh, capsys):
    (fleet / "heading-os-data-marlow-carter").mkdir()
    (fleet / "31c-crm-marlow-carter").mkdir()
    assert ofb.preserve_crm_contacts("marlow-carter") is True
    assert "nothing to preserve" in capsys.readouterr().out


def test_reassignment_uses_the_same_resolution(fleet, gh, capsys):
    """`--reassign-to` is an explicit operator instruction. It used to silently
    do nothing on a current-model exec."""
    _seed(fleet, "heading-os-data-marlow-carter", "crm/contacts")
    ofb.reassign_contacts("marlow-carter", "jordan-blake")
    moved = fleet / "data" / "crm" / "contacts" / "alpha.md"
    assert moved.is_file()
    assert "owner: jordan-blake" in moved.read_text(encoding="utf-8")


# ============================================================
# Finding 3 -- a 403 that read exactly like "not a member"
# ============================================================

def _teams_env(gh, membership, org=None):
    """Scripted answers. The TEAM probe and the ORG probe differ only by the
    `/teams/` segment, so the patterns must include it or one shadows the other.
    """
    gh.table[("/teams ",)] = _cp(0, stdout="leadership\n")
    gh.table[("/teams/leadership/memberships/marlow-carter",)] = membership
    gh.table[("/memberships/marlow-carter",)] = org or _cp(1, stderr="HTTP 404")


def test_an_unreadable_team_probe_is_not_read_as_clean(gh, capsys):
    _teams_env(gh, _cp(1, stderr="HTTP 403: Resource not accessible"))
    residual = ofb.check_residual_access("marlow-carter", {})
    assert any("COULD NOT BE CHECKED" in r for r in residual)
    assert "No org or team access found" not in capsys.readouterr().out


def test_a_genuine_404_still_reads_as_not_a_member(gh, capsys):
    """The fix must not turn every clean exec into a residual-access alarm."""
    _teams_env(gh, _cp(1, stderr="HTTP 404: Not Found"))
    assert ofb.check_residual_access("marlow-carter", {}) == []
    assert "No org or team access found" in capsys.readouterr().out


def test_a_real_membership_is_still_reported(gh):
    _teams_env(gh, _cp(0, stdout='{"state":"active"}'))
    residual = ofb.check_residual_access("marlow-carter", {})
    assert any(r == f"team membership in {ofb.github_org()}/leadership"
               for r in residual)


def test_an_unreadable_ORG_probe_is_not_read_as_clean_either(gh, capsys):
    """The same 403 blind spot, in the probe above the team loop.

    `check_residual_access` asks two questions with the identical shape: org
    membership, then team membership. This file's Finding 3 fixed and pinned the
    TEAM one and left the ORG one measured by nothing. MEASURED 2026-09-01:
    replacing `elif "404" not in (member.stderr or "")` with `elif False` - so an
    org-level 403 reads exactly as "not a member" - left this file green at 47
    passed.

    Org membership is the WIDER grant of the two: it reaches every repository in
    the org, which is why `safety_gate` names it, and Step 1d's re-verification
    is this same function. A blind spot here is a blind spot in the verification
    of the broadest access the departing exec holds.
    """
    _teams_env(gh, _cp(1, stderr="HTTP 404: Not Found"),
               org=_cp(1, stderr="HTTP 403: Resource not accessible by token"))
    residual = ofb.check_residual_access("marlow-carter", {})
    assert any("org membership" in r and "COULD NOT BE CHECKED" in r
               for r in residual), residual
    assert "No org or team access found" not in capsys.readouterr().out
    # And it reaches the verdict, which is what makes it more than a print.
    assert ofb.offboard_verdict(True, True, residual, [])[0] is False


def test_a_genuine_org_404_still_reads_as_not_a_member(gh, capsys):
    """Anchor for the row above: reporting every org probe as unreadable would
    pass it, and would make every clean offboard permanently INCOMPLETE."""
    _teams_env(gh, _cp(1, stderr="HTTP 404: Not Found"),
               org=_cp(1, stderr="HTTP 404: Not Found"))
    assert ofb.check_residual_access("marlow-carter", {}) == []
    assert "No org or team access found" in capsys.readouterr().out


def test_an_unlistable_teams_endpoint_is_not_read_as_no_teams(gh, capsys):
    """The third COULD-NOT-CHECK arm, which also had no witness.

    When `gh api orgs/<org>/teams` itself fails there is no team loop to run at
    all, so the per-team branch above cannot cover it. Measured 2026-09-01:
    deleting this arm left the file green at 47 passed, and the run then said
    "No org or team access found" having enumerated no teams whatsoever.
    """
    gh.table[("/teams ",)] = _cp(1, stderr="HTTP 403: Resource not accessible")
    gh.table[("/memberships/marlow-carter",)] = _cp(1, stderr="HTTP 404")
    residual = ofb.check_residual_access("marlow-carter", {})
    assert any("team memberships COULD NOT BE CHECKED" in r for r in residual), \
        residual
    assert "No org or team access found" not in capsys.readouterr().out


def test_a_missing_gh_binary_leaves_the_residual_check_saying_so(gh):
    """`except FileNotFoundError` inside `check_residual_access`, unmeasured.

    `test_a_missing_gh_binary_is_a_failure_not_a_crash` covers `_archive_repo`,
    which is a different function with its own handler; measured 2026-09-01,
    emptying THIS one left the file green at 47 passed. With no `gh` on PATH the
    check answers nothing at all, and an empty residual list is what a clean exec
    looks like - the run would have reported no residual access having made no
    request.
    """
    gh.table[("gh", "api")] = FileNotFoundError("gh not found")
    residual = ofb.check_residual_access("marlow-carter", {})
    assert any("COULD NOT BE CHECKED" in r for r in residual), residual
    assert ofb.offboard_verdict(True, True, residual, [])[0] is False


def test_the_unreadable_probe_reaches_the_verdict(gh):
    """Step 1d's re-check IS `check_residual_access`, so a blind spot there is a
    blind spot in the verification, not only in the report."""
    complete, reasons = ofb.offboard_verdict(
        True, True, ["team membership COULD NOT BE CHECKED: 403"], [])
    assert complete is False


def test_the_removal_step_says_so_when_it_cannot_ask(gh, capsys):
    """It used to `continue` on any non-zero exit, so the DELETE was never even
    attempted on a team that may still grant access."""
    _teams_env(gh, _cp(1, stderr="HTTP 403: Resource not accessible"))
    ofb.remove_residual_access("marlow-carter", {})
    out = capsys.readouterr().out
    assert "Could not check membership" in out


# ============================================================
# Finding 4 -- two sentences about plumbing that does not exist
# ============================================================

def test_the_docstring_no_longer_claims_the_list_is_handed_on():
    doc = " ".join(ofb.revoke_github_access.__doc__.split())
    assert "REPORTING ONLY" in doc
    assert "is handed to `remove_residual_access`, which now removes it" not in doc


def test_the_call_site_comment_no_longer_claims_it_either():
    """Two independent false statements about the same non-existent plumbing.
    Fixing one leaves the other asserting it."""
    src = (ROOT / "scripts/offboard-exec.py").read_text(encoding="utf-8")
    assert "The first pass tells the removal step what to work on" not in src
    assert "REPORTING ONLY and deliberately unused" in src


def test_the_removal_step_really_does_take_no_repo_list():
    import inspect
    params = list(inspect.signature(ofb.remove_residual_access).parameters)
    assert params == ["slug", "exec_info"]


# ============================================================
# Finding 7 -- a crashed radar reported as a quiet one
# ============================================================

@pytest.fixture
def radar_env(tmp_path, monkeypatch):
    monkeypatch.setattr(orn, "get_workspace_root", lambda: tmp_path)
    monkeypatch.setattr(orn, "load_env", lambda root: None)
    (tmp_path / "scripts").mkdir(parents=True)
    (tmp_path / orn.OPS_RADAR).write_text("#\n", encoding="utf-8")
    sent = []
    monkeypatch.setattr(orn.telegram_notify, "notify",
                        lambda r, m: sent.append((r, m)) or True)
    monkeypatch.setenv("OPS_RADAR_TELEGRAM_TARGET", "@someone")
    monkeypatch.setattr(sys, "argv", ["ops-radar-notify.py"])
    return sent


def _radar_returns(monkeypatch, rc, out="", err=""):
    calls = []

    def run(cmd, **kw):
        calls.append(cmd)
        if "heal" in cmd:
            return _cp(0)
        return _cp(rc, stdout=out, stderr=err)
    monkeypatch.setattr(orn.subprocess, "run", run)
    return calls


def test_a_crashed_radar_is_not_reported_as_nothing_due(
        radar_env, monkeypatch, capsys):
    _radar_returns(monkeypatch, 1, out="", err="ImportError: no module named x")
    assert orn.main() == 1
    assert radar_env == [], "a crash must not send a nudge"


def test_a_genuinely_quiet_radar_still_exits_zero_and_sends_nothing(
        radar_env, monkeypatch):
    _radar_returns(monkeypatch, 0, out="")
    assert orn.main() == 0
    assert radar_env == []


def test_a_radar_with_something_due_still_sends(radar_env, monkeypatch):
    _radar_returns(monkeypatch, 0, out="2 due")
    assert orn.main() == 0
    assert radar_env == [("@someone", "2 due")]


def test_the_heal_step_stays_best_effort(radar_env, monkeypatch):
    """Its docstring calls it best-effort and the finding does not reach it;
    a failing heal must still leave the nudge working."""
    def run(cmd, **kw):
        if "heal" in cmd:
            return _cp(3, stderr="heal blew up")
        return _cp(0, stdout="1 due")
    monkeypatch.setattr(orn.subprocess, "run", run)
    assert orn.main() == 0
    assert radar_env == [("@someone", "1 due")]


# ============================================================
# Finding 5 -- a false statement about the past
# ============================================================

def test_the_eof_fence_the_docstring_called_broken_actually_works():
    """It was repaired in 76c63fd four days after the measurement the paragraph
    is dated to, and the sentence stayed behind."""
    assert opg.FRONTMATTER_RE.sub("", "---\nid: x\n---", count=1) == ""
    assert opg.FRONTMATTER_RE.sub("", "---\nid: x\n---\nbody\n", count=1) == "body\n"


def test_the_docstring_records_the_repair_instead_of_the_defect():
    doc = " ".join(opg.parse_frontmatter.__doc__.split())
    assert "repaired in 76c63fd" in doc
    assert "Two residual edge cases" not in doc


def test_the_edge_case_that_is_still_true_was_not_deleted_with_it():
    """A blanket deletion would have dropped a true statement with the false
    one: the partial-dict fallback on unparseable YAML is still real."""
    doc = " ".join(opg.parse_frontmatter.__doc__.split())
    assert "falls back to its regex parser (partial dict)" in doc


# ============================================================
# Finding 6 -- precedence advertised over an unordered set
# ============================================================

def test_the_resolver_tokens_have_no_intra_note_precedence():
    """The report's suggested remedy -- an ordered tuple -- is a provable no-op:
    within one note all four tokens map to the same key."""
    src = (ROOT / "scripts/odin_pagerank.py").read_text(encoding="utf-8")
    assert "Register resolver tokens (most specific first)." not in src
    assert "There is NO precedence among the four" in src


def test_the_precedence_that_is_real_is_first_note_wins(tmp_path):
    """`setdefault` across the sorted walk. Undocumented anywhere else, and the
    rule a reader of that loop actually needs."""
    d = tmp_path / "knowledge"
    (d / "positions").mkdir(parents=True)
    # `aaa` has no id, so its key is its stem; its TITLE slugifies to the
    # contested token. `zzz` carries that token as its own id.
    (d / "positions" / "aaa.md").write_text(
        "---\ntitle: Shared Token\n---\n\nbody\n", encoding="utf-8")
    (d / "positions" / "zzz.md").write_text(
        "---\nid: shared-token\ntitle: Zed\n---\n\nbody\n", encoding="utf-8")
    g = opg.build_graph(d)
    assert g._resolver["shared-token"] == "aaa", (
        "the first note in the sorted walk owns the token, even against another "
        "note's own id")


# ============================================================
# Finding 9 -- an unlocked read-modify-write with a sweep inside
# ============================================================

def _own_body(node: ast.AST):
    """Every node inside `node`, EXCLUDING anything in a nested function or
    class. A guard parked in dead code is not a guard."""
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef,
                              ast.ClassDef, ast.Lambda)):
            continue
        yield child
        yield from _own_body(child)


def _func(module: ast.Module, name: str) -> ast.FunctionDef:
    for node in ast.walk(module):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{name} is not defined any more")


def test_the_ack_write_takes_the_same_lock_the_autoheal_does():
    """Asserted on the AST, scoped to `cmd_ack`, never on a source substring.

    This was `'file_lock(state_dir / (ACK_FILE + ".lock")' in src` over the whole
    file, which is the wrong instrument in BOTH directions. MEASURED 2026-09-01:

      * moving the real write out from under the lock and leaving the same call
        text inside a nested function that is never called left the file GREEN
        at 56 passed - the write was unlocked and the test said it was locked;
      * re-wrapping the one live call across three lines, changing nothing at
        all, turned the file RED.

    What matters is not that the characters appear somewhere in the file. It is
    that the load-modify-save of the ack file happens INSIDE a `file_lock`
    context in `cmd_ack` itself, so that is what is asserted. `_own_body` skips
    nested definitions, so the parked-in-dead-code spelling fails here.
    """
    tree = ast.parse((ROOT / "scripts/ops-radar.py").read_text(encoding="utf-8"))
    ack = _func(tree, "cmd_ack")

    locks = [n for n in _own_body(ack)
             if isinstance(n, ast.With)
             and any(isinstance(item.context_expr, ast.Call)
                     and getattr(item.context_expr.func, "id", None) == "file_lock"
                     for item in n.items)]
    assert len(locks) == 1, (
        f"cmd_ack holds {len(locks)} file_lock blocks of its own; expected "
        "exactly one around the read-modify-write")

    def _ack_file_calls(scope):
        """Calls that touch the ACK file, found by their arguments rather than
        by callee name, so renaming the helper does not silently empty this."""
        return [n for n in _own_body(scope)
                if isinstance(n, ast.Call)
                and any(isinstance(sub, ast.Name) and sub.id == "ACK_FILE"
                        for arg in n.args for sub in ast.walk(arg))]

    under_lock = {id(n) for n in _ack_file_calls(locks[0])}
    all_touches = _ack_file_calls(ack)
    # Three: the lock's own path, the load, and the save. The whole
    # read-modify-write, not just the write - `save_json_atomic` already makes
    # the write alone atomic, and that was never what raced.
    assert len(all_touches) >= 3, (
        f"only {len(all_touches)} ACK_FILE touches found in cmd_ack; the "
        "read-modify-write this test exists for is no longer here")
    outside = [ast.unparse(n) for n in all_touches if id(n) not in under_lock]
    assert outside == [], (
        f"these ACK_FILE operations sit outside the lock and can race: {outside}")


def _stub_signal(key: str, severity: str, *, due: bool = True,
                 tier: str = "A") -> dict:
    """A signal carrying every key `ops_signals.SIGNAL_KEYS` declares.

    `cmd_ack` does not merely look the key up: it passes the swept list to
    `autoheal_signals`, which subscripts `sig["due"]` on any Tier-A entry. A
    stub that omits a field is not a smaller stub, it is a crash.
    """
    return {"key": key, "value": None, "threshold": None, "due": due,
            "severity": severity, "tier": tier, "summary": f"{key}: stub"}


def test_the_stub_signal_still_matches_the_shape_the_producers_declare():
    """So a field added to `SIGNAL_KEYS` fails here, loudly, instead of leaving
    the stub below quietly short by one again."""
    from scripts.utils.ops_signals import SIGNAL_KEYS
    assert set(_stub_signal("ollama", "warn")) == set(SIGNAL_KEYS)


def test_an_ack_is_still_written_under_the_lock(tmp_path, monkeypatch):
    """The key is pinned to a Tier-A one, and the stub is a full signal. Both
    were wrong together until 2026-08-30, and each hid the other.

    The stub returned `{"key": next(iter(opr.KNOWN_KEYS)), "severity": "warn"}`.
    A set has no order and Python randomizes string hashing per process, so the
    chosen key differed between xdist workers; only "ollama" and "memory_index"
    are read by `autoheal_signals`, so 2 of the 12 members reached `sig["due"]`
    and raised KeyError there. The test passed alone, passed in most workers,
    and failed in the full suite about one run in six. `PYTHONHASHSEED=6`
    reproduces the failing draw on the old code.

    Pinning "ollama" runs the Tier-A path on every execution rather than on a
    coin flip, which is the coverage the old version only ever had by accident.
    """
    key = "ollama"
    assert key in opr.KNOWN_KEYS and key in opr.TIER_A_TARGETS
    monkeypatch.setattr(opr, "gather_live_signals",
                        lambda e, d: [_stub_signal(key, "warn")])
    args = types.SimpleNamespace(key=key, ttl=None)
    assert opr.cmd_ack(args, tmp_path, ROOT, tmp_path) == 0
    saved = opr.load_json(tmp_path / opr.ACK_FILE)
    assert saved[key]["acked_band"] == "warn"


def test_the_ack_band_comes_from_the_sweep_and_not_from_a_default(tmp_path,
                                                                 monkeypatch):
    """`acked_band` is what makes an ack re-surface on worsening, so reading it
    off the live severity is the behaviour, not an incidental. A stub reporting
    "high" must be stored as "high"."""
    monkeypatch.setattr(opr, "gather_live_signals",
                        lambda e, d: [_stub_signal("ollama", "high")])
    args = types.SimpleNamespace(key="ollama", ttl=None)
    assert opr.cmd_ack(args, tmp_path, ROOT, tmp_path) == 0
    assert opr.load_json(tmp_path / opr.ACK_FILE)["ollama"]["acked_band"] == "high"


def test_an_autoheal_key_is_banded_from_the_synthetic_signal(tmp_path,
                                                             monkeypatch):
    """The `autoheal_signals` line, which nothing exercised.

    The synthetic `<target>_autoheal` keys are in `KNOWN_KEYS` but never in
    `gather_live_signals`, so without that line `cur` is None and the band falls
    through to "ok". `ack_suppressed` compares `severity_rank(critical) <=
    severity_rank(ok)`, which is false, so the ack silences nothing while
    reporting success - which the comment above the line says is "worse than the
    exit-2 refusal it replaced". MEASURED 2026-09-01: deleting the line left the
    file green at 56 passed.

    The escalated Tier-A state is built from real inputs rather than by stubbing
    `autoheal_signals`, so this measures the wiring and not the stub.
    """
    key = "ollama_autoheal"
    assert key in opr.KNOWN_KEYS
    monkeypatch.setattr(opr, "gather_live_signals",
                        lambda e, d: [_stub_signal("ollama", "warn", due=True)])
    escalate = opr.ops.AUTOHEAL_ESCALATE
    (tmp_path / opr.AUTOHEAL_FILE).write_text(
        json.dumps({"ollama": {"failures": escalate}}), encoding="utf-8")

    args = types.SimpleNamespace(key=key, ttl=None)
    assert opr.cmd_ack(args, tmp_path, ROOT, tmp_path) == 0
    saved = opr.load_json(tmp_path / opr.ACK_FILE)
    assert saved[key]["acked_band"] == "critical", (
        "the synthetic auto-heal signal was banded from the live list alone, "
        f"so the ack silences nothing: {saved[key]}")


def test_two_acks_in_sequence_both_survive(tmp_path, monkeypatch):
    """The lost update the lock prevents: each ack loaded the file before the
    other saved, and the window contained a full live signal sweep."""
    keys = sorted(opr.KNOWN_KEYS)[:2]
    monkeypatch.setattr(opr, "gather_live_signals", lambda e, d: [])
    for k in keys:
        opr.cmd_ack(types.SimpleNamespace(key=k, ttl=None), tmp_path, ROOT, tmp_path)
    saved = opr.load_json(tmp_path / opr.ACK_FILE)
    assert set(saved) == set(keys)


def test_an_unknown_key_still_refuses_before_touching_the_file(tmp_path, capsys):
    args = types.SimpleNamespace(key="not-a-real-signal", ttl=None)
    assert opr.cmd_ack(args, tmp_path, ROOT, tmp_path) == 2
    assert not (tmp_path / opr.ACK_FILE).exists()


# ============================================================
# Finding 8 -- REFUTED
# ============================================================

def test_the_host_list_can_never_be_empty():
    """REFUTES the report. It claims an empty host list makes `check` print
    "ollama DOWN: " and exit 1 instead of the documented exit 2. The branch is
    unreachable: `ollama_hosts_in_use` ends in `seen or [OLLAMA_HOST]`, and
    OLLAMA_HOST is a non-empty module constant, never env-derived.
    """
    from scripts.utils.ops_signals import OLLAMA_HOST, ollama_hosts_in_use
    assert OLLAMA_HOST
    assert ollama_hosts_in_use()


def test_the_fallback_constant_is_not_environment_derived():
    """What would falsify the refutation: OLLAMA_HOST becoming configurable and
    settable to an empty string."""
    src = (ROOT / "scripts/utils/ops_signals.py").read_text(encoding="utf-8")
    line = next(ln for ln in src.splitlines() if ln.startswith("OLLAMA_HOST"))
    assert "environ" not in line and "getenv" not in line
