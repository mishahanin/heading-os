#!/usr/bin/env python3
"""Minimal cost-tracking stub for the Inbox Pulse daemon.

STUB -- full CostTracker lands in Phase 4 (task 4.5), which will extend
this module with monthly tracking, Telegram alerts, and degraded-mode
operation.  This stub exists to close the Phase 3 exposure window: Haiku
LLM calls ship in Phase 3, but the full cost tracker was originally
planned for Phase 4, creating a 3-day window where budget could burn
unmonitored.  This stub closes that gap with minimal complexity.

Tests: tests/test_a_day_that_could_not_be_read_and_was_called_quiet.py, tests/inbox_pulse/test_cost_stub.py

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
``state/email-triage/cost-tracker.json``, resolved by
``scripts.inbox_pulse.paths.get_state_dir()``: ``INBOX_PULSE_STATE_DIR``
when that env var is set, else under the DATA root (never the engine
tree -- runtime state is DATA).  With no override and no writable data
root, ``_state_path()`` raises ``DataRootError`` rather than resolving
into the public engine clone; ``record_call`` propagates that refusal and
``check_daily_cap`` reports the cap as reached.  Written
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
    from scripts.utils import claude_models

    record_call(claude_models.latest("haiku"), input_tokens=1500, output_tokens=300)
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
from scripts.inbox_pulse.paths import get_state_dir
from scripts.utils.workspace import (
    DataRootError,
    get_default_tz,
    get_default_tz_name,
    require_writable_data_root,
)

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


# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------

def _state_path() -> Path:
    """The cost-tracker file, beside the daemon's other state.

    Delegates to `paths.get_state_dir()` rather than deriving a root here. It
    used to fall back to the ENGINE workspace root when INBOX_PULSE_STATE_DIR
    was unset, while every other daemon file resolved
    `<data_root>/state/email-triage`. On this workspace the two trees are
    separate, so the spend ledger landed in the code repository and the daemon's
    own logs, cursor and heartbeat landed in the data overlay: a budget cap
    reading a file nobody else writes. The env override still wins, and still
    does so inside get_state_dir().

    With no override, the data root is REQUIRED rather than merely resolved.
    `get_data_root()` has a documented last resort: with no env override, no
    in-tree data and no sibling overlay, it answers `<workspace_root>/examples`,
    which is INSIDE the engine clone, and `get_state_dir()` creates what it
    resolves. The engine repository is public, so on a data-less clone (a fresh
    contributor checkout, or CI) the plain call had this stub write a spend
    ledger (model ids, token counts, dollar amounts, one key per operator day)
    into the tree that gets pushed, and mkdir the directory for it even when no
    call was ever recorded. Refusing is the only correct answer there.

    Measured 2026-08-26 on a worktree with no sibling overlay: `_state_path()`
    answered `<engine>/examples/state/email-triage/cost-tracker.json` and the
    parent directory had already been created.

    Raises:
        DataRootError: when no writable data root backs this workspace and no
            INBOX_PULSE_STATE_DIR override was given.
    """
    if not os.environ.get("INBOX_PULSE_STATE_DIR", "").strip():
        # Same override key get_state_dir() reads, checked here so the refusal
        # happens BEFORE that function mkdirs the directory it resolved.
        require_writable_data_root()
    return get_state_dir() / "cost-tracker.json"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _today_str() -> str:
    """Today's date as YYYY-MM-DD in the CONFIGURED timezone.

    Not UTC+4. This docstring said "the configured timezone, UTC+4" and a
    comment above said "+4:00 UTC, no DST", while the code has always called
    get_default_tz(), which reads HEADING_OS_TZ and is UTC when that is unset.
    On any other setting the daily cap rolls over at a wall-clock time the
    comments denied, and the reader trusts the comment.
    """
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
        model:         Resolved model id, from claude_models.latest("haiku").
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

    Fails CLOSED when the ledger has nowhere to live: `_state_path()` refuses on
    a clone with no writable data root, and a guard that answers a question must
    not raise in place of answering it, so the refusal is logged and reported as
    "cap reached". Spend that cannot be recorded is spend that must not happen.
    `record_call()` does the opposite on purpose: it is a WRITE, and a write
    that cannot land is a loud failure, not a quiet True.
    """
    today = _today_str()
    try:
        path = _state_path()
    except DataRootError as exc:
        logger.warning(
            "cost-tracker: no writable data root (%s); reporting the daily cap "
            "as reached, because spend cannot be recorded anywhere.", exc,
        )
        return True
    state = _load_state(path)
    today_spend = state.get("daily_totals", {}).get(today, {}).get("spend_usd", 0.0)
    return today_spend >= DAILY_CAP_USD
