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


class LedgerUnreadableError(Exception):
    """The spend ledger exists but could not be read back.

    Distinct from the ledger being ABSENT, which is a real and honest zero. A
    reader that cannot tell those two apart reads a corrupt file as "no spend
    today", which is the direction `check_daily_cap` must never fail in.
    """


def _load_state(path: Path, strict: bool = False) -> dict:
    """The ledger, or an empty day. `strict=True` refuses to invent the empty day.

    Measured 2026-08-31 on one ledger file in two states: with
    `{"daily_totals": {<today>: {"spend_usd": 9.99}}}` on disk
    `check_daily_cap()` answered True, and after overwriting the same file with
    `not json at all` it answered False on nothing but a log line, after which
    the next `record_call` rewrote the file from near zero. So a corrupt ledger
    RAISED the cap instead of holding it, and the docstring on `check_daily_cap`
    argues the opposite case two lines above the read: "Spend that cannot be
    recorded is spend that must not happen."

    `record_call` still asks non-strict on purpose. It is a WRITE, it holds the
    only copy of the call it was handed, and re-founding the day from zero loses
    less than dropping that call entirely.

    A ledger that PARSES and is not an object is refused the same way, and that
    half arrived on 2026-08-31 after the first. `json.loads("[1, 2, 3]")` raises
    nothing, so the list went straight back to `check_daily_cap`, whose
    `state.get(...)` then died with `AttributeError: 'list' object has no
    attribute 'get'` - a guard that neither answered nor held.

    `UnicodeDecodeError` joined the caught set on 2026-09-01, and it is the
    third spelling of one defect rather than a new one. It is a `ValueError`,
    NOT an `OSError`, and it is not a `json.JSONDecodeError` either, so bytes
    that are not valid UTF-8 sailed past a two-name except clause that reads as
    though it covers "the file would not read". Measured that day on one ledger
    file in six byte states, `check_daily_cap()` then `record_call()` on each:

        valid utf-8, over cap      -> True                  / recorded
        lone continuation byte     -> RAISED UnicodeDecodeError / RAISED
        truncated JSON             -> True                  / recorded
        latin-1 encoded            -> RAISED UnicodeDecodeError / RAISED
        utf-16 with BOM            -> RAISED UnicodeDecodeError / RAISED
        a NUL-padded short write   -> True                  / recorded

    Three of six crashed BOTH entry points. A ledger reaches those states the
    ordinary way: a partial write that lands mid-codepoint, a file restored
    through a tool that re-encoded it, or an editor that saved as latin-1.
    """
    if not path.exists():
        return {"daily_totals": {}}
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError, OSError) as exc:
        logger.warning("cost-tracker: could not read state file %s: %s", path, exc)
        if strict:
            raise LedgerUnreadableError(str(exc)) from exc
        return {"daily_totals": {}}
    if not isinstance(state, dict):
        reason = f"top-level value is {type(state).__name__}, not an object"
        logger.warning("cost-tracker: state file %s is not a ledger: %s",
                       path, reason)
        if strict:
            raise LedgerUnreadableError(reason)
        return {"daily_totals": {}}
    return state


def _today_spend(state: dict, today: str) -> float:
    """Today's recorded spend, or `LedgerUnreadableError` when it cannot be read.

    Every hop is shape-checked, because each one used to be a bare `.get` chain
    ending in a `>=` against a float. Measured 2026-08-31 against
    `check_daily_cap()` on nine ledger states, one file, one call each:

        absent                           -> False
        not json at all                  -> True
        daily_totals is a string         -> RAISED AttributeError
        daily_totals is a list           -> RAISED AttributeError
        the day entry is a string        -> RAISED AttributeError
        spend_usd is a string            -> RAISED TypeError
        spend_usd is None                -> RAISED TypeError
        top level is a list              -> RAISED AttributeError
        over cap, well formed            -> True

    Six of nine raised out of a function whose own docstring says a guard "must
    not raise in place of answering". The unparseable case had been closed
    earlier the same day; the neighbouring branch carried the identical shape
    one layer in, where the bytes are valid JSON and the OBJECT is wrong.

    An absent `daily_totals`, an absent day and an absent `spend_usd` stay a
    real zero: nothing was recorded, which is honest. Only a value that is
    PRESENT and of the wrong type is unreadable.
    """
    daily = state.get("daily_totals", {})
    if not isinstance(daily, dict):
        raise LedgerUnreadableError(
            f"daily_totals is {type(daily).__name__}, not an object")
    day = daily.get(today, {})
    if not isinstance(day, dict):
        raise LedgerUnreadableError(
            f"the entry for {today} is {type(day).__name__}, not an object")
    spend = day.get("spend_usd", 0.0)
    # `bool` is an `int` subclass and `True >= 5.0` is a silent False, so a
    # ledger reading `"spend_usd": true` would have turned the cap off.
    if isinstance(spend, bool) or not isinstance(spend, (int, float)):
        raise LedgerUnreadableError(
            f"spend_usd for {today} is {type(spend).__name__}, not a number")
    return float(spend)


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
    # Re-found anything the shape check cannot use, rather than raising. This is
    # the same trade `_load_state` already makes for a ledger that will not
    # parse: this function is a WRITE holding the only copy of the call it was
    # handed, so losing a day's history costs less than dropping the call and
    # every call after it. Before 2026-08-31 a `daily_totals` that arrived as a
    # string reached `daily[today] = _empty_day()` and raised TypeError, and it
    # would have raised on every subsequent call too, because nothing rewrites a
    # file the writer refuses to touch.
    daily = state.get("daily_totals")
    if not isinstance(daily, dict):
        if daily is not None:
            logger.warning("cost-tracker: daily_totals was %s, not an object; "
                           "re-founding it", type(daily).__name__)
        daily = {}
    state["daily_totals"] = daily

    day = daily.get(today)
    if not isinstance(day, dict):
        if day is not None:
            logger.warning("cost-tracker: the entry for %s was %s, not an "
                           "object; re-founding the day", today,
                           type(day).__name__)
        day = _empty_day()
    else:
        # A day missing a counter, or holding a string where a number belongs,
        # is the same class one level down: `day["spend_usd"] + call_cost` on a
        # str is a TypeError out of a write path that must not have one.
        for key, default in _empty_day().items():
            value = day.get(key, default)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                logger.warning("cost-tracker: %s for %s was %r; re-founding "
                               "that counter", key, today, value)
                value = default
            day[key] = value
    daily[today] = day

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

    Fails CLOSED on a corrupt ledger too, for the same stated reason and since
    2026-08-31. A ledger that will not parse is spend that cannot be READ, which
    is the same standing as spend that cannot be recorded: the day's real total
    could be anything, including well past the cap. Until that date the corrupt
    path returned False on a log line, so a truncated or half-written file
    turned the hard cap off and let the day start again from zero.

    "Corrupt" means BOTH shapes, and the second half landed later the same day.
    Unparseable bytes were closed first; a file whose bytes are valid JSON and
    whose OBJECT is wrong still went through a bare `.get` chain and raised
    `AttributeError` or `TypeError` out of this function. Six of nine probed
    ledger states raised - the measurement is in `_today_spend`. Raising is not
    failing closed: this function answers a question, and an exception is the
    caller's problem to handle, on the one path where the caller handling it
    badly means spending money nobody is counting.
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
    try:
        state = _load_state(path, strict=True)
        today_spend = _today_spend(state, today)
    except LedgerUnreadableError as exc:
        logger.warning(
            "cost-tracker: ledger %s is unreadable (%s); reporting the daily "
            "cap as reached, because today's spend cannot be read.", path, exc,
        )
        return True
    return today_spend >= DAILY_CAP_USD
