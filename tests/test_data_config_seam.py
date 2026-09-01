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
    """The fleet registry is `admin/executives.json`, not `config/exec-registry.json`.

    Corrected 2026-08-23. The seam was right - the registry does live under the
    data root - but the FILENAME was the retired one, and no such file exists on
    any machine. `load_exec_registry` therefore always returned an empty fleet,
    and this test passed because it created the file it was about to read.
    """
    (data_root / "admin").mkdir(parents=True, exist_ok=True)
    (data_root / "admin" / "executives.json").write_text(
        json.dumps({"executives": [{"slug": "a", "status": "active", "role": "exec"}]}),
        encoding="utf-8",
    )
    assert len(load_exec_registry_fresh().get("executives", [])) == 1


def test_exec_registry_ignores_the_retired_config_path(data_root):
    """A leftover `config/exec-registry.json` must not resurrect the old model."""
    (data_root / "config" / "exec-registry.json").write_text(
        json.dumps({"executives": [{"slug": "ghost", "status": "active",
                                    "role": "exec"}]}),
        encoding="utf-8",
    )
    assert load_exec_registry_fresh().get("executives", []) == []


def load_exec_registry_fresh():
    from scripts.utils.workspace import load_exec_registry
    return load_exec_registry()


def test_an_undecodable_admin_config_degrades_and_says_so(data_root, capsys):
    """`load_admin_config`'s `UnicodeDecodeError` arm, with no case on it.

    The handler catches the triple `(json.JSONDecodeError, OSError,
    UnicodeDecodeError)` and the docstring argues at length for why an
    unreadable file must not be answered in silence. Measured 2026-09-01:
    dropping `UnicodeDecodeError` from that tuple left this file and all 18
    files naming these loaders green.

    It escapes the other two because it is neither. A decode failure happens
    inside `read_text()`, BEFORE `json.loads` is reached, so `JSONDecodeError`
    never applies; and `UnicodeDecodeError` subclasses `ValueError`, not
    `OSError`, so the file-error clause walks past it. One non-UTF-8 byte in a
    hand-edited `admin.json` would therefore raise out of a loader that promises
    `{}`, and `load_github_org` and `get_admin_slugs` sit directly on it.

    Two sibling loaders in the same module -- `_read_registry_or_empty` and
    `get_workspace_identity` -- carry the identical triple, so this is the
    third copy of one decision and the only one this file could see.
    """
    from scripts.utils.workspace import load_admin_config
    (data_root / "config" / "admin.json").write_bytes(b'{"github_org": "\xff31c"}')

    assert load_admin_config() == {}
    assert "could not be read" in capsys.readouterr().err, (
        "the loader degraded in silence; an operator with a corrupt admin.json "
        "would see default admin gating and be told nothing"
    )


def test_loaders_degrade_when_data_config_absent(data_root):
    """Missing config-data files degrade to empty, never crash."""
    from scripts.utils.workspace import load_admin_config, load_exec_registry
    assert load_admin_config() == {}
    assert load_exec_registry().get("executives", []) == []  # empty default, no crash
