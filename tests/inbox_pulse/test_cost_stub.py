"""Tests for the Phase 0 cost-tracker stub (scripts/inbox_pulse/cost.py).

The arithmetic half:
1. record_call accumulates tokens and spend correctly for known models.
2. check_daily_cap returns False when spend is below threshold.
3. check_daily_cap returns True at or above threshold.
4. Unknown models are charged at Opus rate (defensive over-estimate).

Each test uses the INBOX_PULSE_STATE_DIR env var (monkeypatched to tmp_path)
so the state file is isolated and does not touch the workspace state directory.

## The shape half, added 2026-08-31, and why it was missing

Earlier the same day `_load_state` gained `strict=True` so a ledger whose BYTES
will not parse reports the cap as reached instead of False. That fix is real and
`tests/test_two_readers_that_failed_open_on_a_file_they_could_not_parse.py`
pins it in both directions with three unparseable ledgers.

The branch one layer in carried the identical shape and nothing anywhere
covered it: bytes that parse cleanly as JSON into the WRONG OBJECT. Measured
2026-08-31 against `check_daily_cap()`, one ledger file, nine states, one call
each, before any change:

    absent                           -> False
    not json at all                  -> True
    daily_totals is a string         -> RAISED AttributeError: 'str' object has no attribute 'get'
    daily_totals is a list           -> RAISED AttributeError: 'list' object has no attribute 'get'
    the day entry is a string        -> RAISED AttributeError: 'str' object has no attribute 'get'
    spend_usd is a string            -> RAISED TypeError: '>=' not supported between instances of 'str' and 'float'
    spend_usd is None                -> RAISED TypeError: '>=' not supported between instances of 'NoneType' and 'float'
    top level is a list              -> RAISED AttributeError: 'list' object has no attribute 'get'
    over cap, well formed            -> True

Six of nine raised, out of a function whose own docstring says a guard that
answers a question "must not raise in place of answering it". Raising is not
failing closed. `check_daily_cap` has no production caller yet - the usage in
this module's header is `if check_daily_cap(): raise` - so what an exception
becomes is entirely the future caller's business, on the one path where getting
it wrong means spending money nobody is counting. That is why the finding is
LATENT rather than live, and it is stated because it would be easy to claim
otherwise.

Why the four tests above could not see it: every one of them writes the ledger
by CALLING `record_call`, so the only file they ever read back is one this
module wrote itself. A producer cannot produce its own corruption. Every test
below puts the bytes on disk by hand.

`record_call` is covered from the other side. It stays deliberately
recovering rather than refusing - it is a write holding the only copy of the
call it was handed - so the assertions there are that it re-founds and keeps
recording, never that it raises.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

# The counter set `_empty_day()` writes, spelled out rather than imported, so a
# renamed or dropped counter fails a test here instead of silently changing what
# the anchors below assert about.
_EMPTY_DAY = {
    "haiku_input_tokens": 0,
    "haiku_output_tokens": 0,
    "opus_input_tokens": 0,
    "opus_output_tokens": 0,
    "calls_haiku": 0,
    "calls_opus": 0,
    "spend_usd": 0.0,
}

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


# ============================================================
# The shape half: bytes that parse, into an object that is not a ledger
# ============================================================

TODAY = "2026-05-27"

# Every one of these is valid JSON. The unparseable forms belong to
# `tests/test_two_readers_that_failed_open_on_a_file_they_could_not_parse.py`
# and are deliberately NOT repeated here; a second copy of a corpus is the one
# that stops being updated.
WRONG_SHAPE_LEDGERS = {
    "top level is a list": [1, 2, 3],
    "top level is a string": "cost-tracker",
    "top level is a number": 0,
    "daily_totals is a string": {"daily_totals": "9.99"},
    "daily_totals is a list": {"daily_totals": [{TODAY: 9.99}]},
    "the day entry is a string": {"daily_totals": {TODAY: "9.99"}},
    "the day entry is a number": {"daily_totals": {TODAY: 9.99}},
    "spend_usd is a string": {"daily_totals": {TODAY: {"spend_usd": "9.99"}}},
    "spend_usd is null": {"daily_totals": {TODAY: {"spend_usd": None}}},
    # `bool` is an `int` subclass, so `True >= 5.0` is a quiet False and this
    # one turned the cap OFF rather than crashing. The loudest failure in the
    # list is not the dangerous one.
    "spend_usd is a boolean": {"daily_totals": {TODAY: {"spend_usd": True}}},
}


def _ledger(tmp_path: Path, monkeypatch, payload=None):
    """A cost module pinned to `tmp_path` and to TODAY, plus the ledger path."""
    monkeypatch.setenv("INBOX_PULSE_STATE_DIR", str(tmp_path))
    from scripts.inbox_pulse import cost
    monkeypatch.setattr(cost, "_today_str", lambda: TODAY)
    path = tmp_path / "cost-tracker.json"
    if payload is not None:
        path.write_text(json.dumps(payload), encoding="utf-8")
    return cost, path


@pytest.mark.parametrize("label", sorted(WRONG_SHAPE_LEDGERS))
def test_a_ledger_that_parses_into_the_wrong_object_reports_the_cap_as_reached(
        tmp_path, monkeypatch, label):
    """Fail CLOSED, and ANSWER rather than raise.

    `pytest.raises` would be the wrong assertion even inverted: the contract is
    a bool. An exception here is not a strict refusal, it is the guard
    declining to answer and handing the decision to whichever caller is least
    equipped to make it.
    """
    cost, _ = _ledger(tmp_path, monkeypatch, WRONG_SHAPE_LEDGERS[label])
    assert cost.check_daily_cap() is True, (
        f"{label}: a ledger nobody can read reported the day as under budget")


@pytest.mark.parametrize("label", sorted(WRONG_SHAPE_LEDGERS))
def test_the_wrong_shape_is_named_in_the_log_not_swallowed(
        tmp_path, monkeypatch, caplog, label):
    """A silent True is indistinguishable from a real $5 day.

    The operator has to be able to tell "you spent the budget" from "your
    ledger is garbage", and only the log line carries that difference.
    """
    cost, _ = _ledger(tmp_path, monkeypatch, WRONG_SHAPE_LEDGERS[label])
    with caplog.at_level(logging.WARNING, logger=cost.logger.name):
        cost.check_daily_cap()
    assert any("unreadable" in r.getMessage() for r in caplog.records), (
        f"{label}: the cap was reported as reached with no reason logged: "
        f"{caplog.text}")


def test_an_absent_ledger_is_still_an_honest_zero(tmp_path, monkeypatch):
    """The other jaw, and the one that decides whether the tests above mean
    anything. A `check_daily_cap` hard-wired to True satisfies every assertion
    in the two tests above and stops the daemon dead on its first ever run,
    before a single cent is spent."""
    cost, path = _ledger(tmp_path, monkeypatch)
    assert not path.exists()
    assert cost.check_daily_cap() is False


@pytest.mark.parametrize("payload,expected", [
    ({}, False),
    ({"daily_totals": {}}, False),
    ({"daily_totals": {TODAY: {}}}, False),
    ({"daily_totals": {"2026-05-26": {"spend_usd": 99.0}}}, False),
    ({"daily_totals": {TODAY: {"spend_usd": 4.99}}}, False),
    ({"daily_totals": {TODAY: {"spend_usd": 5.0}}}, True),
    ({"daily_totals": {TODAY: {"spend_usd": 5}}}, True),      # int, not float
    ({"daily_totals": {TODAY: {"spend_usd": 9.99}}}, True),
])
def test_a_well_formed_ledger_keeps_answering_on_the_money(
        tmp_path, monkeypatch, payload, expected):
    """The shape guard must not start refusing ledgers that are simply thin.

    An absent `daily_totals`, an absent day and an absent `spend_usd` are all
    honest zeroes: nothing was recorded. Only a value that is PRESENT and of
    the wrong type is unreadable. Yesterday's $99 is on the list because a
    guard that summed the whole file instead of reading today would pass every
    other row here.
    """
    cost, _ = _ledger(tmp_path, monkeypatch, payload)
    assert cost.check_daily_cap() is expected


@pytest.mark.parametrize("label", sorted(WRONG_SHAPE_LEDGERS))
def test_record_call_re_founds_a_wrong_shaped_ledger_instead_of_dying(
        tmp_path, monkeypatch, label):
    """The write path takes the opposite trade, deliberately, and must keep it.

    `record_call` holds the only copy of the call it was handed. Dropping that
    call loses a real number; re-founding the day loses a history the file had
    already lost. Before 2026-08-31 it did neither: a `daily_totals` of `"9.99"`
    reached `daily[today] = _empty_day()` and raised TypeError, and would have
    raised on every call after it, because nothing rewrites a file the writer
    refuses to touch. So the cap would then be reading a file that stopped
    being updated the moment it broke.
    """
    cost, path = _ledger(tmp_path, monkeypatch, WRONG_SHAPE_LEDGERS[label])

    # 1_000_000 input * 0.80/mtok = $0.80
    cost.record_call("claude-haiku-4-5-20251001", 1_000_000, 0)

    day = json.loads(path.read_text(encoding="utf-8"))["daily_totals"][TODAY]
    assert day["calls_haiku"] == 1, f"{label}: the call was not recorded"
    assert day["haiku_input_tokens"] == 1_000_000
    assert day["spend_usd"] == pytest.approx(0.80, rel=1e-6)
    # And the cap can read what the writer just wrote.
    assert cost.check_daily_cap() is False


def test_record_call_does_not_re_found_a_ledger_it_can_read(tmp_path, monkeypatch):
    """Anchor for the test above. Re-founding on EVERY call would satisfy all
    ten cases there and would silently zero the day's real spend on every
    write, which is the same cap failure wearing the writer's hat."""
    cost, path = _ledger(tmp_path, monkeypatch,
                         {"daily_totals": {TODAY: {**_EMPTY_DAY,
                                                   "calls_haiku": 3,
                                                   "spend_usd": 4.5}}})
    cost.record_call("claude-haiku-4-5-20251001", 1_000_000, 0)

    day = json.loads(path.read_text(encoding="utf-8"))["daily_totals"][TODAY]
    assert day["calls_haiku"] == 4, "the existing history was thrown away"
    assert day["spend_usd"] == pytest.approx(5.30, rel=1e-6)
    # 4.50 already recorded plus 0.80 crosses the cap. A re-founding writer
    # would leave 0.80 here and answer False, so this line is the discriminator,
    # not decoration.
    assert cost.check_daily_cap() is True


# ============================================================
# The third spelling: bytes that are not UTF-8 at all
# ============================================================
#
# `except (json.JSONDecodeError, OSError)` reads like "the file would not read"
# and is not. `UnicodeDecodeError` is a `ValueError`; it is neither an `OSError`
# nor a `json.JSONDecodeError`, so it walked straight out of both entry points.
# Every literal below is written as a byte escape rather than a pasted
# character, so nothing invisible can enter this file.

NON_UTF8_LEDGERS = {
    # A continuation byte with no lead byte. What a write torn mid-codepoint
    # leaves behind.
    "lone continuation byte": b'{"daily_totals": \x80}',
    # 0xe9 is latin-1 for an accented e; in UTF-8 it is a lead byte with no
    # continuation. An editor saving the file in the wrong encoding.
    "latin-1 encoded": b'{"daily_totals": {"note": "caf\xe9"}}',
    # A UTF-16 BOM. A file round-tripped through a tool that re-encoded it.
    "utf-16 with a BOM": b'\xff\xfe{\x00"\x00a\x00"\x00:\x001\x00}\x00',
    # Valid JSON followed by binary junk: the tail of a longer file that a
    # shorter write did not fully overwrite.
    "trailing binary junk": b'{"daily_totals": {}}\x00\xfe\xff\x80',
}


@pytest.mark.parametrize("label", sorted(NON_UTF8_LEDGERS))
def test_a_ledger_that_is_not_utf8_reports_the_cap_as_reached(
        tmp_path, monkeypatch, label):
    """The same contract as the two corpora above, at the decode layer."""
    monkeypatch.setenv("INBOX_PULSE_STATE_DIR", str(tmp_path))
    from scripts.inbox_pulse import cost
    monkeypatch.setattr(cost, "_today_str", lambda: TODAY)
    (tmp_path / "cost-tracker.json").write_bytes(NON_UTF8_LEDGERS[label])

    assert cost.check_daily_cap() is True, (
        f"{label}: bytes that will not decode reported the day as under budget")


@pytest.mark.parametrize("label", sorted(NON_UTF8_LEDGERS))
def test_record_call_survives_a_ledger_that_is_not_utf8(
        tmp_path, monkeypatch, label):
    """The write path too. It crashed on exactly the same three inputs, which
    is worse than the read crashing: a writer that cannot write stops updating
    the file the cap reads, so the corruption becomes permanent."""
    monkeypatch.setenv("INBOX_PULSE_STATE_DIR", str(tmp_path))
    from scripts.inbox_pulse import cost
    monkeypatch.setattr(cost, "_today_str", lambda: TODAY)
    path = tmp_path / "cost-tracker.json"
    path.write_bytes(NON_UTF8_LEDGERS[label])

    cost.record_call("claude-haiku-4-5-20251001", 1_000_000, 0)

    day = json.loads(path.read_text(encoding="utf-8"))["daily_totals"][TODAY]
    assert day["calls_haiku"] == 1, f"{label}: the call was not recorded"
    assert day["spend_usd"] == pytest.approx(0.80, rel=1e-6)


def test_a_ledger_with_non_ascii_that_IS_valid_utf8_is_read_normally(
        tmp_path, monkeypatch):
    """The anchor. Catching `UnicodeDecodeError` must not become "refuse any
    byte over 0x7F". A ledger carrying a non-ASCII note is perfectly readable
    and its spend still decides the answer, so a guard that failed closed on
    the mere presence of one would stop the daemon over a comment.
    """
    monkeypatch.setenv("INBOX_PULSE_STATE_DIR", str(tmp_path))
    from scripts.inbox_pulse import cost
    monkeypatch.setattr(cost, "_today_str", lambda: TODAY)
    path = tmp_path / "cost-tracker.json"
    # "cafe" with an acute e, plus a Cyrillic word, encoded correctly this
    # time. Written as escapes so this file stays pure ASCII on disk and no
    # character in it can be invisible.
    note = "caf\u00e9 \u0431\u044e\u0434\u0436\u0435\u0442"
    payload = {"note": note,
               "daily_totals": {TODAY: {"spend_usd": 1.0}}}
    path.write_bytes(json.dumps(payload, ensure_ascii=False).encode("utf-8"))

    assert cost.check_daily_cap() is False
    payload["daily_totals"][TODAY]["spend_usd"] = 9.99
    path.write_bytes(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    assert cost.check_daily_cap() is True


# ============================================================
# The fourth spelling: a ledger that is there and cannot be OPENED
# ============================================================
#
# `_load_state` catches `OSError` beside the two decode errors, and until
# 2026-09-01 nothing exercised that half. Every corrupt-ledger corpus in this
# repository puts BYTES on disk and lets `read_text` succeed: the ten wrong
# SHAPES above, the four unparseable byte strings in
# `tests/test_two_readers_that_failed_open_on_a_file_they_could_not_parse.py`,
# and the four non-UTF-8 ones above. None of them makes the open itself fail.
#
# MEASURED 2026-09-01 by narrowing the except clause to the two it was already
# covered for:
#
#     -   except (json.JSONDecodeError, UnicodeDecodeError, OSError) as exc:
#     +   except (json.JSONDecodeError, UnicodeDecodeError) as exc:
#
#     tests/inbox_pulse                     -> 226 passed  (baseline: 226 passed)
#     the 45-file wide set + tests/contract -> 7 failed, 1199 passed, 3 skipped
#                                              (identical to baseline; those 7
#                                               are sandbox-environment
#                                               failures, present either way)
#     the WHOLE tests/ suite                -> 50 failed, 20042 passed,
#                                              104 skipped, and the same 50 fail
#                                              with the mutation reverted
#
# Nothing anywhere failed, so the OSError name in that tuple is currently held
# in place by nothing but the fact that no one has deleted it.

def test_a_ledger_that_cannot_be_opened_reports_the_cap_as_reached(
        tmp_path, monkeypatch):
    """A directory where the ledger should be: present, and unopenable.

    This is the shape a botched restore or a container mount leaves, and it is
    the one corrupt-ledger case where `read_text` never returns at all. The
    contract is the same as for every other unreadable ledger: ANSWER, and
    answer True.
    """
    cost, path = _ledger(tmp_path, monkeypatch)
    path.mkdir()

    assert cost.check_daily_cap() is True, (
        "a ledger the process cannot open reported the day as under budget")


def test_a_ledger_that_cannot_be_opened_says_so_in_the_log(
        tmp_path, monkeypatch, caplog):
    """A silent True is indistinguishable from a real $5 day, here too."""
    cost, path = _ledger(tmp_path, monkeypatch)
    path.mkdir()

    with caplog.at_level(logging.WARNING, logger=cost.logger.name):
        cost.check_daily_cap()
    assert any("unreadable" in r.getMessage() for r in caplog.records), (
        f"the cap was reported as reached with no reason logged: {caplog.text}")


def test_record_call_fails_loudly_when_the_ledger_cannot_be_written(
        tmp_path, monkeypatch):
    """The write path's opposite trade, at the same input.

    `record_call` recovers from every ledger it can still overwrite, and the
    tests above pin that. It must NOT extend the recovery to a ledger it cannot
    write at all: a write that never lands has to be loud, because the
    alternative is a daemon that believes it is metering spend while the file
    has not changed since the fault. `check_daily_cap` already holds that day
    closed; a swallowed write would be the one path that reopens it.
    """
    cost, path = _ledger(tmp_path, monkeypatch)
    path.mkdir()

    with pytest.raises(OSError):
        cost.record_call("claude-haiku-4-5-20251001", 1_000_000, 0)


# ============================================================
# The ledger's atomic write, measured on an interruption
# ============================================================

def test_an_interrupted_ledger_write_leaves_the_previous_ledger_intact(
        tmp_path, monkeypatch):
    """`_save_state` says "Atomic write: write to .tmp then os.replace"; the
    module header says a crash mid-write "never corrupts the file". NEW
    2026-09-01, because nothing measured either sentence.

    MEASURED that day by replacing the body with the naive form:

        -   tmp_fd, tmp_name = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
        -   ... json.dump into it, then os.replace(tmp_name, path) ...
        +   with open(path, "w", encoding="utf-8") as fh:
        +       json.dump(state, fh, indent=2)

        tests/inbox_pulse            -> 226 passed  (baseline: 226 passed)
        the 45-file wide set + contract -> 7 failed, 1199 passed, 3 skipped
                                        (identical to baseline)

    The interruption needs no patching of the stdlib: `json.dump` streams, so a
    value it cannot serialise lands the earlier bytes and then raises, which is
    what a crash mid-write looks like from the file's side.

    The assertion is on the BYTES rather than on `check_daily_cap()`, and that
    is the point. A torn ledger is unreadable, so the cap answers True either
    way and would not discriminate; what the naive write actually destroys is
    the day's recorded spend, which is the number the operator is owed.
    """
    cost, path = _ledger(tmp_path, monkeypatch,
                         {"daily_totals": {TODAY: {**_EMPTY_DAY,
                                                   "calls_haiku": 3,
                                                   "spend_usd": 4.5}}})
    before = path.read_bytes()

    # `set()` is not JSON-serialisable, ordered after a long string so the
    # encoder has already written real bytes by the time it gives up.
    with pytest.raises(TypeError):
        cost._save_state(path, {"pad": "x" * 200, "boom": set()})

    assert path.read_bytes() == before, (
        "an interrupted ledger write changed the file; the previous ledger "
        "must survive byte for byte")
    assert list(tmp_path.glob("*.tmp")) == [], (
        "the failed write left its scratch file behind")
    # And the day's real spend is still the number the cap reads.
    day = json.loads(path.read_text(encoding="utf-8"))["daily_totals"][TODAY]
    assert day["spend_usd"] == pytest.approx(4.5)
    assert cost.check_daily_cap() is False


def test_a_successful_ledger_write_still_replaces_the_previous_one(tmp_path,
                                                                   monkeypatch):
    """Anchor. A `_save_state` that simply never wrote would satisfy the test
    above, and the cap would then read a ledger frozen at whatever it held when
    the daemon started."""
    cost, path = _ledger(tmp_path, monkeypatch,
                         {"daily_totals": {TODAY: {**_EMPTY_DAY,
                                                   "spend_usd": 4.5}}})
    cost._save_state(path, {"daily_totals": {TODAY: {**_EMPTY_DAY,
                                                     "spend_usd": 7.25}}})

    day = json.loads(path.read_text(encoding="utf-8"))["daily_totals"][TODAY]
    assert day["spend_usd"] == pytest.approx(7.25)
    assert cost.check_daily_cap() is True
    assert list(tmp_path.glob("*.tmp")) == []


def test_the_local_empty_day_still_matches_the_one_the_module_writes():
    """`_EMPTY_DAY` is a hand copy, and a hand copy drifts. If a counter is
    renamed, the anchors above would seed a day the writer does not recognise
    and would start re-founding it, which is the behaviour they exist to
    refute."""
    from scripts.inbox_pulse import cost

    assert cost._empty_day() == _EMPTY_DAY


def test_yesterdays_row_survives_a_re_founding(tmp_path, monkeypatch):
    """Re-founding is scoped to the day that cannot be read. Dropping the whole
    file would make one bad row erase a month of history."""
    cost, path = _ledger(tmp_path, monkeypatch, {"daily_totals": {
        "2026-05-26": {**_EMPTY_DAY, "spend_usd": 1.25},
        TODAY: "not a day",
    }})
    cost.record_call("claude-haiku-4-5-20251001", 1_000_000, 0)

    totals = json.loads(path.read_text(encoding="utf-8"))["daily_totals"]
    assert totals["2026-05-26"]["spend_usd"] == pytest.approx(1.25)
    assert totals[TODAY]["spend_usd"] == pytest.approx(0.80, rel=1e-6)
