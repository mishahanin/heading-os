"""Tests for scripts/sync-exchange-pulse.py — the daemon health pulse parser.

The spine under test: _last_job_ok() finds the most recent successful sync line
in daemon.log and reports a friendly relative age. The regression that motivated
this file: the R12 trace-id convention (2026-06-03) inserted an optional
"[<hex>] " correlation token between INFO and the message, and the parser regex
was not updated — so it silently skipped every post-R12 line and fell back to a
days-old pre-R12 line, reporting a false "Nd ago" while the daemon was healthy.
The module is loaded by path because its filename is kebab-case.
"""
import importlib.util
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_spec = importlib.util.spec_from_file_location(
    "sync_exchange_pulse", ROOT / "scripts" / "sync-exchange-pulse.py"
)
sync_exchange_pulse = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sync_exchange_pulse)


def _stamp(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S") + ",000"


def test_parses_post_r12_trace_id_line(tmp_path, monkeypatch):
    """A recent line carrying the [<hex>] trace-id token must be matched."""
    recent = datetime.now() - timedelta(minutes=12)
    log = tmp_path / "daemon.log"
    log.write_text(
        f"{_stamp(recent)} INFO [0d6113bbcc6b40cb85f03d73eb194e43] "
        "job-ok sync-exchange (exit=0)\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(sync_exchange_pulse, "LOG_FILE", log)
    assert sync_exchange_pulse._last_job_ok() == "12m ago"


def test_does_not_fall_back_to_stale_pre_r12_line(tmp_path, monkeypatch):
    """With both formats present, the newest (trace-id) line wins — not the old one.

    This is the exact failure mode: an old pre-R12 line sits days back, a fresh
    post-R12 line is minutes old. The buggy regex reported the stale one.
    """
    stale = datetime.now() - timedelta(days=6)
    fresh = datetime.now() - timedelta(minutes=3)
    log = tmp_path / "daemon.log"
    log.write_text(
        f"{_stamp(stale)} INFO job-ok sync-exchange (exit=0)\n"
        f"{_stamp(fresh)} INFO [abc123def456] job-ok sync-exchange (exit=0)\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(sync_exchange_pulse, "LOG_FILE", log)
    result = sync_exchange_pulse._last_job_ok()
    assert result == "3m ago"
    assert "d ago" not in result


def test_still_parses_pre_r12_line(tmp_path, monkeypatch):
    """Backward compatibility: the old format without a trace-id still matches."""
    recent = datetime.now() - timedelta(minutes=5)
    log = tmp_path / "daemon.log"
    log.write_text(
        f"{_stamp(recent)} INFO job-ok sync-exchange (exit=0)\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(sync_exchange_pulse, "LOG_FILE", log)
    assert sync_exchange_pulse._last_job_ok() == "5m ago"


def test_missing_log_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(sync_exchange_pulse, "LOG_FILE", tmp_path / "absent.log")
    assert sync_exchange_pulse._last_job_ok() is None
