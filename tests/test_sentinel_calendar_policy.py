import logging
import sys
from datetime import datetime
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
