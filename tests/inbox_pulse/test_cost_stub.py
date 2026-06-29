"""Tests for the Phase 0 cost-tracker stub (scripts/inbox_pulse/cost.py).

Four tests covering:
1. record_call accumulates tokens and spend correctly for known models.
2. check_daily_cap returns False when spend is below threshold.
3. check_daily_cap returns True at or above threshold.
4. Unknown models are charged at Opus rate (defensive over-estimate).

Each test uses the INBOX_PULSE_STATE_DIR env var (monkeypatched to tmp_path)
so the state file is isolated and does not touch the workspace state directory.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _read_state(state_dir: Path) -> dict:
    path = state_dir / "cost-tracker.json"
    assert path.exists(), f"State file not found: {path}"
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_record_call_updates_daily_total(tmp_path, monkeypatch):
    """record_call accumulates tokens and spend for haiku + opus calls."""
    monkeypatch.setenv("INBOX_PULSE_STATE_DIR", str(tmp_path))

    # Re-import after env var is set so _state_path() resolves to tmp_path.
    # We also need to monkeypatch _today_str so the test is date-independent.
    from scripts.inbox_pulse import cost
    monkeypatch.setattr(cost, "_today_str", lambda: "2026-05-27")

    # Haiku call: 1_000_000 input + 500_000 output
    # cost = (1.0 * 0.80) + (0.5 * 4.00) = 0.80 + 2.00 = $2.80
    cost.record_call("claude-haiku-4-5-20251001", 1_000_000, 500_000)

    state = _read_state(tmp_path)
    day = state["daily_totals"]["2026-05-27"]
    assert day["haiku_input_tokens"] == 1_000_000
    assert day["haiku_output_tokens"] == 500_000
    assert day["calls_haiku"] == 1
    assert day["spend_usd"] == pytest.approx(2.80, rel=1e-6)
    # Opus counters untouched
    assert day["opus_input_tokens"] == 0
    assert day["calls_opus"] == 0

    # Opus call: 100_000 input + 50_000 output
    # cost = (0.1 * 15.00) + (0.05 * 75.00) = 1.50 + 3.75 = $5.25
    # total = 2.80 + 5.25 = $8.05
    cost.record_call("claude-opus-4-7", 100_000, 50_000)

    state = _read_state(tmp_path)
    day = state["daily_totals"]["2026-05-27"]
    assert day["opus_input_tokens"] == 100_000
    assert day["opus_output_tokens"] == 50_000
    assert day["calls_opus"] == 1
    assert day["calls_haiku"] == 1  # unchanged
    assert day["spend_usd"] == pytest.approx(8.05, rel=1e-6)


def test_check_daily_cap_below_threshold_returns_false(tmp_path, monkeypatch):
    """check_daily_cap returns False when spend is below $5."""
    monkeypatch.setenv("INBOX_PULSE_STATE_DIR", str(tmp_path))

    from scripts.inbox_pulse import cost
    monkeypatch.setattr(cost, "_today_str", lambda: "2026-05-27")

    # Empty state: no spend yet
    assert cost.check_daily_cap() is False

    # Record one Haiku call totalling $1.50
    # 1_000_000 input * 0.80/mtok = $0.80
    # 175_000 output * 4.00/mtok = $0.70
    # total = $1.50
    cost.record_call("claude-haiku-4-5-20251001", 1_000_000, 175_000)

    state = _read_state(tmp_path)
    day = state["daily_totals"]["2026-05-27"]
    assert day["spend_usd"] == pytest.approx(1.50, rel=1e-6)
    assert cost.check_daily_cap() is False


def test_check_daily_cap_at_or_above_threshold_returns_true(tmp_path, monkeypatch):
    """check_daily_cap returns True at exactly $5 and above."""
    monkeypatch.setenv("INBOX_PULSE_STATE_DIR", str(tmp_path))

    from scripts.inbox_pulse import cost
    monkeypatch.setattr(cost, "_today_str", lambda: "2026-05-27")

    # Accumulate exactly $5.00 with Haiku:
    # 6_250_000 input tokens * 0.80/mtok = $5.00
    cost.record_call("claude-haiku-4-5-20251001", 6_250_000, 0)

    state = _read_state(tmp_path)
    day = state["daily_totals"]["2026-05-27"]
    assert day["spend_usd"] == pytest.approx(5.00, rel=1e-6)
    assert cost.check_daily_cap() is True

    # Add more: 250_000 output tokens * 4.00/mtok = $1.00 extra -> $6.00 total
    # But we already proved True at $5.00; verify it stays True above cap too.
    cost.record_call("claude-haiku-4-5-20251001", 0, 250_000)

    state = _read_state(tmp_path)
    day = state["daily_totals"]["2026-05-27"]
    assert day["spend_usd"] == pytest.approx(6.00, rel=1e-6)
    assert cost.check_daily_cap() is True


def test_unknown_model_charges_at_opus_rate(tmp_path, monkeypatch):
    """Unknown model names fall back to Opus rate (defensive over-estimate)."""
    monkeypatch.setenv("INBOX_PULSE_STATE_DIR", str(tmp_path))

    from scripts.inbox_pulse import cost
    monkeypatch.setattr(cost, "_today_str", lambda: "2026-05-27")

    # Unknown model: 1_000_000 input + 500_000 output charged at Opus rate
    # cost = (1.0 * 15.00) + (0.5 * 75.00) = 15.00 + 37.50 = $52.50
    cost.record_call("claude-some-future-model-2027", 1_000_000, 500_000)

    state = _read_state(tmp_path)
    day = state["daily_totals"]["2026-05-27"]

    # Treated as Opus bucket (defensive)
    assert day["opus_input_tokens"] == 1_000_000
    assert day["opus_output_tokens"] == 500_000
    assert day["calls_opus"] == 1
    assert day["calls_haiku"] == 0
    assert day["spend_usd"] == pytest.approx(52.50, rel=1e-6)
