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

from scripts.utils import daemon_heartbeat, tracing


def _heartbeat_path(root: Path, name: str) -> Path:
    return root / ".daemon-state" / "heartbeats" / f"{name}.json"


def test_beat_writes_well_formed_file_with_trace_id(tmp_path, monkeypatch):
    monkeypatch.setattr(daemon_heartbeat, "get_workspace_root", lambda: tmp_path)
    tracing.set("test-trace-id-abc123")
    try:
        daemon_heartbeat.beat("sync-exchange", config_version="3")
    finally:
        tracing.clear()

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
    # config_version omitted -> defaults to "unversioned". BOTH fields, because
    # the module docstring's claim is that they are "identical by construction",
    # and only `version` was ever asserted on this path: changing
    # `config_loaded_version`'s else-branch alone left every test naming this
    # module green (measured 2026-09-01). `daemon-fleet-health.py` reads
    # `config_loaded_version` to spot config skew across the fleet, so a wrong
    # default there is a wrong skew verdict, not a cosmetic field.
    assert bridge_data["version"] == "unversioned"
    assert bridge_data["config_loaded_version"] == "unversioned"
    assert bridge_data["version"] == bridge_data["config_loaded_version"]
    assert sentinel_data["config_loaded_version"] == "unversioned"


def test_beat_never_raises_on_unwritable_root(tmp_path, monkeypatch):
    # Point the root at a path whose parent is a file, so mkdir/write fails.
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x", encoding="utf-8")
    monkeypatch.setattr(daemon_heartbeat, "get_workspace_root", lambda: blocker)
    # Must not raise; the warning is logged internally.
    daemon_heartbeat.beat("eval-drift")


def test_a_failed_beat_leaves_no_tempfile_behind(tmp_path, monkeypatch):
    """`_atomic_write_json`'s documented cleanup: "The tempfile is unlinked on
    any failure before re-raising."

    Nothing reached it. The one failure case above blocks at `mkdir`, which is
    the first statement and is BEFORE `mkstemp`, so the cleanup branch had never
    executed in any test -- deleting it outright left every file naming this
    module green (measured 2026-09-01).

    The failure is arranged without patching anything: the destination path is
    made a non-empty DIRECTORY, so `mkdir(parents=True, exist_ok=True)` and
    `mkstemp` both succeed and `os.replace` is the call that fails. Patching
    `os.replace` would have worked too, but `daemon_heartbeat` does a plain
    `import os`, so patching through it rebinds the stdlib for anything else
    running in the same process.

    Why it matters: a daemon beats roughly once a minute for as long as it runs.
    A leaked tempfile per failed beat is about 1,440 files a day accumulating in
    `.daemon-state/heartbeats/`, in exactly the situation -- writes failing --
    where the operator least wants a second problem. `mkstemp` names them with a
    random suffix and no `.json`, so the fleet-health reader that globs `*.json`
    never sees them and nothing reports the pile.
    """
    monkeypatch.setattr(daemon_heartbeat, "get_workspace_root", lambda: tmp_path)
    beats = tmp_path / ".daemon-state" / "heartbeats"
    blocked = beats / "sentinel.json"
    blocked.mkdir(parents=True)
    (blocked / "occupant").write_text("x", encoding="utf-8")

    before = sorted(p.name for p in beats.iterdir())
    daemon_heartbeat.beat("sentinel")          # must not raise: the total promise
    after = sorted(p.name for p in beats.iterdir())

    assert after == before, (
        f"the failed write left {sorted(set(after) - set(before))} behind in "
        f"the heartbeats directory"
    )


def test_the_failure_arrangement_really_fails(tmp_path, monkeypatch):
    """The control for the test above.

    A cleanup test whose write SUCCEEDS proves nothing: no tempfile is left
    behind when there was never a failure. So assert the arrangement is hostile
    -- the beat produced no heartbeat file -- which is what makes the emptiness
    next door meaningful rather than automatic.
    """
    monkeypatch.setattr(daemon_heartbeat, "get_workspace_root", lambda: tmp_path)
    beats = tmp_path / ".daemon-state" / "heartbeats"
    blocked = beats / "sentinel.json"
    blocked.mkdir(parents=True)
    (blocked / "occupant").write_text("x", encoding="utf-8")

    daemon_heartbeat.beat("sentinel")

    assert blocked.is_dir(), "the destination stopped being the obstacle"
    assert sorted(p.name for p in blocked.iterdir()) == ["occupant"], (
        "the beat wrote through the obstacle, so the failure never happened "
        "and the cleanup assertion next door is vacuous"
    )
    # And the happy path in the same tree still works, so the obstacle is
    # specific to this daemon's file rather than to the directory.
    daemon_heartbeat.beat("fireside")
    assert (beats / "fireside.json").is_file()
