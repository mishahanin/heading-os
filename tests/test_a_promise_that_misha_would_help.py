"""Shard 06-p1: three swap paths that told a member "Misha will help" and never
told Misha, a roster index that scheduled people who had left, a monitor green
over a dead handler, and eight smaller places where the file's own words and its
code disagreed.

* ``_handle_b_tap``: B accepts, ``_apply_bilateral_swap`` returns False because
  the schedule moved under the request. B saw "Misha will help", an
  ``apply_failed`` event was written, and that was all. A - whose last message
  was "Request sent... I'll let you know" - waited forever, Misha heard nothing,
  and ``apply_failed`` is terminal so the expiry sweep (``proposed_to_b`` only)
  never reached it either. Three silent exits from the one branch that promises
  a human will step in.

* ``_handle_a_tap``: the DM to B fails (B blocked the bot is the likely cause).
  A is told "Misha will help"; Misha gets one ``errors.log`` line nobody is
  alerted to. The ``b_unreachable`` branch two dozen lines above DOES DM Misha.

* ``build_roster_by_name`` indexed the WHOLE roster. ``cross_reference`` keeps
  excluded members in it with ``active=False``, and ``_handle_chat_member`` marks
  leavers the same way - so a cycle config naming a departed member bound their
  slot, they were sent the 2wk/3day/day-of DMs (no DM loop checks ``active``),
  and ``speaker_gaps`` stayed silent because it skips them as candidates AND
  counts their handle as scheduled.

* ``cmd_heartbeat`` pinged healthchecks.io and appended a ``heartbeat-tick``
  without calling Telegram at all, while ``poll-tick`` - which
  ``cmd_health_check`` treats as equivalent - is written only after a successful
  ``getUpdates``. With Telegram unreachable the heartbeat kept stamping and
  health-check kept printing "healthy".

* ``cmd_email_backup`` counted only ``start_received`` and ``swap_requested`` as
  engagement, so a member who submitted an ``/idea`` was mailed "I haven't seen
  a response"; and it never read the ``_log_dm("email-backup", ...)`` rows
  written to dedupe against, so a rerun on one Sunday mailed real people twice.

* ``cmd_speaker_dms`` and ``cmd_dayof_reminders`` both did
  ``entry["speaker_name"].split()[0]`` unguarded - an IndexError out of the
  middle of a send loop, which ``cmd_email_backup`` already guards against and
  documents.

* ``ensure_state_dir``'s self-heal caught FileNotFoundError and ValueError.
  openpyxl raises neither on a file that is not a workbook.

* ``seed_opt_ins`` (extracted from ``cmd_bootstrap`` step 6) copied the reactor's
  handle verbatim, seeding non-roster reactors and ``username: None``.

* ``cmd_unpin_weekly`` never cleared ``last-pinned.json`` on failure, so a
  hand-unpinned message made it warn every Wednesday forever;
  ``cmd_cycle_rollover`` omitted TypeError where the import-time guard has it;
  and two expiry messages hardcoded "24h" against a constant.

Run: python3 -m pytest tests/test_a_promise_that_misha_would_help.py
"""
from __future__ import annotations

import importlib.util
import json
import sys
import zipfile
from datetime import timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


@pytest.fixture(scope="module")
def fb():
    path = ROOT / "scripts" / "fireside-bot.py"
    spec = importlib.util.spec_from_file_location("p06p1_fireside_bot", str(path))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["p06p1_fireside_bot"] = mod
    spec.loader.exec_module(mod)
    return mod


class _Bot:
    """Records every outbound call so a silent path is visible as an absence."""

    def __init__(self, fail_send_to=None):
        self.messages: list[tuple[int, str]] = []
        self.toasts: list[str] = []
        self.edits: list[str] = []
        self._fail_send_to = fail_send_to

    def send_message(self, chat_id, text, **_kw):
        if self._fail_send_to is not None and int(chat_id) == int(self._fail_send_to):
            raise _TelegramError("Forbidden: bot was blocked by the user")
        self.messages.append((int(chat_id), text))
        return {"message_id": 999}

    def send_dm(self, user_id, text, **_kw):
        return self.send_message(user_id, text)

    def answer_callback_query(self, _cq_id, text="", **_kw):
        self.toasts.append(text)

    def edit_message_text(self, _chat, _mid, text, **_kw):
        self.edits.append(text)

    def to(self, user_id) -> list[str]:
        return [t for cid, t in self.messages if cid == int(user_id)]


_TelegramError = None  # bound in the fixture below


@pytest.fixture(autouse=True)
def _bind_error(fb):
    global _TelegramError
    _TelegramError = fb.TelegramAPIError


@pytest.fixture(autouse=True)
def _state_in_tmp(fb, tmp_path, monkeypatch):
    """Point the one constant every fireside writer resolves through at a
    tmp_path, for every test in the module.

    Autouse rather than opt-in. The four `_handle_a_tap` tests below never asked
    for isolation because they only assert on messages, and the undeliverable
    branch they exercise calls `log_error`, which appended to the operator's live
    `errors.log` until 2026-08-29. Two fixtures further down already redirected
    the constant, which is precisely the "landed in one of N copies" shape this
    file's own docstring catalogues.
    """
    state = tmp_path / "fireside-state"
    state.mkdir()
    monkeypatch.setattr(fb, "state_dir", lambda p=state: p)
    return state


MISHA = 1000
A_ID = 111
B_ID = 222

CTX = {
    "a_username": "alpha", "a_user_id": A_ID,
    "a_current_date": "2026-09-07", "a_current_slot": 1,
    "b_username": "bravo", "b_user_id": B_ID,
    "target_date": "2026-09-14", "target_slot": 2,
}


@pytest.fixture()
def swap(fb, monkeypatch):
    """Isolate the swap handlers: no disk, no Telegram, Misha reachable."""
    events: list[dict] = []
    logged: list[tuple] = []
    monkeypatch.setattr(fb, "_append_swap_event", events.append)
    monkeypatch.setattr(fb, "_log_event",
                        lambda kind, **kw: logged.append((kind, kw)))
    monkeypatch.setattr(fb, "misha_user_id", lambda: MISHA)
    return events, logged


# ============================================================
# The accept that could not be applied
# ============================================================

def _b_tap_accept(fb, bot, ok: bool, monkeypatch):
    monkeypatch.setattr(fb, "_apply_bilateral_swap", lambda **_kw: ok)
    fb._handle_b_tap(bot, "cq1", "rid1", "y", dict(CTX), "proposed_to_b",
                     None, None, B_ID)


def test_a_failed_apply_tells_a(fb, swap, monkeypatch):
    """A's last message was "Request sent... I'll let you know"."""
    bot = _Bot()
    _b_tap_accept(fb, bot, ok=False, monkeypatch=monkeypatch)
    assert bot.to(A_ID), "A was never told the swap they asked for had failed"
    assert "could not be applied" in bot.to(A_ID)[0]


def test_a_failed_apply_tells_misha(fb, swap, monkeypatch):
    """B was told "Misha will help", so Misha has to actually be told."""
    bot = _Bot()
    _b_tap_accept(fb, bot, ok=False, monkeypatch=monkeypatch)
    assert bot.to(MISHA), "the only human who can fix it heard nothing"
    assert "FAILED to apply" in bot.to(MISHA)[0]


def test_a_failed_apply_names_both_speakers_to_misha(fb, swap, monkeypatch):
    bot = _Bot()
    _b_tap_accept(fb, bot, ok=False, monkeypatch=monkeypatch)
    msg = bot.to(MISHA)[0]
    assert "@alpha" in msg and "@bravo" in msg
    assert "2026-09-07" in msg and "2026-09-14" in msg


def test_a_failed_apply_says_nothing_was_changed(fb, swap, monkeypatch):
    """The schedule is untouched; a message that left that open would send two
    people to the wrong session."""
    bot = _Bot()
    _b_tap_accept(fb, bot, ok=False, monkeypatch=monkeypatch)
    assert "Nothing was changed" in bot.to(MISHA)[0]
    assert "stays at" in bot.to(A_ID)[0]


def test_a_failed_apply_is_still_recorded_as_terminal(fb, swap, monkeypatch):
    events, _ = swap
    bot = _Bot()
    _b_tap_accept(fb, bot, ok=False, monkeypatch=monkeypatch)
    assert [e["event"] for e in events] == ["apply_failed"]


def test_a_failed_apply_is_not_reopened(fb, swap, monkeypatch):
    """It failed because the rows it names are gone. A fresh 24h window would
    re-propose a swap against entries that no longer exist."""
    events, _ = swap
    bot = _Bot()
    _b_tap_accept(fb, bot, ok=False, monkeypatch=monkeypatch)
    assert not any(e["event"] == "proposed_to_b" for e in events)


def test_a_failed_apply_is_logged_to_sessions(fb, swap, monkeypatch):
    _, logged = swap
    bot = _Bot()
    _b_tap_accept(fb, bot, ok=False, monkeypatch=monkeypatch)
    kinds = [k for k, _ in logged]
    assert "swap_failed" in kinds


def test_a_successful_apply_still_works(fb, swap, monkeypatch):
    """The green path, so a blanket failure branch cannot pass these tests."""
    events, logged = swap
    bot = _Bot()
    _b_tap_accept(fb, bot, ok=True, monkeypatch=monkeypatch)
    assert [e["event"] for e in events] == ["b_accepted", "completed"]
    assert "accepted" in bot.to(A_ID)[0]
    assert "done (bilateral)" in bot.to(MISHA)[0]
    assert [k for k, _ in logged] == ["swap_completed"]


def test_a_decline_still_tells_both(fb, swap, monkeypatch):
    """The neighbouring outcome that always did notify; it must survive."""
    bot = _Bot()
    monkeypatch.setattr(fb, "_apply_bilateral_swap", lambda **_kw: True)
    fb._handle_b_tap(bot, "cq1", "rid1", "n", dict(CTX), "proposed_to_b",
                     None, None, B_ID)
    assert "declined the swap" in bot.to(A_ID)[0]
    assert "declined" in bot.to(MISHA)[0]


# ============================================================
# The DM to B that never arrived
# ============================================================

@pytest.fixture()
def a_tap(fb, swap, monkeypatch):
    """`_handle_a_tap` reads its candidate list off the request context."""
    monkeypatch.setattr(fb, "load_state", lambda _n: [])
    return swap


def _a_tap(fb, bot):
    ctx = {
        "a_username": "alpha", "a_user_id": A_ID,
        "a_current_date": "2026-09-07", "a_current_slot": 1,
        "candidates": [{
            "kind": "counterparty", "date": "2026-09-14", "day": "Mon", "slot": 2,
            "b_username": "bravo", "b_user_id": B_ID, "b_name": "Bravo B",
        }],
    }
    fb._handle_a_tap(bot, "cq1", "rid1", "0", ctx, "initiated", None, None, A_ID)


def test_an_undeliverable_proposal_tells_misha(fb, a_tap, monkeypatch):
    """A was promised "Misha will help" and one errors.log line was all."""
    bot = _Bot(fail_send_to=B_ID)
    _a_tap(fb, bot)
    assert bot.to(MISHA), "Misha was told nothing about a swap he was promised to"
    assert "/swap stuck" in bot.to(MISHA)[0]


def test_the_message_to_misha_names_the_likely_cause(fb, a_tap, monkeypatch):
    bot = _Bot(fail_send_to=B_ID)
    _a_tap(fb, bot)
    msg = bot.to(MISHA)[0]
    assert "blocked the bot" in msg
    assert "@bravo" in msg


def test_an_undeliverable_proposal_still_promises_help_to_a(fb, a_tap):
    bot = _Bot(fail_send_to=B_ID)
    _a_tap(fb, bot)
    assert any("Misha will help" in t for t in bot.toasts)


def test_an_undeliverable_proposal_leaves_the_request_retappable(fb, a_tap):
    """No event is appended on purpose: an event here becomes the status and
    ends a request that is still recoverable."""
    events, _ = a_tap
    bot = _Bot(fail_send_to=B_ID)
    _a_tap(fb, bot)
    assert events == []
    assert bot.edits == [], "editing away the keyboard would strand A too"


def test_a_deliverable_proposal_is_unaffected(fb, a_tap):
    events, _ = a_tap
    bot = _Bot()
    _a_tap(fb, bot)
    assert [e["event"] for e in events] == ["a_tapped_counterparty", "proposed_to_b"]
    assert bot.to(MISHA) == [], "a working proposal must not page Misha"


# ============================================================
# The expiry messages that hardcoded the window
# ============================================================

def test_the_expiry_messages_quote_the_constant(fb, monkeypatch):
    from datetime import timedelta as _td
    past = (fb.local_now() - _td(hours=1)).isoformat()
    monkeypatch.setattr(fb, "_load_swap_requests", lambda: {"rid1": [
        {"rid": "rid1", "event": "proposed_to_b", "deadline": past,
         "a_user_id": A_ID, "a_username": "alpha", "b_username": "bravo"},
    ]})
    monkeypatch.setattr(fb, "_append_swap_event", lambda _e: None)
    monkeypatch.setattr(fb, "misha_user_id", lambda: MISHA)
    monkeypatch.setattr(fb, "SWAP_B_RESPONSE_TTL_HOURS", 48)

    bot = _Bot()
    fb._sweep_expired_swap_requests(bot)
    assert "no response in 48h" in bot.to(A_ID)[0]
    assert "no response in 48h" in bot.to(MISHA)[0]
    assert "24h" not in bot.to(A_ID)[0]


def test_the_expiry_sweep_still_notifies_both(fb, monkeypatch):
    from datetime import timedelta as _td
    past = (fb.local_now() - _td(hours=1)).isoformat()
    monkeypatch.setattr(fb, "_load_swap_requests", lambda: {"rid1": [
        {"rid": "rid1", "event": "proposed_to_b", "deadline": past,
         "a_user_id": A_ID, "a_username": "alpha", "b_username": "bravo"},
    ]})
    monkeypatch.setattr(fb, "_append_swap_event", lambda _e: None)
    monkeypatch.setattr(fb, "misha_user_id", lambda: MISHA)
    bot = _Bot()
    fb._sweep_expired_swap_requests(bot)
    assert bot.to(A_ID) and bot.to(MISHA)


# ============================================================
# The index that scheduled people who had gone
# ============================================================

def _roster(**overrides) -> dict:
    base = {
        "alpha": {"name": "Alpha One", "telegram_user_id": A_ID, "active": True},
        "bravo": {"name": "Bravo Two", "telegram_user_id": B_ID, "active": True},
    }
    base.update(overrides)
    return base


def test_an_excluded_member_is_left_unbound(fb, monkeypatch):
    monkeypatch.setattr(fb, "log_error", lambda *a, **k: None)
    roster = _roster(bravo={"name": "Bravo Two", "active": False,
                            "excluded_from_fireside": True})
    by_name = fb.build_roster_by_name(roster)
    assert "Alpha One" in by_name
    assert "Bravo Two" not in by_name


def test_a_departed_member_is_left_unbound(fb, monkeypatch):
    """`_handle_chat_member` marks a leaver `active=False` and nothing else."""
    monkeypatch.setattr(fb, "log_error", lambda *a, **k: None)
    by_name = fb.build_roster_by_name(
        _roster(bravo={"name": "Bravo Two", "active": False}))
    assert "Bravo Two" not in by_name


def test_the_reason_is_logged_not_swallowed(fb, monkeypatch):
    """An unbound name otherwise reads as a spelling mistake in the config."""
    seen: list[str] = []
    monkeypatch.setattr(fb, "log_error", lambda msg, *a, **k: seen.append(str(msg)))
    fb.build_roster_by_name(_roster(bravo={"name": "Bravo Two", "active": False}))
    assert any("no longer active" in s and "Bravo Two" in s for s in seen)


def test_an_exclusion_is_named_as_an_exclusion(fb, monkeypatch):
    seen: list[str] = []
    monkeypatch.setattr(fb, "log_error", lambda msg, *a, **k: seen.append(str(msg)))
    fb.build_roster_by_name(_roster(bravo={"name": "Bravo Two", "active": True,
                                           "excluded_from_fireside": True}))
    assert any("excluded from fireside" in s for s in seen)


def test_a_member_with_no_active_key_is_still_bound(fb):
    """`speaker_gaps` reads `active` with a True default; this must match it, or
    a roster written before the field existed unbinds wholesale."""
    by_name = fb.build_roster_by_name({"alpha": {"name": "Alpha One"}})
    assert "Alpha One" in by_name


def test_an_excluded_name_reaches_missing_speakers(fb, monkeypatch):
    """The surface that makes it visible: the discrepancy report already prints
    `missing_speakers`, and an unbound row is what puts a name there."""
    monkeypatch.setattr(fb, "log_error", lambda *a, **k: None)
    from datetime import date as _date
    by_name = fb.build_roster_by_name(
        _roster(bravo={"name": "Bravo Two", "active": False}))
    weeks = [{"week": 1, "theme": "T", "mon": ["Alpha One", "Bravo Two"], "wed": []}]
    entries, missing = fb.build_schedule(by_name, start_monday=_date(2026, 9, 7),
                                         weeks=weeks)
    assert missing == ["Bravo Two"]
    bound = {e["speaker_name"]: e["speaker_username"] for e in entries}
    assert bound["Bravo Two"] is None
    assert bound["Alpha One"] == "alpha"


def test_the_ambiguous_name_guard_still_holds(fb, monkeypatch):
    """The older refusal must survive the new one above it."""
    monkeypatch.setattr(fb, "log_error", lambda *a, **k: None)
    by_name = fb.build_roster_by_name({
        "a1": {"name": "Alex Kim", "active": True},
        "a2": {"name": "Alex Kim", "active": True},
    })
    assert "Alex Kim" not in by_name


# ============================================================
# The heartbeat that never called Telegram
# ============================================================

@pytest.fixture()
def ticks(fb, tmp_path, monkeypatch):
    rows: list[dict] = []
    pings: list[str] = []
    monkeypatch.setattr(fb, "append_jsonl", lambda _n, row: rows.append(row))
    monkeypatch.setattr(fb, "hc_ping", pings.append)
    monkeypatch.setattr(fb, "log_error", lambda *a, **k: None)
    return rows, pings


class _WebhookBot:
    def __init__(self, info=None, boom=False):
        self._info = info if info is not None else {"pending_update_count": 0}
        self._boom = boom

    def get_webhook_info(self):
        if self._boom:
            raise _TelegramError("Bad Gateway", status_code=502)
        return self._info


def test_the_heartbeat_calls_telegram_before_stamping(fb, ticks, monkeypatch,
                                                      capsys):
    rows, pings = ticks
    monkeypatch.setattr(fb, "get_bot", lambda: _WebhookBot(
        {"pending_update_count": 3, "last_error_message": "connection refused"}))
    fb.cmd_heartbeat(None)
    assert len(rows) == 1
    assert rows[0]["pending_update_count"] == 3
    assert rows[0]["webhook_last_error"] == "connection refused"
    assert pings == ["FIRESIDE_HC_POLL"]


def test_an_unreachable_telegram_stamps_no_tick(fb, ticks, monkeypatch, capsys):
    """It used to stamp anyway, so the monitor reported the outage as health."""
    rows, pings = ticks
    monkeypatch.setattr(fb, "get_bot", lambda: _WebhookBot(boom=True))
    fb.cmd_heartbeat(None)
    assert rows == [], "a tick was written for a run that never reached Telegram"
    assert pings == [], "healthchecks.io was pinged green over an outage"
    assert "unreachable" in capsys.readouterr().err


def test_the_wrapper_asks_telegram_for_the_webhook(monkeypatch):
    """The wrapper itself, not a stub of it. Every test above replaces
    `get_bot()`, so a `get_webhook_info` that called `getMe` would pass all of
    them - and `getMe` carries neither the pending count nor the last error,
    which are the only two things this call exists to fetch."""
    from scripts.utils.telegram_bot import TelegramBot

    called: list[str] = []
    bot = TelegramBot("token")
    monkeypatch.setattr(bot, "_call",
                        lambda method, **kw: called.append(method) or {})
    bot.get_webhook_info()
    assert called == ["getWebhookInfo"]


def test_the_health_check_reports_the_webhook_backlog(fb, tmp_path, monkeypatch,
                                                      capsys):
    log = tmp_path / "dm-log.jsonl"
    log.write_text(json.dumps({
        "ts": fb.local_now().isoformat(), "dm_type": "heartbeat-tick",
        "pending_update_count": 41, "webhook_last_error": "read timeout",
    }) + "\n", encoding="utf-8")
    monkeypatch.setattr(fb, "state_path", lambda _n: log)
    fb.cmd_health_check(None)
    out = capsys.readouterr().out
    assert "pending_update_count=41" in out
    assert "read timeout" in out


def test_the_health_check_does_not_claim_the_handler_is_alive(fb, tmp_path,
                                                              monkeypatch, capsys):
    """A fresh tick proves a process reached Telegram. In webhook mode that is a
    DIFFERENT process from the one consuming updates."""
    log = tmp_path / "dm-log.jsonl"
    log.write_text(json.dumps({
        "ts": fb.local_now().isoformat(), "dm_type": "heartbeat-tick",
    }) + "\n", encoding="utf-8")
    monkeypatch.setattr(fb, "state_path", lambda _n: log)
    fb.cmd_health_check(None)
    out = capsys.readouterr().out
    assert "reached Telegram" in out
    assert "does not prove the webhook handler is alive" in out


def test_a_stale_tick_still_alerts(fb, tmp_path, monkeypatch, capsys):
    """The behaviour the command exists for must survive the new reporting."""
    old = (fb.local_now() - timedelta(hours=3)).isoformat()
    log = tmp_path / "dm-log.jsonl"
    log.write_text(json.dumps({"ts": old, "dm_type": "poll-tick"}) + "\n",
                   encoding="utf-8")
    monkeypatch.setattr(fb, "state_path", lambda _n: log)
    monkeypatch.setattr(fb, "misha_user_id", lambda: None)
    fb.cmd_health_check(None)
    assert "last liveness tick was 180 min ago" in capsys.readouterr().out


# ============================================================
# The backup email that misdescribed its own recipients
# ============================================================

def _window_entry(fb, days=3):
    d = (fb._today_local_date() + timedelta(days=days)).isoformat()
    return d, [{
        "week": 1, "session_date": d, "day": "Mon", "slot": 1, "theme": "T",
        "speaker_name": "Alpha One", "speaker_username": "alpha",
        "swapped_with": None, "no_show": False, "completed": False,
    }]


@pytest.fixture()
def mail(fb, tmp_path, monkeypatch):
    """email-backup with the send subprocess captured."""
    sent: list[list[str]] = []
    logged: list[tuple] = []

    class _R:
        returncode = 0
        stderr = ""

    monkeypatch.setattr(fb, "state_path", lambda name: tmp_path / name)
    monkeypatch.setattr(fb, "_log_dm",
                        lambda *a, **k: logged.append((a, k)))
    import subprocess as _sp
    monkeypatch.setattr(_sp, "run", lambda cmd, **kw: (sent.append(cmd), _R())[1])
    return tmp_path, sent, logged


def _wire_mail(fb, monkeypatch, schedule, sessions_rows=(), dm_rows=(), tmp=None):
    roster = {"alpha": {"name": "Alpha One", "email": "a@example.com",
                        "telegram_user_id": A_ID, "active": True}}
    monkeypatch.setattr(fb, "load_state",
                        lambda n: {fb.SCHEDULE: schedule, fb.TRIBE_ROSTER: roster}.get(n))
    (tmp / fb.SESSIONS_LOG).write_text(
        "".join(json.dumps(r) + "\n" for r in sessions_rows), encoding="utf-8")
    (tmp / fb.DM_LOG).write_text(
        "".join(json.dumps(r) + "\n" for r in dm_rows), encoding="utf-8")


@pytest.mark.parametrize("event_type", ["idea_submitted", "opt_in_changed"])
def test_an_engaged_member_is_not_mailed(fb, mail, monkeypatch, capsys, event_type):
    """Both are written by `_log_event` with a user_id and both prove the member
    is alive and using the bot. Neither was counted."""
    tmp, sent, _ = mail
    _, schedule = _window_entry(fb)
    _wire_mail(fb, monkeypatch, schedule,
               sessions_rows=[{"event_type": event_type, "user_id": A_ID}], tmp=tmp)
    fb.cmd_email_backup(None)
    assert sent == [], f"{event_type} is engagement and was mailed anyway"
    assert "skipped=1" in capsys.readouterr().out


@pytest.mark.parametrize("event_type", ["start_received", "swap_requested"])
def test_the_original_two_events_still_count(fb, mail, monkeypatch, event_type):
    tmp, sent, _ = mail
    _, schedule = _window_entry(fb)
    _wire_mail(fb, monkeypatch, schedule,
               sessions_rows=[{"event_type": event_type, "user_id": A_ID}], tmp=tmp)
    fb.cmd_email_backup(None)
    assert sent == []


def test_a_silent_member_is_still_mailed(fb, mail, monkeypatch):
    """The green path: this command exists for exactly this member."""
    tmp, sent, _ = mail
    _, schedule = _window_entry(fb)
    _wire_mail(fb, monkeypatch, schedule, tmp=tmp)
    fb.cmd_email_backup(None)
    assert len(sent) == 1
    assert "a@example.com" in sent[0]


def test_an_unrelated_event_is_not_engagement(fb, mail, monkeypatch):
    """`tribe_join` carries a user_id too and proves nothing about the bot."""
    tmp, sent, _ = mail
    _, schedule = _window_entry(fb)
    _wire_mail(fb, monkeypatch, schedule,
               sessions_rows=[{"event_type": "tribe_join", "user_id": A_ID}], tmp=tmp)
    fb.cmd_email_backup(None)
    assert len(sent) == 1


def test_a_second_run_on_one_day_does_not_mail_twice(fb, mail, monkeypatch,
                                                     capsys):
    """The rows written to dedupe against were never read."""
    tmp, sent, _ = mail
    session_date, schedule = _window_entry(fb)
    today = fb._today_local_date().isoformat()
    _wire_mail(fb, monkeypatch, schedule, dm_rows=[{
        "ts": f"{today}T09:00:00+04:00", "dm_type": "email-backup",
        "speaker_username": "alpha", "session_date": session_date,
        "delivered": True,
    }], tmp=tmp)
    fb.cmd_email_backup(None)
    assert sent == [], "a rerun sent a real person a second identical email"
    assert "already-mailed-today=1" in capsys.readouterr().out


def test_the_second_sunday_of_the_window_still_mails(fb, mail, monkeypatch):
    """Two emails per session is deliberate, mirroring the 2wk and 3day DMs. A
    whole-window dedupe would silence the second one."""
    tmp, sent, _ = mail
    session_date, schedule = _window_entry(fb)
    last_week = (fb._today_local_date() - timedelta(days=7)).isoformat()
    _wire_mail(fb, monkeypatch, schedule, dm_rows=[{
        "ts": f"{last_week}T09:00:00+04:00", "dm_type": "email-backup",
        "speaker_username": "alpha", "session_date": session_date,
        "delivered": True,
    }], tmp=tmp)
    fb.cmd_email_backup(None)
    assert len(sent) == 1


def test_a_failed_earlier_send_is_retried(fb, mail, monkeypatch):
    """`delivered: False` is not a delivery, so it must not suppress the retry."""
    tmp, sent, _ = mail
    session_date, schedule = _window_entry(fb)
    today = fb._today_local_date().isoformat()
    _wire_mail(fb, monkeypatch, schedule, dm_rows=[{
        "ts": f"{today}T09:00:00+04:00", "dm_type": "email-backup",
        "speaker_username": "alpha", "session_date": session_date,
        "delivered": False,
    }], tmp=tmp)
    fb.cmd_email_backup(None)
    assert len(sent) == 1


def test_the_whole_window_form_of_the_check_still_exists(fb, tmp_path):
    """`cmd_speaker_dms` relies on the no-`on_date` form; adding the parameter
    must not have changed it."""
    log = tmp_path / "dm-log.jsonl"
    log.write_text(json.dumps({
        "ts": "2026-01-01T09:00:00+04:00", "dm_type": "2wk",
        "speaker_username": "alpha", "session_date": "2026-09-07",
        "delivered": True,
    }) + "\n", encoding="utf-8")
    assert fb._dm_already_sent(log, "alpha", "2wk", "2026-09-07") is True
    assert fb._dm_already_sent(log, "alpha", "2wk", "2026-09-07",
                               on_date="2026-08-25") is False


# ============================================================
# The blank name in the middle of a send loop
# ============================================================

def _blank_name_schedule(fb, days):
    d = (fb._today_local_date() + timedelta(days=days)).isoformat()
    return [
        {"week": 1, "session_date": d, "day": "Mon", "slot": 1, "theme": "T",
         "speaker_name": "   ", "speaker_username": "alpha"},
        {"week": 1, "session_date": d, "day": "Mon", "slot": 2, "theme": "T",
         "speaker_name": "Bravo Two", "speaker_username": "bravo"},
    ]


def test_a_blank_name_does_not_strand_the_speakers_after_it(fb, tmp_path,
                                                            monkeypatch, capsys):
    """IndexError out of the middle of the loop: earlier speakers kept their DM,
    later ones got nothing, no summary printed, hc_ping never fired."""
    schedule = _blank_name_schedule(fb, days=2)
    roster = {"alpha": {"telegram_user_id": A_ID}, "bravo": {"telegram_user_id": B_ID}}
    bot = _Bot()
    monkeypatch.setattr(fb, "get_bot", lambda: bot)
    monkeypatch.setattr(fb, "load_state",
                        lambda n: {fb.SCHEDULE: schedule, fb.TRIBE_ROSTER: roster}.get(n))
    monkeypatch.setattr(fb, "state_path", lambda n: tmp_path / n)
    monkeypatch.setattr(fb, "_log_dm", lambda *a, **k: None)
    pings: list[str] = []
    monkeypatch.setattr(fb, "hc_ping", pings.append)

    fb.cmd_speaker_dms(None)
    assert bot.to(B_ID), "the speaker after the blank row never got their reminder"
    assert pings, "hc_ping never fired, so the healthcheck saw a silent job"
    assert "3day=2" in capsys.readouterr().out


def test_a_blank_name_falls_back_to_the_handle(fb, tmp_path, monkeypatch):
    schedule = _blank_name_schedule(fb, days=2)
    roster = {"alpha": {"telegram_user_id": A_ID}, "bravo": {"telegram_user_id": B_ID}}
    bot = _Bot()
    monkeypatch.setattr(fb, "get_bot", lambda: bot)
    monkeypatch.setattr(fb, "load_state",
                        lambda n: {fb.SCHEDULE: schedule, fb.TRIBE_ROSTER: roster}.get(n))
    monkeypatch.setattr(fb, "state_path", lambda n: tmp_path / n)
    monkeypatch.setattr(fb, "_log_dm", lambda *a, **k: None)
    monkeypatch.setattr(fb, "hc_ping", lambda _n: None)
    fb.cmd_speaker_dms(None)
    assert "@alpha" in bot.to(A_ID)[0]


def test_a_real_first_name_is_still_used(fb, tmp_path, monkeypatch):
    schedule = _blank_name_schedule(fb, days=2)
    roster = {"alpha": {"telegram_user_id": A_ID}, "bravo": {"telegram_user_id": B_ID}}
    bot = _Bot()
    monkeypatch.setattr(fb, "get_bot", lambda: bot)
    monkeypatch.setattr(fb, "load_state",
                        lambda n: {fb.SCHEDULE: schedule, fb.TRIBE_ROSTER: roster}.get(n))
    monkeypatch.setattr(fb, "state_path", lambda n: tmp_path / n)
    monkeypatch.setattr(fb, "_log_dm", lambda *a, **k: None)
    monkeypatch.setattr(fb, "hc_ping", lambda _n: None)
    fb.cmd_speaker_dms(None)
    assert "Bravo" in bot.to(B_ID)[0]
    assert "Bravo Two" not in bot.to(B_ID)[0]


def test_the_day_of_reminder_survives_a_blank_name_too(fb, tmp_path, monkeypatch,
                                                       capsys):
    """This is the DM carrying the Zoom link."""
    schedule = _blank_name_schedule(fb, days=0)
    roster = {"alpha": {"telegram_user_id": A_ID}, "bravo": {"telegram_user_id": B_ID}}
    bot = _Bot()
    monkeypatch.setattr(fb, "get_bot", lambda: bot)
    monkeypatch.setattr(fb, "load_state",
                        lambda n: {fb.SCHEDULE: schedule, fb.TRIBE_ROSTER: roster,
                                   fb.HELMSMEN: {}}.get(n))
    monkeypatch.setattr(fb, "state_path", lambda n: tmp_path / n)
    monkeypatch.setattr(fb, "_log_dm", lambda *a, **k: None)
    monkeypatch.setattr(fb, "hc_ping", lambda _n: None)
    monkeypatch.setattr(fb, "_zoom_url", lambda: "https://zoom.example/j/1")

    fb.cmd_dayof_reminders(None)
    assert bot.to(B_ID), "the speaker after the blank row lost the Zoom link"
    assert "https://zoom.example/j/1" in bot.to(A_ID)[0]


# ============================================================
# The self-heal that crashed on a file that is not a workbook
# ============================================================

def test_the_unreadable_sheet_tuple_names_the_openpyxl_errors(fb):
    """Neither is an OSError and neither is a ValueError, so both have to be
    named. The handler list had been written from what `load_tribe_metadata`
    raises deliberately, and a corrupt file never reaches that code."""
    from openpyxl.utils.exceptions import InvalidFileException
    assert zipfile.BadZipFile in fb._UNREADABLE_SHEET
    assert InvalidFileException in fb._UNREADABLE_SHEET
    assert ValueError in fb._UNREADABLE_SHEET


def test_neither_openpyxl_error_is_covered_by_the_old_handlers(fb):
    """Source-pinned reason: if either were an OSError or a ValueError subclass
    the widened tuple would be pointless, and a later reader would drop it."""
    from openpyxl.utils.exceptions import InvalidFileException
    for exc in (zipfile.BadZipFile, InvalidFileException):
        assert not issubclass(exc, (OSError, ValueError)), exc


@pytest.mark.parametrize("boom", ["badzip", "invalidfile"])
def test_a_corrupt_sheet_writes_the_placeholder_instead_of_crashing(
        fb, tmp_path, monkeypatch, capsys, boom):
    from openpyxl.utils.exceptions import InvalidFileException
    exc = zipfile.BadZipFile("File is not a zip file") if boom == "badzip" \
        else InvalidFileException("not a .xlsx file")

    saved: dict = {}
    monkeypatch.setattr(fb, "state_dir", lambda p=tmp_path: p)
    monkeypatch.setattr(fb, "state_path", lambda n: tmp_path / n)
    monkeypatch.setattr(fb, "load_tribe_metadata",
                        lambda: (_ for _ in ()).throw(exc))
    monkeypatch.setattr(fb, "save_state",
                        lambda name, data: saved.__setitem__(name, data))
    monkeypatch.setattr(fb, "load_state", lambda _n: {})
    monkeypatch.setattr(fb, "log_error", lambda *a, **k: None)

    fb.ensure_state_dir()
    assert saved.get(fb.TRIBE_ROSTER) == {}
    assert "EMPTY" in capsys.readouterr().err


def test_a_readable_sheet_still_heals_the_roster(fb, tmp_path, monkeypatch):
    saved: dict = {}
    monkeypatch.setattr(fb, "state_dir", lambda p=tmp_path: p)
    monkeypatch.setattr(fb, "state_path", lambda n: tmp_path / n)
    monkeypatch.setattr(fb, "load_tribe_metadata",
                        lambda: {"alpha": {"name": "Alpha One", "active": True}})
    monkeypatch.setattr(fb, "save_state",
                        lambda name, data: saved.__setitem__(name, data))
    monkeypatch.setattr(fb, "load_state", lambda _n: {})
    fb.ensure_state_dir()
    assert saved[fb.TRIBE_ROSTER]["alpha"]["name"] == "Alpha One"


# ============================================================
# The opt-ins seeded from a handle nobody checked
# ============================================================

REACTIONS = {
    "helmsman": [{"username": "alpha", "user_id": A_ID}],
    "wildcard": [
        {"username": "bravo", "user_id": B_ID},
        {"username": "stranger", "user_id": 777},   # in the group, not the xlsx
        {"username": None, "user_id": 888},         # no Telegram handle
    ],
}


def test_a_non_roster_reactor_is_not_seeded(fb):
    """They then appeared on the wildcard roster DM'd to every Helmsman."""
    opt_ins, dropped = fb.seed_opt_ins(REACTIONS, _roster())
    handles = [x["username"] for x in opt_ins["wildcard"]]
    assert handles == ["bravo"]
    assert any("stranger" in d for d in dropped)


def test_a_handleless_reactor_is_not_seeded_as_none(fb):
    """`cmd_helmsman_brief` rendered it as the literal `  - @None`."""
    opt_ins, dropped = fb.seed_opt_ins(REACTIONS, _roster())
    assert all(x["username"] for x in opt_ins["wildcard"])
    assert any("(no handle)" in d for d in dropped)


def test_a_bound_reactor_is_still_seeded(fb):
    opt_ins, _ = fb.seed_opt_ins(REACTIONS, _roster())
    assert opt_ins["helmsman"] == [{"username": "alpha", "user_id": A_ID}]


def test_the_canonical_roster_key_is_stored_not_the_reported_handle(fb):
    """A reclaimed handle would otherwise resolve to the previous owner's rows -
    the same handle-takeover guard `_resolve_my_username` documents."""
    reactions = {"helmsman": [{"username": "someone-elses-handle", "user_id": A_ID}],
                 "wildcard": []}
    opt_ins, _ = fb.seed_opt_ins(reactions, _roster())
    assert opt_ins["helmsman"][0]["username"] == "alpha"


def test_an_unbound_roster_member_cannot_opt_in(fb):
    """No telegram_user_id means bootstrap never bound them, which is exactly
    what `_handle_message_reaction` refuses on."""
    roster = {"alpha": {"name": "Alpha One", "active": True}}
    opt_ins, dropped = fb.seed_opt_ins(REACTIONS, roster)
    assert opt_ins["helmsman"] == []
    assert len(dropped) == 4


# ============================================================
# The unpin that warned forever
# ============================================================

class _UnpinBot:
    def __init__(self, status=None):
        self._status = status

    def unpin_chat_message(self, _chat, _mid):
        if self._status is not None:
            raise _TelegramError("nope", status_code=self._status)
        return True


@pytest.fixture()
def unpin(fb, monkeypatch):
    saved: dict = {}
    monkeypatch.setattr(fb, "load_state", lambda _n: {"message_id": 4242})
    monkeypatch.setattr(fb, "save_state",
                        lambda name, data: saved.__setitem__(name, data))
    monkeypatch.setenv("FIRESIDE_TRIBE_CHAT_ID", "-100999")
    return saved


def test_a_hand_unpinned_message_clears_the_recorded_id(fb, unpin, monkeypatch,
                                                        capsys):
    """It warned every Wednesday forever, with no way out but hand-editing."""
    monkeypatch.setattr(fb, "get_bot", lambda: _UnpinBot(status=400))
    fb.cmd_unpin_weekly(None)
    assert unpin[fb.LAST_PINNED] == {"message_id": None}
    assert "clearing the recorded id" in capsys.readouterr().err


def test_a_transient_failure_keeps_the_recorded_id(fb, unpin, monkeypatch,
                                                   capsys):
    """Dropping it here would leave a real pinned message pinned for good."""
    monkeypatch.setattr(fb, "get_bot", lambda: _UnpinBot(status=502))
    fb.cmd_unpin_weekly(None)
    assert unpin == {}
    assert "keeping message_id=4242" in capsys.readouterr().err


def test_a_transport_failure_keeps_the_recorded_id(fb, unpin, monkeypatch):
    """A `status_code` of None is a request that never reached Telegram, so it
    says nothing about whether the message is still pinned."""
    class _NoStatus:
        def unpin_chat_message(self, _c, _m):
            raise fb.TelegramAPIError("connection reset")

    monkeypatch.setattr(fb, "get_bot", _NoStatus)
    fb.cmd_unpin_weekly(None)
    assert unpin == {}


def test_a_successful_unpin_still_clears_it(fb, unpin, monkeypatch, capsys):
    monkeypatch.setattr(fb, "get_bot", lambda: _UnpinBot())
    fb.cmd_unpin_weekly(None)
    assert unpin[fb.LAST_PINNED] == {"message_id": None}
    assert "unpinned message_id=4242" in capsys.readouterr().out


def test_the_docstring_no_longer_states_a_time_it_cannot_verify(fb):
    """"Wed 16:00 local (after Wed session)" cannot both be true: sessions are
    at 18:30, so 16:00 is two and a half hours BEFORE, and the pinned message
    carries that day's Zoom link.

    The record has to carry BOTH numbers, not just the word "wrong". The real
    cron lives outside this file, so a reader who cannot see it has only this
    paragraph to check it against - "the note was a little off" tells them
    nothing they can act on.
    """
    doc = fb.cmd_unpin_weekly.__doc__
    assert doc.index("AFTER the Wednesday session") < doc.index("16:00")
    assert "16:00" in doc and "18:30" in doc, (
        "the contradiction is recorded with both times, not erased"
    )
    assert "BEFORE the Wednesday session" in doc


# ============================================================
# The wrong-shaped config that escaped its own handler
# ============================================================

def test_a_wrong_shaped_config_is_a_clean_error(fb, monkeypatch, capsys):
    """TypeError, which the import-time guard over the same loader catches and
    this caller did not."""
    def _boom():
        raise TypeError("list indices must be integers or slices, not str")
    monkeypatch.setattr(fb, "_load_fireside_config_fresh", _boom)
    monkeypatch.setattr(fb, "load_state", lambda _n: [])
    fb.cmd_cycle_rollover(type("A", (), {"dry_run": True})())
    err = capsys.readouterr().err
    assert "cannot read cycle config" in err


@pytest.mark.parametrize("exc", [OSError("gone"), ValueError("bad json"),
                                 KeyError("cycle_1_start_monday")])
def test_the_original_three_are_still_caught(fb, monkeypatch, capsys, exc):
    def _boom():
        raise exc
    monkeypatch.setattr(fb, "_load_fireside_config_fresh", _boom)
    monkeypatch.setattr(fb, "load_state", lambda _n: [])
    fb.cmd_cycle_rollover(type("A", (), {"dry_run": True})())
    assert "cannot read cycle config" in capsys.readouterr().err


# ============================================================
# The candidate docstring that described a filter the code never had
# ============================================================

def test_the_candidate_docstring_says_dates_not_weeks(fb):
    doc = fb.find_swap_candidates.__doc__
    assert "session DATES" in doc
    # The correction quotes the sentence it replaced, so pin the order.
    assert doc.index("session DATES") < doc.index('"A\'s own current week"')


def test_the_same_week_is_still_offered(fb):
    """The behaviour the corrected docstring now describes: A speaks Monday, and
    the Wednesday of that week is a legitimate target."""
    from datetime import date as _date
    # All three slots filled on the Wednesday, so it is a COUNTERPARTY session
    # rather than a vacancy - the case the docstring's "A's own current week"
    # claimed was excluded.
    schedule = [
        {"session_date": "2026-09-07", "day": "Mon", "slot": 1,
         "speaker_username": "alpha", "speaker_name": "Alpha One"},
    ] + [
        {"session_date": "2026-09-09", "day": "Wed", "slot": n,
         "speaker_username": f"b{n}", "speaker_name": f"Bravo {n}"}
        for n in (1, 2, 3)
    ]
    out = fb.find_swap_candidates(schedule, "alpha", _date(2026, 9, 1))
    assert [c["date"] for c in out if c["kind"] == "counterparty"] == ["2026-09-09"]


def test_a_speakers_own_date_is_still_excluded(fb):
    from datetime import date as _date
    schedule = [
        {"session_date": "2026-09-07", "day": "Mon", "slot": 1,
         "speaker_username": "alpha", "speaker_name": "Alpha One"},
        {"session_date": "2026-09-07", "day": "Mon", "slot": 2,
         "speaker_username": "bravo", "speaker_name": "Bravo Two"},
    ]
    out = fb.find_swap_candidates(schedule, "alpha", _date(2026, 9, 1))
    assert out == []
