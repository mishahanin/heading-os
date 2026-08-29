"""One byte-corrupt heartbeat must not end the whole fleet report.

`daemon-fleet-health._read_heartbeat` promises, in its own docstring, "the
heartbeat dict or a synthetic 'missing'/'error' record". It read the file with
``hb.read_text(encoding="utf-8")`` and caught only
``(OSError, json.JSONDecodeError)``. Undecodable bytes raise
``UnicodeDecodeError``, a ``ValueError``, which is neither, so the exception
escaped the function that promised a record and killed the loop in `main`.

These files are written by OTHER machines: exec workspaces, and the CRM mirrors
an exec's `push-all.py` pushes. A torn write on somebody else's host therefore
took down the one tool whose job is to say which daemons are down. That is the
same failure the non-dict branch below the handler was already written to
close, left open one exception class over.

This defect was not in the audit reports; it was found while fixing the
identical hole in `watchdog_core._read_beat` and is fixed here for the same
reason. No daemon runs and no live `.daemon-state` is read.

Run: python3 -m pytest
tests/test_a_fleet_report_that_died_on_one_undecodable_heartbeat.py
"""
import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Pinned instant, so no assertion below depends on the day the suite ran.
NOW = datetime(2026, 3, 17, 9, 30, 0, tzinfo=timezone.utc)

UNDECODABLE = b"\xff\xfe\x00{"


@pytest.fixture(scope="module")
def fh():
    path = Path(__file__).resolve().parent.parent / "scripts" / "daemon-fleet-health.py"
    spec = importlib.util.spec_from_file_location("daemon_fleet_health_undecodable", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _local_ws(root: Path, name: str, payload: bytes) -> Path:
    ws = root / name
    (ws / ".daemon-state").mkdir(parents=True)
    (ws / ".daemon-state" / "heartbeat.json").write_bytes(payload)
    return ws


def test_an_undecodable_heartbeat_yields_an_error_record(tmp_path, fh):
    ws = _local_ws(tmp_path, "acme-telecom-ws", UNDECODABLE)
    # The corpus is real: the file exists, is non-empty, and genuinely will not
    # decode. A missing file would take the 'missing' branch and prove nothing.
    hb = ws / ".daemon-state" / "heartbeat.json"
    assert hb.stat().st_size > 0
    with pytest.raises(UnicodeDecodeError):
        hb.read_text(encoding="utf-8")

    record = fh._read_heartbeat(ws)

    assert record["status"] == "error"
    assert record["workspace"] == str(ws)
    assert "parse failed" in record["detail"]


def test_a_crm_mirror_with_undecodable_bytes_yields_a_record_too(tmp_path, fh):
    ws = tmp_path / "31c-crm-jbond"
    ws.mkdir()
    (ws / "bridge-heartbeat.json").write_bytes(UNDECODABLE)

    record = fh._read_heartbeat(ws, kind="crm-mirror")

    assert record["status"] == "error"


def test_a_readable_neighbour_is_still_reported(tmp_path, fh):
    """The cost of the crash was the workspaces that never got read at all."""
    good_payload = json.dumps({
        "status": "ok",
        "last_heartbeat": NOW.isoformat(),
        "version": "3",
    }).encode("utf-8")
    bad = _local_ws(tmp_path, "acme-telecom-ws", UNDECODABLE)
    good = _local_ws(tmp_path, "universal-exports-ws", good_payload)

    records = [fh._read_heartbeat(bad), fh._read_heartbeat(good)]

    assert [r["status"] for r in records] == ["error", "ok"]


def test_valid_json_still_parses_after_the_widened_handler(tmp_path, fh):
    payload = json.dumps({"status": "ok", "last_heartbeat": NOW.isoformat()}).encode("utf-8")
    ws = _local_ws(tmp_path, "universal-exports-ws", payload)

    record = fh._read_heartbeat(ws)

    assert record["status"] == "ok"
    assert record["workspace"] == str(ws)
