"""Regression: an exec workspace must resolve PERSONAL data to its sibling data
repo, never into the engine clone.

Bug (2026-06-26): the new provisioning model (admin/provision/provision_exec.py)
gives an exec the same topology as the CEO -- an engine clone plus a sibling
data repo (`../.heading-os-data-{slug}`, or a generically-named
`../.heading-os-data`). But scripts/utils/workspace.get_personal_root() still
used the retired two-layer model and hard-coded `engine/personal` for execs, so
`/crm` read CRM contacts out of the engine tree (empty -> "no contacts") instead
of the data repo where they actually live. The CEO branch already routed through
get_data_root(); the exec branch was never migrated.

The forbidden workaround that surfaced in the field was a local symlink
(`ln -s ../../.heading-os-data/crm engine/personal/crm`). Symlinks are
categorically banned; the fix is in the resolver. These tests pin the resolver.
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _make_exec_engine(tmp_path, slug="jane-doe"):
    """Build a minimal exec engine clone (markers + exec identity) and point the
    resolver at it via WORKSPACE_ROOT."""
    engine = tmp_path / ".heading-os"
    (engine / ".claude").mkdir(parents=True)
    (engine / "CLAUDE.md").write_text("x", encoding="utf-8")
    (engine / ".workspace-identity.json").write_text(
        json.dumps({"role": "exec", "slug": slug, "type": "exec-workspace", "org": "31c"}),
        encoding="utf-8",
    )
    return engine


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("HEADING_OS_DATA", raising=False)
    from scripts.utils import workspace
    workspace._reset_identity_cache()
    yield
    workspace._reset_identity_cache()


def test_exec_slug_named_sibling_is_the_personal_root(tmp_path, monkeypatch):
    """provision_exec.py default: `../.heading-os-data-{slug}`."""
    engine = _make_exec_engine(tmp_path, slug="jane-doe")
    data = tmp_path / ".heading-os-data-jane-doe"
    (data / "crm" / "contacts").mkdir(parents=True)
    monkeypatch.setenv("WORKSPACE_ROOT", str(engine))

    from scripts.utils.workspace import get_personal_root, get_crm_contacts_dir
    assert get_personal_root() == data.resolve()
    assert get_crm_contacts_dir() == data.resolve() / "crm" / "contacts"
    # Must NOT resolve into the engine clone.
    assert get_personal_root() != engine / "personal"


def test_exec_generic_sibling_is_the_personal_root(tmp_path, monkeypatch):
    """Dima's actual layout: data repo cloned as generic `../.heading-os-data`."""
    engine = _make_exec_engine(tmp_path, slug="jane-doe")
    data = tmp_path / ".heading-os-data"
    (data / "crm" / "contacts").mkdir(parents=True)
    monkeypatch.setenv("WORKSPACE_ROOT", str(engine))

    from scripts.utils.workspace import get_personal_root, get_crm_contacts_dir
    assert get_personal_root() == data.resolve()
    assert get_crm_contacts_dir() == data.resolve() / "crm" / "contacts"


def test_exec_heading_os_data_env_override_wins(tmp_path, monkeypatch):
    """An explicit HEADING_OS_DATA override beats sibling discovery."""
    engine = _make_exec_engine(tmp_path, slug="jane-doe")
    override = tmp_path / "elsewhere" / "exec-data"
    (override / "crm" / "contacts").mkdir(parents=True)
    monkeypatch.setenv("WORKSPACE_ROOT", str(engine))
    monkeypatch.setenv("HEADING_OS_DATA", str(override))

    from scripts.utils.workspace import get_personal_root
    assert get_personal_root() == override.resolve()


def test_exec_personal_data_never_lands_in_engine_tree(tmp_path, monkeypatch):
    """The core invariant: personal getters never point into the engine clone."""
    engine = _make_exec_engine(tmp_path, slug="jane-doe")
    data = tmp_path / ".heading-os-data-jane-doe"
    (data / "crm" / "contacts").mkdir(parents=True)
    monkeypatch.setenv("WORKSPACE_ROOT", str(engine))

    from scripts.utils.workspace import (
        get_personal_root, get_crm_contacts_dir, get_people_file, get_outputs_dir,
    )
    for p in (get_personal_root(), get_crm_contacts_dir(), get_people_file(), get_outputs_dir()):
        assert engine not in p.parents and p != engine, f"{p} leaked into the engine clone"
