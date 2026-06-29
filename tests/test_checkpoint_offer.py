"""Regression: checkpoint-offer reason must not duplicate the options block (RU+EN once each).

Bug: REASON_WRAPPER embedded the already-bilingual {body} twice (once in its RU
section, once in its EN section), so the /checkpoint + /compact options rendered
four times instead of two. The fix gives each language section its own
single-language body.
"""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "checkpoint_offer", str(ROOT / ".claude" / "hooks" / "checkpoint-offer.py")
)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)


def test_hard_offer_options_appear_once_per_language():
    """Each option (/checkpoint, /compact) appears exactly twice: once RU, once EN."""
    reason = _mod.build_reason("hard", 47.0, 53.0)
    assert reason.count("`/checkpoint`") == 2, (
        f"`/checkpoint` should appear twice (RU+EN), got {reason.count('`/checkpoint`')}:\n{reason}"
    )
    assert reason.count("`/compact`") == 2, (
        f"`/compact` should appear twice (RU+EN), got {reason.count('`/compact`')}"
    )


def test_soft_offer_options_appear_once_per_language():
    reason = _mod.build_reason("soft", 40.0, 60.0)
    assert reason.count("`/checkpoint`") == 2, (
        f"`/checkpoint` should appear twice (RU+EN), got {reason.count('`/checkpoint`')}"
    )
    assert reason.count("`/compact`") == 2


def test_hard_offer_still_bilingual_and_substantive():
    """The fix must keep both languages and the hard-threshold wording."""
    reason = _mod.build_reason("hard", 47.0, 53.0)
    assert "жёсткий порог" in reason, "RU hard-threshold text missing"
    assert "hard threshold reached" in reason, "EN hard-threshold text missing"
    assert "47%" in reason, "used percentage not interpolated"
