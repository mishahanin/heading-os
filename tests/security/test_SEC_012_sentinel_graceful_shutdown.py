#!/usr/bin/env python3
"""SEC-012: sentinel's shutdown must interrupt the wait, not wait it out.

Until 2026-08-27 this file's only positive guard was `assert "_stop_event" in
content` over a 3,000-line file. The name was there. The shutdown it certified
did not work: `main()` registered SIGINT/SIGTERM with `signal.signal`, whose
handler runs between bytecodes while the event loop sits blocked in `select()`
with nothing pending, so `Event.set()` went unnoticed until the current
`asyncio.wait_for` timed out on its own. Measured against the exact shape of the
wait in `start()`: SIGTERM at 2 s, `wait_for(..., timeout=30)` returned at
29.54 s. With the real `check_interval` that is up to fifteen minutes, and
systemd's `TimeoutStopSec=90s` turns the graceful stop into a SIGKILL.

The BEHAVIOURAL proof lives in
`tests/test_a_shutdown_that_took_thirty_seconds.py`, which sends a real signal
to a child process and asserts the wait returns in under a second. This file
keeps the two structural claims that file cannot make cheaply, and both now
distinguish the working mechanism from the broken one.
"""

import ast
from pathlib import Path

import pytest
from tests.security.conftest import read_file_content

BEHAVIOURAL = (Path(__file__).resolve().parent.parent
               / "test_a_shutdown_that_took_thirty_seconds.py")


def test_the_stop_event_is_wired_through_the_running_loop(scripts_dir):
    """`_stop_event` existing proves nothing; what sets it is the control.

    `loop.add_signal_handler` writes to the loop's self-pipe, which IS what
    `select()` is watching. A bare `signal.signal` registration does not, and
    that is the whole defect. Asserted on the AST so a mention inside a comment
    or a docstring cannot satisfy it.
    """
    tree = ast.parse(read_file_content(scripts_dir / "sentinel.py"))
    installer = next(
        (n for n in ast.walk(tree)
         if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
         and n.name == "install_signal_handlers"),
        None,
    )
    assert installer is not None, (
        "sentinel.py has no install_signal_handlers; SIGTERM cannot interrupt "
        "the interval wait")
    calls = {n.func.attr for n in ast.walk(installer)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
    assert "add_signal_handler" in calls, (
        "the handlers are not installed through the running loop, so a signal "
        "cannot wake a blocked select(); see the 29.54s measurement above")
    assert "set" in calls or any(
        isinstance(n, ast.Attribute) and n.attr == "_stop_event"
        for n in ast.walk(installer)), "the handler does not touch _stop_event"

    start = next(
        (n for n in ast.walk(tree)
         if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == "start"),
        None,
    )
    assert start is not None
    assert any(isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
               and n.func.attr == "install_signal_handlers" for n in ast.walk(start)), (
        "start() never installs the handlers, so a live daemon boots without them")


def test_sentinel_no_bare_asyncio_sleep_in_loop(scripts_dir):
    """The main run loop must not use bare asyncio.sleep() for long waits."""
    content = read_file_content(scripts_dir / "sentinel.py")
    lines = content.split("\n")

    in_start_method = False
    for i, line in enumerate(lines, 1):
        if "async def start(" in line:
            in_start_method = True
        elif in_start_method and line.strip().startswith("async def "):
            in_start_method = False

        if in_start_method and "asyncio.sleep(self.config.check_interval)" in line:
            pytest.fail(
                f"Line {i}: bare asyncio.sleep() in main loop blocks graceful shutdown. "
                f"Use asyncio.wait_for(self._stop_event.wait(), timeout=interval) instead."
            )


def test_the_behavioural_proof_is_present_and_sends_a_real_signal():
    """A structural control must name where its behavioural proof lives.

    Without this, deleting the behavioural file leaves SEC-012 green over two
    AST assertions again, which is the state this rewrite is correcting.
    """
    assert BEHAVIOURAL.is_file(), f"{BEHAVIOURAL.name} is missing"
    text = BEHAVIOURAL.read_text(encoding="utf-8")
    assert "send_signal" in text and "SIGTERM" in text, (
        "the behavioural test no longer delivers a real signal")
    assert 'result["elapsed"] < 3.0' in text, (
        "the behavioural test no longer bounds how long the wake takes, so it "
        "would pass against a shutdown that waits out the interval")
