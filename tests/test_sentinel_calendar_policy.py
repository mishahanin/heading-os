import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.sentinel import (
    CalendarPolicyEngine,
    build_tribe_decline_message,
    select_decline_message,
    subject_has_rune,
)


def test_rune_matches_bracketed_tag_case_insensitive():
    assert subject_has_rune("[RUNE] Signals sync") is True
    assert subject_has_rune("[rune] signals sync") is True
    assert subject_has_rune("Signals sync [Rune]") is True


def test_rune_requires_brackets():
    assert subject_has_rune("Prune the backlog") is False
    assert subject_has_rune("Tribune all-hands") is False
    assert subject_has_rune("RUNE without brackets") is False


def test_rune_survives_whitespace_and_zero_width_noise():
    """The noise has to sit INSIDE the tag, which is the only place it matters.

    The fixture here was `"  [RUNE]\\u200b   Signals "` until 2026-09-01, with
    the zero-width space AFTER the closing bracket. `"[rune]" in
    "  [rune]\\u200b   signals "` is true with no normalisation at all, so the
    test passed over an implementation with `_normalize_subject` deleted from
    both ends. MEASURED: replacing the body of `subject_has_rune` with a plain
    `token in (subject or "").lower()` left the whole file green.

    A zero-width character lands inside a tag the ordinary way: the operator
    pastes `[RUNE]` out of a chat client or a web composer into the subject
    line, and one U+200B or U+FEFF rides along inside the brackets. The tag is
    the escape hatch that forces an internal invite to be held for the operator
    instead of auto-declined, so a tag the detector cannot see is an invite
    declined against the sender's explicit override.
    """
    assert subject_has_rune("[RU\u200bNE] Signals") is True
    assert subject_has_rune("\ufeff[R\u2060UNE] Signals") is True
    assert subject_has_rune("  [RUNE]\u200b   Signals ") is True


def test_rune_empty_subject_is_false():
    assert subject_has_rune("") is False
    assert subject_has_rune(None) is False


def test_tribe_message_includes_example_subject_and_alternative():
    msg = build_tribe_decline_message("Weekly sync", "Tuesday, July 28 at 14:30 local time")
    assert "internal Tribe request" in msg
    assert "Tuesday, July 28 at 14:30 local time" in msg
    assert "[RUNE] Weekly sync" in msg  # ready-to-copy example


def test_tribe_message_drops_alternative_sentence_when_none():
    msg = build_tribe_decline_message("Weekly sync", None)
    assert "Could we do" not in msg
    assert "[RUNE] Weekly sync" in msg


def test_tribe_message_template_override_formats_placeholders():
    tmpl = "Hi. Try {alternative}. Tag: {rune_token} {subject}"
    msg = build_tribe_decline_message("Q3 plan", "next week", template=tmpl)
    assert msg == "Hi. Try next week. Tag: [RUNE] Q3 plan"


def test_tribe_message_broken_template_falls_back_to_default():
    msg = build_tribe_decline_message("Weekly sync", "next week", template="Try {when}")
    assert "internal Tribe request" in msg      # fell back to default body
    assert "[RUNE] Weekly sync" in msg


def test_selector_tribe_uses_tribe_builder():
    msg = select_decline_message(True, "Weekly sync", "next week", {})
    assert "internal Tribe request" in msg
    assert "[RUNE] Weekly sync" in msg


def test_selector_non_tribe_uses_generic_default_with_alternative():
    msg = select_decline_message(False, "Weekly sync", "next week", {})
    assert msg == ("Due to some conflicts, I'd like to propose a new day and time "
                   "for our meeting. How about next week?")


def test_selector_non_tribe_no_alternative_omits_how_about():
    msg = select_decline_message(False, "Weekly sync", None, {})
    assert msg == ("Due to some conflicts, I'd like to propose a new day and time "
                   "for our meeting.")


def test_selector_honors_configured_rune_token():
    msg = select_decline_message(True, "Q3 plan", None, {"rune_token": "[HELM]"})
    assert "[HELM] Q3 plan" in msg


# ---------------------------------------------------------------------------
# A hand-edited sentinel_config.yaml key written with nothing after it
#
# `rune_token:` and `decline_message:` with a blank value parse as None, not as
# an absent key, so `config.get(key, default)` hands the None straight through -
# the default fires only on ABSENCE. Every case above this block passes a
# well-formed dict or omits the key entirely, so none of them reached it.
#
# These matter more than an ordinary robustness gap because the value ends up in
# a decline reply SENT to a real person. The auto-decline policy itself is the
# operator's design and frozen; what is asserted here is only that the reply says
# something usable and that producing it does not crash.
# ---------------------------------------------------------------------------

import pytest  # noqa: E402

from scripts.sentinel import _DEFAULT_RUNE_TOKEN  # noqa: E402

BLANK_YAML_VALUES = [None, "", "   ", [], {}, 0]


@pytest.mark.parametrize("blank", BLANK_YAML_VALUES,
                         ids=["null", "empty", "spaces", "list", "dict", "zero"])
def test_a_blank_rune_token_does_not_tell_the_tribe_to_type_it_literally(blank):
    """MEASURED 2026-09-01, before the fix, with `rune_token:` blank in the yaml:

        detector  subject_has_rune("[RUNE] x", None)   ->  True
        message   "...resend the same invite with the tag None added ...
                   Your subject would look exactly like this: None Weekly sync"

    `subject_has_rune` carried an `or _DEFAULT_RUNE_TOKEN` fallback;
    `build_tribe_decline_message` did not. So the detector kept looking for
    `[RUNE]` while the message a Tribe member reads told them to type `None`,
    and a member who followed the instruction exactly was declined again next
    time. One fix, two copies, and the copy that was missed is the one that
    reaches a person.
    """
    msg = select_decline_message(True, "Weekly sync", None, {"rune_token": blank})

    assert f"{_DEFAULT_RUNE_TOKEN} Weekly sync" in msg, msg
    for spelling in ("None", "[]", "{}", "the tag  ", "the tag 0"):
        assert spelling not in msg, (f"a blank rune_token reached the message "
                                     f"as {spelling!r}: {msg}")


@pytest.mark.parametrize("blank", BLANK_YAML_VALUES,
                         ids=["null", "empty", "spaces", "list", "dict", "zero"])
def test_the_detector_and_the_message_agree_on_the_tag(blank):
    """The half that makes the test above mean something: whatever tag the
    message tells them to use has to be the tag the detector accepts."""
    msg = select_decline_message(True, "Weekly sync", None, {"rune_token": blank})
    tag = msg.rsplit("look exactly like this:", 1)[1].replace("Weekly sync", "").strip()

    assert tag, msg
    assert subject_has_rune(f"{tag} Weekly sync", blank) is True, (
        f"the message told them to type {tag!r} and the detector rejects it")


@pytest.mark.parametrize("blank", BLANK_YAML_VALUES,
                         ids=["null", "empty", "spaces", "list", "dict", "zero"])
def test_a_blank_decline_message_does_not_crash_the_whole_invite_cycle(blank):
    """MEASURED 2026-09-01, before the fix: `decline_message:` blank made
    `msg += f" How about {alternative}?"` raise
    `TypeError: unsupported operand type(s) for +=: 'NoneType' and 'str'`.

    `select_decline_message` is called OUTSIDE the try that wraps
    `decline_invite`, so the raise leaves the `for invite in invites` loop, skips
    `self.state.save()`, and lands in the cycle handler's
    `except Exception: logger.error("Meeting invite check failed")`. Every invite
    in that batch is left unprocessed and the earlier decisions already taken in
    that loop are never saved, so the next cycle reaches the same invite and does
    the same thing: a permanent livelock behind one log line.
    """
    msg = select_decline_message(False, "Weekly sync", "next week",
                                 {"decline_message": blank})

    assert isinstance(msg, str) and msg.strip(), repr(msg)
    assert "How about next week?" in msg, msg


@pytest.mark.parametrize("blank", [None, "", "   ", [], {}],
                         ids=["null", "empty", "spaces", "list", "dict"])
def test_a_blank_or_non_string_tribe_template_falls_back_to_the_default_body(blank):
    """`if template:` let a non-string through to `.format`, and AttributeError
    is not in the caught tuple. A `tribe_decline_message:` written as a YAML
    list raised out of the same unprotected call site as the case above."""
    msg = select_decline_message(True, "Weekly sync", "next week",
                                 {"tribe_decline_message": blank})

    assert "internal Tribe request" in msg, msg
    assert f"{_DEFAULT_RUNE_TOKEN} Weekly sync" in msg, msg


def test_a_configured_token_and_message_are_still_honoured():
    """The anti-vacuity jaw. A fallback that ignored the config entirely would
    satisfy every case above; these two prove it still reads what is set."""
    msg = select_decline_message(True, "Q3 plan", None, {"rune_token": "[HELM]"})
    assert "[HELM] Q3 plan" in msg
    assert _DEFAULT_RUNE_TOKEN not in msg

    generic = select_decline_message(False, "Q3 plan", None,
                                     {"decline_message": "Cannot make this one."})
    assert generic == "Cannot make this one."


TZ = ZoneInfo("Asia/Dubai")
LOG = logging.getLogger("test-sentinel")


def _engine(cfg):
    return CalendarPolicyEngine(cfg, TZ, LOG)


def test_is_tribe_explicit_domain_list():
    eng = _engine({"tribe_domains": ["31c.io"]})
    assert eng._is_tribe("kolleg@31c.io") is True
    assert eng._is_tribe("KOLLEG@31C.IO") is True
    assert eng._is_tribe("someone@example.org") is False


def test_is_tribe_defaults_to_operator_domain(monkeypatch):
    monkeypatch.setattr("scripts.sentinel.get_operator",
                        lambda: {"email": "ceo@example.org"})
    eng = _engine({"tribe_domains": []})
    assert eng._is_tribe("peer@example.org") is True
    assert eng._is_tribe("outsider@other.com") is False


def test_is_tribe_handles_empty_sender():
    eng = _engine({"tribe_domains": ["31c.io"]})
    assert eng._is_tribe("") is False


ACCEPT_CFG = {"tribe_domains": ["31c.io"], "protected_blocks": [],
              "vip_senders": [], "external_domains": []}
CONFLICT_CFG = {"tribe_domains": ["31c.io"],
                "protected_blocks": [{"days": [0, 1, 2, 3, 4, 5, 6]}],
                "vip_senders": [], "external_domains": ["example.org"]}


def _invite(subject, sender, hour=13):
    start = datetime(2026, 7, 25, hour, 0, tzinfo=TZ)   # Saturday
    end = datetime(2026, 7, 25, hour, 30, tzinfo=TZ)
    return {"subject": subject, "sender_email": sender, "start": start, "end": end,
            "duration_minutes": 30, "attendee_count": 1, "body": ""}


def test_rune_forces_escalate_even_when_would_accept():
    eng = _engine(ACCEPT_CFG)
    res = eng.evaluate(_invite("[RUNE] Weekly sync", "kolleg@31c.io"), [])
    assert res["decision"] == "escalate"
    assert res["is_tribe"] is True


def test_rune_disabled_falls_through_to_normal_decision():
    cfg = dict(ACCEPT_CFG, rune_override_enabled=False)
    eng = _engine(cfg)
    res = eng.evaluate(_invite("[RUNE] Weekly sync", "kolleg@31c.io"), [])
    assert res["decision"] == "accept"


def test_tribe_conflict_declines_and_flags_tribe():
    eng = _engine(CONFLICT_CFG)
    res = eng.evaluate(_invite("Weekly sync", "kolleg@31c.io"), [])
    assert res["decision"] == "decline"
    assert res["is_tribe"] is True


def test_external_conflict_escalates_unchanged():
    eng = _engine(CONFLICT_CFG)
    res = eng.evaluate(_invite("Weekly sync", "vendor@example.org"), [])
    assert res["decision"] == "escalate"
    assert res["is_tribe"] is False


# ---------------------------------------------------------------------------
# The same hand-edited yaml, in the keys that are not strings
#
# `_configured_text` closed this for `rune_token`, `decline_message` and
# `tribe_decline_message`. The keys beside them that hold a LIST, a MAPPING or a
# NUMBER were left on the old footing and fail the same way: an empty dash makes
# a None ENTRY, a trailing colon makes a mapping, a key written with nothing
# after it parses as None rather than as an absent key, and `.get(key, default)`
# fires only on ABSENCE.
#
# `evaluate` is called at try-depth 0 inside the `for invite in invites` loop,
# so a raise here leaves the loop, skips `self.state.save()`, and lands in the
# cycle handler's `except Exception: logger.error("Meeting invite check
# failed")`. Every invite in the batch is left unprocessed with the earlier
# decisions unsaved, and the next cycle does it again: a permanent livelock
# behind one log line.
# ---------------------------------------------------------------------------

MONDAY = datetime(2026, 7, 27, 13, 0, tzinfo=TZ)


def _weekday_invite(sender="kolleg@31c.io", subject="Weekly sync"):
    return {"subject": subject, "sender_email": sender,
            "start": MONDAY, "end": MONDAY.replace(minute=30),
            "duration_minutes": 30, "attendee_count": 1, "body": ""}


ADJACENT_EVENT = [{"start": datetime(2026, 7, 27, 12, 0, tzinfo=TZ),
                   "end": datetime(2026, 7, 27, 12, 55, tzinfo=TZ),
                   "subject": "standing sync"}]

# Each entry is one way a hand-edited sentinel_config.yaml goes wrong, with the
# exception it raised against the shipped code on 2026-09-01.
MALFORMED_CALENDAR_CONFIG = [
    ("tribe_domains_null_entry", {"tribe_domains": [None]}, False),
    ("tribe_domains_mapping_entry", {"tribe_domains": [{"31c.io": None}]}, False),
    ("tribe_domains_blank_key", {"tribe_domains": None}, False),
    ("vip_senders_null_entry", {"vip_senders": [None]}, False),
    ("vip_senders_blank_key", {"vip_senders": None}, False),
    ("external_domains_null_entry", {"external_domains": [None]}, False),
    ("external_domains_blank_key", {"external_domains": None}, False),
    ("protected_blocks_blank_key", {"protected_blocks": None}, False),
    ("protected_blocks_string_entry", {"protected_blocks": ["mornings"]}, False),
    ("protected_blocks_null_entry", {"protected_blocks": [None]}, False),
    ("protected_blocks_blank_days", {"protected_blocks": [{"days": None}]}, False),
    ("protected_blocks_blank_before",
     {"protected_blocks": [{"days": [0], "before": None}]}, False),
    # `after: 9:00` unquoted is sexagesimal YAML 1.1 and arrives as int 540.
    ("protected_blocks_unquoted_time",
     {"protected_blocks": [{"days": [0, 1, 2, 3, 4], "after": 540}]}, False),
    ("protected_blocks_unquoted_range",
     {"protected_blocks": [{"days": [0, 1, 2, 3, 4], "start": 540, "end": 1020}]},
     False),
    ("day_themes_blank_key", {"day_themes": None}, False),
    ("day_themes_written_as_list", {"day_themes": ["Strategy & Leadership"]}, False),
    ("day_themes_blank_value", {"day_themes": {0: None}}, False),
    ("max_duration_minutes_blank", {"max_duration_minutes": None}, False),
    ("max_attendees_blank", {"max_attendees": None}, False),
    ("max_consecutive_blank", {"max_consecutive": None}, False),
    ("min_gap_minutes_blank", {"min_gap_minutes": None}, True),
    ("min_gap_minutes_quoted", {"min_gap_minutes": "15"}, True),
]


@pytest.mark.parametrize("with_events", [False, True],
                         ids=["no-neighbour", "adjacent-neighbour"])
@pytest.mark.parametrize("name,override", [(c[0], c[1])
                                           for c in MALFORMED_CALENDAR_CONFIG],
                         ids=[c[0] for c in MALFORMED_CALENDAR_CONFIG])
def test_a_hand_edited_calendar_key_does_not_livelock_the_invite_loop(
        name, override, with_events):
    """MEASURED 2026-09-01, one malformed key at a time against the shipped code.

    Fourteen of these raised AttributeError or TypeError out of `evaluate`:
    `'NoneType' object has no attribute 'lower'` from the three domain lists,
    `'NoneType' object is not iterable` from a blank list key, `'str' object has
    no attribute 'get'` from a protected block written as a string, `'NoneType'
    object has no attribute 'get'` from a blank `day_themes`, and `'>' not
    supported between instances of 'int' and 'NoneType'` from the numeric
    thresholds.

    Every case runs twice, and the second run is not padding. With a neighbouring
    event the invite picks up a back-to-back violation, routes to `decline`, and
    `find_alternative_slot` then reads `protected_blocks` through a SECOND copy
    of the same loop in `_check_protected_time_simple`. Without a neighbour that
    copy is never reached: measured 2026-09-01, reverting only the second copy
    left this file green until the neighbour was added.

    What is asserted is only that a decision comes back at all. Which decision
    is the operator's frozen design and is pinned by the cases above and below,
    not here.
    """
    cfg = dict(ACCEPT_CFG, **override)
    result = _engine(cfg).evaluate(_weekday_invite(),
                                   ADJACENT_EVENT if with_events else [])

    assert result["decision"] in {"accept", "decline", "escalate"}, result
    assert isinstance(result["reasons"], list), result


def test_a_malformed_calendar_key_says_which_key_it_dropped(caplog):
    """A dropped value must be a loud one. Silence about an exclusion reads as
    coverage, which is what `.claude/rules/scope-claims.md` is about."""
    with caplog.at_level(logging.WARNING, logger="test-sentinel"):
        _engine(dict(ACCEPT_CFG, vip_senders=[None, "ceo@example.org"])).evaluate(
            _weekday_invite(), [])

    assert any("vip_senders" in r.message for r in caplog.records), caplog.records


def test_an_unquoted_time_bound_is_reported_by_key(caplog):
    """`after: 9:00` is the edit that reads correct and is not a string."""
    cfg = dict(ACCEPT_CFG,
               protected_blocks=[{"days": [0, 1, 2, 3, 4], "after": 540}])
    with caplog.at_level(logging.WARNING, logger="test-sentinel"):
        res = _engine(cfg).evaluate(_weekday_invite(), [])
    assert res["decision"] == "accept", res
    assert any("protected_blocks[].after" in r.message for r in caplog.records), (
        [r.message for r in caplog.records])


def test_a_blank_bound_is_not_read_as_a_whole_day_block():
    """The direction the hardening must not go.

    `protected_blocks: [{days: [0], before: }]` is a malformed BOUND, not a
    declaration that Monday is blocked. The all-day branch is still decided by
    which keys are present, so this stays an accept. Reading the blank value
    instead would turn an invite that is accepted today into an auto-decline
    sent to a real organizer, and the auto-decline design is frozen.
    """
    cfg = dict(ACCEPT_CFG,
               protected_blocks=[{"days": [0], "before": None, "after": None}])
    assert _engine(cfg).evaluate(_weekday_invite(), [])["decision"] == "accept"


def test_a_vip_written_without_a_dash_is_still_a_vip():
    """`vip_senders: ceo@example.org` iterated CHARACTERS, so no entry matched.

    It did not crash; it silently ignored the whole list, and a VIP the operator
    had configured was auto-declined instead of held. Recognising the scalar can
    only move a decision from decline toward escalate.
    """
    # The sender's domain is deliberately NOT in `external_domains`. It was
    # `example.org` here for one draft, which CONFLICT_CFG lists as external, so
    # `_is_vip_or_external` returned True down the other branch and the case
    # passed with the whole `vip_senders` list ignored.
    cfg = dict(CONFLICT_CFG, vip_senders="chair@invented-board.test")
    invite = _weekday_invite(sender="chair@invented-board.test")
    assert _engine(dict(cfg, vip_senders=[])).evaluate(invite, [])["is_vip"] is False
    res = _engine(cfg).evaluate(invite, [])
    assert res["is_vip"] is True
    assert res["decision"] == "escalate"


def test_a_null_sender_email_does_not_raise_out_of_the_rune_branch():
    """`(sender_email or "")` was on `_is_tribe` and not on `_is_vip_or_external`.

    Both read the same `invite.get("sender_email", "")`, and a default fires
    only on an ABSENT key, so a present-but-null value reached `.lower()`. The
    RUNE branch of `evaluate` calls the unguarded one, which puts the raise on
    the override path the operator uses to force an invite to be held.
    """
    invite = _weekday_invite(subject="[RUNE] Weekly sync")
    invite["sender_email"] = None
    res = _engine(ACCEPT_CFG).evaluate(invite, [])
    assert res["decision"] == "escalate"
    assert res["is_vip"] is False

    plain = _weekday_invite()
    plain["sender_email"] = None
    assert _engine(ACCEPT_CFG).evaluate(plain, [])["decision"] == "accept"


def test_the_hardening_did_not_neuter_a_well_formed_config():
    """The anti-vacuity jaw. A reader that dropped EVERY value would satisfy
    every case above, and would also silently disable protected time, the VIP
    list and the duration cap."""
    eng = _engine(CONFLICT_CFG)
    assert eng.evaluate(_weekday_invite(), [])["decision"] == "decline"

    vip = _engine(dict(ACCEPT_CFG, vip_senders=["vip@example.org"],
                       max_attendees=2))
    over = _weekday_invite(sender="vip@example.org")
    over["attendee_count"] = 9
    assert vip.evaluate(over, [])["is_vip"] is True
    assert vip.evaluate(over, [])["decision"] == "escalate"

    capped = _engine(dict(ACCEPT_CFG, max_duration_minutes=20))
    long_one = _weekday_invite()
    long_one["duration_minutes"] = 45
    res = capped.evaluate(long_one, [])
    assert res["violations"] == ["duration"], res
    assert "20m limit" in res["reasons"][0], res


def test_the_shipped_example_config_still_decides_exactly_as_before():
    """The jaw with teeth: the frozen policy, read from the file operators copy.

    Every case above feeds the engine a dict built in this file. This one loads
    `scripts/sentinel_config.example.yaml` and asserts the decisions its
    protected blocks, VIP list and external domains produce. MEASURED
    2026-09-01: 196 invites across seven days, seven start hours and four
    senders were evaluated against that file before and after the config
    hardening, and the full tuple of (decision, is_vip, is_tribe, violations,
    proposed_alternative) hashed identically both times. The cases below are the
    corners of that sweep, spelled out so a future change has to explain itself.
    """
    import yaml

    root = Path(__file__).resolve().parent.parent
    cfg = yaml.safe_load(
        (root / "scripts/sentinel_config.example.yaml").read_text())["calendar"]
    eng = CalendarPolicyEngine(cfg, TZ, LOG)

    def at(day_offset, hour, sender):
        start = datetime(2026, 7, 27, hour, 0, tzinfo=TZ) + timedelta(days=day_offset)
        return eng.evaluate({"subject": "Weekly sync product review",
                             "sender_email": sender, "start": start,
                             "end": start + timedelta(minutes=45),
                             "duration_minutes": 45, "attendee_count": 3,
                             "body": ""}, [])

    tribe = "kolleg@31c.io"
    # Monday 08:00 is inside "before: 09:30" -> a hard protected-time decline.
    assert at(0, 8, tribe)["decision"] == "decline"
    assert "protected_time" in at(0, 8, tribe)["violations"]
    # Saturday is an all-day block written with `days` and no bounds at all.
    assert at(5, 13, tribe)["decision"] == "decline"
    # Monday 13:00 is unprotected; the theme keywords still disagree with the
    # Monday theme, which is a SOFT violation and escalates rather than declines.
    assert at(0, 13, tribe)["decision"] == "escalate"
    # A configured VIP and a configured external domain are never auto-declined.
    assert at(0, 8, "investor@example.com")["decision"] == "escalate"
    assert at(0, 8, "investor@example.com")["is_vip"] is True
    assert at(5, 13, "x@partner.example.com")["is_vip"] is True
    assert at(5, 13, "x@partner.example.com")["decision"] == "escalate"
    # A stranger on an unlisted domain is not a VIP and is not Tribe.
    assert at(0, 13, "a@other.test")["is_vip"] is False
    assert at(0, 13, "a@other.test")["is_tribe"] is False
