"""Two security controls over sentinel.py that were substring scans, and the
production defect one of them was blessing.

SEC-012 claims to verify "sentinel uses interruptible sleep for graceful
shutdown". Its whole positive guard was `assert "_stop_event" in content` over a
3,000-line file. The name is there. The shutdown it certifies did not work:
`main()` registers SIGINT/SIGTERM with `signal.signal`, and that handler runs
between bytecodes while the event loop sits blocked in `select()` with nothing
pending, so `Event.set()` goes unnoticed until the current `asyncio.wait_for`
times out on its own.

Measured 2026-08-27 against the exact shape of the wait in `start()`:

    signal.signal + Event.set()      SIGTERM at 2s -> wait returned at 29.54s
    loop.add_signal_handler          SIGTERM at 2s -> wait returned at  1.84s

With the real `check_interval` that is up to fifteen minutes of "shutting
down", and systemd's default `TimeoutStopSec=90s` turns a graceful stop into a
SIGKILL - which is exactly the ungraceful shutdown SEC-012 exists to prevent.

SEC-010 is the same shape one control over: `assert "os.replace(" in content`
and `assert ".tmp" in content or "with_suffix" in content`. The one runtime test
that claims to cover it globs `state.json.*` for leftovers, while
`Path("state.json").with_suffix(".tmp")` produces `state.tmp` - so the leftover
check matched nothing it could ever find, and no test failed the write to prove
the existing file survives.

Found by the third defect-class fan-out over `tests/`, 2026-08-27, lens
`the-test-that-tests-the-test`.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


# ============================================================
# 1. The shutdown that was not interruptible
# ============================================================

_PROBE = textwrap.dedent("""
    import asyncio, logging, signal, sys, time
    sys.path.insert(0, {root!r})
    from scripts.sentinel import Sentinel

    class _Stub:
        pass

    async def main():
        s = _Stub()
        s.logger = logging.getLogger("probe")
        s._running = True
        s._stop_event = asyncio.Event()
        mechanism = Sentinel.install_signal_handlers(s)
        print("MECHANISM", mechanism, flush=True)
        print("READY", flush=True)
        t0 = time.monotonic()
        try:
            await asyncio.wait_for(s._stop_event.wait(), timeout={timeout})
            print("WOKE", round(time.monotonic() - t0, 2), flush=True)
        except asyncio.TimeoutError:
            print("TIMEOUT", round(time.monotonic() - t0, 2), flush=True)
        print("RUNNING", s._running, flush=True)

    asyncio.run(main())
""")


def _run_probe(sig: int, timeout: float = 20.0) -> dict:
    """Wait in a CHILD, signal it, and read back how long the wait took.

    A child, not this process: if the handler failed to install, the default
    disposition for SIGTERM kills whatever receives it, and under `-n auto`
    that would be a pytest worker rather than a failed assertion.
    """
    script = _PROBE.format(root=str(ROOT), timeout=timeout)
    proc = subprocess.Popen(
        [sys.executable, "-c", script], stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True, cwd=str(ROOT))
    out = {}
    try:
        # Wait for READY so the signal cannot land before the handler exists.
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            line = proc.stdout.readline()
            if not line:
                break
            if line.startswith("MECHANISM"):
                out["mechanism"] = line.split()[1]
            if line.startswith("READY"):
                break
        else:
            proc.kill()
            pytest.fail("probe never reached READY")
        sent_at = time.monotonic()
        proc.send_signal(sig)
        rest, err = proc.communicate(timeout=timeout + 15)
    except subprocess.TimeoutExpired:
        proc.kill()
        pytest.fail("probe never returned")
    out["elapsed_wall"] = time.monotonic() - sent_at
    for line in rest.splitlines():
        parts = line.split()
        if parts and parts[0] in ("WOKE", "TIMEOUT"):
            out["outcome"], out["elapsed"] = parts[0], float(parts[1])
        if parts and parts[0] == "RUNNING":
            out["running"] = parts[1] == "True"
    out["stderr"] = err
    return out


@pytest.mark.skipif(os.name == "nt", reason="POSIX signal delivery; Windows keeps signal.signal")
@pytest.mark.parametrize("sig", [signal.SIGTERM, signal.SIGINT])
def test_a_signal_wakes_the_wait_at_once_instead_of_at_the_timeout(sig):
    """The behavioural guard SEC-012 never had.

    With `signal.signal` this returns at the 20-second timeout. With
    `loop.add_signal_handler` it returns in well under a second, because the
    handler writes to the loop's self-pipe, which is what `select()` watches.
    """
    result = _run_probe(sig, timeout=20.0)
    assert result.get("mechanism") == "loop", (
        f"handlers were installed via {result.get('mechanism')!r}; on POSIX the "
        f"loop path is required or shutdown waits out the interval. "
        f"stderr: {result.get('stderr', '')[:400]}")
    assert result.get("outcome") == "WOKE", (
        f"the wait was not interrupted: {result}. This is the defect SEC-012 "
        "claimed to cover with a substring scan.")
    assert result["elapsed"] < 3.0, (
        f"the wait took {result['elapsed']}s of a 20s timeout; the signal did "
        "not wake the loop, it only arrived before the deadline")
    assert result.get("running") is False, "_running was not cleared by the handler"


@pytest.mark.skipif(os.name == "nt", reason="POSIX signal delivery")
def test_the_handler_installer_reports_the_mechanism_it_installed():
    """A tool says only what its method established.

    `install_signal_handlers` returns "loop" or "signal.signal" so a caller can
    state which it got, rather than assert a graceful shutdown it never checked.
    """
    import asyncio
    import logging

    from scripts.sentinel import Sentinel

    class _Stub:
        pass

    async def _go():
        s = _Stub()
        s.logger = logging.getLogger("probe")
        s._running = True
        s._stop_event = asyncio.Event()
        return Sentinel.install_signal_handlers(s)

    assert asyncio.run(_go()) == "loop"


def test_start_actually_installs_the_handlers():
    """The gap a mutation found: nothing tied the installer to `start`.

    Every other test here calls `install_signal_handlers` directly, so gutting
    `start()` left the whole file green while the daemon booted with no handler
    at all. `co_names` is the cheap proof that the call site reaches the name;
    the behavioural probes above prove what the name does.
    """
    from scripts.sentinel import Sentinel

    assert "install_signal_handlers" in Sentinel.start.__code__.co_names, (
        "Sentinel.start no longer installs the signal handlers, so a live "
        "daemon waits out the whole check interval on SIGTERM")


def test_the_wait_loop_breaks_when_the_stop_event_is_set():
    """`wait_for` returning is not enough: the loop must not run another cycle.

    Without the explicit break, a woken wait fell straight back into the top of
    `while self._running` and ran a full check cycle on the way out.
    """
    src = (ROOT / "scripts" / "sentinel.py").read_text(encoding="utf-8")
    assert "if self._stop_event.is_set():\n                    break" in src, (
        "the wait no longer leaves the loop when the stop event is set")


# ============================================================
# 2. The atomic state write nothing measured
# ============================================================

def test_a_failed_state_save_leaves_the_previous_state_intact(tmp_path, monkeypatch):
    """SEC-010's substring scan cannot tell a real atomic write from `open(w)`.

    Fail `os.replace` and read the old file back: that is the property the
    control exists to buy, and no test asserted it.
    """
    from scripts import sentinel as sen

    path = tmp_path / "state.json"
    sm = sen.StateManager(path)
    sm.data["email"]["processed_ids"] = ["first"]
    sm.save()
    before = path.read_bytes()

    def _boom(*a, **k):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(sen.os, "replace", _boom)
    sm.data["email"]["processed_ids"] = ["second"]
    with pytest.raises(OSError):
        sm.save()

    assert path.read_bytes() == before
    assert json.loads(path.read_text(encoding="utf-8"))["email"]["processed_ids"] == ["first"]


def test_the_state_write_leaves_no_tmp_sibling(tmp_path):
    """The glob that could never match.

    `tests/integration/test_sentinel_components.py` looked for `state.json.*`,
    while `Path("state.json").with_suffix(".tmp")` produces `state.tmp`. The
    leftover assertion was true by construction of the wrong pattern.
    """
    from scripts import sentinel as sen

    sm = sen.StateManager(tmp_path / "state.json")
    sm.save()
    leftovers = sorted(p.name for p in tmp_path.iterdir() if p.name != "state.json")
    assert leftovers == [], f"tmp siblings left behind: {leftovers}"
    assert (tmp_path / "state.tmp").exists() is False


def test_the_tmp_name_this_writer_actually_uses():
    """Pinned so the sibling glob and the writer cannot drift apart again."""
    assert Path("state.json").with_suffix(".tmp").name == "state.tmp"
