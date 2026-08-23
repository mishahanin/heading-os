"""Tests for scripts/fireside-bot-daemon.py."""
from __future__ import annotations

import importlib.util
import subprocess
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
        "cycle-end-invite", "cycle-rollover",
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


def _reaped_pid() -> int:
    """A PID that was alive, then exited and was reaped. Genuinely stale.

    The 2026-08-23 audit caught the previous version asserting that PID 999999
    "cannot" exist. On this machine `/proc/sys/kernel/pid_max` is 4194304, so
    999999 is an ordinary allocatable PID: whether the test measured anything
    depended on which processes the host happened to be running. A busy CI
    runner or a container with a large PID space could fail it outright.

    Spawning and reaping a child gives a PID that is dead by construction, on
    every platform, and the caller can verify the daemon module agreed it was
    alive first.
    """
    proc = subprocess.Popen([sys.executable, "-c", ""])
    proc.wait(timeout=30)
    return proc.pid


def test_is_daemon_alive_false_when_pid_is_stale(daemon_mod, tmp_path, monkeypatch):
    pid_file = tmp_path / "daemon.pid"
    pid_file.write_text(str(_reaped_pid()))
    monkeypatch.setattr(daemon_mod, "PID_FILE", pid_file)
    assert daemon_mod.is_daemon_alive() is False


def test_is_daemon_alive_true_for_a_live_pid(daemon_mod, tmp_path, monkeypatch):
    """The other direction. Without it, a probe stuck at False would pass.

    That is not hypothetical: the stale-PID test above is the only caller of
    `_pid_is_running`, and an `is_daemon_alive` that always returned False
    satisfied the whole file.
    """
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        pid_file = tmp_path / "daemon.pid"
        pid_file.write_text(str(proc.pid))
        monkeypatch.setattr(daemon_mod, "PID_FILE", pid_file)
        assert daemon_mod.is_daemon_alive() is True
    finally:
        proc.kill()
        proc.wait(timeout=30)


def test_is_daemon_alive_false_for_a_nonsense_pid_file(daemon_mod, tmp_path,
                                                       monkeypatch):
    pid_file = tmp_path / "daemon.pid"
    monkeypatch.setattr(daemon_mod, "PID_FILE", pid_file)
    for junk in ("", "not-a-pid", "0", "-1", "  \n"):
        pid_file.write_text(junk)
        assert daemon_mod.is_daemon_alive() is False, repr(junk)
