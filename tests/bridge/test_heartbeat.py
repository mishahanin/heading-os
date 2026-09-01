"""Tests for the heartbeat writer (Phase 1.152).

**The file mode was a claim nothing pinned.** `heartbeat.py:115` asks
`atomic_write_text` for `mode=0o600` under a comment explaining why the payload
must not be world-readable: `last_error` is raw log text and can carry file
paths, conversation ids or mail subjects, and aggregators run as the same user
so nothing needs the group or other bits. No test in this repository read the
mode of `heartbeat.json`. MEASURED 2026-08-31 by changing that argument to
`mode=0o644`::

    $ .venv/bin/python -m pytest tests/bridge tests/test_daemon_heartbeat.py -q
    1315 passed, 1 skipped

Byte-for-byte the unmutated result. A grep for `0o600` or `st_mode` across
`tests/` returns no hit against any heartbeat test, so the argument could be
dropped, widened, or lost in a refactor of `atomic_write_text`'s signature
without a single red line. `test_the_heartbeat_is_not_readable_by_other_accounts`
below is the case that fails without it.
"""
import json
import os
import stat
import sys

import pytest

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


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="Windows carries no POSIX owner/group/other bits; os.chmod there "
           "sets the read-only attribute and os.stat reports 0o666 for a "
           "writable file, so the claim this pins does not exist on it")
def test_the_heartbeat_is_not_readable_by_other_accounts(workspace_root):
    """0o600 exactly, asserted on the file rather than on the argument.

    The payload embeds `last_error`, which is whatever text the daemon last
    logged at WARNING or above: a traceback with absolute paths, an Exchange
    conversation id, a mail subject. 0o644 publishes that to every account on
    the machine, and the aggregator that reads it (`daemon-fleet-health.py`)
    runs as the same user, so no wider bit buys anything.
    """
    write_heartbeat(workspace_root, "v1")
    hb = workspace_root / ".daemon-state" / "heartbeat.json"
    mode = stat.S_IMODE(os.stat(hb).st_mode)
    assert oct(mode) == "0o600", (
        f"heartbeat.json is {oct(mode)}; it carries raw log text and must stay "
        f"owner-only")
    assert not mode & (stat.S_IRGRP | stat.S_IROTH), oct(mode)


@pytest.mark.skipif(sys.platform == "win32", reason="see the sibling above")
def test_the_mode_survives_an_overwrite_of_an_existing_heartbeat(workspace_root):
    """The second beat is the one that matters.

    `atomic_write_text` writes a fresh temp file and `os.replace`s it over the
    target every 60 seconds. A mode applied only on creation would leave the
    first beat correct and every later beat at the process umask, which is the
    state a single-write test cannot distinguish.
    """
    write_heartbeat(workspace_root, "v1")
    write_heartbeat(workspace_root, "v2")
    hb = workspace_root / ".daemon-state" / "heartbeat.json"
    assert oct(stat.S_IMODE(os.stat(hb).st_mode)) == "0o600"
    assert json.loads(hb.read_text())["config_loaded_version"] == "v2"


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
