"""Tests for fireside-bot.py auth gate (_is_authorized_user + /start handler).

Authorization is by immutable Telegram user_id ONLY. A @username is mutable and
reclaimable, so a username match is never proof of identity and never binds
telegram_user_id. Binding happens exclusively through the trusted `bootstrap`
(Telethon enumeration of the real group). These tests pin that invariant and
guard against handle-takeover regressions.

History: an earlier build (2026-05-29) authorized and lazily bound user_id from
a self-reported username to heal the 54/56 entries the xlsx-only self-heal left
with telegram_user_id=None. A security review flagged that as a spoofable-field
auth bypass; the binding-by-username paths were removed.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def fb():
    """Load fireside-bot.py as a module (hyphen in filename)."""
    path = Path(__file__).resolve().parent.parent / "scripts" / "fireside-bot.py"
    spec = importlib.util.spec_from_file_location("fireside_bot", str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def state_dir(fb, tmp_path, monkeypatch):
    """Redirect STATE_DIR to a temp path and seed a minimal roster.

    bob: active member, NOT yet bound (telegram_user_id=None) -- the
        handle-takeover target.
    alice: active member, bound to a real user_id.
    carol:     excluded member, unbound.
    """
    monkeypatch.setattr(fb, "STATE_DIR", tmp_path)
    roster = {
        "bob": {
            "name": "Bob Member",
            "active": True,
            "telegram_user_id": None,
        },
        "alice": {
            "name": "Alice Member",
            "active": True,
            "telegram_user_id": 100000001,
        },
        "carol": {
            "name": "Carol Member",
            "active": False,
            "excluded_from_fireside": True,
            "telegram_user_id": None,
        },
    }
    (tmp_path / fb.TRIBE_ROSTER).write_text(
        json.dumps(roster, ensure_ascii=False), encoding="utf-8"
    )
    return tmp_path


def _read_roster(fb, state_dir):
    return json.loads((state_dir / fb.TRIBE_ROSTER).read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# _is_authorized_user: user_id is the only authority
# ---------------------------------------------------------------------------

def test_authorized_by_user_id_match(fb, state_dir):
    """Direct user_id hit on an active, non-excluded member authorizes."""
    assert fb._is_authorized_user(100000001) is True


def test_unauthorized_when_no_username_and_no_id_match(fb, state_dir):
    """Unknown user_id with no username argument -> False."""
    assert fb._is_authorized_user(999999999) is False


def test_username_match_alone_does_not_authorize(fb, state_dir):
    """SECURITY: a username match must never authorize on its own.

    bob is active but unbound. A DM whose user_id is not bound, even
    when it carries the matching @username, must be rejected.
    """
    assert fb._is_authorized_user(100000002, username="bob") is False


def test_username_match_does_not_persist_user_id(fb, state_dir):
    """SECURITY: a username-only attempt must not write telegram_user_id.

    Persisting on username match is exactly the handle-takeover binding hole.
    """
    before = _read_roster(fb, state_dir)
    fb._is_authorized_user(100000002, username="bob")
    after = _read_roster(fb, state_dir)
    assert after == before
    assert after["bob"]["telegram_user_id"] is None


def test_handle_takeover_blocked(fb, state_dir):
    """SECURITY (headline): an attacker who has claimed a member's dropped
    @handle cannot gain that member's authorization, and the roster is not
    mutated to bind the attacker's user_id."""
    attacker_user_id = 666000666
    assert fb._is_authorized_user(attacker_user_id, username="bob") is False
    roster = _read_roster(fb, state_dir)
    assert roster["bob"]["telegram_user_id"] is None


def test_username_match_case_insensitive_still_rejected(fb, state_dir):
    """Case variation in the (untrusted) username changes nothing -> still False."""
    assert fb._is_authorized_user(100000002, username="Bob") is False


def test_excluded_member_user_id_not_authorized(fb, state_dir):
    """An excluded member never authorizes even by a real user_id binding."""
    # Bind carol to a user_id, then confirm exclusion still blocks.
    roster = _read_roster(fb, state_dir)
    roster["carol"]["telegram_user_id"] = 555
    (state_dir / fb.TRIBE_ROSTER).write_text(
        json.dumps(roster, ensure_ascii=False), encoding="utf-8"
    )
    assert fb._is_authorized_user(555, username="carol") is False


def test_unknown_username_not_authorized(fb, state_dir):
    """A username not in the roster -> False."""
    assert fb._is_authorized_user(111, username="someoutsider") is False


def test_user_id_match_unaffected_by_username_and_roster_unchanged(fb, state_dir):
    """A valid user_id authorizes regardless of username, and never rewrites."""
    before = _read_roster(fb, state_dir)
    assert fb._is_authorized_user(100000001, username="alice") is True
    after = _read_roster(fb, state_dir)
    assert before == after, "roster must not be rewritten on a user_id match"


def test_falsy_user_id_rejected(fb, state_dir):
    """user_id of 0/None -> False (no roster scan)."""
    assert fb._is_authorized_user(0, username="alice") is False


# ---------------------------------------------------------------------------
# /start handler: greets bound members, never binds from username
# ---------------------------------------------------------------------------

class _FakeBot:
    """Captures bot I/O so handlers can be asserted offline."""

    def __init__(self):
        self.sent = []        # list of (user_id, text)
        self.answers = []     # list of (cq_id, text) from answer_callback_query
        self._next_msg_id = 1000

    def send_message(self, user_id, text, parse_mode="", reply_markup=None):
        self.sent.append((user_id, text))
        self._next_msg_id += 1
        return {"message_id": self._next_msg_id}

    def answer_callback_query(self, cq_id, text=None):
        self.answers.append((cq_id, text))

    def edit_message_reply_markup(self, chat_id, msg_id, markup):
        pass


@pytest.fixture
def no_forward(fb, monkeypatch):
    """Stub _maybe_forward_outsider; record that it fired."""
    calls = []
    monkeypatch.setattr(
        fb, "_maybe_forward_outsider",
        lambda bot, user_id, username, text: calls.append((user_id, username)),
    )
    return calls


def _dm(text, user_id, username):
    return {
        "chat": {"type": "private"},
        "text": text,
        "from": {"id": user_id, "username": username},
    }


def test_start_bound_member_gets_welcome(fb, state_dir, no_forward):
    """A bound, active member who sends /start receives the welcome DM and the
    roster is not rewritten."""
    bot = _FakeBot()
    before = _read_roster(fb, state_dir)
    fb._handle_message(bot, _dm("/start", 100000001, "alice"))
    assert bot.sent == [(100000001, fb.WELCOME_DM)]
    assert no_forward == []
    assert _read_roster(fb, state_dir) == before


def test_start_unbound_member_is_rejected_and_not_bound(fb, state_dir, no_forward):
    """SECURITY: /start from an unbound member (matching username, new user_id)
    must NOT bind user_id; it gets the private-bot reply and is forwarded to
    Misha so he can re-run bootstrap."""
    bot = _FakeBot()
    fb._handle_message(bot, _dm("/start", 100000002, "bob"))
    assert bot.sent == [(100000002, fb.UNAUTHORIZED_REPLY)]
    assert no_forward == [(100000002, "bob")]
    roster = _read_roster(fb, state_dir)
    assert roster["bob"]["telegram_user_id"] is None


def test_start_handle_takeover_blocked(fb, state_dir, no_forward):
    """SECURITY: attacker holding a member's @handle sends /start -> rejected,
    no binding written."""
    bot = _FakeBot()
    fb._handle_message(bot, _dm("/start", 666000666, "bob"))
    assert bot.sent == [(666000666, fb.UNAUTHORIZED_REPLY)]
    roster = _read_roster(fb, state_dir)
    assert roster["bob"]["telegram_user_id"] is None


def test_start_outsider_gets_unauthorized(fb, state_dir, no_forward):
    """/start from a complete outsider -> private-bot reply + forward."""
    bot = _FakeBot()
    fb._handle_message(bot, _dm("/start", 777, "randomperson"))
    assert bot.sent == [(777, fb.UNAUTHORIZED_REPLY)]
    assert no_forward == [(777, "randomperson")]


# ---------------------------------------------------------------------------
# Identity-for-command-logic is by user_id, never the self-reported @username.
# (Closes the second-order handle-takeover vector: /swap and /me must operate on
# the caller's own schedule rows, identified by the immutable user_id, even if
# the caller's current @handle matches a different member's roster key.)
# ---------------------------------------------------------------------------

def test_resolve_my_username_by_user_id(fb, state_dir):
    """_resolve_my_username returns the roster key bound to the user_id."""
    assert fb._resolve_my_username(100000001) == "alice"


def test_resolve_my_username_unknown_user_id_returns_none(fb, state_dir):
    """A user_id not bound to any roster entry -> None (no username fallback)."""
    assert fb._resolve_my_username(424242) is None


def test_resolve_my_username_unbound_member_returns_none(fb, state_dir):
    """An active member whose telegram_user_id is None is not resolvable by a
    stranger's user_id -- there is no self-reported-username fallback to abuse."""
    # bob is active but unbound; a foreign user_id must not resolve to it.
    assert fb._resolve_my_username(999999) is None


def test_swap_uses_user_id_identity_not_self_reported_username(
    fb, state_dir, no_forward, monkeypatch
):
    """SECURITY (headline, second-order): /swap must act on the caller's OWN
    schedule identity (resolved from user_id), not the self-reported @username.

    The caller's user_id (100000001) is bound to roster key 'alice' but
    they self-report the @handle 'bob' (a different member's key, e.g.
    a dropped handle they have claimed). The swap flow must be kicked off for
    'alice', never 'bob'.
    """
    captured = {}
    monkeypatch.setattr(
        fb, "_swap_kickoff_for_a",
        lambda bot, user_id, username: captured.update(user_id=user_id, username=username),
    )
    bot = _FakeBot()
    fb._handle_message(bot, _dm("/swap", 100000001, "bob"))
    assert captured == {"user_id": 100000001, "username": "alice"}


def test_swap_unresolvable_user_id_is_rejected(fb, state_dir, no_forward, monkeypatch):
    """If somehow an authorized-looking user_id has no roster binding, /swap
    refuses rather than falling back to the self-reported username."""
    called = []
    monkeypatch.setattr(
        fb, "_swap_kickoff_for_a",
        lambda *a, **k: called.append(a),
    )
    bot = _FakeBot()
    # 424242 is not bound to any entry; username matches an active key but must
    # not be used to derive identity.
    fb._handle_message(bot, _dm("/swap", 424242, "bob"))
    # Not authorized at the gate, so kickoff never runs.
    assert called == []
    assert bot.sent == [(424242, fb.UNAUTHORIZED_REPLY)]


# ---------------------------------------------------------------------------
# Swap inline-button taps: authorization + ownership are user_id-gated.
# ---------------------------------------------------------------------------

def _cq(data, user_id, username):
    return {
        "id": "cq1",
        "data": data,
        "from": {"id": user_id, "username": username},
        "message": {"chat": {"id": user_id}, "message_id": 5},
    }


def test_callback_query_rejects_unauthorized_tapper(fb, state_dir):
    """An outsider tapping a swap button is rejected by user_id auth."""
    bot = _FakeBot()
    fb._handle_callback_query(bot, _cq("sw:a:rid123:0", 888888, "outsider"))
    assert bot.answers == [("cq1", "Not authorized.")]
    assert bot.sent == []


def test_a_tap_rejects_non_owner(fb, state_dir):
    """SECURITY: only the user_id that initiated a swap may tap its buttons.

    A different (even authorized) member cannot hijack someone else's swap
    request by tapping their inline buttons.
    """
    bot = _FakeBot()
    ctx = {"a_user_id": 100000001}  # request owned by alice
    fb._handle_a_tap(
        bot, cq_id="cq1", rid="rid123", choice="0", ctx=ctx,
        status="initiated", msg_chat_id=111, msg_id=5, tapper_user_id=100000002,
    )
    assert bot.answers == [("cq1", "This button is for someone else.")]


def test_b_tap_rejects_non_owner(fb, state_dir):
    """SECURITY: only the intended counterparty B (by immutable user_id) may
    accept/decline a bilateral swap. A different member tapping B's buttons is
    rejected -- symmetric to the A-side ownership gate."""
    bot = _FakeBot()
    ctx = {"b_user_id": 100000001}  # swap proposed to alice
    fb._handle_b_tap(
        bot, cq_id="cq1", rid="rid123", choice="y", ctx=ctx,
        status="proposed_to_b", msg_chat_id=111, msg_id=5, tapper_user_id=100000002,
    )
    assert bot.answers == [("cq1", "This button is for someone else.")]


def test_b_tap_rejects_when_no_b_bound(fb, state_dir):
    """If the swap context has no b_user_id, no one can tap B's buttons."""
    bot = _FakeBot()
    fb._handle_b_tap(
        bot, cq_id="cq1", rid="rid123", choice="y", ctx={},
        status="proposed_to_b", msg_chat_id=111, msg_id=5, tapper_user_id=100000001,
    )
    assert bot.answers == [("cq1", "This button is for someone else.")]


# ---------------------------------------------------------------------------
# Launch-announcement reactions (opt-ins): bound members only, canonical handle.
# ---------------------------------------------------------------------------

def _reaction(user_id, username, emojis, msg_id=42):
    return {
        "message_id": msg_id,
        "user": {"id": user_id, "username": username},
        "new_reaction": [{"type": "emoji", "emoji": e} for e in emojis],
    }


def test_reaction_records_bound_member_with_canonical_username(fb, state_dir, monkeypatch):
    """A bound member's opt-in is stored under the canonical roster key, not the
    self-reported handle (which here is deliberately a stale/wrong value)."""
    monkeypatch.setenv("FIRESIDE_LAUNCH_ANNOUNCEMENT_MSG_ID", "42")
    fb._handle_message_reaction(_reaction(100000001, "some_other_handle", ["🧭"]))
    opt_ins = json.loads((state_dir / fb.OPT_INS).read_text(encoding="utf-8"))
    assert opt_ins["helmsman"] == [{"user_id": 100000001, "username": "alice"}]


def test_reaction_ignores_unbound_user(fb, state_dir, monkeypatch):
    """SECURITY/integrity: an unbound user_id (outsider, or member not yet
    bootstrapped) reacting does NOT create an opt-in."""
    monkeypatch.setenv("FIRESIDE_LAUNCH_ANNOUNCEMENT_MSG_ID", "42")
    fb._handle_message_reaction(_reaction(555000555, "outsider", ["🧭", "🌟"]))
    path = state_dir / fb.OPT_INS
    if path.exists():
        opt_ins = json.loads(path.read_text(encoding="utf-8"))
        assert opt_ins["helmsman"] == [] and opt_ins["wildcard"] == []


def test_reaction_removal_by_user_id(fb, state_dir, monkeypatch):
    """Removing a reaction clears the opt-in, keyed by user_id."""
    monkeypatch.setenv("FIRESIDE_LAUNCH_ANNOUNCEMENT_MSG_ID", "42")
    (state_dir / fb.OPT_INS).write_text(
        json.dumps({"helmsman": [{"user_id": 100000001, "username": "alice"}],
                    "wildcard": []}),
        encoding="utf-8",
    )
    fb._handle_message_reaction(_reaction(100000001, "alice", []))
    opt_ins = json.loads((state_dir / fb.OPT_INS).read_text(encoding="utf-8"))
    assert opt_ins["helmsman"] == []


def test_reaction_on_wrong_message_ignored(fb, state_dir, monkeypatch):
    """Reactions on messages other than the launch announcement are ignored."""
    monkeypatch.setenv("FIRESIDE_LAUNCH_ANNOUNCEMENT_MSG_ID", "42")
    fb._handle_message_reaction(_reaction(100000001, "alice", ["🧭"], msg_id=99))
    assert not (state_dir / fb.OPT_INS).exists()
