"""End-to-end subprocess tests for the bridge hook router.

The hook is invoked by Claude Code as a child process with stdin payload.
These tests exercise the real subprocess interface, not the in-process API,
so any change to the hook contract is caught here."""
import json
import subprocess
import sys
from pathlib import Path

HOOK = Path(__file__).resolve().parents[2] / ".claude" / "hooks" / "bridge-hook.py"


def _invoke(subcommand: str, payload: dict) -> subprocess.CompletedProcess:
    """Helper: run the hook with stdin payload, return CompletedProcess.
    HOME/USERPROFILE must already be set by the caller (via _setup_env)."""
    return subprocess.run(
        [sys.executable, str(HOOK), subcommand],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
    )


def _setup_env(tmp_path, monkeypatch):
    """Both HOME (POSIX) and USERPROFILE (Windows) point at tmp_path."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))


def test_session_start_writes_registry(tmp_path, monkeypatch):
    """SessionStart hook writes one entry keyed by cwd containing session_id + metadata."""
    _setup_env(tmp_path, monkeypatch)
    payload = {
        "session_id": "sid-abc",
        "transcript_path": "/path/to/transcript.jsonl",
        "cwd": str(tmp_path / "ws"),
        "source": "startup",
        "hook_event_name": "SessionStart",
    }
    r = _invoke("session-start", payload)
    assert r.returncode == 0
    assert r.stdout == "", f"SessionStart hook leaked to stdout: {r.stdout!r}"
    reg = tmp_path / ".claude" / "state" / "active-sessions.json"
    assert reg.exists()
    data = json.loads(reg.read_text())
    assert data[str(tmp_path / "ws")]["session_id"] == "sid-abc"
    assert data[str(tmp_path / "ws")]["transcript_path"] == "/path/to/transcript.jsonl"
    assert "started_at" in data[str(tmp_path / "ws")]


def test_session_start_dedupes_on_cwd(tmp_path, monkeypatch):
    """Two SessionStart events with the same cwd produce ONE registry entry
    (the second overwrites the first; no duplicate accumulation)."""
    _setup_env(tmp_path, monkeypatch)
    payload = {
        "session_id": "sid-abc",
        "transcript_path": "/x",
        "cwd": str(tmp_path / "ws"),
        "source": "startup",
        "hook_event_name": "SessionStart",
    }
    for _ in range(2):
        r = _invoke("session-start", payload)
        assert r.returncode == 0
    reg = tmp_path / ".claude" / "state" / "active-sessions.json"
    data = json.loads(reg.read_text())
    assert len(data) == 1


def test_session_end_removes_registry_entry(tmp_path, monkeypatch):
    """SessionEnd hook removes the registry entry for the given cwd."""
    _setup_env(tmp_path, monkeypatch)
    cwd = str(tmp_path / "ws")
    start_payload = {
        "session_id": "sid-abc", "transcript_path": "/x", "cwd": cwd,
        "source": "startup", "hook_event_name": "SessionStart",
    }
    _invoke("session-start", start_payload)
    end_payload = {"cwd": cwd, "hook_event_name": "SessionEnd"}
    r = _invoke("session-end", end_payload)
    assert r.returncode == 0
    assert r.stdout == "", f"SessionEnd hook leaked to stdout: {r.stdout!r}"
    reg = tmp_path / ".claude" / "state" / "active-sessions.json"
    data = json.loads(reg.read_text())
    assert cwd not in data


def test_session_end_is_idempotent_when_cwd_not_in_registry(tmp_path, monkeypatch):
    """Calling SessionEnd for a cwd that was never registered is a no-op (returncode 0)."""
    _setup_env(tmp_path, monkeypatch)
    payload = {"cwd": str(tmp_path / "never-registered"), "hook_event_name": "SessionEnd"}
    r = _invoke("session-end", payload)
    assert r.returncode == 0


def test_unknown_subcommand_returns_one(tmp_path, monkeypatch):
    """An unrecognized subcommand prints to stderr and exits 1."""
    _setup_env(tmp_path, monkeypatch)
    r = _invoke("bogus-subcommand", {})
    assert r.returncode == 1
    assert "unknown" in r.stderr.lower()


def test_session_start_with_missing_session_id_returns_one(tmp_path, monkeypatch):
    """If the payload lacks session_id, hook exits 1 with stderr message."""
    _setup_env(tmp_path, monkeypatch)
    payload = {"cwd": str(tmp_path / "ws"), "hook_event_name": "SessionStart"}
    r = _invoke("session-start", payload)
    assert r.returncode == 1
    assert "session_id" in r.stderr or "cwd" in r.stderr


def test_session_start_with_malformed_stdin_returns_one(tmp_path, monkeypatch):
    """Malformed stdin JSON falls through to missing-fields error (returncode 1)."""
    _setup_env(tmp_path, monkeypatch)
    r = subprocess.run(
        [sys.executable, str(HOOK), "session-start"],
        input="not-json{garbage",
        capture_output=True,
        text=True,
    )
    assert r.returncode == 1
    # Falls into session_start with empty payload -> missing session_id/cwd
    assert "session_id" in r.stderr or "cwd" in r.stderr


def test_session_start_recovers_from_corrupt_registry(tmp_path, monkeypatch):
    """A corrupt registry file is silently overwritten with a clean entry."""
    _setup_env(tmp_path, monkeypatch)
    reg = tmp_path / ".claude" / "state" / "active-sessions.json"
    reg.parent.mkdir(parents=True)
    reg.write_text("GARBAGE{not-json{")
    payload = {
        "session_id": "sid-after-corruption",
        "cwd": str(tmp_path / "ws"),
        "source": "startup",
        "hook_event_name": "SessionStart",
    }
    r = _invoke("session-start", payload)
    assert r.returncode == 0
    data = json.loads(reg.read_text())
    assert data[str(tmp_path / "ws")]["session_id"] == "sid-after-corruption"


def test_session_start_records_parseable_started_at(tmp_path, monkeypatch):
    """started_at field round-trips through datetime.fromisoformat with UTC tzinfo."""
    from datetime import datetime, timezone
    _setup_env(tmp_path, monkeypatch)
    cwd = str(tmp_path / "ws")
    payload = {
        "session_id": "sid-ts", "cwd": cwd, "source": "startup",
        "hook_event_name": "SessionStart",
    }
    r = _invoke("session-start", payload)
    assert r.returncode == 0
    data = json.loads((tmp_path / ".claude" / "state" / "active-sessions.json").read_text())
    parsed = datetime.fromisoformat(data[cwd]["started_at"])
    assert parsed.tzinfo == timezone.utc


def test_session_start_records_int_pid(tmp_path, monkeypatch):
    """pid field is recorded as a positive integer (Claude Code process PID)."""
    _setup_env(tmp_path, monkeypatch)
    cwd = str(tmp_path / "ws")
    payload = {
        "session_id": "sid-pid", "cwd": cwd, "source": "startup",
        "hook_event_name": "SessionStart",
    }
    r = _invoke("session-start", payload)
    assert r.returncode == 0
    data = json.loads((tmp_path / ".claude" / "state" / "active-sessions.json").read_text())
    pid = data[cwd]["pid"]
    assert isinstance(pid, int)
    assert pid > 0


def test_stop_without_origin_is_noop(tmp_path, monkeypatch):
    """When BRIDGE_ORIGIN is unset, /stop returns 0 with no prompt (background safe)."""
    _setup_env(tmp_path, monkeypatch)
    monkeypatch.delenv("BRIDGE_ORIGIN", raising=False)
    r = _invoke("stop", {"session_id": "sid", "cwd": "/ws"})
    assert r.returncode == 0
    assert "stay or browser" not in r.stderr.lower()


def test_stop_with_browser_origin_prompts_and_defaults_stay(tmp_path, monkeypatch):
    """When BRIDGE_ORIGIN=browser is set, the hook prompts. With no tty + short
    timeout, it defaults to 'stay' and writes the decision to stderr."""
    _setup_env(tmp_path, monkeypatch)
    monkeypatch.setenv("BRIDGE_ORIGIN", "browser")
    monkeypatch.setenv("BRIDGE_STOP_TIMEOUT", "1")  # speed up test
    r = _invoke("stop", {"session_id": "sid", "cwd": "/ws"})
    assert r.returncode == 0
    assert "stay" in r.stderr.lower()


def test_stop_does_not_hang_when_no_tty(tmp_path, monkeypatch):
    """Regression: the prompt must read from /dev/tty (POSIX) or the Win32
    console (Windows), NOT from sys.stdin (which delivered the JSON payload
    and is at EOF). With no controlling tty in pytest subprocess context,
    the helper must short-circuit so the hook defaults to stay within
    BRIDGE_STOP_TIMEOUT, not the subprocess.run() timeout."""
    import time as _t
    _setup_env(tmp_path, monkeypatch)
    monkeypatch.setenv("BRIDGE_ORIGIN", "browser")
    monkeypatch.setenv("BRIDGE_STOP_TIMEOUT", "1")
    started = _t.time()
    r = _invoke("stop", {"session_id": "sid", "cwd": "/ws"})
    elapsed = _t.time() - started
    assert r.returncode == 0
    assert "stay" in r.stderr.lower()
    assert elapsed < 4, f"hook hung for {elapsed:.1f}s - tty detection broken"


# Direct unit tests for the helpers (no subprocess - testing pure logic)

def test_find_daemon_state_finds_at_ancestor(tmp_path):
    """_find_daemon_state walks up from start looking for .daemon-state/port."""
    # Import the hook module directly for unit-test access to helpers.
    import importlib.util
    spec = importlib.util.spec_from_file_location("bridge_hook_helpers", HOOK)
    hook_mod = importlib.util.module_from_spec(spec)
    sys.modules["bridge_hook_helpers"] = hook_mod
    spec.loader.exec_module(hook_mod)
    # Plant .daemon-state/port at tmp_path, walk up from a nested subdir.
    state_dir = tmp_path / ".daemon-state"
    state_dir.mkdir()
    (state_dir / "port").write_text("31415")
    nested = tmp_path / "a" / "b" / "c"
    nested.mkdir(parents=True)
    found = hook_mod._find_daemon_state(nested)
    assert found == state_dir


def test_find_daemon_state_returns_none_when_absent(tmp_path):
    """Returns None when no .daemon-state/port exists in the ancestor chain."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("bridge_hook_helpers", HOOK)
    hook_mod = importlib.util.module_from_spec(spec)
    sys.modules["bridge_hook_helpers"] = hook_mod
    spec.loader.exec_module(hook_mod)
    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)
    assert hook_mod._find_daemon_state(nested) is None


def test_stop_handles_non_numeric_timeout_env(tmp_path, monkeypatch):
    """A non-numeric BRIDGE_STOP_TIMEOUT falls back to the 5s default
    without raising. The hook still completes and defaults to stay."""
    _setup_env(tmp_path, monkeypatch)
    monkeypatch.setenv("BRIDGE_ORIGIN", "browser")
    monkeypatch.setenv("BRIDGE_STOP_TIMEOUT", "not-a-number")
    import time as _t
    started = _t.time()
    r = _invoke("stop", {"session_id": "sid", "cwd": "/ws"})
    elapsed = _t.time() - started
    assert r.returncode == 0
    assert "stay" in r.stderr.lower()
    # The fallback is 5s. With subprocess overhead, expect elapsed in [5, 9).
    # Most importantly: must NOT crash on the bad env var.
    assert elapsed < 9, f"hook hung for {elapsed:.1f}s on bad timeout env"
