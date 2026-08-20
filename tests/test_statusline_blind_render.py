#!/usr/bin/env python3
"""A render that measured nothing must not decide anything about the offer.

`checkpoint-statusline.py` runs on every turn and is the only writer of the
three hysteresis keys the Stop hook reads: `needs_compact_offer`, `offer_level`,
`offer_bucket`. It derives them from the payload's `context_window`.

When that reading is absent the hook used to RAISE, so it wrote no state and a
pending offer survived to the next render. The malformed-payload hardening of
2026-08-20 removed the raise - correctly, because a status line that vanishes is
indistinguishable from a hook that has stopped running - but the payload then
fell through to the below-threshold branch, which sets `needs_compact_offer` to
False. A queued hard-threshold save was therefore dropped in silence, one render
before the Stop hook would have acted on it.

"I could not measure" is not "below the threshold". The rule is
`.claude/rules/scope-claims.md` § fail toward over-reporting: when the evidence
is unavailable, keep the wider state rather than the convenient one.

These tests drive the real hook as a subprocess against a sandboxed project and
data root, so they exercise the branch through the same path the harness does.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HOOK = ROOT / ".claude" / "hooks" / "checkpoint-statusline.py"

PENDING = {
    "needs_compact_offer": True,
    "offer_level": "hard",
    "offer_bucket": 45,
    "last_offered_bucket": 40,
    "used_percentage": 46.0,
}
OFFER_KEYS = ("needs_compact_offer", "offer_level", "offer_bucket")


def _run(tmp_path, payload: dict, pre: dict) -> dict:
    session = "probe-session-0000"
    project = tmp_path / "project"
    (project / ".claude" / "state").mkdir(parents=True, exist_ok=True)
    state = project / ".claude" / "state" / f"checkpoint-{session[:32]}.json"
    state.write_text(json.dumps(pre), encoding="utf-8")

    env = dict(os.environ,
               CLAUDE_CODE_SESSION_ID=session,
               HEADING_OS_DATA=str(tmp_path / "data"))
    payload = {"session_id": session, "cwd": str(project),
               "transcript_path": "", **payload}
    result = subprocess.run(
        [sys.executable, str(HOOK)], input=json.dumps(payload),
        capture_output=True, text=True, cwd=project, env=env, timeout=60)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip(), "the status line must always print something"
    return json.loads(state.read_text(encoding="utf-8"))


@pytest.mark.parametrize("payload,label", [
    ({}, "no context_window key at all"),
    ({"context_window": {}}, "an empty context_window"),
    ({"context_window": {"remaining_percentage": None}}, "a null reading"),
    ({"context_window": "not-a-mapping"}, "a context_window of the wrong type"),
])
def test_a_pending_offer_survives_a_render_that_measured_nothing(tmp_path, payload, label):
    after = _run(tmp_path, payload, dict(PENDING))
    kept = {k: after.get(k) for k in OFFER_KEYS}
    assert kept == {k: PENDING[k] for k in OFFER_KEYS}, (
        f"{label}: the render cleared a queued offer it could not measure"
    )


def test_a_readable_render_below_the_threshold_still_clears_it(tmp_path):
    """The negative half. A render that DID measure, and measured low, must
    still clear the offer - otherwise this fix would pin an offer forever."""
    after = _run(tmp_path, {"context_window": {"remaining_percentage": 95.0}}, dict(PENDING))
    assert after.get("needs_compact_offer") is False
    assert after.get("offer_level") is None


def test_a_readable_render_above_the_threshold_still_queues_one(tmp_path):
    """And the other negative half: measuring high must still raise an offer."""
    pre = dict(PENDING, needs_compact_offer=False, offer_level=None,
               offer_bucket=None, last_offered_bucket=0)
    after = _run(tmp_path, {"context_window": {"remaining_percentage": 8.0}}, pre)
    assert after.get("needs_compact_offer") is True
    assert after.get("offer_level") in ("soft", "hard")


MEASURED = ("used_percentage", "remaining_percentage", "current_bucket",
            "context_window_size", "context_input_tokens")


@pytest.mark.parametrize("payload,label", [
    ({}, "no context_window key at all"),
    ({"context_window": {}}, "an empty context_window"),
    ({"context_window": "not-a-mapping"}, "a context_window of the wrong type"),
])
def test_the_last_good_reading_survives_a_render_that_measured_nothing(
    tmp_path, payload, label
):
    """Not a display detail.

    `checkpoint-offer.py::_used_percentage` reads `used_percentage` out of this
    file and returns None on a null, so a blind render that stamps null leaves
    the next Stop with no reading and every threshold decision made blind.

    Observed live on 2026-08-20 in the compaction watch log: the value went
    51.0 -> null -> 52.0 inside three minutes, on a session sitting above the
    hard threshold.
    """
    pre = dict(PENDING, used_percentage=51.0, remaining_percentage=49.0,
               current_bucket=50, context_window_size=750000,
               context_input_tokens=382500)
    after = _run(tmp_path, payload, pre)
    kept = {k: after.get(k) for k in MEASURED}
    assert kept == {k: pre[k] for k in MEASURED}, (
        f"{label}: a render that measured nothing overwrote the last reading"
    )


def test_a_readable_render_does_replace_the_reading(tmp_path):
    """The negative half: a render that DID measure must update it."""
    pre = dict(PENDING, used_percentage=51.0, current_bucket=50)
    after = _run(tmp_path, {"context_window": {"remaining_percentage": 20.0}}, pre)
    assert after.get("used_percentage") == 80.0
    assert after.get("current_bucket") == 80
