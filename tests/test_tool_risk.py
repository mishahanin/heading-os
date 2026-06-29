"""Tier resolution + the non-overridable send invariant (R3, Step 1).

Exercises ``scripts.utils.tool_risk`` as a pure loader against a temp ledger:
tier resolution from the JSON, the safety invariant (a ledger marking
``email_send`` autonomous still resolves gated), and unknown -> gated.

No daemon, no network, no live queue. Each test points the loader at a temp
``config/tool-risk.json`` via monkeypatched workspace root + cache reset.

Run: python3 -m pytest tests/test_tool_risk.py
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils import tool_risk


def _write_ledger(root: Path, data: dict) -> None:
    (root / "config").mkdir(parents=True, exist_ok=True)
    (root / "config" / "tool-risk.json").write_text(json.dumps(data), encoding="utf-8")


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    """Point tool_risk at a temp ledger and reset its cache around each test."""
    def _make(data: dict):
        _write_ledger(tmp_path, data)
        monkeypatch.setattr(tool_risk, "get_workspace_root", lambda: tmp_path)
        tool_risk.load(force=True)
        return tmp_path
    yield _make
    # Clear the cache rather than re-reading: get_workspace_root is still
    # monkeypatched to tmp_path here, so load(force=True) would repopulate
    # _CACHE from the temp ledger and leak it into later suites. Setting it to
    # None forces the next load() (after monkeypatch is undone) to re-read the
    # real config/tool-risk.json.
    tool_risk._CACHE = None


def test_tier_resolution_from_ledger(ledger):
    ledger({
        "version": 1,
        "tiers": {
            "note": {"tier": "autonomous", "reason": "no-op"},
            "pipeline_update": {"tier": "notify", "reason": "reversible"},
        },
        "send_capable": [],
    })
    assert tool_risk.tier_for("note") == "autonomous"
    assert tool_risk.tier_for("pipeline_update") == "notify"


def test_send_capable_invariant_overrides_ledger(ledger):
    # Tampered ledger: email_send marked autonomous, but it is send_capable.
    ledger({
        "version": 1,
        "tiers": {"email_send": {"tier": "autonomous", "reason": "tampered"}},
        "send_capable": ["email_send", "telegram_send"],
    })
    assert tool_risk.tier_for("email_send") == "gated"
    assert tool_risk.tier_for("telegram_send") == "gated"


def test_unknown_type_resolves_gated(ledger):
    ledger({"version": 1, "tiers": {}, "send_capable": []})
    assert tool_risk.tier_for("does_not_exist") == "gated"


def test_missing_ledger_resolves_gated(tmp_path, monkeypatch):
    # No config/tool-risk.json on disk at all.
    monkeypatch.setattr(tool_risk, "get_workspace_root", lambda: tmp_path)
    tool_risk.load(force=True)
    try:
        assert tool_risk.tier_for("note") == "gated"
        assert tool_risk.tier_for("email_send") == "gated"
    finally:
        tool_risk._CACHE = None  # clear; do not re-read while monkeypatched


def test_invalid_tier_value_resolves_gated(ledger):
    ledger({
        "version": 1,
        "tiers": {"weird": {"tier": "bogus", "reason": "typo"}},
        "send_capable": [],
    })
    assert tool_risk.tier_for("weird") == "gated"
