"""Handler/wrapper tests for the topic feature in fireside-bot.py.

Loads the hyphenated module via importlib (same fixture style as
test_fireside_bot_auth.py), redirects STATE_DIR to tmp_path, and uses a fake
TelegramBot that records calls instead of hitting the network.
"""
from __future__ import annotations

import importlib.util
import sys
from argparse import Namespace
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
BOT = ROOT / "scripts" / "fireside-bot.py"


@pytest.fixture(scope="module")
def fb():
    spec = importlib.util.spec_from_file_location("fireside_bot", str(BOT))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class FakeBot:
    def __init__(self):
        self.sent = []        # (chat_id, text, kwargs)
        self.edits = []       # (chat_id, msg_id, text)
        self.answered = []    # (cq_id, text)
        self.pins = []        # (chat_id, msg_id)
        self._next_msg_id = 1000
        self.fail_send = False
        self.fail_pin = False
        self.error_cls = RuntimeError

    def send_message(self, chat_id, text, **kwargs):
        if self.fail_send:
            raise self.error_cls("boom")
        self.sent.append((chat_id, text, kwargs))
        self._next_msg_id += 1
        return {"message_id": self._next_msg_id}

    def send_dm(self, user_id, text, **kwargs):
        return self.send_message(user_id, text, **kwargs)

    def edit_message_text(self, chat_id, msg_id, text, **kwargs):
        self.edits.append((chat_id, msg_id, text))
        return {"message_id": msg_id}

    def edit_message_reply_markup(self, chat_id, msg_id, markup):
        return {"message_id": msg_id}

    def answer_callback_query(self, cq_id, text=None):
        self.answered.append((cq_id, text))

    def pin_chat_message(self, chat_id, msg_id, **kwargs):
        if self.fail_pin:
            raise self.error_cls("boom")
        self.pins.append((chat_id, msg_id))


@pytest.fixture
def authd(fb, tmp_path, monkeypatch):
    """STATE_DIR -> tmp_path; one authorized bound member; force authorization on."""
    monkeypatch.setattr(fb, "STATE_DIR", tmp_path)
    monkeypatch.setattr(fb, "_is_authorized_user", lambda uid, username=None: True)
    monkeypatch.setattr(fb, "_resolve_my_username", lambda uid: "alice")
    return fb


def _msg(text, uid=555, username="alice"):
    return {"chat": {"type": "private"}, "text": text,
            "from": {"id": uid, "username": username}}


def test_idea_command_appends_and_acks(authd, tmp_path):
    from scripts import fireside_topics as ft
    bot = FakeBot()
    authd._handle_message(bot, _msg("/idea hands-on DPI capture"))
    ideas = ft.load_ideas(tmp_path)
    assert len(ideas) == 1 and ideas[0]["text"] == "hands-on DPI capture"
    assert ideas[0]["user_id"] == 555
    assert bot.sent and bot.sent[-1][0] == 555
    assert "✓" in bot.sent[-1][1] or "logged" in bot.sent[-1][1].lower()


def test_idea_command_empty_shows_usage(authd, tmp_path):
    from scripts import fireside_topics as ft
    bot = FakeBot()
    authd._handle_message(bot, _msg("/idea"))
    assert ft.load_ideas(tmp_path) == []          # nothing stored
    assert "/idea" in bot.sent[-1][1]             # usage hint echoed


def test_ideas_lookalike_not_captured(authd, tmp_path):
    """/ideas foo must NOT be treated as an /idea submission (M3 boundary)."""
    from scripts import fireside_topics as ft
    bot = FakeBot()
    authd._handle_message(bot, _msg("/ideas foo bar"))
    assert ft.load_ideas(tmp_path) == []


def _set_schedule(fb, monkeypatch, sched):
    monkeypatch.setattr(fb, "load_state",
                        lambda name: sched if name == fb.SCHEDULE else (
                            {} if name in (fb.TRIBE_ROSTER, fb.HELMSMEN) else None))


_SCHED2 = [
    {"week": 1, "cycle": 1, "day": "Mon", "session_date": "2026-06-29", "slot": 1},
    {"week": 1, "cycle": 1, "day": "Wed", "session_date": "2026-07-01", "slot": 1},
    {"week": 2, "cycle": 1, "day": "Mon", "session_date": "2026-07-06", "slot": 1},
    {"week": 2, "cycle": 1, "day": "Wed", "session_date": "2026-07-08", "slot": 1},
]


def test_topic_nudge_posts_when_active_midcycle(authd, monkeypatch):
    bot = FakeBot()
    monkeypatch.setattr(authd, "get_bot", lambda: bot)
    monkeypatch.setenv("FIRESIDE_TRIBE_CHAT_ID", "-100123")
    _set_schedule(authd, monkeypatch, _SCHED2)
    monkeypatch.setattr(authd, "_today_local_date", lambda: date(2026, 6, 30))  # week 1
    authd.cmd_topic_nudge(Namespace(dry_run=False))
    assert bot.sent and bot.sent[0][0] == -100123
    assert "/idea" in bot.sent[0][1]
    assert bot.pins == []   # nudge is never pinned


def test_topic_nudge_skips_final_week(authd, monkeypatch):
    bot = FakeBot()
    monkeypatch.setattr(authd, "get_bot", lambda: bot)
    monkeypatch.setenv("FIRESIDE_TRIBE_CHAT_ID", "-100123")
    _set_schedule(authd, monkeypatch, _SCHED2)
    monkeypatch.setattr(authd, "_today_local_date", lambda: date(2026, 7, 5))  # final-week Sunday
    authd.cmd_topic_nudge(Namespace(dry_run=False))
    assert bot.sent == []   # owned by the cycle-end invite


def test_topic_nudge_skips_when_cycle_over(authd, monkeypatch):
    bot = FakeBot()
    monkeypatch.setattr(authd, "get_bot", lambda: bot)
    monkeypatch.setenv("FIRESIDE_TRIBE_CHAT_ID", "-100123")
    _set_schedule(authd, monkeypatch, _SCHED2)
    monkeypatch.setattr(authd, "_today_local_date", lambda: date(2026, 7, 20))
    authd.cmd_topic_nudge(Namespace(dry_run=False))
    assert bot.sent == []


def test_topic_nudge_dry_run_sends_nothing(authd, monkeypatch, capsys):
    bot = FakeBot()
    monkeypatch.setattr(authd, "get_bot", lambda: bot)
    monkeypatch.setenv("FIRESIDE_TRIBE_CHAT_ID", "-100123")
    _set_schedule(authd, monkeypatch, _SCHED2)
    monkeypatch.setattr(authd, "_today_local_date", lambda: date(2026, 6, 30))
    authd.cmd_topic_nudge(Namespace(dry_run=True))
    assert bot.sent == []
    assert "/idea" in capsys.readouterr().out


def test_topic_digest_sends_new_and_advances_cursor(authd, tmp_path, monkeypatch):
    from scripts import fireside_topics as ft
    bot = FakeBot()
    monkeypatch.setattr(authd, "get_bot", lambda: bot)
    monkeypatch.setenv("MISHA_TELEGRAM_USER_ID", "999")
    ids = [ft.append_idea(tmp_path, now_iso="2026-06-2%dT10:00:00+04:00" % k,
                          user_id=k, username=f"u{k}", name=f"N{k}",
                          text=f"idea {k}", cycle=1) for k in range(2)]
    authd.cmd_topic_digest(Namespace(dry_run=False))
    assert bot.sent and bot.sent[-1][0] == 999
    assert "idea 0" in bot.sent[-1][1] and "idea 1" in bot.sent[-1][1]
    assert ft.load_topic_state(tmp_path)["last_digest_idea_id"] == ids[-1]


def test_topic_digest_silent_when_no_new(authd, tmp_path, monkeypatch):
    from scripts import fireside_topics as ft
    bot = FakeBot()
    monkeypatch.setattr(authd, "get_bot", lambda: bot)
    monkeypatch.setenv("MISHA_TELEGRAM_USER_ID", "999")
    idea_id = ft.append_idea(tmp_path, now_iso="2026-06-25T10:00:00+04:00",
                             user_id=1, username="u", name="N", text="only one", cycle=1)
    st = ft.load_topic_state(tmp_path); st["last_digest_idea_id"] = idea_id
    ft.save_topic_state(tmp_path, st)
    authd.cmd_topic_digest(Namespace(dry_run=False))
    assert bot.sent == []   # no DM sent


def test_cycle_end_invite_drafts_to_ceo_with_buttons(authd, tmp_path, monkeypatch):
    from scripts import fireside_topics as ft
    bot = FakeBot()
    monkeypatch.setattr(authd, "get_bot", lambda: bot)
    monkeypatch.setenv("MISHA_TELEGRAM_USER_ID", "999")
    _set_schedule(authd, monkeypatch, _SCHED2)
    monkeypatch.setattr(authd, "_today_local_date", lambda: date(2026, 7, 5))  # final-week Sunday
    ft.append_idea(tmp_path, now_iso="2026-07-01T10:00:00+04:00", user_id=1,
                   username="u", name="N", text="capstone idea", cycle=1)
    authd.cmd_cycle_end_invite(Namespace(dry_run=False))
    assert bot.sent and bot.sent[-1][0] == 999
    markup = bot.sent[-1][2].get("reply_markup")
    payloads = [b["callback_data"] for row in markup["inline_keyboard"] for b in row]
    assert "cycle_invite:send" in payloads and "cycle_invite:cancel" in payloads
    assert "capstone idea" in bot.sent[-1][1]            # backlog summary rode along
    pend = ft.load_topic_state(tmp_path)["pending_cycle_invite"]
    assert pend is not None
    assert pend["cycle"] == 1
    assert pend["text"] and "/idea" in pend["text"]
    assert isinstance(pend["approval_msg_id"], int)


def test_cycle_end_invite_noop_off_trigger_day(authd, monkeypatch):
    bot = FakeBot()
    monkeypatch.setattr(authd, "get_bot", lambda: bot)
    monkeypatch.setenv("MISHA_TELEGRAM_USER_ID", "999")
    _set_schedule(authd, monkeypatch, _SCHED2)
    monkeypatch.setattr(authd, "_today_local_date", lambda: date(2026, 7, 1))  # mid-cycle Wed
    authd.cmd_cycle_end_invite(Namespace(dry_run=False))
    assert bot.sent == []


def test_cycle_end_invite_idempotent(authd, tmp_path, monkeypatch):
    bot = FakeBot()
    monkeypatch.setattr(authd, "get_bot", lambda: bot)
    monkeypatch.setenv("MISHA_TELEGRAM_USER_ID", "999")
    _set_schedule(authd, monkeypatch, _SCHED2)
    monkeypatch.setattr(authd, "_today_local_date", lambda: date(2026, 7, 5))
    authd.cmd_cycle_end_invite(Namespace(dry_run=False))
    authd.cmd_cycle_end_invite(Namespace(dry_run=False))  # second run same day
    ceo_dms = [s for s in bot.sent if s[0] == 999]
    assert len(ceo_dms) == 1


def _cq(data, uid=999):
    return {"id": "cq1", "data": data, "from": {"id": uid, "username": "misha"},
            "message": {"chat": {"id": uid}, "message_id": 4242}}


def _seed_pending(fb, tmp_path):
    from scripts import fireside_topics as ft
    st = ft.load_topic_state(tmp_path)
    st["pending_cycle_invite"] = {"text": "INVITE BODY", "approval_msg_id": 4242,
                                  "drafted_at": "2026-07-05T11:00:00+04:00", "cycle": 1}
    ft.save_topic_state(tmp_path, st)


def test_cycle_invite_send_posts_and_clears(authd, tmp_path, monkeypatch):
    from scripts import fireside_topics as ft
    bot = FakeBot()
    monkeypatch.setenv("FIRESIDE_TRIBE_CHAT_ID", "-100123")
    monkeypatch.setenv("MISHA_TELEGRAM_USER_ID", "999")
    _seed_pending(authd, tmp_path)
    authd._handle_callback_query(bot, _cq("cycle_invite:send"))
    assert any(c[0] == -100123 and c[1] == "INVITE BODY" for c in bot.sent)
    assert bot.pins and bot.pins[0][0] == -100123
    assert ft.load_topic_state(tmp_path)["pending_cycle_invite"] is None
    assert bot.edits and "Sent" in bot.edits[-1][2]


def test_cycle_invite_cancel_clears_without_posting(authd, tmp_path, monkeypatch):
    from scripts import fireside_topics as ft
    bot = FakeBot()
    monkeypatch.setenv("FIRESIDE_TRIBE_CHAT_ID", "-100123")
    monkeypatch.setenv("MISHA_TELEGRAM_USER_ID", "999")
    _seed_pending(authd, tmp_path)
    authd._handle_callback_query(bot, _cq("cycle_invite:cancel"))
    assert all(c[0] != -100123 for c in bot.sent)   # nothing posted to group
    assert ft.load_topic_state(tmp_path)["pending_cycle_invite"] is None
    assert bot.edits and "Cancel" in bot.edits[-1][2]


def test_cycle_invite_non_ceo_rejected(authd, tmp_path, monkeypatch):
    from scripts import fireside_topics as ft
    bot = FakeBot()
    monkeypatch.setenv("FIRESIDE_TRIBE_CHAT_ID", "-100123")
    monkeypatch.setenv("MISHA_TELEGRAM_USER_ID", "999")
    _seed_pending(authd, tmp_path)
    authd._handle_callback_query(bot, _cq("cycle_invite:send", uid=111))  # not the CEO
    assert all(c[0] != -100123 for c in bot.sent)               # not posted
    assert ft.load_topic_state(tmp_path)["pending_cycle_invite"] is not None  # untouched


def test_ensure_state_dir_creates_topic_files(authd, tmp_path):
    authd.ensure_state_dir()
    assert (tmp_path / "topic-ideas.jsonl").exists()
    assert (tmp_path / "topic-collection-state.json").exists()


def test_topic_ideas_list_prints_backlog(authd, tmp_path, capsys):
    from scripts import fireside_topics as ft
    ft.append_idea(tmp_path, now_iso="2026-06-25T10:00:00+04:00", user_id=1,
                   username="u", name="Alice", text="DPI capture walkthrough", cycle=1)
    authd.cmd_topic_ideas(Namespace(cycle=None, new=False))
    out = capsys.readouterr().out
    assert "DPI capture walkthrough" in out and "Alice" in out


def test_topic_digest_sends_plaintext_parse_mode(authd, tmp_path, monkeypatch):
    """CEO digest must be plain text (parse_mode='') so a Markdown-breaking idea can't 400."""
    from scripts import fireside_topics as ft
    bot = FakeBot()
    monkeypatch.setattr(authd, "get_bot", lambda: bot)
    monkeypatch.setenv("MISHA_TELEGRAM_USER_ID", "999")
    ft.append_idea(tmp_path, now_iso="2026-06-25T10:00:00+04:00", user_id=1,
                   username="u", name="N", text="what about *DPI", cycle=1)
    authd.cmd_topic_digest(Namespace(dry_run=False))
    assert bot.sent[-1][2].get("parse_mode") == ""


def test_topic_digest_failed_send_keeps_cursor(authd, tmp_path, monkeypatch):
    from scripts import fireside_topics as ft
    bot = FakeBot(); bot.error_cls = authd.TelegramAPIError; bot.fail_send = True
    monkeypatch.setattr(authd, "get_bot", lambda: bot)
    monkeypatch.setenv("MISHA_TELEGRAM_USER_ID", "999")
    ft.append_idea(tmp_path, now_iso="2026-06-25T10:00:00+04:00", user_id=1,
                   username="u", name="N", text="idea", cycle=1)
    authd.cmd_topic_digest(Namespace(dry_run=False))
    assert ft.load_topic_state(tmp_path)["last_digest_idea_id"] is None  # cursor NOT advanced


def test_cycle_end_invite_draft_plaintext_parse_mode(authd, tmp_path, monkeypatch):
    from scripts import fireside_topics as ft
    bot = FakeBot()
    monkeypatch.setattr(authd, "get_bot", lambda: bot)
    monkeypatch.setenv("MISHA_TELEGRAM_USER_ID", "999")
    _set_schedule(authd, monkeypatch, _SCHED2)
    monkeypatch.setattr(authd, "_today_local_date", lambda: date(2026, 7, 5))
    ft.append_idea(tmp_path, now_iso="2026-07-01T10:00:00+04:00", user_id=1,
                   username="u", name="N", text="idea *with markdown", cycle=1)
    authd.cmd_cycle_end_invite(Namespace(dry_run=False))
    assert bot.sent[-1][2].get("parse_mode") == ""


def test_cycle_invite_pin_failure_still_clears_no_double_post(authd, tmp_path, monkeypatch):
    from scripts import fireside_topics as ft
    bot = FakeBot(); bot.error_cls = authd.TelegramAPIError; bot.fail_pin = True
    monkeypatch.setenv("FIRESIDE_TRIBE_CHAT_ID", "-100123")
    monkeypatch.setenv("MISHA_TELEGRAM_USER_ID", "999")
    _seed_pending(authd, tmp_path)
    authd._handle_callback_query(bot, _cq("cycle_invite:send"))
    posts = [c for c in bot.sent if c[0] == -100123]
    assert len(posts) == 1                                           # posted once
    assert ft.load_topic_state(tmp_path)["pending_cycle_invite"] is None  # pending cleared despite pin fail
    # a second tap must NOT double-post
    authd._handle_callback_query(bot, _cq("cycle_invite:send"))
    posts2 = [c for c in bot.sent if c[0] == -100123]
    assert len(posts2) == 1


def test_cycle_invite_send_failure_keeps_pending(authd, tmp_path, monkeypatch):
    from scripts import fireside_topics as ft
    bot = FakeBot(); bot.error_cls = authd.TelegramAPIError; bot.fail_send = True
    monkeypatch.setenv("FIRESIDE_TRIBE_CHAT_ID", "-100123")
    monkeypatch.setenv("MISHA_TELEGRAM_USER_ID", "999")
    _seed_pending(authd, tmp_path)
    authd._handle_callback_query(bot, _cq("cycle_invite:send"))
    assert all(c[0] != -100123 for c in bot.sent)                   # nothing posted
    assert ft.load_topic_state(tmp_path)["pending_cycle_invite"] is not None  # pending kept for retry
