"""Tests for scripts/inbox_pulse/paths.py and scripts/inbox_pulse/state.py.

The two modules are tested together because get_state_dir() from paths.py
underpins every helper in state.py. All tests use the INBOX_PULSE_STATE_DIR
env-var override to avoid touching the real workspace state directory.
"""

from __future__ import annotations

import importlib
import json
import os
from datetime import datetime
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _reload_paths():
    """Re-import paths module with cleared module-level caches.

    get_state_dir() and get_workspace_root() cache their results at
    module level. Between tests that alter INBOX_PULSE_STATE_DIR we must
    reset the caches so each test sees a fresh resolution.
    """
    import scripts.inbox_pulse.paths as mod
    mod._workspace_root_cache = None
    mod._state_dir_cache = None
    return mod


# ---------------------------------------------------------------------------
# paths.py tests
# ---------------------------------------------------------------------------


def test_get_state_dir_honors_env_var(tmp_path, monkeypatch):
    """INBOX_PULSE_STATE_DIR env var takes highest priority."""
    monkeypatch.setenv("INBOX_PULSE_STATE_DIR", str(tmp_path))
    paths = _reload_paths()

    result = paths.get_state_dir()

    assert result == tmp_path
    assert tmp_path.exists()


def test_get_state_dir_falls_back_to_data_root(monkeypatch, tmp_path):
    """Without env override, state dir is <data_root>/state/email-triage/.

    Runtime state (cursor, ledger, cost tracker, logs) is data, so it must
    resolve under the DATA root via the data-root seam -- never inside the
    engine tree, which must stay clean. Regression for the Steward-cutover
    finding that email-triage was writing state/email-triage/ into the engine
    clone.
    """
    monkeypatch.delenv("INBOX_PULSE_STATE_DIR", raising=False)
    paths = _reload_paths()

    monkeypatch.setattr(paths, "_state_dir_cache", None)

    # Mock get_data_root so we don't touch the real data overlay during the test.
    monkeypatch.setattr(paths, "get_data_root", lambda: tmp_path)

    result = paths.get_state_dir()

    expected = tmp_path / "state" / "email-triage"
    assert result == expected
    assert expected.exists()


def test_get_workspace_root_finds_dir_with_config_and_scripts(tmp_path, monkeypatch):
    """get_workspace_root() walks parents until it finds config/ and scripts/."""
    # Build a minimal fake workspace tree under tmp_path.
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "config").mkdir()
    (workspace / "scripts").mkdir()

    # Create a nested file path simulating paths.py living at
    # workspace/scripts/inbox_pulse/paths.py
    nested = workspace / "scripts" / "inbox_pulse"
    nested.mkdir()
    fake_file = nested / "paths.py"
    fake_file.touch()

    paths = _reload_paths()

    # Override the module's _THIS_FILE so the walk starts from the nested dir.
    monkeypatch.setattr(paths, "_THIS_FILE", fake_file.resolve())
    monkeypatch.setattr(paths, "_workspace_root_cache", None)

    result = paths.get_workspace_root()

    assert result == workspace.resolve()


# ---------------------------------------------------------------------------
# state.py tests
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_paths_cache(tmp_path, monkeypatch):
    """For every state.py test: point INBOX_PULSE_STATE_DIR at tmp_path and
    clear the module-level caches so each test gets a fresh state dir."""
    monkeypatch.setenv("INBOX_PULSE_STATE_DIR", str(tmp_path))
    _reload_paths()
    yield
    # Clear again after the test for clean teardown.
    _reload_paths()


def _import_state():
    """Import state module with reloaded paths cache."""
    import scripts.inbox_pulse.state as mod
    importlib.reload(mod)
    return mod


def test_append_jsonl_writes_one_line_per_call(tmp_path):
    """Two append_jsonl calls produce exactly two parseable JSON lines."""
    state = _import_state()

    state.append_jsonl("test.jsonl", {"a": 1})
    state.append_jsonl("test.jsonl", {"b": 2})

    lines = (tmp_path / "test.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0]) == {"a": 1}
    assert json.loads(lines[1]) == {"b": 2}


def test_load_state_returns_default_when_missing(tmp_path):
    """load_state returns the default value when the file does not exist."""
    state = _import_state()

    result = state.load_state("missing.json", default={"foo": "bar"})

    assert result == {"foo": "bar"}


def test_load_state_raises_on_corrupted_json(tmp_path):
    """load_state raises json.JSONDecodeError on corrupted content (loud failure)."""
    state = _import_state()
    (tmp_path / "corrupt.json").write_text("not { valid json !!!", encoding="utf-8")

    with pytest.raises(json.JSONDecodeError):
        state.load_state("corrupt.json")


def test_save_state_is_atomic(tmp_path):
    """save_state leaves no .tmp files and the data roundtrips correctly."""
    state = _import_state()
    payload = {"version": 1, "items": [1, 2, 3]}

    state.save_state("roundtrip.json", payload)

    # No .tmp files should remain.
    tmp_files = list(tmp_path.glob("*.tmp"))
    assert tmp_files == [], f"Leftover .tmp files: {tmp_files}"

    # Data roundtrips.
    loaded = state.load_state("roundtrip.json")
    assert loaded == payload


def test_write_heartbeat_records_pid_and_timestamp(tmp_path):
    """write_heartbeat() writes last_heartbeat, daemon_pid, and queue_depth."""
    state = _import_state()

    state.write_heartbeat()

    data = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert "last_heartbeat" in data
    assert "daemon_pid" in data
    assert "queue_depth" in data
    assert data["daemon_pid"] == os.getpid()
    assert data["queue_depth"] == 0
    # last_heartbeat must parse as ISO-8601.
    parsed = datetime.fromisoformat(data["last_heartbeat"])
    assert parsed.tzinfo is not None


def test_write_heartbeat_merges_extra(tmp_path):
    """write_heartbeat(extra=...) merges extra fields on top of defaults."""
    state = _import_state()

    extra = {"queue_depth": 5, "last_email_processed_at": "2026-05-27T12:00:00+04:00"}
    state.write_heartbeat(extra=extra)

    data = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert data["queue_depth"] == 5
    assert data["last_email_processed_at"] == "2026-05-27T12:00:00+04:00"
    # Base fields still present.
    assert "last_heartbeat" in data
    assert "daemon_pid" in data
