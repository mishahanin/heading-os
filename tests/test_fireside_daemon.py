"""Tests for scripts/fireside-bot-daemon.py."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture(scope="module")
def daemon_mod():
    """Load fireside-bot-daemon.py as a module (hyphen in filename)."""
    path = Path(__file__).resolve().parent.parent / "scripts" / "fireside-bot-daemon.py"
    spec = importlib.util.spec_from_file_location("fireside_bot_daemon", str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_job_specs_complete(daemon_mod):
    """Every JOB_SPECS entry in fireside-bot-daemon.py is the expected set.

    Job functions live as cmd_* in scripts/fireside-bot.py. The R14 heartbeat
    job (bf4f1acb) is the 10th entry; keep this set in lockstep with it.
    """
    expected = {
        "poll", "heartbeat", "speaker-dms", "sunday-preview", "dayof-reminders",
        "helmsman-brief", "weekly-discrepancy-report", "email-backup",
        "unpin-weekly", "health-check", "topic-nudge", "topic-digest",
        "cycle-end-invite",
    }
    assert set(daemon_mod.JOB_SPECS.keys()) == expected


def test_job_specs_have_trigger(daemon_mod):
    """Every job spec includes either a cron or interval trigger config."""
    for name, spec in daemon_mod.JOB_SPECS.items():
        assert "trigger" in spec, f"{name} missing trigger key"
        kind = spec["trigger"]["kind"]
        assert kind in ("cron", "interval"), f"{name} unknown trigger kind {kind}"


def test_is_daemon_alive_false_when_no_pid_file(daemon_mod, tmp_path, monkeypatch):
    monkeypatch.setattr(daemon_mod, "PID_FILE", tmp_path / "no-such.pid")
    assert daemon_mod.is_daemon_alive() is False


def test_is_daemon_alive_false_when_pid_is_stale(daemon_mod, tmp_path, monkeypatch):
    pid_file = tmp_path / "daemon.pid"
    pid_file.write_text("999999")  # implausible PID
    monkeypatch.setattr(daemon_mod, "PID_FILE", pid_file)
    assert daemon_mod.is_daemon_alive() is False
