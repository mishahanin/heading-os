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


def _cyrillic(text: str) -> str:
    return "".join(ch for ch in text if "Ѐ" <= ch <= "ӿ")


def test_hard_offer_names_each_option_once():
    reason = _mod.build_reason("hard", 47.0, 53.0)
    assert reason.count("`/checkpoint`") == 1, (
        f"`/checkpoint` should appear once, got {reason.count('`/checkpoint`')}:\n{reason}"
    )
    assert reason.count("`/compact`") == 1, (
        f"`/compact` should appear once, got {reason.count('`/compact`')}"
    )


def test_soft_offer_names_each_option_once():
    reason = _mod.build_reason("soft", 40.0, 60.0)
    assert reason.count("`/checkpoint`") == 1
    assert reason.count("`/compact`") == 1


def test_the_offer_carries_no_second_language():
    """The duplication this file exists to prevent came back once already. It is
    pinned by the absence of Cyrillic rather than by a count, because a count
    stays green when the second rendering is a paraphrase rather than a copy."""
    for level in ("hard", "soft"):
        reason = _mod.build_reason(level, 47.0, 53.0)
        found = _cyrillic(reason)
        assert not found, f"{level} offer carries a second language: {found!r}"


def test_hard_offer_stays_substantive():
    reason = _mod.build_reason("hard", 47.0, 53.0)
    assert "hard threshold reached" in reason, "hard-threshold text missing"
    assert "47%" in reason, "used percentage not interpolated"
    assert 'Do not offer "continue without compact"' in reason


def test_soft_offer_still_allows_continuing():
    """The soft threshold is an offer, not a gate: option 3 must survive."""
    reason = _mod.build_reason("soft", 40.0, 60.0)
    assert "continue without compact - keep working as is" in reason
    assert "60%" in reason, "remaining percentage not interpolated"
