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


# These three seeded `<workspace_root>/.daemon-state/active-sessions.json`, a
# path nothing in this repository writes. `.claude/hooks/bridge-hook.py` writes
# `~/.claude/state/active-sessions.json`, so the counter always took its
# file-absent branch and reported 0, and two of these tests asserted exactly
# that 0 as correct. The third asserted 3 and passed only because it was reading
# the OPERATOR's live registry, which happened to hold three sessions. They now
# seed the registry where the hook puts it. Rewritten 2026-08-25 with the fix.

def _seed_registry(home, payload):
    reg = home / ".claude" / "state" / "active-sessions.json"
    reg.parent.mkdir(parents=True, exist_ok=True)
    reg.write_text(payload if isinstance(payload, str) else json.dumps(payload),
                   encoding="utf-8")
    return reg


def _sessions_reported(workspace_root):
    write_heartbeat(workspace_root)
    hb = workspace_root / ".daemon-state" / "heartbeat.json"
    return json.loads(hb.read_text())["active_sessions"]


def test_active_sessions_count_zero_when_no_sessions_file(workspace_root, tmp_path,
                                                          monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    assert _sessions_reported(workspace_root) == 0


def test_active_sessions_count_from_sessions_file(workspace_root, tmp_path,
                                                  monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    _seed_registry(tmp_path, {"sess-a": {}, "sess-b": {}, "sess-c": {}})
    assert _sessions_reported(workspace_root) == 3


def test_active_sessions_count_resilient_to_malformed_file(workspace_root, tmp_path,
                                                           monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    _seed_registry(tmp_path, "{not valid json at all")
    # Malformed registry -> 0, NOT an exception that breaks the heartbeat.
    assert _sessions_reported(workspace_root) == 0


def test_the_counter_reads_the_registry_the_hook_writes(tmp_path, monkeypatch):
    """The path is the finding; pin it against the hook's own constant."""
    import importlib.util
    from pathlib import Path as _Path

    from scripts.bridge_daemon.sessions import registry_path

    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    root = _Path(__file__).resolve().parent.parent.parent
    spec = importlib.util.spec_from_file_location(
        "bridge_hook_under_test", root / ".claude" / "hooks" / "bridge-hook.py")
    hook = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hook)

    assert registry_path().parts[-3:] == hook.REGISTRY.parts[-3:]


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
