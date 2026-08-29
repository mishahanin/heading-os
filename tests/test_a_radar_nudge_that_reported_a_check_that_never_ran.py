#!/usr/bin/env python3
"""`scripts/ops-radar-notify.py` exiting 0 over a radar that produced no verdict.

The daily oneshot unit (scripts/templates/systemd/ops-radar.service) carries
"deliberately NO SuccessExitStatus=1", so the notifier's exit code is the only
thing standing between a broken radar and a green timer. One of the two paths
that reach "the radar gave me no line" was taught to say so, and the other was
not:

    if proc.returncode != 0:            # crashed:  return 1   <- fixed
        ...
    except Exception as exc:            # never ran: return 0   <- not fixed

Measured before the change, with `TimeoutExpired` raised from the `--quiet`
subprocess:

    [ops-radar-notify] radar check failed to run (TimeoutExpired: ...); exiting 0
    main() returned 0

`QUIET_TIMEOUT` seconds elapsed, nothing measured whether anything was due, and
the timer reported success. That is the same inference the branch below it
exists to refuse, in the sibling nobody updated. No audit shard reported this
one; it was found while checking the docstring finding that sits beside it.

The transport is stubbed throughout and asserted to stay untouched. Nothing here
sends, and nothing reaches the network or the clock.

Run: .venv/bin/python -m pytest
     tests/test_a_radar_nudge_that_reported_a_check_that_never_ran.py -q
"""
from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
MODULE_PATH = ROOT / "scripts" / "ops-radar-notify.py"


def _load():
    spec = importlib.util.spec_from_file_location("ops_radar_notify_verdict", MODULE_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


orn = _load()


class _Wire:
    """Stands in for the Telegram transport and records what it was asked to do."""

    def __init__(self):
        self.calls = []
        self.result = True

    def notify(self, target, message):
        self.calls.append((target, message))
        return self.result


@pytest.fixture
def rig(tmp_path, monkeypatch):
    """A workspace with an ops-radar.py present, a stub wire, and a stub runner."""
    (tmp_path / "scripts").mkdir(parents=True)
    (tmp_path / "scripts" / "ops-radar.py").write_text("print('x')\n", encoding="utf-8")

    wire = _Wire()
    monkeypatch.setattr(orn, "get_workspace_root", lambda: tmp_path)
    monkeypatch.setattr(orn, "load_env", lambda root: None)
    monkeypatch.setattr(orn.telegram_notify, "notify", wire.notify)
    monkeypatch.setattr(orn.sys, "argv", ["ops-radar-notify.py"])
    monkeypatch.setenv("OPS_RADAR_TELEGRAM_TARGET", "@invented-alert-channel")
    monkeypatch.delenv("ODIN_CADENCE_TELEGRAM_TARGET", raising=False)

    seen = []

    def install(quiet_behaviour):
        def fake_run(cmd, **kwargs):
            stage = cmd[-1]
            seen.append(stage)
            if stage != "--quiet":
                return subprocess.CompletedProcess(cmd, 0, "", "")
            return quiet_behaviour(cmd, kwargs)
        monkeypatch.setattr(orn.subprocess, "run", fake_run)

    return type("Rig", (), {"wire": wire, "seen": seen, "install": staticmethod(install)})


def _raises(exc):
    def behaviour(cmd, kwargs):
        raise exc
    return behaviour


def _completes(returncode, stdout="", stderr=""):
    def behaviour(cmd, kwargs):
        return subprocess.CompletedProcess(cmd, returncode, stdout, stderr)
    return behaviour


# ============================================================
# 1 - a check that never completed is not "nothing due"
# ============================================================

@pytest.mark.parametrize(
    "exc",
    [
        subprocess.TimeoutExpired(["ops-radar.py", "--quiet"], 120),
        OSError(12, "Cannot allocate memory"),
        MemoryError("out of memory forking the radar"),
    ],
    ids=["timeout", "oserror", "memoryerror"],
)
def test_a_radar_that_never_finished_exits_non_zero(rig, exc):
    rig.install(_raises(exc))
    assert orn.main() == 1
    assert rig.seen == ["heal", "--quiet"], rig.seen


def test_a_radar_that_never_finished_sends_nothing(rig):
    """The measurement failed; there is no line, and none may be invented."""
    rig.install(_raises(subprocess.TimeoutExpired(["ops-radar.py", "--quiet"], 120)))
    orn.main()
    assert rig.wire.calls == [], rig.wire.calls


def test_a_crashed_radar_still_exits_non_zero(rig):
    """The sibling path, pinned beside the one that was missing it."""
    rig.install(_completes(3, stdout="", stderr="Traceback (most recent call last):"))
    assert orn.main() == 1
    assert rig.wire.calls == [], rig.wire.calls


# ============================================================
# 2 - the paths that legitimately exit 0 still do
# ============================================================

def test_a_quiet_radar_with_nothing_due_exits_zero_and_sends_nothing(rig):
    rig.install(_completes(0, stdout="   \n"))
    assert orn.main() == 0
    assert rig.wire.calls == [], rig.wire.calls


def test_a_due_signal_is_sent_and_exits_zero(rig):
    rig.install(_completes(0, stdout="ops-radar: 2 item(s) due\n"))
    assert orn.main() == 0
    assert rig.wire.calls == [("@invented-alert-channel", "ops-radar: 2 item(s) due")], \
        rig.wire.calls


def test_a_transient_send_failure_is_still_swallowed(rig):
    """The one thing the docstring's exit-0 clause survives for.

    The measurement happened and the message was lost; /prime is the backstop.
    """
    rig.install(_completes(0, stdout="ops-radar: 1 item(s) due\n"))
    rig.wire.result = False
    assert orn.main() == 0
    assert len(rig.wire.calls) == 1, rig.wire.calls


def test_a_heal_step_that_never_ran_does_not_gate_the_nudge(rig, monkeypatch):
    """Heal is best effort by design; only the `--quiet` verdict decides."""
    seen = []

    def fake_run(cmd, **kwargs):
        stage = cmd[-1]
        seen.append(stage)
        if stage == "heal":
            raise subprocess.TimeoutExpired(cmd, 1900)
        return subprocess.CompletedProcess(cmd, 0, "ops-radar: 1 item(s) due\n", "")

    monkeypatch.setattr(orn.subprocess, "run", fake_run)
    assert orn.main() == 0
    assert seen == ["heal", "--quiet"], seen
    assert len(rig.wire.calls) == 1, rig.wire.calls


# ============================================================
# 3 - the send guard REFUSES; it is never removed to be tested
# ============================================================

def test_no_configured_recipient_means_no_send_attempt(rig, monkeypatch):
    """The docstring promises "never a send attempt" when nothing is configured."""
    monkeypatch.delenv("OPS_RADAR_TELEGRAM_TARGET", raising=False)
    monkeypatch.delenv("ODIN_CADENCE_TELEGRAM_TARGET", raising=False)
    rig.install(_completes(0, stdout="ops-radar: 4 item(s) due\n"))

    assert orn.main() == 0
    assert rig.wire.calls == [], rig.wire.calls


def test_the_odin_target_is_the_documented_fallback(rig, monkeypatch):
    monkeypatch.delenv("OPS_RADAR_TELEGRAM_TARGET", raising=False)
    monkeypatch.setenv("ODIN_CADENCE_TELEGRAM_TARGET", "@invented-fallback-channel")
    rig.install(_completes(0, stdout="ops-radar: 1 item(s) due\n"))

    assert orn.main() == 0
    assert [t for t, _ in rig.wire.calls] == ["@invented-fallback-channel"], rig.wire.calls


def test_an_absent_radar_script_exits_zero_without_running_anything(tmp_path, monkeypatch):
    wire = _Wire()
    (tmp_path / "scripts").mkdir(parents=True)
    monkeypatch.setattr(orn, "get_workspace_root", lambda: tmp_path)
    monkeypatch.setattr(orn, "load_env", lambda root: None)
    monkeypatch.setattr(orn.telegram_notify, "notify", wire.notify)
    monkeypatch.setattr(orn.sys, "argv", ["ops-radar-notify.py"])

    def refuse(cmd, **kwargs):
        raise AssertionError(f"no subprocess may run without ops-radar.py: {cmd}")

    monkeypatch.setattr(orn.subprocess, "run", refuse)
    assert orn.main() == 0
    assert wire.calls == [], wire.calls
