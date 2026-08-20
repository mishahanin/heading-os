#!/usr/bin/env python3
"""A done marker written during a turn survives the Stop that ends it.

The Stop hook prints one command at every pause of an unattended run: end the
stretch with `scripts/checkpoint-paths.py --done "<line>"`. Measured on
2026-08-20 across two consecutive turns of a live session, that command did
nothing. The marker reached the state file and was gone by the time the hook
looked for it, and the hook continued the stretch both times while the CLI had
printed `done recorded`.

The cause is ordering in `unattended_turn`. The window is cleared FIRST, on the
grounds that a Stop whose `prompt_id` differs from the recorded
`unattended_turn_id` closes a turn the operator started, so any marker predating
it describes a plan he has since replaced. That reasoning is right about the
counter and wrong about the marker: the comparison is on turn IDENTITY, not on
age, so it cannot tell last night's marker from one written seconds earlier in
the very turn now ending. And the common case IS the operator's turn - the first
pause after any instruction he gives - so `--done` worked only from the second
consecutive continuation onward.

What separates the two is whether this hook has already ACTED on the marker.
`_pause_unattended` stamps `unattended_paused_at` when it does. A marker
carrying that stamp has had its effect and belongs to a finished stretch. One
without it has never been seen here, so it was written during the turn now
ending and describes the plan the operator gave at the start of it.

The retirement of a consumed marker is kept, rather than left entirely to
`.claude/hooks/unattended-resume.py`, which clears the window at the prompt
boundary where "stale" is knowable directly. That hook is the primary path; this
is the backstop for a session where it is not registered or where it failed.
"""
from __future__ import annotations

import contextlib
import json
import os
import subprocess  # nosec B404 - fixed argv, never shell=True
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

OFFER = ROOT / ".claude" / "hooks" / "checkpoint-offer.py"
SESSION = "3f21ac90-77bd-4d18-9a02-1c4e5b6d8e70"
SLUG = "3f21ac90-77bd-4d18-9a02-1c4e5b6d"

# The turn the operator's instruction opened, and the older one the previous
# continuation recorded. They differ, which is the whole point.
TURN = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
PRIOR_TURN = "99999999-8888-7777-6666-555555555555"


def _env() -> dict:
    env = dict(os.environ)
    for leak in ("CLAUDE_PROJECT_DIR", "WORKSPACE_ROOT", "CLAUDE_HANDOFF_AUTO"):
        env.pop(leak, None)
    env.update({
        "CLAUDE_HANDOFF_SOFT_THRESHOLD": "40",
        "CLAUDE_HANDOFF_HARD_THRESHOLD": "45",
        "CLAUDE_CODE_SESSION_ID": SESSION,
        "CLAUDE_HANDOFF_UNATTENDED_WAIT": "1",
        "CLAUDE_HANDOFF_UNATTENDED_POLL": "1",
        "CLAUDE_HANDOFF_UNATTENDED_MAX": "100",
        # A pause notifies. Blank targets keep this file off the network.
        "CHECKPOINT_TELEGRAM_TARGET": "",
        "OPS_RADAR_TELEGRAM_TARGET": "",
        "ODIN_CADENCE_TELEGRAM_TARGET": "",
    })
    return env


def _run(tmp: Path, **state_over) -> tuple[str, dict]:
    """Drive the real hook in a scratch root. Returns (stdout, state after)."""
    state = {
        "needs_compact_offer": True,
        "offer_level": "soft",
        "offer_bucket": 8,
        "current_bucket": 8,
        "last_offered_bucket": 0,
        "used_percentage": 41.0,
        "remaining_percentage": 59.0,
        "auto": False,
        "session_unattended": True,
    }
    state.update(state_over)
    state_path = tmp / ".claude" / "state" / f"checkpoint-{SLUG}.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state), encoding="utf-8")

    transcript = tmp / "transcript.jsonl"
    transcript.write_text("", encoding="utf-8")
    payload = {
        "session_id": SESSION,
        "transcript_path": str(transcript),
        "cwd": str(tmp),
        "prompt_id": TURN,
        "hook_event_name": "Stop",
        "stop_hook_active": False,
    }
    proc = subprocess.run(  # nosec B603 - fixed argv, no shell
        [sys.executable, str(OFFER)],
        input=json.dumps(payload, ensure_ascii=False),
        capture_output=True, text=True, env=_env(), cwd=str(tmp), timeout=120,
    )
    after = {}
    with contextlib.suppress(OSError, ValueError):
        after = json.loads(state_path.read_text(encoding="utf-8"))
    return proc.stdout, after


def _continued(stdout: str) -> bool:
    """The hook continues a stretch by printing a block decision, and only so."""
    if not stdout.strip():
        return False
    with contextlib.suppress(ValueError):
        return json.loads(stdout).get("decision") == "block"
    return False


# ------------------------------------------------------------------ the defect


def test_a_marker_written_this_turn_stops_the_stretch(tmp_path):
    """The regression. The marker is fresh, the turn is the operator's, and the
    hook must read it rather than clear it on the way past."""
    stdout, after = _run(
        tmp_path,
        unattended_turn_id=PRIOR_TURN,
        unattended_continuations=1,
        unattended_last_at="2026-08-20T09:00:00+00:00",
        unattended_done_at="2026-08-20T09:04:00+00:00",
        unattended_done_note="plan X: 7 of 7 items",
    )
    assert not _continued(stdout), (
        "the hook continued a stretch the assistant had already declared "
        "finished; `--done` is the only command it offers for ending one"
    )
    assert after.get("unattended_paused_at"), (
        "the pause left no record, so `--unattended status` cannot say the "
        "stretch stopped or why"
    )
    assert "plan X: 7 of 7 items" in (after.get("unattended_stop_reason") or ""), (
        "the recorded reason dropped the assistant's own note"
    )


def test_the_note_survives_the_clear_intact(tmp_path):
    """Preserving the timestamp without its note would pause with `no note
    given`, which reads as a marker nobody wrote."""
    _, after = _run(
        tmp_path,
        unattended_turn_id=PRIOR_TURN,
        unattended_done_at="2026-08-20T09:04:00+00:00",
        unattended_done_note="merge-contacts: 326 of 326 round-trip",
    )
    assert "326 of 326 round-trip" in (after.get("unattended_stop_reason") or "")


# ---------------------------------------------------------------- the backstop


def test_a_marker_this_hook_already_acted_on_is_retired(tmp_path):
    """`unattended_paused_at` is the proof the marker has had its effect. One
    that carries it describes a stretch that ended before the operator spoke, so
    his new instruction gets a clean window even when the UserPromptSubmit hook
    never ran."""
    stdout, after = _run(
        tmp_path,
        unattended_turn_id=PRIOR_TURN,
        unattended_continuations=40,
        unattended_last_at="2026-08-19T23:00:00+00:00",
        unattended_done_at="2026-08-19T23:05:00+00:00",
        unattended_done_note="last night's plan",
        unattended_paused_at="2026-08-19T23:06:00+00:00",
        unattended_stop_reason="the plan is finished: last night's plan",
    )
    assert _continued(stdout), (
        "last night's finished stretch is still stopping this morning's work"
    )
    assert "unattended_done_at" not in after
    assert "unattended_paused_at" not in after


def test_the_ceiling_still_resets_on_a_new_turn(tmp_path):
    """The counter bounds ONE uninterrupted stretch. Preserving the marker must
    not accidentally preserve a count half spent last night."""
    stdout, after = _run(
        tmp_path,
        unattended_turn_id=PRIOR_TURN,
        unattended_continuations=99,
        unattended_last_at="2026-08-19T23:00:00+00:00",
    )
    assert _continued(stdout)
    assert after.get("unattended_continuations") == 1, (
        "a ceiling carried over from the previous stretch cuts this one short"
    )
