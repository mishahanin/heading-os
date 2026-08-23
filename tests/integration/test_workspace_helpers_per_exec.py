"""One exec-repo topology, pinned in one place.

Rewritten 2026-08-23. The audit found this file and
`tests/integration/test_aggregate_crm_per_exec.py` asserting two different
fleet layouts, both green:

- here: `assert path == workspace.parent / "31c-crm-marlow-carter"`
- there: "The legacy 31c-crm-{slug} model is retired", testing
  `.heading-os-data-{slug}`

Measured, the second is the shipped one. `scripts/aggregate-crm.py` clones
`heading-os-data-{slug}` and reads the fleet from
`<data-root>/admin/executives.json`; `provision_exec.py` and the data-root seam
use the same dotted sibling name. What the engine helper returned was the
retired name, and `scripts/utils/workspace.load_exec_registry` read
`config/exec-registry.json`, a path that exists nowhere - so it always returned
an empty fleet and `admin-health.py` and `transfer-contact.py` saw no execs at
all, silently.

Both helpers now speak the live topology, and the last test here pins the two
implementations against each other so they cannot drift apart again.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from scripts.utils.workspace import (  # noqa: E402
    get_all_active_exec_slugs,
    get_per_exec_repo_path,
    get_workspace_root,
    load_exec_registry,
)

LIVE_PREFIX = ".heading-os-data-"
RETIRED_PREFIX = "31c-crm-"


def test_the_per_exec_path_is_the_dotted_data_overlay_sibling():
    path = get_per_exec_repo_path("marlow-carter")
    assert path == get_workspace_root().parent / f"{LIVE_PREFIX}marlow-carter"


def test_the_retired_naming_is_gone():
    """The specific regression: `31c-crm-{slug}` must not come back."""
    path = get_per_exec_repo_path("marlow-carter")
    assert RETIRED_PREFIX not in path.name, path


def test_the_per_exec_path_handles_an_arbitrary_slug():
    path = get_per_exec_repo_path("test-slug")
    assert path.name == f"{LIVE_PREFIX}test-slug"
    assert path.parent == get_workspace_root().parent


@pytest.mark.parametrize("bad", ["", "../escape", "path/traversal", "back\\slash"])
def test_the_per_exec_path_rejects_an_unsafe_slug(bad: str):
    with pytest.raises(ValueError):
        get_per_exec_repo_path(bad)


def test_the_registry_is_read_from_the_data_overlay(monkeypatch, tmp_path):
    """It read `config/exec-registry.json`, which exists nowhere."""
    from scripts.utils import workspace as ws

    registry_dir = tmp_path / "admin"
    registry_dir.mkdir()
    (registry_dir / "executives.json").write_text(
        '{"version": 1, "executives": [{"slug": "probe", "role": "exec", '
        '"status": "active"}]}',
        encoding="utf-8",
    )
    monkeypatch.setattr(ws, "get_data_root", lambda: tmp_path)
    assert [e["slug"] for e in ws.load_exec_registry()["executives"]] == ["probe"]


def test_an_absent_registry_is_an_empty_fleet_not_an_error(monkeypatch, tmp_path):
    """A data-less engine clone has no fleet, and that is not a failure."""
    from scripts.utils import workspace as ws

    monkeypatch.setattr(ws, "get_data_root", lambda: tmp_path)
    assert ws.load_exec_registry()["executives"] == []


def test_the_live_registry_is_actually_readable():
    """The whole defect was a loader that silently returned nobody."""
    registry = load_exec_registry()
    assert isinstance(registry.get("executives"), list)


def test_active_slugs_exclude_admin(monkeypatch):
    from scripts.utils import workspace as ws_module

    monkeypatch.setattr(ws_module, "load_exec_registry", lambda: {
        "version": "test",
        "executives": [
            {"slug": "ceo-test", "role": "admin", "status": "active"},
            {"slug": "exec-test", "role": "exec", "status": "active"},
        ],
    })
    slugs = get_all_active_exec_slugs()
    assert "ceo-test" not in slugs
    assert "exec-test" in slugs


def test_active_slugs_exclude_inactive(monkeypatch):
    from scripts.utils import workspace as ws_module

    monkeypatch.setattr(ws_module, "load_exec_registry", lambda: {
        "version": "test",
        "executives": [
            {"slug": "active-exec", "role": "exec", "status": "active"},
            {"slug": "offboarded-exec", "role": "exec", "status": "offboarded"},
            {"slug": "pending-exec", "role": "exec", "status": "pending"},
        ],
    })
    slugs = get_all_active_exec_slugs()
    assert slugs == ["active-exec"]


def test_active_slugs_are_sorted():
    slugs = get_all_active_exec_slugs()
    assert slugs == sorted(slugs)


def test_the_two_implementations_agree_on_the_layout():
    """The contract test the audit asked for.

    `scripts/aggregate-crm.py` keeps its own root-parameterised copy for
    testability. One name, two functions, is exactly how the topologies drifted
    apart; this pins them together.
    """
    import importlib.util

    root = Path(__file__).resolve().parent.parent.parent
    spec = importlib.util.spec_from_file_location(
        "_aggregate_crm_under_test", root / "scripts" / "aggregate-crm.py")
    agg = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(agg)

    workspace = get_workspace_root()
    for slug in ("marlow-carter", "rowan-ashby", "a"):
        assert (agg.get_per_exec_repo_path_for_workspace(workspace, slug)
                == get_per_exec_repo_path(slug)), slug
