"""Regression: the checkpoint offer renders its options block exactly once.

Two bugs, one after the other, in the same three lines of text.

First, REASON_WRAPPER embedded an already-bilingual {body} twice, once in its
Russian section and once in its English one, so the /checkpoint and /compact
options rendered four times. That fix gave each language section its own
single-language body, which brought it down to twice.

Twice was still wrong, because this reason text goes to stderr and the operator
reads it: a full Russian section, then a full English one, then the assistant's
own answer saying the same thing a third time. The hook now emits English only.
English rather than Russian because the hook ships in a public engine, and the
wrapper asks for the reply in whatever language the operator is speaking, so
dropping the Russian section costs the operator nothing.
"""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "checkpoint_offer", str(ROOT / ".claude" / "hooks" / "checkpoint-offer.py")
)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)


# The reason builders gained `state`, `state_path` and `session` on 2026-08-19,
# because the compaction sentence now reports whether HERDR hosts this session
# and caches that answer. `compact_host` is pre-seeded here so the unit tests
# never shell out to herdr and never depend on a machine that has it.
def _reason(level: str, used: float, remaining: float, host: str = "not-hosted") -> str:
    return _mod.build_reason(
        level, used, remaining,
        {"compact_host": host}, Path("/nonexistent/state.json"), "test-session",
    )


def _cyrillic(text: str) -> str:
    return "".join(ch for ch in text if "Ѐ" <= ch <= "ӿ")


def test_hard_offer_names_each_option_once():
    reason = _reason("hard", 47.0, 53.0)
    assert reason.count("`/checkpoint`") == 1, (
        f"`/checkpoint` should appear once, got {reason.count('`/checkpoint`')}:\n{reason}"
    )
    assert reason.count("`/compact`") == 1, (
        f"`/compact` should appear once, got {reason.count('`/compact`')}"
    )


def test_soft_offer_names_each_option_once():
    reason = _reason("soft", 40.0, 60.0)
    assert reason.count("`/checkpoint`") == 1
    assert reason.count("`/compact`") == 1


def test_the_offer_carries_no_second_language():
    """The duplication this file exists to prevent came back once already. It is
    pinned by the absence of Cyrillic rather than by a count, because a count
    stays green when the second rendering is a paraphrase rather than a copy."""
    for level in ("hard", "soft"):
        reason = _reason(level, 47.0, 53.0)
        found = _cyrillic(reason)
        assert not found, f"{level} offer carries a second language: {found!r}"


def test_hard_offer_stays_substantive():
    reason = _reason("hard", 47.0, 53.0)
    assert "hard threshold reached" in reason, "hard-threshold text missing"
    assert "47%" in reason, "used percentage not interpolated"
    # The `Do not offer "continue without compact"` line was DELETED on
    # 2026-08-19. It was right while the native firing point was unknown, since
    # offering "continue" then meant offering an unknown. The point is measured
    # now, so the option carries its own number and the operator can weigh it.
    assert 'Do not offer' not in reason, "the withdrawn suppression line came back"
    assert "Continue as is" in reason, "the hard body must offer the fourth option"


def test_soft_offer_still_allows_continuing():
    """The soft threshold is an offer, not a gate: option 3 must survive."""
    reason = _reason("soft", 40.0, 60.0)
    assert "Continue as is - Claude Code compacts by itself at" in reason
    assert "60%" in reason, "remaining percentage not interpolated"


def test_the_native_point_is_the_trigger_not_the_window(monkeypatch):
    """The window is a ceiling, not a firing point.

    Caught live on 2026-08-19 by reading the hook's own output: with
    CLAUDE_CODE_AUTO_COMPACT_WINDOW=750000 the offer told the operator that
    "Claude Code compacts by itself at 750000 tokens". It fires near 584000 -
    166000 tokens earlier - because the harness reserves output tokens off the
    window and then takes a buffer fraction on top.
    """
    monkeypatch.setattr(_mod.CP, "compact_point", lambda: ("tokens", 750000))
    phrase = _mod._native_phrase()
    assert "584000" in phrase, f"the derived trigger is missing: {phrase}"
    assert phrase.startswith("roughly"), (
        "the figure depends on a remote-config buffer fraction, so it may not be "
        f"presented as exact: {phrase}"
    )
    assert "750000" in phrase, "the window it was derived from should stay visible"


def test_an_unconfigured_window_names_no_number(monkeypatch):
    """scope-claims: an environment that set nothing gets told so."""
    monkeypatch.setattr(_mod.CP, "compact_point", lambda: None)
    assert _mod._native_phrase() == "a point this hook cannot determine"


def test_the_offer_never_prints_the_raw_window_as_the_firing_point(monkeypatch):
    monkeypatch.setattr(_mod.CP, "compact_point", lambda: ("tokens", 750000))
    reason = _reason("hard", 47.0, 53.0)
    assert "by itself at 750000 tokens" not in reason, (
        "the window is being announced as the trigger again"
    )
