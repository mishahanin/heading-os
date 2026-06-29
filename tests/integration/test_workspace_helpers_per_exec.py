"""Integration tests for per-exec repo path helpers in workspace.py."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from scripts.utils.workspace import (
    get_per_exec_repo_path,
    get_all_active_exec_slugs,
    get_workspace_root,
)


def test_get_per_exec_repo_path_returns_sibling_directory():
    path = get_per_exec_repo_path("sam-carter")
    workspace = get_workspace_root()
    assert path == workspace.parent / "31c-crm-sam-carter"


def test_get_per_exec_repo_path_handles_arbitrary_slug():
    path = get_per_exec_repo_path("test-slug")
    assert path.name == "31c-crm-test-slug"
    assert path.parent == get_workspace_root().parent


def test_get_per_exec_repo_path_rejects_invalid_slug():
    import pytest
    with pytest.raises(ValueError):
        get_per_exec_repo_path("")
    with pytest.raises(ValueError):
        get_per_exec_repo_path("../escape")
    with pytest.raises(ValueError):
        get_per_exec_repo_path("path/traversal")
    with pytest.raises(ValueError):
        get_per_exec_repo_path("back\\slash")


def test_get_all_active_exec_slugs_excludes_admin(monkeypatch):
    """Verify admin-role execs are excluded from the active list."""
    from scripts.utils import workspace as ws_module
    fake_registry = {
        "version": "test",
        "executives": [
            {"slug": "ceo-test", "role": "admin", "status": "active"},
            {"slug": "exec-test", "role": "exec", "status": "active"},
        ],
    }
    monkeypatch.setattr(ws_module, "load_exec_registry", lambda: fake_registry)
    slugs = get_all_active_exec_slugs()
    assert "ceo-test" not in slugs
    assert "exec-test" in slugs


def test_get_all_active_exec_slugs_excludes_inactive(monkeypatch):
    """Verify inactive-status execs are filtered out."""
    from scripts.utils import workspace as ws_module
    fake_registry = {
        "version": "test",
        "executives": [
            {"slug": "active-exec", "role": "exec", "status": "active"},
            {"slug": "offboarded-exec", "role": "exec", "status": "offboarded"},
            {"slug": "pending-exec", "role": "exec", "status": "pending"},
        ],
    }
    monkeypatch.setattr(ws_module, "load_exec_registry", lambda: fake_registry)
    slugs = get_all_active_exec_slugs()
    assert "active-exec" in slugs
    assert "offboarded-exec" not in slugs
    assert "pending-exec" not in slugs


def test_get_all_active_exec_slugs_returns_sorted():
    slugs = get_all_active_exec_slugs()
    assert slugs == sorted(slugs)


if __name__ == "__main__":
    print("Direct invocation mode: 3 of 6 tests run (excludes_admin + excludes_inactive + rejects_invalid_slug require pytest fixtures).")
    print("For full coverage, use: pytest tests/integration/test_workspace_helpers_per_exec.py")
    print()
    test_get_per_exec_repo_path_returns_sibling_directory()
    test_get_per_exec_repo_path_handles_arbitrary_slug()
    test_get_all_active_exec_slugs_returns_sorted()
    print("Direct OK (3 of 6); run `pytest` for full 6/6.")
