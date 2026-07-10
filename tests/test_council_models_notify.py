"""Unit tests for scripts/council-models-notify.py -- signature + dedup state."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "council-models-notify.py"
_spec = importlib.util.spec_from_file_location("council_models_notify", SCRIPT)
notify = importlib.util.module_from_spec(_spec)
sys.modules["council_models_notify"] = notify
_spec.loader.exec_module(notify)


def _finding(provider, status, candidate=None):
    return {"provider": provider, "pin": "x", "status": status,
            "candidate": candidate, "detail": "d"}


def test_signature_only_actionable_sorted():
    findings = [
        _finding("grok", "newer", "grok-4.6"),
        _finding("gemini", "ok"),
        _finding("kimi", "broken"),
    ]
    sig = notify._signature(findings)
    assert sig == ["grok:newer:grok-4.6", "kimi:broken:-"]


def test_signature_empty_when_all_ok():
    findings = [_finding("grok", "ok"), _finding("kimi", "ok"), _finding("gemini", "unknown")]
    assert notify._signature(findings) == []


def test_state_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(notify, "get_outputs_dir", lambda: tmp_path)
    assert notify._load_last_signature() == []  # absent -> empty
    notify._save_signature(["grok:newer:grok-4.6"])
    assert notify._load_last_signature() == ["grok:newer:grok-4.6"]
    # Overwrite (e.g. reset to empty after all-clear).
    notify._save_signature([])
    assert notify._load_last_signature() == []


def test_load_signature_tolerates_malformed(tmp_path, monkeypatch):
    monkeypatch.setattr(notify, "get_outputs_dir", lambda: tmp_path)
    (tmp_path / "operations" / "council").mkdir(parents=True)
    notify._state_path().write_text("not json{", encoding="utf-8")
    assert notify._load_last_signature() == []
