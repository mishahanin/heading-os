"""Tests for the email-backup fallback in scripts/fireside-bot.py.

email-backup is the last resort for a speaker who never answered the bot's DMs.
It shelled out to a bare "python", which does not exist on the service host
(only python3 and the venv), so from the job's first run every send raised
FileNotFoundError, was swallowed into errors.log, and the job printed sent=0
while pinging its healthcheck green. Nothing outside the log could tell a
never-worked fallback from a week where nobody needed one.

This test pins the interpreter: the subprocess is spawned with sys.executable,
the interpreter already running the daemon, which carries the pinned deps.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from datetime import timedelta
from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def fb():
    """Load fireside-bot.py as a module (hyphen in filename)."""
    path = Path(__file__).resolve().parent.parent / "scripts" / "fireside-bot.py"
    spec = importlib.util.spec_from_file_location("fireside_bot", str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_email_backup_spawns_sys_executable(fb, tmp_path, monkeypatch, capsys):
    """The send subprocess uses sys.executable, not a bare "python"."""
    today = fb._today_local_date()
    session_date = (today + timedelta(days=3)).isoformat()

    schedule = [{
        "week": 1, "session_date": session_date, "day": "Mon", "slot": 1,
        "theme": "A first job", "speaker_name": "Test Speaker",
        "speaker_username": "testspeaker", "swapped_with": None,
        "no_show": False, "completed": False,
    }]
    roster = {"testspeaker": {"name": "Test Speaker", "email": "test@example.com",
                              "telegram_user_id": 42, "active": True}}

    # A failed 2wk DM. Since 2026-08-24 silence alone arms the fallback, so this
    # row is no longer the trigger; it is left as one realistic shape of it.
    dm_log = tmp_path / fb.DM_LOG
    dm_log.write_text(json.dumps({
        "dm_type": "2wk", "speaker_username": "testspeaker",
        "session_date": session_date, "delivered": False,
    }) + "\n", encoding="utf-8")

    monkeypatch.setattr(fb, "state_path", lambda name: tmp_path / name)
    monkeypatch.setattr(fb, "load_state",
                        lambda name: {fb.SCHEDULE: schedule, fb.TRIBE_ROSTER: roster}.get(name))

    spawned = []

    class _Result:
        returncode = 0
        stderr = ""

    def _fake_run(cmd, **kwargs):
        spawned.append(cmd)
        return _Result()

    monkeypatch.setattr(fb, "_log_dm", lambda *a, **k: None)
    monkeypatch.setattr("subprocess.run", _fake_run)

    fb.cmd_email_backup(fb.argparse.Namespace())

    assert len(spawned) == 1, f"expected one send, got {spawned}"
    cmd = spawned[0]
    assert cmd[0] == sys.executable
    assert cmd[0] != "python", "a bare 'python' does not resolve on the service host"
    assert cmd[1] == "scripts/send-email.py"
    assert "test@example.com" in cmd
    assert "sent=1" in capsys.readouterr().out
