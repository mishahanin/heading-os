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
# Brave session was up. Truncated in width, and the capturing operator's home
# directory replaced by a synthetic one: this repository is public, and a home
# path is not an entity token, so `content-guard.py` reports it clean. Do NOT
# restore a real path here when refreshing the capture. The parser reads the
# port flag, `--type=` and argv0 only, so the value is inert to every assertion.
PS_OUTPUT = """\
 2234780 /bin/bash /usr/bin/brave-browser --remote-debugging-port=9222 --profile-directory=Default --user-data-dir=/home/builder/.config/BraveSoftware/Brave-Browser --no-first-run
 2234786 /opt/brave.com/brave/brave --remote-debugging-port=9222 --profile-directory=Default --user-data-dir=/home/builder/.config/BraveSoftware/Brave-Browser --no-first-run
 2234851 /opt/brave.com/brave/brave --type=renderer --crashpad-handler-pid=2234789 --user-data-dir=/home/builder/.config/BraveSoftware/Brave-Browser --remote-debugging-port=9222
 2234852 /opt/brave.com/brave/brave --type=renderer --crashpad-handler-pid=2234789 --user-data-dir=/home/builder/.config/BraveSoftware/Brave-Browser --remote-debugging-port=9222
 2234789 /opt/brave.com/brave/chrome_crashpad_handler --monitor-self --database=/home/builder/.config/BraveSoftware/Brave-Browser
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


def test_no_targets_while_cdp_still_answers_is_not_a_success(lock, signals, monkeypatch):
    """The negative half of the test above, and the original 2026-07-27 symptom.

    `stop_comet` takes the "already stopped" exit only when BOTH conditions hold:
    no PID to signal AND the port has gone quiet. Nothing measured the second
    one. MEASURED 2026-09-01: narrowing that condition to `if not targets` left
    all 46 tests across the three files that import this module green, while
    `stop` printed "Browser already stopped; clearing lock", returned True, and
    deleted the lock with Brave still answering CDP - which is, word for word,
    the defect this file's docstring was opened for.

    `ps` failing to name the owner is not hypothetical: `_pids_for_cdp_port`
    returns [] on win32 by design, and on POSIX whenever `ps` is absent or
    errors.
    """
    monkeypatch.setattr(browser, "_pids_for_cdp_port", lambda port: [])
    monkeypatch.setattr(browser, "_pid_is_browser", lambda pid, browser_name: False)
    _cdp(monkeypatch, True)  # the browser is up and answering

    assert browser.stop_comet(timeout=0.2) is False, (
        "stop reported success with the browser still answering CDP"
    )
    assert lock.exists(), "the lock was cleared while the browser was still up"


def test_the_sigkill_wait_is_capped_at_five_seconds(lock, signals, monkeypatch):
    """`min(timeout, 5.0)` is a stated bound with nothing standing on it.

    MEASURED 2026-09-01: replacing it with a bare `timeout` survived all 46
    tests. A caller passing a generous SIGTERM budget then pays it twice, and
    `cmd_stop` runs in the foreground of the operator's terminal.
    """
    waits = []
    monkeypatch.setattr(browser, "_pids_for_cdp_port", lambda port: [1894831])
    monkeypatch.setattr(browser, "_pid_is_browser", lambda pid, browser_name: False)
    _cdp(monkeypatch, True)
    monkeypatch.setattr(
        browser, "_wait_until_cdp_down",
        lambda port, timeout: waits.append(timeout) or False,
    )

    assert browser.stop_comet(timeout=60.0) is False
    assert waits == [60.0, 5.0], (
        f"the SIGTERM and SIGKILL waits were {waits}; the SIGKILL wait is "
        "supposed to be capped at 5 s"
    )


# A `ps -eo pid=,args=` line whose first token is not a PID. An argv holding a
# newline wraps onto a continuation line, and that line still carries the CDP
# flag inherited from the process it belongs to. `head.isdigit()` is what keeps
# `int(head)` off it.
PS_WITH_A_WRAPPED_ARGV = """\
 2234786 /opt/brave.com/brave/brave --remote-debugging-port=9222 --user-data-dir=/home/builder/.config/Brave
 2234900 /opt/brave.com/brave/brave --type=utility --lang=en-GB --window-title=first line
second line of a wrapped argv --remote-debugging-port=9222
"""


def test_a_wrapped_ps_line_does_not_crash_the_parser():
    """MEASURED 2026-09-01: dropping the `head.isdigit()` guard survived all 46
    tests across the three files importing this module, because every captured
    fixture line begins with a PID. On this input the same deletion raises
    `ValueError: invalid literal for int()` out of `stop`, which is the one
    command an operator reaches for when the browser is already misbehaving."""
    assert browser._parse_cdp_owner_pids(
        PS_WITH_A_WRAPPED_ARGV, 9222, self_pid=1
    ) == [2234786]


# ---------------------------------------------------------------------------
# The no-owner state on a platform that can never name an owner
# ---------------------------------------------------------------------------
#
# `stop_comet`'s docstring said "`port` alone is a complete instruction: with no
# lock file this stops whatever holds that port". It is complete on POSIX, where
# `_pids_for_cdp_port` reads `ps`. On Windows that function returns [] by
# design, and `_adopt_running_cdp` writes no lock precisely when it cannot
# identify an owner, which on Windows is always. So `stop --port N` there had no
# lock, no tracked PID and no port owner: both signal rounds iterated an empty
# list, `_wait_until_cdp_down` burned the full timeout twice, and the call
# returned False after about fifteen seconds having signalled nothing.

def test_a_serving_port_with_no_owner_on_windows_refuses_at_once(monkeypatch, capsys):
    monkeypatch.setattr(browser.sys, "platform", "win32")
    monkeypatch.setattr(browser, "_active_lock_file", lambda: None)
    monkeypatch.setattr(browser, "_pids_for_cdp_port", lambda port: [])
    monkeypatch.setattr(browser, "_cdp_ready", lambda *a, **k: True)

    waited = []
    monkeypatch.setattr(browser, "_wait_until_cdp_down",
                        lambda port, timeout: waited.append(timeout) or False)
    sent = []
    monkeypatch.setattr(browser.os, "kill", lambda pid, sig: sent.append((pid, sig)))

    assert browser.stop_comet(port=9222) is False
    assert waited == [], (
        f"it waited out {waited} seconds of timeout with nothing to signal")
    assert sent == [], "it signalled something after reporting it had no owner"

    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert "cannot identify" in combined, combined
    assert "9222" in combined


def test_the_same_state_on_posix_still_signals_and_waits(monkeypatch):
    """The anchor, and the reason the refusal is platform-scoped. On POSIX an
    empty owner list with the port still serving is a race worth signalling
    through, not a permanent state."""
    monkeypatch.setattr(browser.sys, "platform", "linux")
    monkeypatch.setattr(browser, "_active_lock_file", lambda: None)
    monkeypatch.setattr(browser, "_pids_for_cdp_port", lambda port: [])
    monkeypatch.setattr(browser, "_cdp_ready", lambda *a, **k: True)

    waited = []

    def _down(port, timeout):
        waited.append(timeout)
        return True

    monkeypatch.setattr(browser, "_wait_until_cdp_down", _down)

    assert browser.stop_comet(port=9222) is True
    assert waited, "POSIX stopped waiting for the port to close"


def test_a_windows_session_with_a_tracked_pid_is_still_stoppable(monkeypatch):
    """The other anchor. The refusal must fire only when there is nothing to
    aim at, never when the lock named an owner."""
    monkeypatch.setattr(browser.sys, "platform", "win32")
    monkeypatch.setattr(browser, "_pids_for_cdp_port", lambda port: [])
    monkeypatch.setattr(browser, "_pid_is_browser", lambda pid, name: True)
    monkeypatch.setattr(browser, "_cdp_ready", lambda *a, **k: True)
    monkeypatch.setattr(browser, "_wait_until_cdp_down", lambda port, timeout: True)
    monkeypatch.setattr(browser, "_clear_lock", lambda lock: None)

    sent = []
    monkeypatch.setattr(browser.os, "kill", lambda pid, sig: sent.append((pid, sig)))

    class _Lock:
        name = "browser-cdp.json"

        def read_text(self):
            return json.dumps({"port": 9222, "pid": 4242, "browser": "brave"})

    monkeypatch.setattr(browser, "_active_lock_file", _Lock)

    assert browser.stop_comet(port=9222) is True
    assert [pid for pid, _sig in sent] == [4242], sent


def test_the_docstring_no_longer_calls_the_port_complete_everywhere():
    """The claim itself. The code fix above is a refusal, not a repair: a
    Windows session with no lock still cannot be stopped by this tool, and the
    docstring has to say which platform its promise holds on."""
    doc = " ".join((browser.stop_comet.__doc__ or "").split())
    active = doc.split("That qualifier was missing")[0]
    assert "ON POSIX" in active, (
        f"the promise is unqualified again: {active!r}")
    assert "NOT complete on Windows" in active, active
