"""CAP-1: on an exec workspace, backup targets the WRITABLE data overlay
(heading-os-data-{slug}), never the READ-ONLY engine clone.

push-all.py's exec branch resolves the push target via get_exec_data_root() and
refuses if that collapses onto the engine root. These tests pin the resolver
invariants that branch depends on: the engine clone is never the backup target,
and the CEO path is unaffected (the exec branch does not fire for ceo-master).
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


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


def test_exec_backup_target_is_data_overlay_not_engine(tmp_path, monkeypatch):
    engine = _make_exec_engine(tmp_path, slug="jane-doe")
    data = tmp_path / ".heading-os-data-jane-doe"
    (data / "crm" / "contacts").mkdir(parents=True)
    monkeypatch.setenv("WORKSPACE_ROOT", str(engine))

    from scripts.utils.workspace import (
        is_exec_workspace, get_exec_data_root, get_workspace_root,
    )
    assert is_exec_workspace() is True
    target = get_exec_data_root()
    assert target == data.resolve()
    # the engine clone is never the backup target, and the data overlay is a
    # sibling, never nested inside the engine clone
    assert target != get_workspace_root()
    assert get_workspace_root() not in target.parents


def test_exec_misconfig_when_data_equals_engine_is_detectable(tmp_path, monkeypatch):
    """If no data sibling exists, get_exec_data_root() falls back to get_data_root().
    The push-all exec branch refuses when that collapses onto the engine root; this
    asserts the two are distinguishable in the normal (sibling-present) layout."""
    engine = _make_exec_engine(tmp_path, slug="jane-doe")
    data = tmp_path / ".heading-os-data-jane-doe"
    (data / "crm" / "contacts").mkdir(parents=True)
    monkeypatch.setenv("WORKSPACE_ROOT", str(engine))
    from scripts.utils.workspace import get_exec_data_root, get_workspace_root
    assert get_exec_data_root() != get_workspace_root()


def test_ceo_path_unchanged(tmp_path, monkeypatch):
    # No identity file -> ceo-master default; the exec branch must NOT fire.
    engine = tmp_path / ".heading-os"
    (engine / ".claude").mkdir(parents=True)
    (engine / "CLAUDE.md").write_text("x", encoding="utf-8")
    monkeypatch.setenv("WORKSPACE_ROOT", str(engine))
    from scripts.utils.workspace import is_exec_workspace, is_ceo_workspace
    assert is_exec_workspace() is False
    assert is_ceo_workspace() is True
