"""Regression tests for the headless-propose wiring in
scripts/odin-cadence-notify.py (F-10.3 propose-tier plan, Step 6).

Encodes CAP-3's success signal: with ODIN_REFLECT_PROPOSE_ENABLED set and a
non-empty cluster_detail, the headless `heading_cli.py skill ... odin reflect
--propose` call fires and its result folds into the existing Telegram line;
with the flag unset, or cluster_detail empty, no such call happens. Also
covers the integrity-check backstop: a detected knowledge/odin-brain/ change
during the call is a CRITICAL failure that withholds the proposal path.

Run: python3 -m pytest tests/test_odin_cadence_notify.py
"""
from __future__ import annotations

import importlib.util
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "odin-cadence-notify.py"


def load_module():
    spec = importlib.util.spec_from_file_location("odin_cadence_notify_mod", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _FakeProc:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class _FakeCadenceModule:
    """Stand-in for the in-process-imported scripts/odin-cadence.py module."""

    DEFAULT_MIN_ENTRIES = 5

    def __init__(self, cluster_detail):
        self._cluster_detail = cluster_detail

    def compute(self, root, min_entries):
        return {"cluster_detail": self._cluster_detail}


def _write_brain_file(brain_dir: Path, name: str, text: str = "content") -> None:
    brain_dir.mkdir(parents=True, exist_ok=True)
    (brain_dir / name).write_text(text, encoding="utf-8")


class _RecordingRun:
    """A `subprocess.run` stand-in that RECORDS instead of raising.

    The three "must not run" tests below used a stub that raised
    `AssertionError`. `_run_headless_propose` wraps its `subprocess.run` call in
    `except Exception`, so the stub's AssertionError was caught by the very
    function under test, logged, and turned into `return None` -- the same
    answer the gate produces when it works. MEASURED 2026-09-01 with the
    `if not cluster_detail: return None` gate replaced by `if False:` and with
    the `ODIN_REFLECT_PROPOSE_ENABLED` gate removed: every one of those tests
    stayed green with both gates gone.

    A recording stub cannot be swallowed, because the assertion happens in the
    TEST after the call returns, not inside the code under test.
    """

    def __init__(self, returncode=0, on_call=None):
        self.calls: list[list[str]] = []
        self._returncode = returncode
        self._on_call = on_call

    def __call__(self, cmd, **kwargs):
        self.calls.append(list(cmd))
        if self._on_call is not None:
            self._on_call(cmd, **kwargs)
        return _FakeProc(returncode=self._returncode)


def test_propose_skipped_when_flag_unset(tmp_path, monkeypatch):
    mod = load_module()
    monkeypatch.delenv("ODIN_REFLECT_PROPOSE_ENABLED", raising=False)
    # A cluster IS present, so the flag is the only thing that can stop the run.
    # Without this the in-process cadence compute fails first (no
    # scripts/odin-cadence.py under tmp_path) and the test passes whether or not
    # the flag gate exists at all.
    monkeypatch.setattr(mod, "_load_cadence_module",
                        lambda path: _FakeCadenceModule([{"episodes": ["e1.md"]}]))
    monkeypatch.setattr(mod, "get_data_root", lambda: tmp_path / "data")
    run = _RecordingRun()
    monkeypatch.setattr(mod.subprocess, "run", run)

    line = mod._maybe_headless_propose(tmp_path, "Odin cadence: 2 reflect clusters ready.")
    assert run.calls == [], "the headless propose ran with the flag unset"
    assert line == "Odin cadence: 2 reflect clusters ready."


def test_propose_skipped_when_clusters_empty(tmp_path, monkeypatch):
    mod = load_module()
    monkeypatch.setenv("ODIN_REFLECT_PROPOSE_ENABLED", "1")
    monkeypatch.setattr(mod, "_load_cadence_module", lambda path: _FakeCadenceModule([]))
    monkeypatch.setattr(mod, "get_data_root", lambda: tmp_path / "data")
    run = _RecordingRun()
    monkeypatch.setattr(mod.subprocess, "run", run)

    line = mod._maybe_headless_propose(tmp_path, "Odin cadence: up to date.")
    assert run.calls == [], "the headless propose ran with no cluster to reflect on"
    assert line == "Odin cadence: up to date."


def test_propose_fires_and_folds_path_into_line(tmp_path, monkeypatch):
    mod = load_module()
    monkeypatch.setenv("ODIN_REFLECT_PROPOSE_ENABLED", "1")
    monkeypatch.setattr(mod, "_load_cadence_module", lambda path: _FakeCadenceModule([{"episodes": ["e1.md", "e2.md"]}]))

    data_root = tmp_path / "data"
    monkeypatch.setattr(mod, "get_data_root", lambda: data_root)
    calls = {}

    def fake_run(cmd, **kwargs):
        calls["cmd"] = cmd
        calls["kwargs"] = kwargs
        # Simulate the headless call producing the deterministic proposal file.
        today = mod.datetime.now(mod.get_default_tz()).date()
        proposal_dir = data_root / "outputs" / "operations" / "odin-reflect-proposals"
        proposal_dir.mkdir(parents=True, exist_ok=True)
        (proposal_dir / f"{today.isoformat()}_odin-reflect-proposal.md").write_text("draft", encoding="utf-8")
        return _FakeProc(returncode=0)

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    line = mod._maybe_headless_propose(tmp_path, "Odin cadence: 1 reflect cluster ready.")

    assert "cmd" in calls
    cmd = calls["cmd"]
    # `assert str(X) or True` can never fail, and the comment that said the
    # timeout was "checked separately below" was false -- fake_run discarded
    # **kwargs, so the headless subprocess could have been launched with no
    # timeout at all, which is a hang in a notify path.
    assert calls["kwargs"].get("timeout") == mod.PROPOSE_HEADLESS_TIMEOUT, (
        f"headless propose ran with timeout={calls['kwargs'].get('timeout')!r}, "
        f"expected {mod.PROPOSE_HEADLESS_TIMEOUT!r}")
    assert "odin" in cmd and "reflect" in cmd and "--propose" in cmd
    from scripts.heading_cli import PROPOSE_DEFAULT_BUDGET_USD
    assert str(PROPOSE_DEFAULT_BUDGET_USD) in cmd
    assert "odin-reflect-proposal.md" in line
    assert line.startswith("Odin cadence: 1 reflect cluster ready.")


def test_propose_integrity_check_fires_on_brain_change(tmp_path, monkeypatch):
    """The core security-backstop case: if knowledge/odin-brain/ changes at all
    during the headless call, log a CRITICAL failure and withhold the proposal
    path -- regardless of what the vendor CLI's own permissions claimed."""
    mod = load_module()
    monkeypatch.setenv("ODIN_REFLECT_PROPOSE_ENABLED", "1")
    monkeypatch.setenv("ODIN_CADENCE_TELEGRAM_TARGET", "-100integrity")
    monkeypatch.setattr(mod, "_load_cadence_module", lambda path: _FakeCadenceModule([{"episodes": ["e1.md"]}]))

    data_root = tmp_path / "data"
    brain_dir = data_root / "knowledge" / "odin-brain"
    _write_brain_file(brain_dir, "principle-1.md")
    monkeypatch.setattr(mod, "get_data_root", lambda: data_root)

    logged = []
    monkeypatch.setattr(mod, "_log", lambda msg: logged.append(msg))

    notified = []
    monkeypatch.setattr(
        mod.telegram_notify, "notify",
        lambda target, message: notified.append((target, message)) or True,
    )

    def fake_run(cmd, **kwargs):
        # Simulate an out-of-scope write happening during the headless call --
        # exactly what the permission layer is supposed to prevent.
        (brain_dir / "principle-1.md").write_text("tampered", encoding="utf-8")
        today = mod.datetime.now(mod.get_default_tz()).date()
        proposal_dir = data_root / "outputs" / "operations" / "odin-reflect-proposals"
        proposal_dir.mkdir(parents=True, exist_ok=True)
        (proposal_dir / f"{today.isoformat()}_odin-reflect-proposal.md").write_text("draft", encoding="utf-8")
        return _FakeProc(returncode=0)

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    line = mod._maybe_headless_propose(tmp_path, "Odin cadence: 1 reflect cluster ready.")

    assert line == "Odin cadence: 1 reflect cluster ready."  # proposal path withheld
    assert any("CRITICAL" in m for m in logged)
    # The integrity failure must ESCALATE to the CEO off-machine, not just log.
    assert len(notified) == 1
    target, message = notified[0]
    assert target == "-100integrity"
    assert "CRITICAL" in message and "odin-brain" in message


def test_propose_integrity_check_survives_telegram_send_failure(tmp_path, monkeypatch):
    """A brain-change escalation whose Telegram send fails must not crash the
    run: notify() never raises, and even a False return leaves main() at exit 0
    with the proposal path still withheld."""
    mod = load_module()
    monkeypatch.setenv("ODIN_REFLECT_PROPOSE_ENABLED", "1")
    monkeypatch.setattr(mod, "_load_cadence_module", lambda path: _FakeCadenceModule([{"episodes": ["e1.md"]}]))

    data_root = tmp_path / "data"
    brain_dir = data_root / "knowledge" / "odin-brain"
    _write_brain_file(brain_dir, "principle-1.md")
    monkeypatch.setattr(mod, "get_data_root", lambda: data_root)
    monkeypatch.setattr(mod, "_log", lambda msg: None)
    monkeypatch.setattr(mod.telegram_notify, "notify", lambda target, message: False)

    def fake_run(cmd, **kwargs):
        (brain_dir / "principle-1.md").write_text("tampered", encoding="utf-8")
        return _FakeProc(returncode=0)

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    line = mod._maybe_headless_propose(tmp_path, "Odin cadence: 1 reflect cluster ready.")
    assert line == "Odin cadence: 1 reflect cluster ready."


def test_run_headless_propose_returns_path_on_cluster(tmp_path, monkeypatch):
    """Extracted core (F-10.3 delivery surface): returns the proposal Path when a
    cluster is present and the headless call produces the dated file."""
    mod = load_module()
    monkeypatch.setenv("ODIN_REFLECT_PROPOSE_ENABLED", "1")
    monkeypatch.setattr(mod, "_load_cadence_module", lambda path: _FakeCadenceModule([{"episodes": ["e1.md"]}]))
    data_root = tmp_path / "data"
    monkeypatch.setattr(mod, "get_data_root", lambda: data_root)

    def fake_run(cmd, **kwargs):
        today = mod.datetime.now(mod.get_default_tz()).date()
        proposal_dir = data_root / "outputs" / "operations" / "odin-reflect-proposals"
        proposal_dir.mkdir(parents=True, exist_ok=True)
        (proposal_dir / f"{today.isoformat()}_odin-reflect-proposal.md").write_text("draft", encoding="utf-8")
        return _FakeProc(returncode=0)

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    p = mod._run_headless_propose(tmp_path)
    assert p is not None
    assert p.name.endswith("_odin-reflect-proposal.md")
    assert p.exists()


def test_run_headless_propose_recovers_via_mtime_fallback_on_date_divergence(tmp_path, monkeypatch):
    """Scrutiny M1: when the file the run writes is named with a date that does
    NOT match the get_default_tz() reconstruction (TZ dual-source divergence, or
    the call crossing local midnight), the same-run mtime fallback still returns
    the freshly-written proposal instead of silently withholding it."""
    mod = load_module()
    monkeypatch.setenv("ODIN_REFLECT_PROPOSE_ENABLED", "1")
    monkeypatch.setattr(mod, "_load_cadence_module", lambda path: _FakeCadenceModule([{"episodes": ["e1.md"]}]))
    data_root = tmp_path / "data"
    monkeypatch.setattr(mod, "get_data_root", lambda: data_root)

    def fake_run(cmd, **kwargs):
        # Write the proposal under a DIFFERENT date than today's reconstruction,
        # so the exact-date path deliberately misses and only the fallback finds it.
        proposal_dir = data_root / "outputs" / "operations" / "odin-reflect-proposals"
        proposal_dir.mkdir(parents=True, exist_ok=True)
        (proposal_dir / "1999-01-01_odin-reflect-proposal.md").write_text("draft", encoding="utf-8")
        return _FakeProc(returncode=0)

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    p = mod._run_headless_propose(tmp_path)
    assert p is not None
    assert p.name == "1999-01-01_odin-reflect-proposal.md"
    assert p.exists()


def test_run_headless_propose_ignores_stale_proposal_when_run_writes_none(tmp_path, monkeypatch):
    """The fallback must not resurrect a pre-existing (stale) proposal file the
    current run did not write: an old file with an mtime before the call started
    is excluded, so a run that produces nothing returns None."""
    mod = load_module()
    monkeypatch.setenv("ODIN_REFLECT_PROPOSE_ENABLED", "1")
    monkeypatch.setattr(mod, "_load_cadence_module", lambda path: _FakeCadenceModule([{"episodes": ["e1.md"]}]))
    data_root = tmp_path / "data"
    monkeypatch.setattr(mod, "get_data_root", lambda: data_root)

    proposal_dir = data_root / "outputs" / "operations" / "odin-reflect-proposals"
    proposal_dir.mkdir(parents=True, exist_ok=True)
    stale = proposal_dir / "2000-01-01_odin-reflect-proposal.md"
    stale.write_text("old", encoding="utf-8")
    old_ts = time.time() - 3600  # an hour ago, well before any call start
    os.utime(stale, (old_ts, old_ts))

    monkeypatch.setattr(mod.subprocess, "run", lambda cmd, **kwargs: _FakeProc(returncode=0))
    assert mod._run_headless_propose(tmp_path) is None


def test_run_headless_propose_none_when_no_cluster(tmp_path, monkeypatch):
    """No cluster -> None (and no headless subprocess), min_entries-independent."""
    mod = load_module()
    monkeypatch.setenv("ODIN_REFLECT_PROPOSE_ENABLED", "1")
    monkeypatch.setattr(mod, "_load_cadence_module", lambda path: _FakeCadenceModule([]))
    monkeypatch.setattr(mod, "get_data_root", lambda: tmp_path / "data")
    run = _RecordingRun()
    monkeypatch.setattr(mod.subprocess, "run", run)

    assert mod._run_headless_propose(tmp_path) is None
    assert run.calls == [], "the headless propose ran with no cluster to reflect on"


def test_a_non_zero_headless_exit_withholds_the_proposal(tmp_path, monkeypatch):
    """A failed headless run must not deliver a proposal, even when a file with
    today's name is sitting in the directory.

    `if proc.returncode != 0` had no witness: replacing it with `if False:` left
    every test in this file green, because the only run that produced a
    proposal file also exited 0. A crashed agent that wrote a partial draft
    would then have been announced to the operator as a finished proposal.
    """
    mod = load_module()
    monkeypatch.setenv("ODIN_REFLECT_PROPOSE_ENABLED", "1")
    monkeypatch.setattr(mod, "_load_cadence_module",
                        lambda path: _FakeCadenceModule([{"episodes": ["e1.md"]}]))
    data_root = tmp_path / "data"
    monkeypatch.setattr(mod, "get_data_root", lambda: data_root)
    monkeypatch.setattr(mod, "_log", lambda msg: None)

    def _write_a_proposal(cmd, **kwargs):
        today = mod.datetime.now(mod.get_default_tz()).date()
        d = data_root / "outputs" / "operations" / "odin-reflect-proposals"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{today.isoformat()}_odin-reflect-proposal.md").write_text("partial", encoding="utf-8")

    run = _RecordingRun(returncode=3, on_call=_write_a_proposal)
    monkeypatch.setattr(mod.subprocess, "run", run)

    assert mod._run_headless_propose(tmp_path) is None
    assert len(run.calls) == 1, "the headless call itself must still have happened"


def test_propose_only_delivers_relative_path_and_no_counts(tmp_path, monkeypatch):
    """--propose-only: exactly one standalone proposal-path send (DATA-relative,
    phone-readable), and the counts subprocess is NEVER run (SC-1 / L1)."""
    mod = load_module()
    data_root = tmp_path / "data"
    proposal = (data_root / "outputs" / "operations" / "odin-reflect-proposals"
                / "2026-07-17_odin-reflect-proposal.md")
    proposal.parent.mkdir(parents=True, exist_ok=True)
    proposal.write_text("draft", encoding="utf-8")

    monkeypatch.setattr(mod, "get_workspace_root", lambda: tmp_path)
    monkeypatch.setattr(mod, "load_env", lambda root: None)
    monkeypatch.setattr(mod, "get_data_root", lambda: data_root)
    monkeypatch.setattr(mod, "_run_headless_propose", lambda root: proposal)
    monkeypatch.setenv("ODIN_CADENCE_TELEGRAM_TARGET", "-100dm")

    # Recording, not raising: `main()`'s counts branch wraps subprocess.run in
    # `except Exception`, so a raising stub is caught by the code under test.
    counts = _RecordingRun()
    monkeypatch.setattr(mod.subprocess, "run", counts)

    notified = []
    monkeypatch.setattr(mod.telegram_notify, "notify",
                        lambda target, message: notified.append((target, message)) or True)

    monkeypatch.setattr(sys, "argv", ["odin-cadence-notify.py", "--propose-only"])
    rc = mod.main()
    assert rc == 0
    assert counts.calls == [], "--propose-only must NOT run the counts subprocess"
    assert len(notified) == 1
    target, message = notified[0]
    assert target == "-100dm"
    assert message == ("Odin reflect proposal ready: "
                       "outputs/operations/odin-reflect-proposals/2026-07-17_odin-reflect-proposal.md")
    assert str(data_root) not in message  # relative, not an absolute WSL path


def test_propose_only_silent_when_no_proposal(tmp_path, monkeypatch):
    """--propose-only with no proposal -> zero sends, exit 0 (SC-2)."""
    mod = load_module()
    monkeypatch.setattr(mod, "get_workspace_root", lambda: tmp_path)
    monkeypatch.setattr(mod, "load_env", lambda root: None)
    monkeypatch.setattr(mod, "_run_headless_propose", lambda root: None)

    counts = _RecordingRun()
    monkeypatch.setattr(mod.subprocess, "run", counts)

    notified = []
    monkeypatch.setattr(mod.telegram_notify, "notify",
                        lambda target, message: notified.append((target, message)) or True)

    monkeypatch.setattr(sys, "argv", ["odin-cadence-notify.py", "--propose-only"])
    rc = mod.main()
    assert rc == 0
    assert counts.calls == [], "--propose-only must NOT run the counts subprocess"
    assert notified == []


def test_propose_only_integrity_alert_and_no_proposal_message(tmp_path, monkeypatch):
    """--propose-only end-to-end integrity case (SC-3): a brain change fires the
    CRITICAL escalation (from _run_headless_propose) and sends NO proposal-ready
    message."""
    mod = load_module()
    monkeypatch.setenv("ODIN_REFLECT_PROPOSE_ENABLED", "1")
    monkeypatch.setenv("ODIN_CADENCE_TELEGRAM_TARGET", "-100dm")
    monkeypatch.setattr(mod, "_load_cadence_module", lambda path: _FakeCadenceModule([{"episodes": ["e1.md"]}]))
    data_root = tmp_path / "data"
    brain_dir = data_root / "knowledge" / "odin-brain"
    _write_brain_file(brain_dir, "principle-1.md")
    monkeypatch.setattr(mod, "get_data_root", lambda: data_root)
    monkeypatch.setattr(mod, "get_workspace_root", lambda: tmp_path)
    monkeypatch.setattr(mod, "load_env", lambda root: None)
    monkeypatch.setattr(mod, "_log", lambda msg: None)

    notified = []
    monkeypatch.setattr(mod.telegram_notify, "notify",
                        lambda target, message: notified.append((target, message)) or True)

    def fake_run(cmd, **kwargs):
        (brain_dir / "principle-1.md").write_text("tampered", encoding="utf-8")
        return _FakeProc(returncode=0)

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    monkeypatch.setattr(sys, "argv", ["odin-cadence-notify.py", "--propose-only"])
    rc = mod.main()
    assert rc == 0
    assert len(notified) == 1  # only the CRITICAL alert, no proposal-ready send
    _, message = notified[0]
    assert "CRITICAL" in message and "odin-brain" in message
    assert "proposal ready" not in message.lower()


if __name__ == "__main__":
    sys.exit(__import__("pytest").main([__file__, "-q"]))
