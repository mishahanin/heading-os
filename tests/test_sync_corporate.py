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
