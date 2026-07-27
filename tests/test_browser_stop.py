"""Regression tests for `scripts.browser.stop_comet`.

Bug caught 2026-07-27: `python scripts/browser.py stop` printed
"Sent SIGTERM to PID <n>" and exited 0 while Brave kept running.

Two defects behind that one symptom:

1. The lock file records `subprocess.Popen(...).pid`, which on Debian/Ubuntu
   is the `/usr/bin/brave-browser` wrapper, not the real browser at
   `/opt/brave.com/brave/brave`. The wrapper exits immediately, so the tracked
   PID is dead (and may have been recycled onto an unrelated process).
2. `stop_comet` signalled that PID, reported success unconditionally, and
   unlinked the lock file in a `finally` block, so the caller was told the
   browser was down when it was not and lost the state needed to retry.

These tests exercise the public `stop_comet()` interface only.
"""

import json
import signal
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts import browser  # noqa: E402


@pytest.fixture
def lock(tmp_path, monkeypatch):
    """Point the module's lock-file lookup at a throwaway file."""
    f = tmp_path / "browser-cdp.json"
    f.write_text(json.dumps({"port": 9222, "pid": 4242, "browser": "brave"}))
    monkeypatch.setattr(browser, "_active_lock_file", lambda: f)
    return f


@pytest.fixture
def signals(monkeypatch):
    """Record every signal stop_comet sends instead of sending it."""
    sent = []
    monkeypatch.setattr(browser.os, "kill", lambda pid, sig: sent.append((pid, sig)))
    return sent


def _cdp(monkeypatch, *states):
    """Make _cdp_ready return each state in turn, holding the last one."""
    seq = list(states)
    monkeypatch.setattr(
        browser, "_cdp_ready", lambda *a, **k: seq.pop(0) if len(seq) > 1 else seq[0]
    )


def test_no_lock_file_is_not_a_success(monkeypatch):
    monkeypatch.setattr(browser, "_active_lock_file", lambda: None)
    assert browser.stop_comet() is False


def test_recycled_tracked_pid_is_never_signalled(lock, signals, monkeypatch):
    """The tracked PID belongs to something else now. Do not kill it."""
    monkeypatch.setattr(browser, "_pids_for_cdp_port", lambda port: [])
    monkeypatch.setattr(browser, "_pid_is_browser", lambda pid, browser_name: False)
    _cdp(monkeypatch, False)

    browser.stop_comet()

    assert 4242 not in [pid for pid, _ in signals], (
        "stop_comet signalled a PID it had not verified as the browser"
    )


def test_targets_the_process_owning_the_cdp_port(lock, signals, monkeypatch):
    """The real browser is found by CDP port, not by the stale tracked PID."""
    monkeypatch.setattr(browser, "_pids_for_cdp_port", lambda port: [1894831])
    monkeypatch.setattr(browser, "_pid_is_browser", lambda pid, browser_name: False)
    _cdp(monkeypatch, True, False)

    assert browser.stop_comet() is True
    assert (1894831, signal.SIGTERM) in signals


def test_survivor_reports_failure_and_keeps_the_lock(lock, signals, monkeypatch):
    """Browser still answering CDP after SIGTERM and SIGKILL: say so."""
    monkeypatch.setattr(browser, "_pids_for_cdp_port", lambda port: [1894831])
    monkeypatch.setattr(browser, "_pid_is_browser", lambda pid, browser_name: False)
    _cdp(monkeypatch, True)  # never goes down

    assert browser.stop_comet(timeout=0.2) is False
    assert lock.exists(), "lock file was discarded while the browser was still up"


def test_escalates_to_sigkill_when_sigterm_is_ignored(lock, signals, monkeypatch):
    monkeypatch.setattr(browser, "_pids_for_cdp_port", lambda port: [1894831])
    monkeypatch.setattr(browser, "_pid_is_browser", lambda pid, browser_name: False)
    _cdp(monkeypatch, True)

    browser.stop_comet(timeout=0.2)

    assert (1894831, signal.SIGKILL) in signals


def test_clears_the_lock_only_once_the_browser_is_gone(lock, signals, monkeypatch):
    monkeypatch.setattr(browser, "_pids_for_cdp_port", lambda port: [1894831])
    monkeypatch.setattr(browser, "_pid_is_browser", lambda pid, browser_name: False)
    _cdp(monkeypatch, True, False)

    assert browser.stop_comet() is True
    assert not lock.exists()


# Real `ps -eo pid=,args=` output captured on WSL2, 2026-07-27, while a CDP
# Brave session was up. Truncated in width only.
PS_OUTPUT = """\
 2234780 /bin/bash /usr/bin/brave-browser --remote-debugging-port=9222 --profile-directory=Default --user-data-dir=/home/administrator/.config/BraveSoftware/Brave-Browser --no-first-run
 2234786 /opt/brave.com/brave/brave --remote-debugging-port=9222 --profile-directory=Default --user-data-dir=/home/administrator/.config/BraveSoftware/Brave-Browser --no-first-run
 2234851 /opt/brave.com/brave/brave --type=renderer --crashpad-handler-pid=2234789 --user-data-dir=/home/administrator/.config/BraveSoftware/Brave-Browser --remote-debugging-port=9222
 2234852 /opt/brave.com/brave/brave --type=renderer --crashpad-handler-pid=2234789 --user-data-dir=/home/administrator/.config/BraveSoftware/Brave-Browser --remote-debugging-port=9222
 2234789 /opt/brave.com/brave/chrome_crashpad_handler --monitor-self --database=/home/administrator/.config/BraveSoftware/Brave-Browser
 9999 grep --color=auto -- --remote-debugging-port=9222
"""


def test_parser_picks_only_the_real_browser_process():
    """Wrapper, renderers, and observers must all be excluded."""
    assert browser._parse_cdp_owner_pids(PS_OUTPUT, 9222, self_pid=1) == [2234786]


def test_parser_excludes_the_bash_launcher_wrapper():
    """2234780 is `/bin/bash /usr/bin/brave-browser`, the PID Popen reports.

    Signalling it orphans the browser instead of stopping it. This is the
    exact defect that made `browser.py stop` a no-op.
    """
    assert 2234780 not in browser._parse_cdp_owner_pids(PS_OUTPUT, 9222, self_pid=1)


def test_parser_excludes_renderers_that_inherit_the_flag():
    got = browser._parse_cdp_owner_pids(PS_OUTPUT, 9222, self_pid=1)
    assert 2234851 not in got and 2234852 not in got


def test_parser_ignores_a_different_port():
    assert browser._parse_cdp_owner_pids(PS_OUTPUT, 9333, self_pid=1) == []


def test_parser_never_returns_its_own_pid():
    assert 2234786 not in browser._parse_cdp_owner_pids(
        PS_OUTPUT, 9222, self_pid=2234786
    )


def test_already_down_is_a_clean_success(lock, signals, monkeypatch):
    """Nothing on the port and no live browser PID: tidy up and succeed."""
    monkeypatch.setattr(browser, "_pids_for_cdp_port", lambda port: [])
    monkeypatch.setattr(browser, "_pid_is_browser", lambda pid, browser_name: False)
    _cdp(monkeypatch, False)

    assert browser.stop_comet() is True
    assert not lock.exists()
    assert signals == []
