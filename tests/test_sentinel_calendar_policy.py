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
