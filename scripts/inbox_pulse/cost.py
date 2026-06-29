#!/usr/bin/env python3
"""Minimal cost-tracking stub for the Inbox Pulse daemon.

STUB -- full CostTracker lands in Phase 4 (task 4.5), which will extend
this module with monthly tracking, Telegram alerts, and degraded-mode
operation.  This stub exists to close the Phase 3 exposure window: Haiku
LLM calls ship in Phase 3, but the full cost tracker was originally
planned for Phase 4, creating a 3-day window where budget could burn
unmonitored.  This stub closes that gap with minimal complexity.

Public API
----------
record_call(model, input_tokens, output_tokens)
    Append a call's token usage to the daily cost-tracker state file.

check_daily_cap() -> bool
    Return True when today's accumulated spend has reached the $5 hard cap.

Caller is responsible for deciding what to do when check_daily_cap()
returns True (e.g., skip the LLM call, raise an exception, alert).

State file
----------
``state/email-triage/cost-tracker.json`` under ``INBOX_PULSE_STATE_DIR``
if that env var is set, else under the workspace root.  Written
atomically (write-to-tmp + os.replace) so a crash mid-write never
corrupts the file.

Pricing constants (Anthropic 2026 published rates, USD per million tokens)
--------------------------------------------------------------------------
- HAIKU_INPUT_USD_PER_MTOK  = 0.80
- HAIKU_OUTPUT_USD_PER_MTOK = 4.00
- OPUS_INPUT_USD_PER_MTOK   = 15.00
- OPUS_OUTPUT_USD_PER_MTOK  = 75.00

Unknown model names are charged at Opus rate (defensive: over-estimate
triggers the cap early rather than silently under-counting spend).

Usage::

    from scripts.inbox_pulse.cost import record_call, check_daily_cap

    record_call("claude-haiku-4-5-20251001", input_tokens=1500, output_tokens=300)
    if check_daily_cap():
        raise RuntimeError("Daily LLM spend cap reached -- aborting call")
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
from scripts.utils.workspace import get_default_tz, get_default_tz_name

__all__ = [
    "record_call",
    "check_daily_cap",
    "HAIKU_INPUT_USD_PER_MTOK",
    "HAIKU_OUTPUT_USD_PER_MTOK",
    "OPUS_INPUT_USD_PER_MTOK",
    "OPUS_OUTPUT_USD_PER_MTOK",
    "DAILY_CAP_USD",
]

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Pricing constants (USD per million tokens)
# ---------------------------------------------------------------------------

HAIKU_INPUT_USD_PER_MTOK: float = 0.80
HAIKU_OUTPUT_USD_PER_MTOK: float = 4.00
OPUS_INPUT_USD_PER_MTOK: float = 15.00
OPUS_OUTPUT_USD_PER_MTOK: float = 75.00

DAILY_CAP_USD: float = 5.0

# local timezone offset (+4:00 UTC, no DST)


# ---------------------------------------------------------------------------
# Path resolution (mirrors observability_safe._debug_trace_path pattern)
# ---------------------------------------------------------------------------

def _workspace_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent


def _state_path() -> Path:
    state_dir = os.environ.get("INBOX_PULSE_STATE_DIR")
    if state_dir:
        base = Path(state_dir)
    else:
        base = _workspace_root() / "state" / "email-triage"
    return base / "cost-tracker.json"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _today_str() -> str:
    """Return today's date as YYYY-MM-DD in local timezone (the configured timezone, UTC+4)."""
    return datetime.now(tz=get_default_tz()).strftime("%Y-%m-%d")


def _is_haiku(model: str) -> bool:
    return "haiku" in model.lower()


def _is_opus(model: str) -> bool:
    return "opus" in model.lower()


def _load_state(path: Path) -> dict:
    if not path.exists():
        return {"daily_totals": {}}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("cost-tracker: could not read state file %s: %s", path, exc)
        return {"daily_totals": {}}


def _save_state(path: Path, state: dict) -> None:
    """Atomic write: write to .tmp then os.replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_fd, tmp_name = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as fh:
            json.dump(state, fh, indent=2)
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _empty_day() -> dict:
    return {
        "haiku_input_tokens": 0,
        "haiku_output_tokens": 0,
        "opus_input_tokens": 0,
        "opus_output_tokens": 0,
        "calls_haiku": 0,
        "calls_opus": 0,
        "spend_usd": 0.0,
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def record_call(model: str, input_tokens: int, output_tokens: int) -> None:
    """Record an LLM call's token usage to the daily cost-tracker state file.

    Args:
        model:         Model name string (e.g. "claude-haiku-4-5-20251001").
        input_tokens:  Number of input tokens consumed by this call.
        output_tokens: Number of output tokens produced by this call.

    Model classification is case-insensitive:
    - Contains "haiku" -> Haiku pricing
    - Contains "opus"  -> Opus pricing
    - Unknown          -> Opus rate (defensive over-estimate; logs a warning)
    """
    if _is_haiku(model):
        input_rate = HAIKU_INPUT_USD_PER_MTOK
        output_rate = HAIKU_OUTPUT_USD_PER_MTOK
        bucket = "haiku"
    elif _is_opus(model):
        input_rate = OPUS_INPUT_USD_PER_MTOK
        output_rate = OPUS_OUTPUT_USD_PER_MTOK
        bucket = "opus"
    else:
        logger.warning(
            "cost-tracker: unrecognised model %r -- charging at Opus rate (defensive).",
            model,
        )
        input_rate = OPUS_INPUT_USD_PER_MTOK
        output_rate = OPUS_OUTPUT_USD_PER_MTOK
        bucket = "opus"

    call_cost = (input_tokens / 1_000_000) * input_rate + (output_tokens / 1_000_000) * output_rate

    today = _today_str()
    path = _state_path()
    state = _load_state(path)
    daily = state.setdefault("daily_totals", {})

    if today not in daily:
        daily[today] = _empty_day()

    day = daily[today]
    day[f"{bucket}_input_tokens"] += input_tokens
    day[f"{bucket}_output_tokens"] += output_tokens
    day[f"calls_{bucket}"] += 1
    day["spend_usd"] = round(day["spend_usd"] + call_cost, 10)

    _save_state(path, state)


def check_daily_cap() -> bool:
    """Return True if today's accumulated spend has reached the $5 daily hard cap.

    The cap exists as a guardrail during Phase 3 (when Haiku calls first happen
    in production) so a misconfigured loop or volume spike cannot burn the
    monthly budget in 48-72 hours.

    Caller decides what to do when this returns True.  Phase 4 task 4.5 will
    extend this stub with monthly tracking, Telegram alerts, and degraded mode.
    """
    today = _today_str()
    path = _state_path()
    state = _load_state(path)
    today_spend = state.get("daily_totals", {}).get(today, {}).get("spend_usd", 0.0)
    return today_spend >= DAILY_CAP_USD
