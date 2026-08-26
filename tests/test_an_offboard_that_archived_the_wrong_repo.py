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

import importlib.util
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
    return types.SimpleNamespace(calls=calls, table=table)


def _archived(calls):
    return [c[3] for c in calls if c[:3] == ["gh", "repo", "archive"]]


def test_the_current_data_overlay_is_the_repo_that_gets_archived(gh, capsys):
    assert ofb.archive_workspace_repo("marlow-carter") is True
    assert f"{ofb.GITHUB_ORG}/heading-os-data-marlow-carter" in _archived(gh.calls)


def test_the_retired_name_is_still_attempted(gh, capsys):
    """Same reasoning `exec_repos` records: a 404 on a retired name costs one
    request; missing a repo the exec still holds costs their access to it."""
    ofb.archive_workspace_repo("marlow-carter")
    assert f"{ofb.GITHUB_ORG}/31c-workspace-marlow-carter" in _archived(gh.calls)


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


def test_the_per_exec_crm_repo_still_uses_the_retired_name_only(gh, capsys):
    """Correct, and deliberate: the current model has no separate CRM repo, so
    a 404 there is the expected answer."""
    gh.table[("archive",)] = _cp(1, stderr="HTTP 404: Not Found")
    assert ofb.archive_per_exec_crm_repo("marlow-carter") is True
    assert _archived(gh.calls) == [f"{ofb.GITHUB_ORG}/31c-crm-marlow-carter"]


def test_a_failed_archive_reaches_the_verdict(gh):
    """It used to be discarded at the call site, so it could not stop the run
    printing "Offboarding complete"."""
    complete, reasons = ofb.offboard_verdict(True, True, [], [], archived=False)
    assert complete is False
    assert any("could not be archived" in r for r in reasons)


@pytest.fixture
def main_env(monkeypatch, tmp_path):
    """`main()` with every external step stubbed, so only the wiring is under
    test. The verdict plumbing is the point: a failed archive has to travel from
    the archive step to the exit code."""
    monkeypatch.setattr(sys, "argv", ["offboard-exec.py", "--exec", "marlow-carter"])
    for name, value in (
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
        ("log_offboarding", lambda slug, info, to: None),
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
    assert any(r == f"team membership in {ofb.GITHUB_ORG}/leadership"
               for r in residual)


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

def test_the_ack_write_takes_the_same_lock_the_autoheal_does():
    src = (ROOT / "scripts/ops-radar.py").read_text(encoding="utf-8")
    assert 'file_lock(state_dir / (ACK_FILE + ".lock")' in src


def test_an_ack_is_still_written_under_the_lock(tmp_path, monkeypatch):
    monkeypatch.setattr(opr, "gather_live_signals",
                        lambda e, d: [{"key": next(iter(opr.KNOWN_KEYS)),
                                       "severity": "warn"}])
    key = next(iter(opr.KNOWN_KEYS))
    args = types.SimpleNamespace(key=key, ttl=None)
    assert opr.cmd_ack(args, tmp_path, ROOT, tmp_path) == 0
    saved = opr.load_json(tmp_path / opr.ACK_FILE)
    assert saved[key]["acked_band"] == "warn"


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
