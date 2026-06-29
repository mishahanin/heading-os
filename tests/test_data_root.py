import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils import paths

ROOT = Path(__file__).resolve().parent.parent


# ---------- Task 1: get_data_root resolution + demo flag ----------

def test_env_override_wins(tmp_path, monkeypatch):
    d = tmp_path / "mydata"
    d.mkdir()
    monkeypatch.setenv("HEADING_OS_DATA", str(d))
    assert paths.get_data_root() == d.resolve()


def test_sibling_data_root(tmp_path, monkeypatch):
    monkeypatch.delenv("HEADING_OS_DATA", raising=False)
    ws = tmp_path / ".heading-os"
    ws.mkdir()
    sib = tmp_path / ".heading-os-data"
    sib.mkdir()
    monkeypatch.setenv("WORKSPACE_ROOT", str(ws))
    assert paths.get_data_root() == sib.resolve()


def test_legacy_in_tree(tmp_path, monkeypatch):
    monkeypatch.delenv("HEADING_OS_DATA", raising=False)
    ws = tmp_path / "ceo-main"
    (ws / "crm" / "contacts").mkdir(parents=True)
    monkeypatch.setenv("WORKSPACE_ROOT", str(ws))
    assert paths.get_data_root() == ws.resolve()


def test_in_tree_wins_over_sibling(tmp_path, monkeypatch):
    # Transitional ceo-main protection: when a workspace has BOTH its own
    # in-tree data AND a sibling .heading-os-data, the in-tree data wins, so
    # building the data repo does not flip live ceo-main onto it (Plan 4 D2).
    monkeypatch.delenv("HEADING_OS_DATA", raising=False)
    ws = tmp_path / "ceo-main"
    (ws / "crm" / "contacts").mkdir(parents=True)
    sib = tmp_path / ".heading-os-data"
    sib.mkdir()
    monkeypatch.setenv("WORKSPACE_ROOT", str(ws))
    assert paths.get_data_root() == ws.resolve()


def test_demo_fallback_and_flag(tmp_path, monkeypatch):
    monkeypatch.delenv("HEADING_OS_DATA", raising=False)
    ws = tmp_path / "engine"
    ws.mkdir()
    monkeypatch.setenv("WORKSPACE_ROOT", str(ws))
    assert paths.get_data_root() == (ws / "examples").resolve()
    assert paths.data_root_is_demo() is True


# ---------- Task 2: schema-version handshake ----------

def test_schema_missing_marker_is_compatible(tmp_path, monkeypatch):
    monkeypatch.setenv("HEADING_OS_DATA", str(tmp_path))  # no .schema-version
    ok, _ = paths.check_schema_compatible()
    assert ok is True


def test_schema_older_data_is_incompatible(tmp_path, monkeypatch):
    (tmp_path / ".schema-version").write_text("0\n", encoding="utf-8")
    monkeypatch.setenv("HEADING_OS_DATA", str(tmp_path))
    ok, msg = paths.check_schema_compatible()
    assert ok is False
    assert "migration" in msg.lower()


def test_schema_equal_is_compatible(tmp_path, monkeypatch):
    (tmp_path / ".schema-version").write_text(str(paths.DATA_SCHEMA_VERSION), encoding="utf-8")
    monkeypatch.setenv("HEADING_OS_DATA", str(tmp_path))
    ok, _ = paths.check_schema_compatible()
    assert ok is True


# ---------- Task 3: fail-closed write guard ----------

def test_require_writable_raises_in_demo(tmp_path, monkeypatch):
    monkeypatch.delenv("HEADING_OS_DATA", raising=False)
    ws = tmp_path / "engine"
    ws.mkdir()
    monkeypatch.setenv("WORKSPACE_ROOT", str(ws))
    with pytest.raises(paths.DataRootError):
        paths.require_writable_data_root()


def test_require_writable_returns_path_when_real(tmp_path, monkeypatch):
    monkeypatch.setenv("HEADING_OS_DATA", str(tmp_path))
    assert paths.require_writable_data_root() == tmp_path.resolve()


# ---------- Task 4: private helpers resolve under the data root ----------

from scripts.utils import workspace


def test_corporate_content_vs_engine_dirs_split(tmp_path, monkeypatch):
    # Plan 4 T2: for the CEO, corporate CONTENT helpers (datastore, context,
    # shared-knowledge, crm-config) resolve under the data root, while ENGINE
    # dirs (reference, config) stay on the workspace/engine root -- even when an
    # explicit data root differs from the workspace root.
    ws = tmp_path / "engine"
    ws.mkdir()
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setenv("WORKSPACE_ROOT", str(ws))
    monkeypatch.setenv("HEADING_OS_DATA", str(data))
    workspace._reset_identity_cache()
    dr, wr = str(data.resolve()), str(ws.resolve())
    # corporate content -> data root
    assert str(workspace.get_datastore_dir()).startswith(dr)
    assert str(workspace.get_context_dir()).startswith(dr)
    assert str(workspace.get_shared_knowledge_dir()).startswith(dr)
    assert str(workspace.get_crm_config_path()).startswith(dr)
    # engine dirs -> workspace/engine root, NOT the data root
    assert str(workspace.get_reference_dir()).startswith(wr)
    assert str(workspace.get_config_dir()).startswith(wr)
    assert not str(workspace.get_reference_dir()).startswith(dr)
    assert not str(workspace.get_config_dir()).startswith(dr)


def test_private_helpers_resolve_under_data_root(tmp_path, monkeypatch):
    # With an explicit data root, CEO private helpers must resolve UNDER it,
    # not under the engine/workspace root. Guards against a future helper that
    # hardcodes a path back into the engine tree.
    monkeypatch.setenv("HEADING_OS_DATA", str(tmp_path))
    workspace._reset_identity_cache()
    dr = str(tmp_path.resolve())
    assert str(workspace.get_crm_contacts_dir()).startswith(dr)
    assert str(workspace.get_knowledge_dir()).startswith(dr)
    assert str(workspace.get_outputs_dir()).startswith(dr)
    assert str(workspace.get_personal_context_dir()).startswith(dr)
    assert str(workspace.get_people_file()).startswith(dr)


# ---------- Task 5: init-data.py scaffolder ----------

def test_init_data_scaffolds_data_root(tmp_path):
    target = tmp_path / ".heading-os-data"
    r = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "init-data.py"), "--path", str(target)],
        capture_output=True, text=True,
    )
    assert r.returncode == 0, r.stderr
    assert (target / ".schema-version").read_text().strip() == str(paths.DATA_SCHEMA_VERSION)
    assert (target / "crm" / "contacts").is_dir()
    assert (target / "outputs").is_dir()
    assert (target / "threads" / "business").is_dir()
    assert (target / "knowledge").is_dir()


def test_init_data_refuses_nonempty(tmp_path):
    target = tmp_path / ".heading-os-data"
    (target / "crm").mkdir(parents=True)
    (target / "crm" / "x.md").write_text("existing", encoding="utf-8")
    r = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "init-data.py"), "--path", str(target)],
        capture_output=True, text=True,
    )
    assert r.returncode == 1
    assert "not empty" in r.stdout.lower()
