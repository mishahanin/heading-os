"""`daemon_heartbeat.beat` promises "Never raises". It only meant OSError.

The module docstring and the function docstring both say the promise without a
qualifier: "a write failure logs a warning so the scheduler keeps ticking and
only the one beat is lost." The handler was `except OSError`. The try block
also calls `get_workspace_root()` and `tracing.get()`, and neither is confined
to OSError. A workspace root that will not resolve (marker file gone, an
unresolvable `~` in WORKSPACE_ROOT, a UID with no passwd entry) raises
RuntimeError straight through and into the caller's scheduler tick.

That matters because callers depend on the promise being total. Every caller of
`beat` is a daemon's scheduled job, and `fireside-bot-daemon` piggybacks the
beat on the one job whose purpose is the healthchecks.io ping. Losing a single
beat is the correct cost of a telemetry fault. Taking the scheduler with it
converts a telemetry fault into a monitoring outage.

No daemon is started. The root is a tmp_path or a fault-injected callable, and
the live `.daemon-state` tree is never written.

Run: python3 -m pytest
tests/test_a_liveness_beat_that_broke_its_never_raises_promise.py
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils import daemon_heartbeat

# Non-OSError failures the try block can genuinely produce. RuntimeError is
# what `Path.expanduser()` raises when no home directory can be determined and
# what a root resolver raises when its markers are gone; ValueError stands for
# the malformed-input family. Neither is an OSError, and that was the whole
# hole.
NON_OSERROR_FAULTS = (RuntimeError("workspace markers missing"),
                      ValueError("workspace root is not a path"))


@pytest.mark.parametrize("fault", NON_OSERROR_FAULTS, ids=lambda e: type(e).__name__)
def test_a_non_oserror_from_root_resolution_is_swallowed(monkeypatch, fault, caplog):
    def _raise():
        raise fault

    monkeypatch.setattr(daemon_heartbeat, "get_workspace_root", _raise)

    with caplog.at_level("WARNING"):
        daemon_heartbeat.beat("fireside")  # must return, not raise

    # Swallowed is not the same as silent: the workspace forbids a handler that
    # neither logs nor re-raises, and a lost beat the operator cannot see is
    # how a daemon goes quiet without anyone noticing.
    assert any("fireside" in r.getMessage() for r in caplog.records)


def test_a_non_oserror_from_tracing_is_swallowed(monkeypatch, tmp_path, caplog):
    monkeypatch.setattr(daemon_heartbeat, "get_workspace_root", lambda: tmp_path)
    monkeypatch.setattr(
        daemon_heartbeat.tracing, "get",
        lambda: (_ for _ in ()).throw(RuntimeError("trace store unavailable")))

    with caplog.at_level("WARNING"):
        daemon_heartbeat.beat("sentinel")

    assert any("sentinel" in r.getMessage() for r in caplog.records)
    # The beat is lost, which is the documented cost. Nothing half-written is
    # left behind for the watchdog to read as a live beat.
    assert not (tmp_path / ".daemon-state" / "heartbeats" / "sentinel.json").exists()


def test_a_healthy_beat_still_lands_after_the_widened_handler(monkeypatch, tmp_path):
    """The widening must not turn a working write into a swallowed no-op."""
    monkeypatch.setattr(daemon_heartbeat, "get_workspace_root", lambda: tmp_path)

    daemon_heartbeat.beat("bridge", config_version="7")

    path = tmp_path / ".daemon-state" / "heartbeats" / "bridge.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["daemon"] == "bridge"
    assert data["config_loaded_version"] == "7"
