"""The fleet dashboard must report a signal that something actually produces.

Until 2026-08-23 `scripts/admin-health.py` sourced its whole `Last Sync` column
from `<exec repo>/.heartbeat.json`, and that file has three independent reasons
it can never arrive:

  1. No script in either repo writes it. `grep -rn '\\.heartbeat\\.json'` returns
     the reader and one other hit, and that other hit is the provisioner.
  2. That provisioner hit puts the name INSIDE the `.gitignore` it writes into
     every exec workspace, so a file written by hand would never be committed
     and never reach the CEO's pull.
  3. `calculate_status` read the key `timestamp` while every dict built in the
     same module wrote `last_sync`, so even a delivered heartbeat scored DEAD.

Measured on the live fleet the day this test was written: three active execs,
zero heartbeat files, every row `DEAD / never / unknown`. A dashboard whose
every row reads DEAD teaches its reader to ignore it.

The replacement is the last commit in the exec's own data overlay, which is
git-native (the sync daemon was retired in favour of git) and needs no new
file, no new writer and no daemon. `.claude/rules/scope-claims.md` is why the
column is named "Last Commit" and not "Last Sync": a commit timestamp
establishes that they committed, not that a sync handshake completed.
"""
from __future__ import annotations

import ast
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "admin-health.py"


def code_strings(path: Path) -> list[str]:
    """Every string literal the module EXECUTES, with docstrings excluded.

    A plain substring scan of the file cannot tell a live path from a docstring
    explaining why that path was removed, and the explanation is the part worth
    keeping. Comments never reach the AST, so they drop out for free.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef,
                                 ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body = getattr(node, "body", None)
        if (body and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)):
            docstrings.add(id(body[0].value))
    return [n.value for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
            and id(n) not in docstrings]


def _load():
    spec = importlib.util.spec_from_file_location("admin_health", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["admin_health"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def mod():
    return _load()


def _git_repo(path: Path, when: str) -> Path:
    """A real git repo with one commit at a controlled author/committer date."""
    path.mkdir(parents=True, exist_ok=True)
    env = {
        "GIT_AUTHOR_DATE": when, "GIT_COMMITTER_DATE": when,
        "GIT_AUTHOR_NAME": "T", "GIT_AUTHOR_EMAIL": "t@example.com",
        "GIT_COMMITTER_NAME": "T", "GIT_COMMITTER_EMAIL": "t@example.com",
        "HOME": str(path), "PATH": "/usr/bin:/bin",
    }
    subprocess.run(["git", "init", "-q", "-b", "main", str(path)], check=True, env=env)
    (path / "f.md").write_text("x", encoding="utf-8")
    subprocess.run(["git", "-C", str(path), "add", "f.md"], check=True, env=env)
    subprocess.run(["git", "-C", str(path), "commit", "-q", "-m", "c"], check=True, env=env)
    return path


# --- the dead heartbeat must not come back -----------------------------------

def test_the_dashboard_no_longer_reads_the_gitignored_heartbeat_file():
    """Reason 1 and 2 above. Reading it is the defect, so no code may name it."""
    offenders = [s for s in code_strings(SCRIPT) if ".heartbeat.json" in s]
    assert not offenders, (
        f"admin-health.py names .heartbeat.json in executable code: {offenders}. "
        "No script writes that file, and scripts/provision-exec.py gitignores it "
        "in every exec workspace, so the column it feeds can only read 'never'."
    )


def test_the_provisioner_still_gitignores_it_so_the_reader_could_not_have_worked():
    """Pins the evidence, so a future reader does not have to re-derive it."""
    gitignore_writer = (ROOT / "scripts" / "provision-exec.py").read_text(encoding="utf-8")
    assert ".heartbeat.json\\n" in gitignore_writer or ".heartbeat.json" in gitignore_writer


def test_status_and_the_reported_field_read_the_same_key(mod):
    """Reason 3: two names for one concept inside one module scored every row DEAD."""
    rec = {"slug": "x", "last_commit": "2026-08-23T10:00:00+00:00"}
    status, _, ago = mod.calculate_status(rec)
    assert status != "DEAD", f"a commit made today scored {status} ({ago})"


# --- the replacement signal ---------------------------------------------------

def test_last_commit_is_read_from_the_exec_repo(mod, tmp_path):
    repo = _git_repo(tmp_path / "repo", "2026-08-20T09:00:00 +0000")
    assert mod.read_last_commit(repo).startswith("2026-08-20T")


def test_a_repo_with_no_commits_reports_none_not_a_crash(mod, tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(empty)], check=True)
    assert mod.read_last_commit(empty) is None


def test_a_path_that_is_not_a_repo_reports_none(mod, tmp_path):
    assert mod.read_last_commit(tmp_path / "nothing-here") is None


def test_an_absent_commit_is_dead_and_says_never(mod):
    status, _, ago = mod.calculate_status({"slug": "x", "last_commit": None})
    assert (status, ago) == ("DEAD", "never")


def test_a_garbage_timestamp_is_dead_and_says_so(mod):
    status, _, ago = mod.calculate_status({"slug": "x", "last_commit": "not-a-date"})
    assert (status, ago) == ("DEAD", "invalid")


# --- thresholds match a commit cadence, not a 60-second heartbeat -------------

def test_the_thresholds_are_scaled_for_commits_not_heartbeats(mod):
    """A person commits daily at best. The old 2-hour OK band called everyone STALE."""
    assert mod.OK_THRESHOLD >= 3 * 86400, (
        "OK_THRESHOLD is still heartbeat-sized; a human committing once a day "
        "would show STALE every morning."
    )
    assert mod.STALE_THRESHOLD > mod.OK_THRESHOLD


# --- facts now come from the registries, not from a file that never arrives ---

def test_platform_and_title_come_from_the_fleet_join(mod, monkeypatch):
    monkeypatch.setattr(mod, "load_fleet", lambda: [{
        "slug": "jane-doe", "name": "Jane Doe", "title": "Chief Strategy Officer",
        "platform": "darwin", "provisioning_status": "active",
        "is_business_exec": True, "is_heading_os_user": True,
    }])
    [row] = mod.enrich_with_registry([{"slug": "jane-doe", "last_commit": None}])
    assert row["name"] == "Jane Doe"
    assert row["title"] == "Chief Strategy Officer"
    assert row["platform"] == "darwin"
    assert row["registry_status"] == "active"


def test_someone_absent_from_both_registries_is_labelled_not_guessed(mod, monkeypatch):
    monkeypatch.setattr(mod, "load_fleet", list)
    [row] = mod.enrich_with_registry([{"slug": "ghost", "last_commit": None}])
    assert row["registry_status"] == "unregistered"
    assert row["platform"] == "unknown"


# --- the clone target must be the repo that exists ----------------------------

def test_the_clone_target_is_not_the_retired_crm_repo_name():
    """`31c-crm-{slug}` is the retired model. Cloning it 404s; the roster holds
    the real name in `data_repo`."""
    offenders = [s for s in code_strings(SCRIPT) if "31c-crm-" in s]
    assert not offenders, (
        f"admin-health.py builds a retired 31c-crm repo name: {offenders}. The live "
        "name is in the roster's data_repo field (heading-os-data-{slug})."
    )


def test_the_clone_name_comes_from_the_roster(mod, monkeypatch):
    monkeypatch.setattr(mod, "load_fleet", lambda: [
        {"slug": "jane-doe", "data_repo": "heading-os-data-jane-doe"},
    ])
    assert mod.repo_name_for("jane-doe") == "heading-os-data-jane-doe"


def test_a_missing_data_repo_field_falls_back_to_the_convention(mod, monkeypatch):
    monkeypatch.setattr(mod, "load_fleet", lambda: [{"slug": "jane-doe"}])
    assert mod.repo_name_for("jane-doe") == "heading-os-data-jane-doe"


# --- the retired aggregation repo must not be named as the source -------------

def test_the_dashboard_does_not_claim_to_read_crm_central():
    """crm-central was the retired aggregation repo. Printing it as the source
    sends a reader looking in a place the data was never in."""
    offenders = [s for s in code_strings(SCRIPT) if "crm-central" in s]
    assert not offenders, f"admin-health.py still prints crm-central: {offenders}"


# --- the json surface stays machine-readable ----------------------------------

def test_json_output_reports_the_field_it_scored(mod, monkeypatch, capsys):
    monkeypatch.setattr(mod, "load_fleet", list)
    rows = mod.enrich_with_registry([
        {"slug": "a", "last_commit": "2026-08-23T10:00:00+00:00", "contact_count": 3},
    ])
    mod.output_json(rows, shared_contacts=0)
    payload = json.loads(capsys.readouterr().out)
    [row] = payload["executives"]
    assert row["last_commit"] == "2026-08-23T10:00:00+00:00"
    assert "last_sync" not in row, "the old key name outlived the field it named"
