import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.sentinel import subject_has_rune


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


from scripts.sentinel import build_tribe_decline_message


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


from scripts.sentinel import select_decline_message


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
