"""The brain-integrity backstop that skipped the one run it was written for.

`_run_headless_propose` in scripts/odin-cadence-notify.py snapshots
knowledge/odin-brain/ before and after the headless `odin reflect --propose`
call, and treats ANY change as a CRITICAL integrity failure. The exception
handler around `subprocess.run` returned before the after-snapshot was taken,
so the check ran only on the happy path where the child exited cleanly inside
its 600 second timeout.

That inverted the backstop. `TimeoutExpired` is raised after the child has run
UNSUPERVISED for up to PROPOSE_HEADLESS_TIMEOUT, writing whatever it liked, and
a hung or looping headless agent is exactly the case the check exists to catch.
Measured 2026-08-29 against a stub that wrote the brain and then blew the
timeout: the file was left reading "TAMPERED", nothing logged CRITICAL, and no
Telegram escalation was sent.

A second hole sat beside it. When the escalation cannot be delivered, notify()
returns False and logs under ITS logger, so on an unconfigured workspace
(DEFAULT_RECIPIENT is "", which telegram_notify documents as never a send
attempt) the most serious message this script can produce went nowhere and this
script's own log said nothing about it.

Nothing here reaches a real send path: telegram_notify.notify is replaced in
every test that can reach it.

Tests: this file.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SOURCE = ROOT / "scripts" / "odin-cadence-notify.py"


@pytest.fixture()
def notify_mod():
    spec = importlib.util.spec_from_file_location("odin_cadence_notify_backstop", str(SOURCE))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _FakeProc:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class _FakeCadence:
    DEFAULT_MIN_ENTRIES = 5

    def compute(self, root, min_entries):
        return {"cluster_detail": [{"episodes": ["e1.md"]}]}


def _arm(mod, tmp_path, monkeypatch, recipient="-100example"):
    """Wire the module onto a tmp data root with one brain file. Returns
    (brain_dir, logged, notified)."""
    data_root = tmp_path / "data"
    brain_dir = data_root / "knowledge" / "odin-brain"
    brain_dir.mkdir(parents=True)
    (brain_dir / "principle-1.md").write_text("original", encoding="utf-8")

    monkeypatch.setenv("ODIN_REFLECT_PROPOSE_ENABLED", "1")
    if recipient is None:
        monkeypatch.delenv("ODIN_CADENCE_TELEGRAM_TARGET", raising=False)
    else:
        monkeypatch.setenv("ODIN_CADENCE_TELEGRAM_TARGET", recipient)
    monkeypatch.setattr(mod, "_load_cadence_module", lambda path: _FakeCadence())
    monkeypatch.setattr(mod, "get_data_root", lambda: data_root)

    logged: list[str] = []
    monkeypatch.setattr(mod, "_log", logged.append)
    notified: list[tuple] = []
    monkeypatch.setattr(
        mod.telegram_notify, "notify",
        lambda target, message: bool(notified.append((target, message))) or True)
    return brain_dir, logged, notified


def test_a_timed_out_child_that_wrote_the_brain_is_caught(notify_mod, tmp_path, monkeypatch):
    """The defect direction. A child that writes the brain and then blows the
    timeout must still trip the integrity check."""
    mod = notify_mod
    brain_dir, logged, notified = _arm(mod, tmp_path, monkeypatch)

    def hung_child(cmd, **kwargs):
        (brain_dir / "principle-1.md").write_text("TAMPERED", encoding="utf-8")
        raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout"))

    monkeypatch.setattr(mod.subprocess, "run", hung_child)

    assert mod._run_headless_propose(tmp_path) is None
    assert any("CRITICAL" in line for line in logged), (
        f"the timed-out run's brain write was never detected; log was {logged!r}")
    assert len(notified) == 1
    target, message = notified[0]
    assert target == "-100example"
    assert "CRITICAL" in message and "odin-brain" in message


def test_any_subprocess_failure_still_checks_the_brain(notify_mod, tmp_path, monkeypatch):
    """TimeoutExpired is the measured case, not the only one: an OSError raised
    while launching the child leaves the same gap if the check is skipped."""
    mod = notify_mod
    brain_dir, logged, notified = _arm(mod, tmp_path, monkeypatch)

    def cannot_launch(cmd, **kwargs):
        (brain_dir / "sneaked-in.md").write_text("new file", encoding="utf-8")
        raise OSError("exec format error")

    monkeypatch.setattr(mod.subprocess, "run", cannot_launch)

    assert mod._run_headless_propose(tmp_path) is None
    assert any("CRITICAL" in line for line in logged)
    assert len(notified) == 1


def test_a_timed_out_child_that_left_the_brain_alone_is_not_an_integrity_failure(
        notify_mod, tmp_path, monkeypatch):
    """The other direction, so the fix cannot be "always shout". A timeout with
    an untouched brain is a missed propose run, which this module treats as
    non-critical: no CRITICAL, no escalation, no proposal path."""
    mod = notify_mod
    _brain_dir, logged, notified = _arm(mod, tmp_path, monkeypatch)

    def hung_child(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout"))

    monkeypatch.setattr(mod.subprocess, "run", hung_child)

    assert mod._run_headless_propose(tmp_path) is None
    assert not any("CRITICAL" in line for line in logged), (
        f"a clean timeout was escalated as an integrity failure; log was {logged!r}")
    assert notified == []
    assert any("failed to run" in line for line in logged)


def test_a_failed_child_never_yields_a_proposal_path(notify_mod, tmp_path, monkeypatch):
    """Falling through the exception handler must not let a stale or concurrent
    proposal file be picked up by the same-run mtime fallback: the run that
    never completed produced nothing, and returns nothing."""
    mod = notify_mod
    _brain_dir, _logged, notified = _arm(mod, tmp_path, monkeypatch)
    proposals = (tmp_path / "data" / "outputs" / "operations"
                 / "odin-reflect-proposals")
    proposals.mkdir(parents=True)

    def hung_child(cmd, **kwargs):
        # Written DURING the call, so its mtime falls inside the fallback window.
        (proposals / "2026-08-29_odin-reflect-proposal.md").write_text(
            "draft", encoding="utf-8")
        raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout"))

    monkeypatch.setattr(mod.subprocess, "run", hung_child)

    assert mod._run_headless_propose(tmp_path) is None
    assert notified == []


def test_an_undeliverable_escalation_says_so_in_this_script_log(
        notify_mod, tmp_path, monkeypatch):
    """Unconfigured target: notify() returns False and logs elsewhere. This
    script must record that its CRITICAL alert reached nobody."""
    mod = notify_mod
    brain_dir, logged, _notified = _arm(mod, tmp_path, monkeypatch, recipient=None)
    monkeypatch.setattr(mod.telegram_notify, "notify", lambda target, message: False)

    def tamper(cmd, **kwargs):
        (brain_dir / "principle-1.md").write_text("TAMPERED", encoding="utf-8")
        return _FakeProc(returncode=0)

    monkeypatch.setattr(mod.subprocess, "run", tamper)

    assert mod._run_headless_propose(tmp_path) is None
    assert any("NOT delivered" in line for line in logged), (
        f"an undelivered CRITICAL escalation was silent; log was {logged!r}")
    assert mod.DEFAULT_RECIPIENT == ""


def test_a_delivered_escalation_does_not_claim_it_failed(notify_mod, tmp_path, monkeypatch):
    """The negative case for the line above: a send that succeeds must not log
    the not-delivered warning."""
    mod = notify_mod
    brain_dir, logged, notified = _arm(mod, tmp_path, monkeypatch)

    def tamper(cmd, **kwargs):
        (brain_dir / "principle-1.md").write_text("TAMPERED", encoding="utf-8")
        return _FakeProc(returncode=0)

    monkeypatch.setattr(mod.subprocess, "run", tamper)

    assert mod._run_headless_propose(tmp_path) is None
    assert len(notified) == 1
    assert not any("NOT delivered" in line for line in logged)


def test_the_happy_path_still_returns_the_proposal(notify_mod, tmp_path, monkeypatch):
    """The `proc is None` guard added beside the fall-through must not swallow a
    clean run."""
    mod = notify_mod
    _brain_dir, _logged, notified = _arm(mod, tmp_path, monkeypatch)
    data_root = tmp_path / "data"

    def clean_child(cmd, **kwargs):
        today = mod.datetime.now(mod.get_default_tz()).date()
        proposals = data_root / "outputs" / "operations" / "odin-reflect-proposals"
        proposals.mkdir(parents=True, exist_ok=True)
        (proposals / f"{today.isoformat()}_odin-reflect-proposal.md").write_text(
            "draft", encoding="utf-8")
        return _FakeProc(returncode=0)

    monkeypatch.setattr(mod.subprocess, "run", clean_child)

    proposal = mod._run_headless_propose(tmp_path)
    assert proposal is not None and proposal.exists()
    assert notified == []


if __name__ == "__main__":
    sys.exit(__import__("pytest").main([__file__, "-q"]))
