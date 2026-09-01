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

THE FIX REACHED ONE READER OF THREE. Measured 2026-09-01, on the tree as it
stood, by calling each reader directly with the same four bytes this file
already uses:

    _read_heartbeat(ws)                 -> {'status': 'error', ...}   (fixed)
    _collect_daemon_beats(ws)           -> UnicodeDecodeError          (open)
    _read_corporate_config_version(ws)  -> UnicodeDecodeError          (open)

Both open readers are called from `main`, on lines 569 and 585, so either one
took the whole report down exactly as `_read_heartbeat` used to. The per-daemon
beat is the worse of the two: it is written by a daemon on THIS host every few
seconds, so a torn write needs no other machine at all. `_collect_daemon_beats`
also had no non-dict branch, so `rec.get` raised AttributeError on a beat
holding `[]`, `null`, a string or a number, which is the second half of the same
lesson `_read_heartbeat` learned below its handler.

Nothing above could see either. Every test in this file called
`_read_heartbeat`, and a name-keyed pin over one function cannot notice that its
sibling twenty lines down reads a file the same way. The four tests added at the
bottom drive the other two readers.

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


# ---------------------------------------------------------------------------
# The two readers the first fix did not reach
# ---------------------------------------------------------------------------

def _beat(ws: Path, name: str, payload: bytes) -> Path:
    d = ws / ".daemon-state" / "heartbeats"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{name}.json").write_bytes(payload)
    return ws


GOOD_BEAT = json.dumps({"last_heartbeat": NOW.isoformat()}).encode("utf-8")


@pytest.mark.parametrize("payload, detail", [
    (UNDECODABLE, "parse failed"),
    (b"{not json", "parse failed"),
    (b"[]", "not an object"),
    (b"null", "not an object"),
    (b'"a string"', "not an object"),
    (b"7", "not an object"),
])
def test_a_per_daemon_beat_this_reader_cannot_use_becomes_an_error_record(
        tmp_path, fh, payload, detail):
    """`_collect_daemon_beats` is documented to RETURN a list, not to raise.

    The corpus is real for the undecodable row: the bytes are on disk and
    `read_text` genuinely refuses them, so this is not a stub standing in for a
    decode failure.
    """
    ws = _beat(tmp_path / "acme-telecom-ws", "sentinel", payload)
    if payload is UNDECODABLE:
        with pytest.raises(UnicodeDecodeError):
            (ws / ".daemon-state" / "heartbeats" / "sentinel.json").read_text(
                encoding="utf-8")

    beats = fh._collect_daemon_beats(ws)

    assert [b["daemon"] for b in beats] == ["sentinel"]
    assert beats[0]["status"] == "error"
    assert detail in beats[0]["detail"]
    assert beats[0]["workspace"] == str(ws)


def test_a_readable_beat_beside_a_corrupt_one_is_still_reported(tmp_path, fh):
    """The cost of the raise was the daemons that never got read at all.

    `zz-sentinel` sorts AFTER the corrupt file, so a reader that dies on the
    first bad beat loses it. That ordering is the whole point of the row.
    """
    ws = tmp_path / "acme-telecom-ws"
    _beat(ws, "aa-corrupt", UNDECODABLE)
    _beat(ws, "zz-sentinel", GOOD_BEAT)

    beats = fh._collect_daemon_beats(ws)

    assert [(b["daemon"], b.get("status")) for b in beats] == [
        ("aa-corrupt", "error"), ("zz-sentinel", None)]
    assert fh._classify_beat(beats[0], 120) == "error"


def test_a_well_formed_beat_still_comes_through_unchanged(tmp_path, fh):
    """The anchor. A guard that rejected everything would pass the rows above."""
    ws = _beat(tmp_path / "universal-exports-ws", "bridge", GOOD_BEAT)

    beats = fh._collect_daemon_beats(ws)

    assert len(beats) == 1
    assert beats[0]["daemon"] == "bridge"
    assert beats[0]["last_heartbeat"] == NOW.isoformat()
    assert beats[0]["workspace"] == str(ws)
    assert "detail" not in beats[0]


@pytest.mark.parametrize("payload, expected", [
    (UNDECODABLE, None),
    (b"version: \xff\xfe", None),
    (b"just: [a, broken\n", None),
    (b"- a-list\n", None),
    (b"", None),
    (b"version: 4\n", "4"),
])
def test_the_corporate_config_reader_answers_none_instead_of_raising(
        tmp_path, fh, payload, expected):
    """Its docstring promises None "if the file is missing or unparseable".

    Undecodable bytes are unparseable, and `UnicodeDecodeError` is neither an
    `OSError` nor a `yaml.YAMLError`, so the promise was broken in the one
    configuration a half-finished `/push-updates` produces. The last row is the
    anchor: a widened handler that swallowed everything would return None here
    too.
    """
    pytest.importorskip("yaml", reason="the reader returns None without PyYAML")
    cfg = tmp_path / "corporate" / "daemon"
    cfg.mkdir(parents=True)
    (cfg / "config.yaml").write_bytes(payload)

    assert fh._read_corporate_config_version(tmp_path) == expected
