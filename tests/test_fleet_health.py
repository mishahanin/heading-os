"""Fleet-health per-daemon reconciliation tests (R14, scrutiny M3).

Loads the hyphenated ``scripts/daemon-fleet-health.py`` module via importlib and
exercises the pure classification + verdict + exit-code functions. Asserts:

  - _classify_beat(): fresh -> ok, stale -> stale, no timestamp -> error;
  - a stale per-daemon beat degrades BOTH the verdict (green -> drift) AND the
    exit code (0 -> 1) even when the bridge record is green (M3);
  - with NO per-daemon beats supplied, the verdict text and exit code are
    byte-identical to the legacy behaviour (back-compat).

No live ``.daemon-state`` is read - records and statuses are constructed inline.

Run: python3 -m pytest tests/test_fleet_health.py
"""
import importlib.util
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Fixed reference instant. The fleet-health classifiers compute age against
# ``datetime.now(timezone.utc)``; the ``fh`` fixture freezes the module's clock
# to exactly this instant (see below) so a record stamped ``NOW - 5s`` is always
# 5s old at assert time, regardless of how long the full suite takes to reach
# this module. Without the freeze the prior module-level ``NOW`` drifted past
# the 120s stale threshold under load, flipping "ok" -> "stale" intermittently.
NOW = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


class _FrozenDatetime(datetime):
    """A datetime whose ``now()`` always returns the fixed ``NOW`` instant."""

    @classmethod
    def now(cls, tz=None):
        return NOW if tz is None else NOW.astimezone(tz)


@pytest.fixture(scope="module")
def fh():
    path = Path(__file__).resolve().parent.parent / "scripts" / "daemon-fleet-health.py"
    spec = importlib.util.spec_from_file_location("daemon_fleet_health_mod", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    # Freeze the module's wall clock to NOW. The classifiers parse the same
    # ``isoformat()`` timestamps the helpers below produce, so age is exact and
    # never load-coupled. ``datetime.fromisoformat`` is unaffected (it lives on
    # the real datetime, which _FrozenDatetime subclasses).
    mod.datetime = _FrozenDatetime
    return mod


def _ok_bridge_record():
    return {
        "status": "ok",
        "last_heartbeat": (NOW - timedelta(seconds=5)).isoformat(),
        "version": "3",
        "workspace": "ws/ceo-main",  # fake label; never used as a real path
    }


def _fresh_beat(name="bridge"):
    return {"daemon": name, "last_heartbeat": (NOW - timedelta(seconds=5)).isoformat()}


def _stale_beat(name="sentinel"):
    return {"daemon": name, "last_heartbeat": (NOW - timedelta(seconds=6000)).isoformat()}


# ============================================================
# _classify_beat
# ============================================================

def test_classify_beat_fresh_ok(fh):
    assert fh._classify_beat(_fresh_beat(), 120) == "ok"


def test_classify_beat_stale(fh):
    assert fh._classify_beat(_stale_beat(), 120) == "stale"


def test_classify_beat_no_timestamp_is_error(fh):
    assert fh._classify_beat({"daemon": "x"}, 120) == "error"
    assert fh._classify_beat({"status": "error", "daemon": "x"}, 120) == "error"


# ============================================================
# M3: a stale beat degrades verdict + exit code under a green bridge
# ============================================================

def test_stale_beat_degrades_verdict(fh):
    records = [_ok_bridge_record()]
    beat_statuses = ["ok", "stale"]  # bridge ok, sentinel stale
    text, color = fh._verdict(records, 120, None, None, beat_statuses)
    assert "drift" in text.lower()
    assert color == fh.YELLOW


def test_stale_beat_degrades_exit_code(fh):
    records = [_ok_bridge_record()]
    # Without beats: green bridge -> exit 0.
    assert fh._classify_fleet_exit_code(records, 120, None, None) == 0
    # With a stale beat: degrades to drift -> exit 1.
    assert fh._classify_fleet_exit_code(records, 120, None, None, ["ok", "stale"]) == 1


def test_error_beat_breaks_fleet(fh):
    records = [_ok_bridge_record()]
    assert fh._classify_fleet_exit_code(records, 120, None, None, ["error"]) == 2
    text, color = fh._verdict(records, 120, None, None, ["error"])
    assert "broken" in text.lower()
    assert color == fh.RED


# ============================================================
# Retired-clone discovery exclusion (ceo-main false-stale fix, 2026-06-20)
# ============================================================

@pytest.mark.parametrize("name", [
    "ceo-main", "ceo-main-kimi", "odin-heading-os",
    "CEO-Main",                      # case-insensitive
    ".heading-os-data", "ceo-main-data",  # engine data siblings, by `-data` suffix
])
def test_non_fleet_siblings_excluded(fh, name):
    assert fh._is_non_fleet_sibling(name) is True


@pytest.mark.parametrize("name", [
    ".heading-os",                   # the live engine itself
    "31c-exec-alice", "31c-crm-bob", "exec-bob",
])
def test_fleet_siblings_kept(fh, name):
    assert fh._is_non_fleet_sibling(name) is False


# ============================================================
# Back-compat: no beats -> byte-identical legacy output
# ============================================================

def test_no_beats_legacy_verdict_text(fh):
    records = [_ok_bridge_record()]
    text, color = fh._verdict(records, 120, None, None)
    assert text == "Fleet healthy: 1 workspace(s) ok."
    assert color == fh.GREEN


def test_no_beats_legacy_exit_code(fh):
    records = [_ok_bridge_record()]
    assert fh._classify_fleet_exit_code(records, 120, None, None) == 0
