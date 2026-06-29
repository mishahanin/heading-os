"""Tests for the per-daemon liveness heartbeat util (R14).

Exercises ``scripts.utils.daemon_heartbeat.beat`` against a temp workspace
root (monkeypatched), with no daemon and no real workspace mutation. Confirms
a well-formed file carrying the current trace_id, and that two different
daemon names write to distinct files that do not collide.

Run: python3 -m pytest tests/test_daemon_heartbeat.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils import daemon_heartbeat, trace


def _heartbeat_path(root: Path, name: str) -> Path:
    return root / ".daemon-state" / "heartbeats" / f"{name}.json"


def test_beat_writes_well_formed_file_with_trace_id(tmp_path, monkeypatch):
    monkeypatch.setattr(daemon_heartbeat, "get_workspace_root", lambda: tmp_path)
    trace.set("test-trace-id-abc123")
    try:
        daemon_heartbeat.beat("sync-exchange", config_version="3")
    finally:
        trace.clear()

    path = _heartbeat_path(tmp_path, "sync-exchange")
    assert path.exists()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["daemon"] == "sync-exchange"
    assert data["trace_id"] == "test-trace-id-abc123"
    assert data["version"] == "3"
    assert data["config_loaded_version"] == "3"
    assert isinstance(data["pid"], int) and data["pid"] > 0
    assert isinstance(data["uptime_s"], int) and data["uptime_s"] >= 0
    # last_heartbeat is ISO-8601 UTC and parseable
    assert "last_heartbeat" in data and "T" in data["last_heartbeat"]


def test_two_daemon_names_do_not_collide(tmp_path, monkeypatch):
    monkeypatch.setattr(daemon_heartbeat, "get_workspace_root", lambda: tmp_path)
    daemon_heartbeat.beat("bridge")
    daemon_heartbeat.beat("sentinel")

    bridge_path = _heartbeat_path(tmp_path, "bridge")
    sentinel_path = _heartbeat_path(tmp_path, "sentinel")
    assert bridge_path.exists()
    assert sentinel_path.exists()
    assert bridge_path != sentinel_path

    bridge_data = json.loads(bridge_path.read_text(encoding="utf-8"))
    sentinel_data = json.loads(sentinel_path.read_text(encoding="utf-8"))
    assert bridge_data["daemon"] == "bridge"
    assert sentinel_data["daemon"] == "sentinel"
    # config_version omitted -> defaults to "unversioned"
    assert bridge_data["version"] == "unversioned"


def test_beat_never_raises_on_unwritable_root(tmp_path, monkeypatch):
    # Point the root at a path whose parent is a file, so mkdir/write fails.
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x", encoding="utf-8")
    monkeypatch.setattr(daemon_heartbeat, "get_workspace_root", lambda: blocker)
    # Must not raise; the warning is logged internally.
    daemon_heartbeat.beat("eval-drift")
