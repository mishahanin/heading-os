#!/usr/bin/env python3
"""Tests for the config-DATA seam: instance config files resolve under the data
root, not the engine root.

HEADING OS engine/data separation. A handful of config/ files carry real
per-instance data (admin.json, exec-registry.json, email-triage-rules.yaml,
service-manifest.json, x-pulse-accounts.yaml). They route private and live in
the data overlay; their loaders must resolve under get_data_root()/config so a
data-less engine clone reads them from the .heading-os-data sibling instead of
finding them absent (the third cutover-bug class fixed in Phase 2 foundation).
Engine config (routing-map.yaml, schemas/, tool-risk.json, wizard-*) stays on
the engine root via get_config_dir().
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


@pytest.fixture
def data_root(tmp_path, monkeypatch):
    """A data root distinct from the workspace root, with a config/ dir."""
    d = tmp_path / ".heading-os-data"
    (d / "config").mkdir(parents=True)
    monkeypatch.setenv("HEADING_OS_DATA", str(d))
    # workspace.py caches identity per-root; reset so is_ceo_workspace() is fresh.
    from scripts.utils import workspace
    workspace._reset_identity_cache()
    return d


def test_data_config_dir_resolves_under_data_root(data_root):
    from scripts.utils.workspace import get_data_config_dir, get_workspace_root
    cfg = get_data_config_dir()
    assert cfg == data_root / "config"
    assert cfg != get_workspace_root() / "config"  # NOT the engine root


def test_engine_config_dir_stays_on_engine_root(data_root):
    """get_config_dir() must remain pinned to the engine root for shareable
    config even when a data root is set."""
    from scripts.utils.workspace import get_config_dir, get_workspace_root
    assert get_config_dir() == get_workspace_root() / "config"


def test_admin_config_loads_from_data_root(data_root):
    from scripts.utils.workspace import load_admin_config
    (data_root / "config" / "admin.json").write_text(
        json.dumps({"owner": "test"}), encoding="utf-8"
    )
    assert load_admin_config() == {"owner": "test"}


def test_exec_registry_loads_from_data_root(data_root):
    from scripts.utils.workspace import load_exec_registry
    (data_root / "config" / "exec-registry.json").write_text(
        json.dumps({"executives": [{"slug": "a", "status": "active", "role": "exec"}]}),
        encoding="utf-8",
    )
    assert len(load_exec_registry().get("executives", [])) == 1


def test_loaders_degrade_when_data_config_absent(data_root):
    """Missing config-data files degrade to empty, never crash."""
    from scripts.utils.workspace import load_admin_config, load_exec_registry
    assert load_admin_config() == {}
    assert load_exec_registry().get("executives", []) == []  # empty default, no crash
