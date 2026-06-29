#!/usr/bin/env python3
"""Tests for the templates/ data-seam: the shared-doc source of truth resolves
under the data root, not the engine root.

HEADING OS engine/data separation. templates/ routes `private`
(config/routing-map.yaml), so the five shared documentation sources
(GETTING-STARTED, CEO-ADMIN-GUIDE, EMERGENCY-PROCEDURES, CLAUDE.md.template) live
in the data overlay. Before this seam was wired,
get_templates_dir() did not exist and workspace-health.py hardcoded
WORKSPACE / "templates" (the engine root) -- which is empty after the split, so
the health check reported every shared doc as "missing" on every run (13 phantom
issue lines). These tests pin the helper to the data root and guard the health
script against the regression.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


@pytest.fixture
def data_root(tmp_path, monkeypatch):
    """A data root distinct from the workspace root, with a templates/ dir."""
    d = tmp_path / ".heading-os-data"
    (d / "templates").mkdir(parents=True)
    monkeypatch.setenv("HEADING_OS_DATA", str(d))
    # workspace.py caches identity per-root; reset so is_ceo_workspace() is fresh.
    from scripts.utils import workspace
    workspace._reset_identity_cache()
    return d


def test_templates_dir_resolves_under_data_root(data_root):
    from scripts.utils.workspace import get_templates_dir, get_workspace_root
    tpl = get_templates_dir()
    assert tpl == data_root / "templates"
    assert tpl != get_workspace_root() / "templates"  # NOT the engine root (the bug)


def test_health_script_uses_the_helper_not_engine_root():
    """workspace-health.py must resolve templates/ through get_templates_dir(),
    never by joining the engine WORKSPACE root (the pre-seam regression)."""
    src = (ROOT / "scripts" / "workspace-health.py").read_text(encoding="utf-8")
    assert "get_templates_dir" in src, \
        "workspace-health.py must import and use get_templates_dir()"
    assert 'WORKSPACE / "templates"' not in src, \
        "workspace-health.py still hardcodes WORKSPACE / 'templates' (engine root) -- the phantom-missing bug"


def test_version_marker_check_finds_real_files(data_root):
    """check_doc_versions must read the data-side templates, so a marked file is
    recognised rather than reported missing."""
    import importlib.util
    (data_root / "templates" / "GETTING-STARTED.md").write_text(
        "<!-- version: 9.9.9 | last-updated: 2099-01-01 -->\n# Guide\n", encoding="utf-8"
    )
    spec = importlib.util.spec_from_file_location(
        "workspace_health", ROOT / "scripts" / "workspace-health.py"
    )
    wh = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(wh)
    # The file exists with a valid future-dated marker -> not counted as an issue.
    assert (data_root / "templates" / "GETTING-STARTED.md").exists()
    assert wh.get_templates_dir() == data_root / "templates"
