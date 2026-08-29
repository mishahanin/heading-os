"""A corrupt heartbeat file must classify as `missing`, not end the pass.

`watchdog_core._read_beat` reads each beat with
``path.read_text(encoding="utf-8")`` and used to catch only
``(OSError, json.JSONDecodeError)``. Undecodable bytes raise
``UnicodeDecodeError``, which is a ``ValueError`` and neither of those, so the
exception escaped `_read_beat`, escaped `check_once`, and landed in the bridge
daemon's blanket per-tick handler, which logs and carries on. One byte-corrupt
file therefore stopped the WHOLE fleet from being classified on every tick,
including the daemons whose own beats were perfectly readable, while the daemon
reported itself healthy. `classify`'s docstring says a beat with no parseable
timestamp is `missing`; for invalid UTF-8 that was not true.

Nothing here starts, restarts or signals a daemon. Every path is a tmp_path
tree and the alert sink is a recording callable.

Run: python3 -m pytest
tests/test_a_beat_reader_that_died_on_bytes_it_could_not_decode.py
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts import watchdog_core

# Pinned instant. Nothing here reads the host clock, so the verdicts below do
# not depend on the hour, the weekday or how long the suite took to get here.
NOW = datetime(2026, 3, 17, 9, 30, 0, tzinfo=timezone.utc)

# Bytes that are not valid UTF-8 under any interpretation: 0xFF never starts a
# UTF-8 sequence. This is what a torn write or a restore through a mangling
# tool leaves behind.
UNDECODABLE = b"\xff\xfe\x00{"


@pytest.fixture
def beats(tmp_path):
    d = tmp_path / ".daemon-state" / "heartbeats"
    d.mkdir(parents=True)
    return d


def _fresh_beat(name: str) -> str:
    return json.dumps({"daemon": name, "last_heartbeat": NOW.isoformat()})


def test_undecodable_bytes_read_as_a_missing_beat(tmp_path, beats):
    (beats / "fireside.json").write_bytes(UNDECODABLE)
    # Guard the guard: the corpus this asserts over is non-empty and really is
    # undecodable, so a silently-absent file cannot make the test vacuous.
    assert (beats / "fireside.json").stat().st_size > 0
    with pytest.raises(UnicodeDecodeError):
        (beats / "fireside.json").read_text(encoding="utf-8")

    assert watchdog_core._read_beat(tmp_path, "fireside") is None


def test_one_corrupt_beat_does_not_stop_the_rest_of_the_fleet(tmp_path, beats):
    """The defect's real cost: the healthy daemons were never classified."""
    (beats / "fireside.json").write_bytes(UNDECODABLE)
    (beats / "bridge.json").write_text(_fresh_beat("bridge"), encoding="utf-8")

    fired = []

    report = watchdog_core.check_once(
        tmp_path,
        now=NOW,
        alert_fn=lambda sev, summary, detail, **kw: fired.append((sev, summary))
        or {"telegram": True},
        state_path=tmp_path / "watchdog-state.json",
        cadence={"fireside": (60, 120), "bridge": (60, 120)},
        realert_min=30,
    )

    statuses = {d["daemon"]: d["status"] for d in report["daemons"]}
    assert statuses == {"fireside": "missing", "bridge": "ok"}, statuses
    # The corrupt one is the ONLY one that alerts. Before the fix no daemon was
    # classified at all, so "bridge is up" was a fact the pass never reached.
    assert [s for s, _ in fired] == ["critical"]
    assert report["verdict"] == "down"


def test_a_corrupt_beat_is_not_confused_with_a_live_one(tmp_path, beats):
    """`missing` is a down state. It must not read as ok just by existing."""
    (beats / "sentinel.json").write_bytes(UNDECODABLE)

    report = watchdog_core.check_once(
        tmp_path,
        now=NOW,
        alert_fn=lambda *a, **k: {"telegram": True},
        state_path=tmp_path / "watchdog-state.json",
        cadence={"sentinel": (60, 120)},
        realert_min=30,
    )
    assert report["daemons"][0]["status"] == "missing"
    assert report["verdict"] == "down"


def test_valid_json_still_parses_after_the_widened_handler(tmp_path, beats):
    """The widening must not swallow a good beat into `missing`."""
    (beats / "bridge.json").write_text(_fresh_beat("bridge"), encoding="utf-8")
    record = watchdog_core._read_beat(tmp_path, "bridge")
    assert record is not None and record["daemon"] == "bridge"
