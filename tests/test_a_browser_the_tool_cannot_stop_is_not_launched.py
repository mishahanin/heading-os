#!/usr/bin/env python3
"""Every path out of `launch` leaves the session either stoppable or stopped.

`scripts/browser.py` had three ways to end up with a running browser that
`stop` could not touch, and all three ran through the same pre-authenticated
`ClaudeCode` profile with an unauthenticated loopback debug port:

* **Reuse accepted any port owner.** `_cdp_ready(port)` answers for ANY
  Chromium-family process serving `/json/version`, and it was checked before
  anything asked whose process it was. Launching Brave while a stray Chrome or
  Comet owned 9222 "succeeded", returned 0 instead of the PID the docstring
  promised, wrote no lock file, and every later `attach()` silently drove the
  wrong browser.
* **The launch timeout leaked its child.** `TimeoutError` was raised and the
  `Popen`'d browser was neither killed nor recorded. The next `launch` then hit
  the reuse branch above, so the session became permanently unstoppable.
* **The PID-reuse guard was a substring test.** `_pid_is_browser` asked
  `process_name in basename`, so a recycled PID on an unrelated process
  verified as the browser and was SIGTERMed. The audit illustrated this with
  `"comet" in "competent"`, which is FALSE — after `com` comes `p` — and the
  first version of this test copied that example and proved nothing against a
  reverted fix. The genuine cases are `cometd` and `unbrave`, both of which do
  contain the name. Equality is not the fix either: the configured
  `process_name` on Debian is `brave` while the binary is `brave-browser`, so
  `==` would make `stop` ignore every tracked PID on Linux. The guard has to
  match at a name boundary.

Fixed 2026-08-24. These tests hold each path.
"""
from __future__ import annotations

import json
import os
import signal
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts import browser  # noqa: E402

pytestmark = pytest.mark.skipif(
    sys.platform == "win32", reason="POSIX process and signal semantics"
)


# ---------------------------------------------------------------------------
# The PID-reuse guard
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("exe_name, process_name", [
    ("brave", "brave"),                 # exact
    ("brave-browser", "brave"),         # the Debian/Ubuntu binary, load-bearing
    ("brave_browser", "brave"),         # underscore is a separator too
    ("Comet", "comet"),                 # macOS, case-insensitive
    ("comet.exe", "comet"),             # a dot is a separator
])
def test_the_guard_still_recognises_the_browser(exe_name, process_name):
    """An over-tight guard makes `stop` ignore the PID it is tracking."""
    assert browser._exe_name_matches(exe_name, process_name), (
        f"{exe_name!r} is {process_name}'s binary and the guard refused it; "
        "`stop` would now ignore its own tracked PID"
    )


# Each of these DOES contain the browser name, so a substring test says yes.
# That is the property under test; a case the old code already refused would
# pass either way and prove nothing.
SUBSTRING_TRAPS = [
    ("cometd", "comet"),                # a daemon that merely starts the same
    ("unbrave", "brave"),               # contains it, does not start with it
    ("mybraveapp", "brave"),
]
# `comet-helper` is deliberately NOT here. It is name-plus-separator, exactly
# like `brave-browser`, which the guard must accept — no shape rule can split
# the two, and Chromium helper processes die with the browser anyway.


@pytest.mark.parametrize("exe_name, process_name", SUBSTRING_TRAPS)
def test_the_substring_traps_really_are_traps(exe_name, process_name):
    """Guard the premise: a case the old code refused anyway tests nothing.

    The audit's own example, `"comet" in "competent"`, is False — after `com`
    comes `p` — and the first draft of this file parametrized on it, so the
    mutation that reverts the guard stayed green.
    """
    assert process_name.lower() in exe_name.lower(), (
        f"{exe_name!r} does not contain {process_name!r}, so the reverted "
        "substring guard would refuse it too and this case proves nothing"
    )


@pytest.mark.parametrize("exe_name, process_name", SUBSTRING_TRAPS)
def test_the_guard_refuses_a_bystander(exe_name, process_name):
    """A recycled PID landing here used to be SIGTERMed."""
    assert not browser._exe_name_matches(exe_name, process_name), (
        f"{exe_name!r} is not {process_name}, and `stop_comet` signals whatever "
        "this returns True for"
    )


@pytest.mark.parametrize("exe_name, process_name", SUBSTRING_TRAPS)
def test_pid_is_browser_goes_through_the_boundary_guard(monkeypatch, exe_name,
                                                        process_name):
    """The guard is only worth anything if the caller actually uses it."""
    monkeypatch.setattr(browser, "_browser_paths",
                        lambda b=browser.DEFAULT_BROWSER: {"process_name": process_name})
    monkeypatch.setattr(browser, "_pid_cmdline", lambda pid: f"/usr/bin/{exe_name} --x")
    assert not browser._pid_is_browser(4242, process_name), (
        "_pid_is_browser reached its own conclusion instead of asking "
        "_exe_name_matches"
    )


def test_pid_is_browser_still_says_yes_to_the_real_binary(monkeypatch):
    monkeypatch.setattr(browser, "_browser_paths",
                        lambda b=browser.DEFAULT_BROWSER: {"process_name": "brave"})
    monkeypatch.setattr(browser, "_pid_cmdline",
                        lambda pid: "/usr/bin/brave-browser --remote-debugging-port=9222")
    assert browser._pid_is_browser(4242, "brave")


# ---------------------------------------------------------------------------
# Reusing a port that is already serving CDP
# ---------------------------------------------------------------------------

def _lock_at(monkeypatch, tmp_path: Path) -> Path:
    lock = tmp_path / "browser-cdp.json"
    monkeypatch.setattr(browser, "lock_file", lambda p=lock: p)
    monkeypatch.setattr(browser, "_legacy_lock_file",
                        lambda p=tmp_path / "comet-cdp.json": p)
    return lock


def test_reuse_refuses_a_port_owned_by_another_browser(monkeypatch, tmp_path):
    """The reported defect: launching Brave onto Comet's port 'succeeded'."""
    _lock_at(monkeypatch, tmp_path)
    monkeypatch.setattr(browser, "_cdp_ready", lambda port, timeout=1.0: True)
    monkeypatch.setattr(browser, "_pids_for_cdp_port", lambda port: [9001])
    monkeypatch.setattr(browser, "_pid_is_browser", lambda pid, name: False)
    with pytest.raises(RuntimeError, match="not brave"):
        browser.launch_comet(port=19222, browser="brave")


def test_reuse_of_our_own_browser_records_a_stoppable_session(monkeypatch, tmp_path):
    """Returned 0 and wrote no lock, so `stop` said 'nothing tracked'."""
    lock = _lock_at(monkeypatch, tmp_path)
    monkeypatch.setattr(browser, "_cdp_ready", lambda port, timeout=1.0: True)
    monkeypatch.setattr(browser, "_pids_for_cdp_port", lambda port: [9002])
    monkeypatch.setattr(browser, "_pid_is_browser", lambda pid, name: True)

    pid = browser.launch_comet(port=19222, browser="brave")

    assert pid == 9002, "the docstring promises a PID; 0 is not one"
    assert lock.exists(), "a reused session with no lock file cannot be stopped"
    state = json.loads(lock.read_text())
    assert state == {"port": 19222, "pid": 9002, "browser": "brave"}


def test_reuse_with_an_unidentifiable_owner_says_so_and_writes_no_lock(
        monkeypatch, tmp_path, capsys):
    """Windows has no `ps`. Claiming the browser there would be a false record."""
    lock = _lock_at(monkeypatch, tmp_path)
    monkeypatch.setattr(browser, "_cdp_ready", lambda port, timeout=1.0: True)
    monkeypatch.setattr(browser, "_pids_for_cdp_port", lambda port: [])

    pid = browser.launch_comet(port=19222, browser="brave")

    assert pid == 0
    assert not lock.exists(), (
        "nothing verified the owner, so a lock naming this browser would be a "
        "record of something no method established"
    )
    out = capsys.readouterr().out
    assert "cannot identify its owner" in out, (
        "a silent 0 is the old behaviour; the caller has to be told the session "
        "is not tracked"
    )


# ---------------------------------------------------------------------------
# The launch timeout
# ---------------------------------------------------------------------------

def _long_lived_child() -> subprocess.Popen:
    """A process that outlives the test unless something kills it."""
    return subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def _reap(proc: subprocess.Popen) -> None:
    """Leave no stray sleeper behind, whatever the assertion did."""
    if _alive(proc):
        proc.kill()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        print(f"teardown: PID {proc.pid} did not exit", file=sys.stderr)


def _alive(proc: subprocess.Popen) -> bool:
    """Whether `proc` is still executing.

    Not `os.kill(pid, 0)`: these are the test's own children, so a terminated
    one stays a reapable zombie until `wait()` collects it, and signal 0 to a
    zombie SUCCEEDS. That read every killed child as alive and failed a test
    whose fix had worked.
    """
    return proc.poll() is None


def _wait_gone(proc: subprocess.Popen, timeout: float = 8.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline and _alive(proc):
        time.sleep(0.05)
    return not _alive(proc)


def test_abandon_launch_kills_the_child_it_started(monkeypatch):
    monkeypatch.setattr(browser, "_pids_for_cdp_port", lambda port: [])
    proc = _long_lived_child()
    try:
        browser._abandon_launch(proc, 19222, "brave")
        assert _wait_gone(proc), (
            "the launcher we started is still running with the debug port open "
            "and nothing recorded it"
        )
    finally:
        _reap(proc)


def test_abandon_launch_kills_the_re_parented_browser_too(monkeypatch):
    """On Debian the launcher forks and exits; killing it orphans the browser."""
    orphan = _long_lived_child()
    launcher = _long_lived_child()
    monkeypatch.setattr(browser, "_pids_for_cdp_port", lambda port: [orphan.pid])
    try:
        browser._abandon_launch(launcher, 19222, "brave")
        assert _wait_gone(orphan), (
            "the process actually holding the CDP port survived the abandoned "
            "launch; that is the leak"
        )
    finally:
        _reap(orphan)
        _reap(launcher)


def test_a_timed_out_launch_does_not_leave_the_browser_running(monkeypatch, tmp_path):
    """End to end: the timeout path must go through the cleanup."""
    lock = _lock_at(monkeypatch, tmp_path)
    exe = tmp_path / "fake-browser"
    exe.write_text("#!/bin/sh\nsleep 60\n")
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)

    monkeypatch.setattr(browser, "_browser_paths", lambda b=browser.DEFAULT_BROWSER: {
        "exe": exe, "user_data": tmp_path / "ud", "process_name": "fake-browser",
    })
    monkeypatch.setattr(browser, "is_running", lambda b=browser.DEFAULT_BROWSER: False)
    monkeypatch.setattr(browser, "_cdp_ready", lambda port, timeout=1.0: False)
    monkeypatch.setattr(browser, "_pids_for_cdp_port", lambda port: [])
    monkeypatch.setattr(browser, "launch_log", lambda p=tmp_path / "launch.log": p)

    # `browser.subprocess` is not a per-module namespace; it IS the global
    # `subprocess` module object, so this spy is installed process-wide for
    # the duration of the test. It recorded EVERY child spawned anywhere in
    # the process until 2026-08-30, and `proc = started[0]` then picked
    # whichever came first: a pytest plugin's or a background thread's child
    # made `_wait_gone` fail against correct production code, and the
    # `finally: _reap(proc)` below SIGKILLed a process this test never
    # started. The spy cannot be un-globalised (browser.py resolves
    # `subprocess.Popen` on that same shared object), so it is made
    # DISCRIMINATING instead: a child is recorded only when its argv names
    # this test's own fake browser. Everything else is spawned and forgotten.
    started = []
    real_popen = subprocess.Popen

    def spy(*args, **kwargs):
        proc = real_popen(*args, **kwargs)
        argv = args[0] if args else kwargs.get("args") or []
        if argv and str(argv[0]) == str(exe):
            started.append(proc)
        return proc

    monkeypatch.setattr(browser.subprocess, "Popen", spy)

    with pytest.raises(TimeoutError):
        browser.launch_comet(port=19222, wait_timeout=1.0, browser="brave")

    assert len(started) == 1, (
        f"expected exactly one launch of {exe}, recorded {len(started)}")
    proc = started[0]
    try:
        assert _wait_gone(proc), (
            "the browser survived its own failed launch: untracked, no lock "
            "file, debug port open"
        )
        assert not lock.exists(), "a failed launch must not record a session"
    finally:
        _reap(proc)


# ---------------------------------------------------------------------------
# The lock file
# ---------------------------------------------------------------------------

def test_the_lock_write_cannot_proceed_without_room_for_a_sibling_tempfile(
        monkeypatch, tmp_path):
    """The behavioural half: does `_write_lock` write the final path IN PLACE?

    A same-directory tempfile plus `os.replace` needs to CREATE a file in the
    lock's directory. Writing the final path in place does not: it only needs
    the existing file, whose own mode bits are untouched. So a directory the
    filesystem will not accept new files into separates the two by behaviour
    rather than by spelling, with no patching of a stdlib primitive.

    Measured 2026-09-01 in a copy of this tree, with `atomic_write_text` in
    `_write_lock` replaced by `path.write_text(...)` across two lines:

        atomic     -> PermissionError, previous lock still on disk verbatim
        in place   -> returned normally, previous lock overwritten

    The grep this replaced could not see that. It asserted
    `"lock_file().write_text" not in src` and `"atomic_write_text" in src`
    over the WHOLE file, and both survive the two-line spelling above: the
    first because no single line holds `lock_file().write_text`, the second
    because the import stays. Run against that mutation on 2026-09-01:

        .venv/bin/python -m pytest -q \\
            tests/test_a_browser_the_tool_cannot_stop_is_not_launched.py
        29 passed

    and 149 passed across the browser and atomic-write files together. That
    set includes `tests/test_atomic_scripts.py`, whose own bare-write detector
    keys on one LINE holding both `lock_file` and `.write_text(`, so the same
    two-line spelling walks past it too.
    """
    state = tmp_path / "state"
    state.mkdir()
    lock = state / "browser-cdp.json"
    lock.write_text('{"port": 1, "pid": 2, "browser": "previous"}\n', encoding="utf-8")
    monkeypatch.setattr(browser, "lock_file", lambda p=lock: p)

    state.chmod(stat.S_IRUSR | stat.S_IXUSR)
    try:
        if os.access(state, os.W_OK):   # root, or a filesystem ignoring the mode
            pytest.skip("this filesystem/user can still create in a read-only dir")
        with pytest.raises(OSError):
            browser._write_lock(19222, 4242, "brave")
        assert json.loads(lock.read_text())["browser"] == "previous", (
            "the previous session record was replaced in place, so this write "
            "does not go through a tempfile and a crash mid-write truncates it"
        )
    finally:
        state.chmod(stat.S_IRWXU)


def test_the_lock_write_still_works_on_an_ordinary_directory(monkeypatch, tmp_path):
    """The other direction. A writer that refuses everything records nothing."""
    lock = tmp_path / "state" / "browser-cdp.json"
    monkeypatch.setattr(browser, "lock_file", lambda p=lock: p)

    browser._write_lock(19222, 4242, "brave")

    assert json.loads(lock.read_text()) == {"port": 19222, "pid": 4242,
                                            "browser": "brave"}


def test_write_lock_itself_calls_the_atomic_helper_and_nothing_else():
    """The structural half, scoped to the function by the AST.

    The whole-file grep it replaces was satisfied by an `atomic_write_text`
    anywhere in the module, including one this function does not call.
    """
    import ast

    tree = ast.parse(Path(browser.__file__).read_text(encoding="utf-8"))
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "_write_lock")
    called = {n.func.id for n in ast.walk(fn)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    attrs = {n.func.attr for n in ast.walk(fn)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}

    assert "atomic_write_text" in called, (
        "the lock file is state; write it through atomic_write_text (tmp + "
        "os.replace), per the workspace no-non-atomic-state-writes rule"
    )
    assert "write_text" not in attrs, (
        f"_write_lock writes the lock in place ({sorted(attrs)}); a crash or a "
        "concurrent read mid-write leaves truncated JSON, and on Windows "
        "`_pids_for_cdp_port` cannot recover the owner from `ps`"
    )


def test_stop_reports_an_unreadable_lock_instead_of_swallowing_it(
        monkeypatch, tmp_path, capsys):
    lock = _lock_at(monkeypatch, tmp_path)
    lock.write_text("{")  # the truncated-write outcome
    monkeypatch.setattr(browser, "_pids_for_cdp_port", lambda port: [])
    monkeypatch.setattr(browser, "_cdp_ready", lambda port, timeout=1.0: False)

    browser.stop_comet()

    assert "unreadable" in capsys.readouterr().out, (
        "the parse error was suppressed into an empty state, so `stop` signalled "
        "nothing and said nothing"
    )


def test_a_lock_holding_a_scalar_does_not_crash_stop(monkeypatch, tmp_path):
    lock = _lock_at(monkeypatch, tmp_path)
    lock.write_text("null")
    monkeypatch.setattr(browser, "_pids_for_cdp_port", lambda port: [])
    monkeypatch.setattr(browser, "_cdp_ready", lambda port, timeout=1.0: False)
    assert browser.stop_comet() is True  # nothing running, lock cleared


# ---------------------------------------------------------------------------
# `status` probes the session's port
# ---------------------------------------------------------------------------

def _status_args(browser_name="brave", port=None):
    import argparse
    return argparse.Namespace(browser=browser_name, port=port)


def test_status_probes_the_port_recorded_in_the_lock(monkeypatch, tmp_path):
    """`status` hardcoded 9222, so a `--port 9333` session read as down."""
    lock = _lock_at(monkeypatch, tmp_path)
    lock.write_text(json.dumps({"port": 9333, "pid": 1, "browser": "brave"}))
    probed = []
    monkeypatch.setattr(browser, "is_running", lambda b: True)
    monkeypatch.setattr(browser, "_port_listening", lambda p: probed.append(p) or True)
    monkeypatch.setattr(browser, "_cdp_ready", lambda p, timeout=1.0: probed.append(p) or True)

    rc = browser.cmd_status(_status_args())

    assert probed == [9333, 9333], f"probed {probed}, not the session's port"
    assert rc == 0


def test_status_port_flag_wins_over_the_lock(monkeypatch, tmp_path):
    lock = _lock_at(monkeypatch, tmp_path)
    lock.write_text(json.dumps({"port": 9333, "pid": 1, "browser": "brave"}))
    probed = []
    monkeypatch.setattr(browser, "is_running", lambda b: True)
    monkeypatch.setattr(browser, "_port_listening", lambda p: probed.append(p) or True)
    monkeypatch.setattr(browser, "_cdp_ready", lambda p, timeout=1.0: probed.append(p) or True)

    browser.cmd_status(_status_args(port=9444))

    assert probed == [9444, 9444]


def test_status_falls_back_to_the_default_port_with_no_lock(monkeypatch, tmp_path):
    _lock_at(monkeypatch, tmp_path)
    probed = []
    monkeypatch.setattr(browser, "is_running", lambda b: True)
    monkeypatch.setattr(browser, "_port_listening", lambda p: probed.append(p) or True)
    monkeypatch.setattr(browser, "_cdp_ready", lambda p, timeout=1.0: probed.append(p) or True)

    browser.cmd_status(_status_args())

    assert probed == [browser.DEFAULT_PORT, browser.DEFAULT_PORT]


def test_the_status_parser_accepts_a_port():
    """A flag documented in the fix but never wired is not a fix."""
    import contextlib
    import io
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), pytest.raises(SystemExit):
        old = sys.argv
        sys.argv = ["browser.py", "status", "--help"]
        try:
            browser.main()
        finally:
            sys.argv = old
    assert "--port" in buf.getvalue()


# ---------------------------------------------------------------------------
# The prose and the code agree about the default
# ---------------------------------------------------------------------------

def test_no_docstring_claims_a_default_the_signature_does_not_have():
    """Three docstrings and a config comment said the default was Comet.

    Stale prose is a defect source here specifically because this module's
    comments are the only explanation of why the CDP-attach pattern exists at
    all, so a reader trusts them.
    """
    src = Path(browser.__file__).read_text(encoding="utf-8")
    assert "default: 'comet'" not in src
    assert browser.DEFAULT_BROWSER == "brave"
    assert "Terminate tracked Comet CDP session" not in src, (
        "the CLI help for `stop` still names one browser as if it were the only "
        "one this tool launches"
    )
