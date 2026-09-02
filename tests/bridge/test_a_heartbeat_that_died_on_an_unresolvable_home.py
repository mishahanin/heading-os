"""`_active_session_count` promised "Returns 0 on any error" over an unguarded call.

The promise was true of `read_registry`, which guards itself. It was never true
of `registry_path()`, which calls `Path.home()`. MEASURED 2026-09-02 on CPython
3.11.15 with `HOME` unset and `pwd.getpwuid` raising `KeyError`:
`Path.home()` raises `RuntimeError: Could not determine home directory.`

`write_heartbeat` reads the count inside the `payload` dict literal, which sits
BETWEEN its two try blocks and is covered by neither. So on a host with no
resolvable home - a systemd unit with a cleared environment and a user with no
passwd entry is the ordinary way to get there - every 60-second tick raised out
of a function whose own docstring promises a logged warning instead, and the
fleet grid lost this daemon's only liveness signal along with the `last_error`
the heartbeat carries.

The anchor below is the case a guard that swallowed everything would also pass:
a healthy registry must still be COUNTED, not reported as 0.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from scripts.bridge_daemon import heartbeat as hb  # noqa: E402
from scripts.bridge_daemon import sessions  # noqa: E402


def _break_home(monkeypatch):
    """Reproduce the measured failure at its real source, not at a stub.

    `Path.home()` consults `HOME` first and falls back to the passwd database,
    so removing only one of the two leaves it working. Both go.
    """
    monkeypatch.delenv("HOME", raising=False)
    monkeypatch.delenv("USERPROFILE", raising=False)
    import pwd

    def _no_such_user(uid):
        raise KeyError(uid)

    monkeypatch.setattr(pwd, "getpwuid", _no_such_user)


def test_the_measured_failure_is_still_the_failure(monkeypatch):
    """If `Path.home()` ever stops raising here, the tests below prove nothing."""
    _break_home(monkeypatch)
    with pytest.raises(RuntimeError):
        Path.home()


def test_an_unresolvable_home_reports_zero_sessions_instead_of_raising(
        tmp_path, monkeypatch):
    _break_home(monkeypatch)
    assert hb._active_session_count(tmp_path) == 0


def test_the_heartbeat_is_still_written_when_the_home_lookup_fails(
        tmp_path, monkeypatch):
    """The field is what degrades; the beat is not allowed to."""
    _break_home(monkeypatch)
    hb.write_heartbeat(tmp_path, config_version="v1")
    path = tmp_path / ".daemon-state" / hb.HEARTBEAT_FILE
    assert path.exists(), "the 60s tick raised instead of writing the heartbeat"
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["active_sessions"] == 0
    assert payload["config_loaded_version"] == "v1"


def test_a_registry_that_reads_is_still_counted(tmp_path, monkeypatch):
    """ANCHOR. Without this, a guard returning 0 unconditionally passes above.

    The whole point of this function is that it reported `active_sessions: 0`
    for a daemon serving live sessions once already.
    """
    registry = tmp_path / "active-sessions.json"
    registry.write_text(json.dumps({
        "sid-a": {"cwd": "/w/a", "session_id": "sid-a"},
        "sid-b": {"cwd": "/w/b", "session_id": "sid-b"},
        "sid-c": {"cwd": "/w/c", "session_id": "sid-c"},
    }), encoding="utf-8")
    monkeypatch.setattr(sessions, "registry_path", lambda: registry)
    assert hb._active_session_count(tmp_path) == 3

    hb.write_heartbeat(tmp_path)
    payload = json.loads(
        (tmp_path / ".daemon-state" / hb.HEARTBEAT_FILE).read_text(encoding="utf-8"))
    assert payload["active_sessions"] == 3


def test_a_broken_registry_still_reports_zero(tmp_path, monkeypatch):
    """The older half of the promise, kept beside the new one."""
    registry = tmp_path / "active-sessions.json"
    registry.write_text("[not json at all", encoding="utf-8")
    monkeypatch.setattr(sessions, "registry_path", lambda: registry)
    assert hb._active_session_count(tmp_path) == 0
