"""Watchdog classify + dedup + recovery tests (R14, plan Step 9).

Drives ``scripts.watchdog_core.check_once`` against a hermetic temp workspace
with the clock, the alert sink, the dedup state file, and the cadence all
injected - no real Telegram, no real config, no live ``.daemon-state``. Covers:

  - classify(): fresh beat -> ok, stale beat -> silent, absent -> missing;
  - a daemon going ok -> down fires exactly one critical alert;
  - while down, a second pass WITHIN the re-alert window does NOT re-fire (dedup);
  - a pass AFTER the window re-fires once;
  - a daemon going down -> ok fires one ``info`` "resumed" alert.

Run: python3 -m pytest tests/test_watchdog.py
"""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts import watchdog_core

NOW0 = datetime(2026, 6, 4, 12, 0, 0, tzinfo=timezone.utc)
CADENCE = {"sentinel": (60, 120)}  # threshold 180s


class _Rec:
    """Records alert_fn calls; never touches Telegram or the live queue."""

    def __init__(self):
        self.calls = []

    def __call__(self, severity, summary, detail="", *, source=""):
        self.calls.append((severity, summary, source))


@pytest.fixture
def root(tmp_path):
    (tmp_path / ".daemon-state" / "heartbeats").mkdir(parents=True)
    return tmp_path


def _write_beat(root: Path, name: str, *, age_s: float, at: datetime = NOW0) -> None:
    ts = (at - timedelta(seconds=age_s)).isoformat()
    p = root / ".daemon-state" / "heartbeats" / f"{name}.json"
    p.write_text(json.dumps({"daemon": name, "last_heartbeat": ts}), encoding="utf-8")


def _run(root, rec, *, now=NOW0):
    return watchdog_core.check_once(
        root,
        now=now,
        alert_fn=rec,
        state_path=root / ".daemon-state" / "watchdog-state.json",
        cadence=CADENCE,
        realert_min=30,
    )


# ============================================================
# classify()
# ============================================================

def test_classify_fresh_is_ok():
    rec = {"last_heartbeat": (NOW0 - timedelta(seconds=10)).isoformat()}
    assert watchdog_core.classify(rec, 180, NOW0) == "ok"


def test_classify_stale_is_silent():
    rec = {"last_heartbeat": (NOW0 - timedelta(seconds=600)).isoformat()}
    assert watchdog_core.classify(rec, 180, NOW0) == "silent"


def test_classify_absent_is_missing():
    assert watchdog_core.classify(None, 180, NOW0) == "missing"
    assert watchdog_core.classify({"daemon": "x"}, 180, NOW0) == "missing"


# ============================================================
# Host scoping: load_expected / load_cadence (fleet split across hosts)
# ============================================================

def _patch_config(monkeypatch, cfg):
    """Patch the load_config the watchdog imports so config reads are hermetic."""
    import scripts.bridge_daemon.config as cfgmod

    monkeypatch.setattr(cfgmod, "load_config", lambda root: cfg)


def test_load_expected_uses_config_scope(monkeypatch, tmp_path):
    _patch_config(monkeypatch, {"daemon": {"watchdog": {"expect": ["bridge"]}}})
    assert watchdog_core.load_expected(tmp_path) == ("bridge",)


def test_load_expected_falls_back_to_full_fleet(monkeypatch, tmp_path):
    # No `expect` key -> full fleet, preserving single-host back-compat.
    _patch_config(monkeypatch, {"daemon": {"watchdog": {}}})
    assert watchdog_core.load_expected(tmp_path) == watchdog_core.EXPECTED_DAEMONS
    # Empty list is treated as unset, not "watch nothing".
    _patch_config(monkeypatch, {"daemon": {"watchdog": {"expect": []}}})
    assert watchdog_core.load_expected(tmp_path) == watchdog_core.EXPECTED_DAEMONS


def test_load_cadence_scoped_to_expected(monkeypatch, tmp_path):
    # Config lists cadence for all five, but this host expects only the bridge:
    # the off-host four must NOT appear in the checked set (else false criticals).
    _patch_config(monkeypatch, {
        "daemon": {
            "watchdog": {
                "expect": ["bridge"],
                "cadence": {
                    "bridge": {"expected": 60, "grace": 120},
                    "fireside": {"expected": 60, "grace": 120},
                    "sentinel": {"expected": 60, "grace": 120},
                },
            }
        }
    })
    cadence = watchdog_core.load_cadence(tmp_path)
    assert set(cadence) == {"bridge"}
    assert cadence["bridge"] == (60, 120)


# ============================================================
# check_once: alert on missed beat
# ============================================================

def test_missing_daemon_fires_one_critical(root):
    rec = _Rec()
    report = _run(root, rec)  # no beat file written -> missing
    assert report["verdict"] == "down"
    assert report["alerts_fired"] == 1
    assert len(rec.calls) == 1
    sev, summary, source = rec.calls[0]
    assert sev == "critical"
    assert "sentinel" in summary and "missing" in summary
    assert source == "watchdog"


def test_stale_beat_fires_critical(root):
    _write_beat(root, "sentinel", age_s=600)
    rec = _Rec()
    report = _run(root, rec)
    assert report["verdict"] == "down"
    assert rec.calls[0][0] == "critical"
    assert "silent" in rec.calls[0][1]


def test_fresh_beat_no_alert(root):
    _write_beat(root, "sentinel", age_s=10)
    rec = _Rec()
    report = _run(root, rec)
    assert report["verdict"] == "ok"
    assert report["alerts_fired"] == 0
    assert rec.calls == []


# ============================================================
# Dedup + recovery
# ============================================================

def test_dedup_suppresses_repeat_within_window(root):
    rec = _Rec()
    _run(root, rec, now=NOW0)                       # first: down -> 1 alert
    _run(root, rec, now=NOW0 + timedelta(minutes=5))  # within 30m window -> no re-alert
    assert len(rec.calls) == 1


def test_realert_after_window(root):
    rec = _Rec()
    _run(root, rec, now=NOW0)                          # down -> alert 1
    _run(root, rec, now=NOW0 + timedelta(minutes=31))  # past 30m window -> alert 2
    assert len(rec.calls) == 2
    assert all(c[0] == "critical" for c in rec.calls)


def test_recovery_fires_one_info(root):
    rec = _Rec()
    _run(root, rec, now=NOW0)  # missing -> down, critical alert
    # Beat resumes; classify ok at NOW1.
    now1 = NOW0 + timedelta(minutes=10)
    _write_beat(root, "sentinel", age_s=5, at=now1)
    report = _run(root, rec, now=now1)
    assert report["verdict"] == "ok"
    # One critical (down) + one info (recovered).
    assert len(rec.calls) == 2
    assert rec.calls[1][0] == "info"
    assert "resumed" in rec.calls[1][1]
