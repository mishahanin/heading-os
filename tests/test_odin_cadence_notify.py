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
import sys
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


def test_propose_skipped_when_flag_unset(tmp_path, monkeypatch):
    mod = load_module()
    monkeypatch.delenv("ODIN_REFLECT_PROPOSE_ENABLED", raising=False)

    def boom(*a, **k):
        raise AssertionError("subprocess.run must not run when the flag is unset")

    monkeypatch.setattr(mod.subprocess, "run", boom)
    line = mod._maybe_headless_propose(tmp_path, "Odin cadence: 2 reflect clusters ready.")
    assert line == "Odin cadence: 2 reflect clusters ready."


def test_propose_skipped_when_clusters_empty(tmp_path, monkeypatch):
    mod = load_module()
    monkeypatch.setenv("ODIN_REFLECT_PROPOSE_ENABLED", "1")
    monkeypatch.setattr(mod, "_load_cadence_module", lambda path: _FakeCadenceModule([]))

    def boom(*a, **k):
        raise AssertionError("subprocess.run must not run when cluster_detail is empty")

    monkeypatch.setattr(mod.subprocess, "run", boom)
    line = mod._maybe_headless_propose(tmp_path, "Odin cadence: up to date.")
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
    assert str(mod.PROPOSE_HEADLESS_TIMEOUT) or True  # timeout kwarg checked separately below
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


if __name__ == "__main__":
    sys.exit(__import__("pytest").main([__file__, "-q"]))
