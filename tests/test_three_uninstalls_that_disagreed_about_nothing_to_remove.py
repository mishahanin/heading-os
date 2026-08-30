#!/usr/bin/env python3
"""One state, three platforms, three different answers.

`scripts/utils/schedule.py` says in its module docstring that install functions
"return True on verified success, False otherwise" and that "all uninstall
functions are idempotent (no-op if nothing to remove)". The boolean IS the
signal, and for the identical state - no task, no plist, no unit, nothing to
remove - the three platform paths answered False, False and True.

That reading matters on exactly the path `uninstall_sync_schedule` was kept
alive for: tearing down a legacy `31c-sync-*` artifact that is already gone.

Measured 2026-08-30 before the fix, with no artifact present:
    windows -> False, darwin -> False, linux -> True.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.utils import schedule  # noqa: E402


class _Result:
    def __init__(self, returncode, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    """A home with no LaunchAgents and no systemd user units."""
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    return tmp_path


@pytest.fixture
def schtasks(monkeypatch):
    """Record every schtasks invocation and answer from a scripted table."""
    calls = []
    table = {}

    def fake_run(cmd, check=False):
        calls.append(list(cmd))
        for prefix, result in table.items():
            if list(cmd)[:len(prefix)] == list(prefix):
                return result
        return _Result(1, stderr="ERROR: The system cannot find the file specified.")

    monkeypatch.setattr(schedule, "_run", fake_run)
    return calls, table


def test_the_three_platforms_agree_that_nothing_to_remove_is_success(
        fake_home, schtasks):
    """The measured divergence: same state, one answer now."""
    _calls, _table = schtasks  # query returns 1 -> task absent
    answers = {
        p: schedule.uninstall_sync_schedule("jamesbond", target_platform=p)
        for p in ("windows", "darwin", "linux")
    }
    assert answers == {"windows": True, "darwin": True, "linux": True}, answers


def test_the_windows_path_does_not_delete_a_task_it_never_found(fake_home, schtasks):
    """A no-op must not issue a destructive command that then reports failure."""
    calls, _table = schtasks
    assert schedule.uninstall_sentinel_schedule("jamesbond", target_platform="windows")
    assert calls == [["schtasks", "/query", "/tn", "31C-Sentinel-jamesbond"]], calls


def test_a_windows_task_that_exists_is_deleted_and_reported_true(fake_home, schtasks):
    """The positive case: absence-is-True must not mask a real removal."""
    calls, table = schtasks
    table[("schtasks", "/query")] = _Result(0, stdout="31C-Sentinel-jamesbond")
    table[("schtasks", "/delete")] = _Result(0)
    assert schedule.uninstall_sentinel_schedule("jamesbond", target_platform="windows")
    assert ["schtasks", "/delete", "/tn", "31C-Sentinel-jamesbond", "/f"] in calls


def test_a_windows_delete_that_fails_is_still_reported_false(fake_home, schtasks):
    """A task that EXISTS and would not delete is a failure, not a no-op."""
    _calls, table = schtasks
    table[("schtasks", "/query")] = _Result(0, stdout="31C-Sentinel-jamesbond")
    table[("schtasks", "/delete")] = _Result(1, stderr="Access is denied.")
    assert schedule.uninstall_sentinel_schedule(
        "jamesbond", target_platform="windows") is False


def test_a_missing_schtasks_binary_is_not_reported_as_nothing_to_remove(
        fake_home, schtasks):
    """"I cannot tell" must not be answered with "there was nothing there"."""
    _calls, table = schtasks
    table[("schtasks",)] = _Result(127, stderr="schtasks: not found")
    assert schedule.uninstall_sentinel_schedule(
        "jamesbond", target_platform="windows") is False


def test_an_existing_launchd_plist_is_removed_and_reported_true(fake_home, monkeypatch):
    """The positive case for darwin: absence-is-True must not mask a real removal."""
    agents = fake_home / "Library" / "LaunchAgents"
    agents.mkdir(parents=True)
    plist = agents / "io.31c.sentinel.jamesbond.plist"
    plist.write_text("<plist/>", encoding="utf-8")
    monkeypatch.setattr(schedule, "_run", lambda cmd, check=False: _Result(0))

    assert schedule.uninstall_sentinel_schedule("jamesbond", target_platform="darwin")
    assert not plist.exists()


def test_an_unsupported_platform_is_still_a_refusal(fake_home, schtasks):
    """The negative case: not every uninstall answer became True."""
    assert schedule.uninstall_sync_schedule(
        "jamesbond", target_platform="plan9") is False
