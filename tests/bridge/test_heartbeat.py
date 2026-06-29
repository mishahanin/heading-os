"""Tests for the heartbeat writer (Phase 1.152)."""
import json

from scripts.bridge_daemon.heartbeat import write_heartbeat


def test_writes_heartbeat_json(workspace_root):
    write_heartbeat(workspace_root, "test-cfg-v1")
    hb = workspace_root / ".daemon-state" / "heartbeat.json"
    assert hb.exists()
    data = json.loads(hb.read_text())
    assert data["pid"] > 0
    assert data["config_loaded_version"] == "test-cfg-v1"
    assert data["last_error"] is None
    assert data["recent_error_count"] == 0
    assert isinstance(data["uptime_s"], int)
    assert "last_heartbeat" in data


def test_active_sessions_count_zero_when_no_sessions_file(workspace_root):
    write_heartbeat(workspace_root)
    hb = workspace_root / ".daemon-state" / "heartbeat.json"
    data = json.loads(hb.read_text())
    assert data["active_sessions"] == 0


def test_active_sessions_count_from_sessions_file(workspace_root):
    sessions = workspace_root / ".daemon-state" / "active-sessions.json"
    sessions.write_text(json.dumps({"sess-a": {}, "sess-b": {}, "sess-c": {}}))
    write_heartbeat(workspace_root)
    hb = workspace_root / ".daemon-state" / "heartbeat.json"
    data = json.loads(hb.read_text())
    assert data["active_sessions"] == 3


def test_active_sessions_count_resilient_to_malformed_file(workspace_root):
    sessions = workspace_root / ".daemon-state" / "active-sessions.json"
    sessions.write_text("{not valid json at all")
    write_heartbeat(workspace_root)
    hb = workspace_root / ".daemon-state" / "heartbeat.json"
    data = json.loads(hb.read_text())
    # Malformed sessions file -> 0, NOT an exception that breaks heartbeat.
    assert data["active_sessions"] == 0


def test_atomic_overwrite(workspace_root):
    write_heartbeat(workspace_root, "v1")
    write_heartbeat(workspace_root, "v2")
    hb = workspace_root / ".daemon-state" / "heartbeat.json"
    data = json.loads(hb.read_text())
    assert data["config_loaded_version"] == "v2"


def test_default_config_version(workspace_root):
    write_heartbeat(workspace_root)  # no config_version arg
    hb = workspace_root / ".daemon-state" / "heartbeat.json"
    data = json.loads(hb.read_text())
    assert data["config_loaded_version"] == "unversioned"


# Phase J - tracker integration tests.


def test_heartbeat_reads_tracker_for_error_fields(workspace_root):
    """When the tracker has records, the heartbeat carries them.

    Exercises the wiring done in Phase J: write_heartbeat() calls
    get_tracker().last_error() and .recent_count() instead of the
    pre-Phase-J hardcoded None / 0.
    """
    from scripts.bridge_daemon.error_tracker import _reset_for_tests, get_tracker
    _reset_for_tests()
    try:
        tracker = get_tracker()
        tracker.record("first failure")
        tracker.record("second failure")

        write_heartbeat(workspace_root, "v1")
        hb = workspace_root / ".daemon-state" / "heartbeat.json"
        data = json.loads(hb.read_text())
        assert data["recent_error_count"] == 2
        assert data["last_error"] == "second failure"
    finally:
        _reset_for_tests()


def test_heartbeat_empty_tracker_yields_none_and_zero(workspace_root):
    """No errors recorded -> heartbeat carries None + 0 (pre-Phase-J defaults
    must remain the empty-state behaviour)."""
    from scripts.bridge_daemon.error_tracker import _reset_for_tests
    _reset_for_tests()
    try:
        write_heartbeat(workspace_root, "v1")
        hb = workspace_root / ".daemon-state" / "heartbeat.json"
        data = json.loads(hb.read_text())
        assert data["recent_error_count"] == 0
        assert data["last_error"] is None
    finally:
        _reset_for_tests()
