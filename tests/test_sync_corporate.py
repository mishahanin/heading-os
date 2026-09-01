"""CAP-3: corporate content is consumed by reading a gitignored heading-os-corporate
clone (.corporate-repo/) directly. The CEO consumes nothing (it publishes UP).

These pin the seam script (scripts/sync-corporate.py) and the get_corporate_root()
resolution it pairs with: CEO -> no-op; exec -> .corporate-repo/ clone, read in place.
"""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _load_sync_corporate():
    spec = importlib.util.spec_from_file_location(
        "sync_corporate_mod", ROOT / "scripts" / "sync-corporate.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _make_exec_engine(tmp_path, slug="jane-doe"):
    engine = tmp_path / ".heading-os"
    (engine / ".claude").mkdir(parents=True)
    (engine / "CLAUDE.md").write_text("x", encoding="utf-8")
    (engine / ".workspace-identity.json").write_text(
        json.dumps({"role": "exec", "slug": slug, "type": "exec-workspace", "org": "31c"}),
        encoding="utf-8",
    )
    return engine


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.delenv("HEADING_OS_DATA", raising=False)
    from scripts.utils import workspace
    workspace._reset_identity_cache()
    yield
    workspace._reset_identity_cache()


def test_ceo_is_noop(tmp_path, monkeypatch):
    engine = tmp_path / ".heading-os"
    (engine / ".claude").mkdir(parents=True)
    (engine / "CLAUDE.md").write_text("x", encoding="utf-8")
    monkeypatch.setenv("WORKSPACE_ROOT", str(engine))
    mod = _load_sync_corporate()
    res = mod.sync_corporate(dry_run=True)
    assert res["status"] == "noop"
    assert res["action"] == "none"


def test_exec_corporate_root_is_the_clone(tmp_path, monkeypatch):
    engine = _make_exec_engine(tmp_path)
    monkeypatch.setenv("WORKSPACE_ROOT", str(engine))
    from scripts.utils.workspace import get_corporate_root, get_workspace_root
    cr = get_corporate_root()
    assert cr.name == ".corporate-repo"
    assert cr.parent == get_workspace_root()


def test_exec_dry_run_plans_clone(tmp_path, monkeypatch):
    engine = _make_exec_engine(tmp_path)
    monkeypatch.setenv("WORKSPACE_ROOT", str(engine))
    mod = _load_sync_corporate()
    res = mod.sync_corporate(dry_run=True)
    assert res["status"] == "dry-run"
    assert res["action"] == "clone"  # no .git in .corporate-repo yet
    assert res["path"].endswith(".corporate-repo")


def test_exec_dry_run_plans_a_pull_once_the_clone_exists(tmp_path, monkeypatch):
    """The other arm of the dry-run branch, which nothing exercised.

    Only the clone arm was covered, so `action` could have been the constant
    "clone" and the file stayed green.
    """
    engine = _make_exec_engine(tmp_path)
    (engine / ".corporate-repo" / ".git").mkdir(parents=True)
    monkeypatch.setenv("WORKSPACE_ROOT", str(engine))
    mod = _load_sync_corporate()
    res = mod.sync_corporate(dry_run=True)
    assert res["status"] == "dry-run"
    assert res["action"] == "pull"


# ============================================================
# The refusals: every one of them a documented fix with no test
# ============================================================
#
# MEASURED 2026-09-01, one mutation at a time against this file: the no-org
# refusal, the interrupted-clone refusal, the missing-executable handler and
# `main`'s exit code could each be removed with the whole file green. Nothing
# below starts a network operation: two branches return before any subprocess,
# and the two that would spawn one are driven with an empty PATH, so `git` and
# `gh` are not findable and no remote can be reached.

def _no_path(monkeypatch, tmp_path):
    """Point PATH at an empty directory: no `git`, no `gh`, no network."""
    empty = tmp_path / "empty-bin"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))


def test_a_missing_org_refuses_before_it_builds_a_nonsense_target(tmp_path, monkeypatch):
    """`load_github_org()` returns "" on a fresh clone, and the clone then
    targeted "/heading-os-corporate"."""
    engine = _make_exec_engine(tmp_path)
    monkeypatch.setenv("WORKSPACE_ROOT", str(engine))
    _no_path(monkeypatch, tmp_path)
    mod = _load_sync_corporate()
    monkeypatch.setattr(mod, "load_github_org", lambda: "")
    res = mod.sync_corporate()
    assert res["status"] == "error"
    assert res["action"] == "none"
    assert "operator.yaml" in res["message"]


def test_an_interrupted_clone_is_named_rather_than_retried(tmp_path, monkeypatch):
    """A directory with no `.git` is the residue of a Ctrl+C'd clone, and
    `gh repo clone` refuses a non-empty target, so the seam stayed bricked with
    an error that never said why."""
    engine = _make_exec_engine(tmp_path)
    (engine / ".corporate-repo").mkdir(parents=True)
    (engine / ".corporate-repo" / "leftover.txt").write_text("x", encoding="utf-8")
    monkeypatch.setenv("WORKSPACE_ROOT", str(engine))
    _no_path(monkeypatch, tmp_path)
    mod = _load_sync_corporate()
    monkeypatch.setattr(mod, "load_github_org", lambda: "example-org")
    res = mod.sync_corporate()
    assert res["status"] == "error"
    assert res["action"] == "clone"
    assert "not a git clone" in res["message"]
    # It refused rather than ran: the residue is untouched.
    assert (engine / ".corporate-repo" / "leftover.txt").exists()


def test_a_machine_without_git_gets_a_result_not_a_traceback(tmp_path, monkeypatch):
    """The pull arm. Only `TimeoutExpired` was caught, so FileNotFoundError went
    straight past the exit-code contract and `--json` printed nothing."""
    engine = _make_exec_engine(tmp_path)
    (engine / ".corporate-repo" / ".git").mkdir(parents=True)
    monkeypatch.setenv("WORKSPACE_ROOT", str(engine))
    _no_path(monkeypatch, tmp_path)
    mod = _load_sync_corporate()
    monkeypatch.setattr(mod, "load_github_org", lambda: "example-org")
    res = mod.sync_corporate()
    assert res["status"] == "error"
    assert res["action"] == "pull"
    assert "git" in res["message"] and "PATH" in res["message"]


def test_a_machine_without_gh_gets_a_result_not_a_traceback(tmp_path, monkeypatch):
    """The clone arm of the same shape."""
    engine = _make_exec_engine(tmp_path)
    monkeypatch.setenv("WORKSPACE_ROOT", str(engine))
    _no_path(monkeypatch, tmp_path)
    mod = _load_sync_corporate()
    monkeypatch.setattr(mod, "load_github_org", lambda: "example-org")
    res = mod.sync_corporate()
    assert res["status"] == "error"
    assert res["action"] == "clone"
    assert "gh" in res["message"] and "PATH" in res["message"]
    assert not (engine / ".corporate-repo").exists(), "nothing may be cloned"


def test_the_exit_code_tells_a_failure_from_a_success(tmp_path, monkeypatch, capsys):
    """The file's stated contract: 0 on success or CEO no-op, 1 on failure.

    `main` returning a constant 0 left the file green, so a headless caller in
    `setup.py` or `/sync` could not tell a bricked seam from a working one.
    """
    engine = _make_exec_engine(tmp_path)
    monkeypatch.setenv("WORKSPACE_ROOT", str(engine))
    _no_path(monkeypatch, tmp_path)
    mod = _load_sync_corporate()
    monkeypatch.setattr(mod, "load_github_org", lambda: "")

    monkeypatch.setattr(sys, "argv", ["sync-corporate.py", "--json"])
    assert mod.main() == 1
    assert json.loads(capsys.readouterr().out)["status"] == "error"

    monkeypatch.setattr(sys, "argv", ["sync-corporate.py", "--json", "--dry-run"])
    assert mod.main() == 0
    assert json.loads(capsys.readouterr().out)["status"] == "dry-run"
