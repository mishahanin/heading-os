"""Tests for the per-channel cap in .claude/skills/yt-pulse/scripts/pulse.py.

Covers cases listed in plans/2026-05-01-per-author-cap-yt-pulse.md
Success Criterion #7:
  - cap=3 with one channel having 5 videos (3 survive + rollup +2)
  - cap=3 with all channels under cap (no rollup)
  - cap=0 disabled (legacy uncapped behaviour)
  - cap=1 extreme case
  - empty input
Plus: channel_id preferred over channel name (Y2 fix), name-variant grouping
falls back gracefully, rollup attached to highest-scoring survivor.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


# Load pulse.py via importlib because its directory is not a Python package
WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
PULSE_PATH = WORKSPACE_ROOT / ".claude" / "skills" / "yt-pulse" / "scripts" / "pulse.py"
spec = importlib.util.spec_from_file_location("pulse_module", PULSE_PATH)
pulse = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pulse)


def _video(score: float, channel_name: str = "", channel_id: str = "") -> dict:
    """Build a synthetic video dict matching what pulse.py produces internally."""
    v = {"_engagement_score": score, "uploader": channel_name}
    if channel_id:
        v["channel_id"] = channel_id
    return v


def _sort_by_score(videos: list[dict]) -> list[dict]:
    return sorted(videos, key=lambda x: x.get("_engagement_score", 0), reverse=True)


# ---------- channel-key resolution (Y2) ----------


def test_channel_key_prefers_channel_id():
    v = _video(1.0, channel_name="Alice", channel_id="UC_alice123")
    assert pulse._channel_key(v) == "UC_alice123"


def test_channel_key_falls_back_to_normalised_name():
    v = _video(1.0, channel_name="  Alice's Channel  ")
    assert pulse._channel_key(v) == "alice's channel"


def test_channel_key_handles_missing_data():
    assert pulse._channel_key({}) == ""


# ---------- apply_per_channel_cap ----------


def test_cap_3_with_one_channel_5_videos_keeps_top_3_with_rollup():
    """cap=3, one channel has 5 videos -> 3 survive, rollup count = 2."""
    videos = _sort_by_score([
        _video(100, channel_id="A"),
        _video(90, channel_id="A"),
        _video(80, channel_id="A"),
        _video(70, channel_id="A"),
        _video(60, channel_id="A"),
        _video(50, channel_id="B"),
    ])
    survivors, rollup = pulse.apply_per_channel_cap(videos, cap=3)
    assert len(survivors) == 4
    a_count = sum(1 for v in survivors if v.get("channel_id") == "A")
    assert a_count == 3
    assert rollup.get("A") == 2

    # Rollup attached to highest-scoring survivor for channel A (score 100)
    a_survivors = [v for v in survivors if v.get("channel_id") == "A"]
    assert a_survivors[0].get("_engagement_score") == 100
    assert a_survivors[0].get("_more_from_channel_count") == 2

    # Other survivors from A do not carry the rollup count
    assert a_survivors[1].get("_more_from_channel_count", 0) == 0


def test_cap_3_when_all_channels_under_cap_no_rollup():
    videos = _sort_by_score([
        _video(100, channel_id="A"),
        _video(90, channel_id="A"),
        _video(80, channel_id="B"),
        _video(70, channel_id="C"),
    ])
    survivors, rollup = pulse.apply_per_channel_cap(videos, cap=3)
    assert len(survivors) == 4
    assert rollup == {}
    assert all(v.get("_more_from_channel_count", 0) == 0 for v in survivors)


def test_cap_0_disables_cap_returns_all_videos():
    """cap=0 disables (legacy uncapped behaviour)."""
    videos = _sort_by_score([
        _video(100, channel_id="A"),
        _video(90, channel_id="A"),
        _video(80, channel_id="A"),
        _video(70, channel_id="A"),
        _video(60, channel_id="A"),
    ])
    survivors, rollup = pulse.apply_per_channel_cap(videos, cap=0)
    assert len(survivors) == 5
    assert rollup == {}


def test_cap_1_extreme_case_keeps_one_per_channel():
    videos = _sort_by_score([
        _video(100, channel_id="A"),
        _video(90, channel_id="A"),
        _video(80, channel_id="B"),
        _video(70, channel_id="B"),
        _video(60, channel_id="C"),
    ])
    survivors, rollup = pulse.apply_per_channel_cap(videos, cap=1)
    assert len(survivors) == 3
    assert rollup == {"A": 1, "B": 1}
    # Verify the highest-scoring video per channel survives
    surviving_scores = {v.get("channel_id"): v["_engagement_score"] for v in survivors}
    assert surviving_scores["A"] == 100
    assert surviving_scores["B"] == 80
    assert surviving_scores["C"] == 60


def test_empty_input_returns_empty():
    survivors, rollup = pulse.apply_per_channel_cap([], cap=3)
    assert survivors == []
    assert rollup == {}


def test_channel_name_variants_fallback_when_no_channel_id():
    """Without channel_id, normalised name groups same-channel variants."""
    videos = _sort_by_score([
        _video(100, channel_name="MyChannel"),
        _video(90, channel_name="mychannel"),  # same channel, different case
        _video(80, channel_name="  MyChannel  "),  # whitespace variant
        _video(70, channel_name="Other"),
    ])
    survivors, rollup = pulse.apply_per_channel_cap(videos, cap=2)
    assert len(survivors) == 3  # 2 from MyChannel + 1 from Other
    assert rollup.get("mychannel") == 1


def test_clean_video_includes_more_from_channel_count():
    """clean_video output should include the rollup field (default 0 when absent)."""
    raw = {"id": "abc", "title": "Test", "uploader": "A",
           "_engagement_score": 50, "_more_from_channel_count": 3}
    cleaned = pulse.clean_video(raw)
    assert cleaned["more_from_channel_count"] == 3


def test_clean_video_default_more_from_channel_count_zero():
    raw = {"id": "abc", "title": "Test", "uploader": "A", "_engagement_score": 50}
    cleaned = pulse.clean_video(raw)
    assert cleaned["more_from_channel_count"] == 0


def test_cap_preserves_global_score_ordering_in_survivors():
    """After cap, surviving set should still be in score-descending order
    (input was sorted; cap walks through in order). Channel A appears 3 times
    so cap=2 drops the 3rd and 4th occurrences (scores 80 and 70)."""
    videos = _sort_by_score([
        _video(100, channel_id="A"),  # A=1, keep
        _video(95, channel_id="B"),   # B=1, keep
        _video(90, channel_id="A"),   # A=2, keep
        _video(85, channel_id="B"),   # B=2, keep
        _video(80, channel_id="A"),   # A=3, drop
        _video(75, channel_id="C"),   # C=1, keep
        _video(70, channel_id="A"),   # A=4, drop
    ])
    survivors, rollup = pulse.apply_per_channel_cap(videos, cap=2)
    scores = [v["_engagement_score"] for v in survivors]
    assert scores == [100, 95, 90, 85, 75]
    assert rollup.get("A") == 2
