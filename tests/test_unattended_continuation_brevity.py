#!/usr/bin/env python3
"""The standing rules go out once per window, not once per pause.

The Stop hook's continuation message is printed to the OPERATOR's transcript as
well as fed to the assistant, and it fires at every pause of an unattended run.
Three of its four instructions never change between one pause and the next; the
assistant read them at continuation 1 of the same window and they are still in
its context. Reprinting them buys nothing and costs him another screen of prose.
He asked twice - the template was cut from ~1,950 characters to 467 on
2026-08-19 and to 372 on 2026-08-20, when this repeat form was added at 155.

What the repeat keeps is what CHANGES: the counter, and the one command the
mechanism can hear. A stretch that cannot be ended is worse than a verbose one.

The exception is a compaction inside a window. That is the single event which
makes "you already read it" false, because the block message carrying the rules
is discarded with the rest of the pre-compaction context.
"""
from __future__ import annotations

import contextlib
import importlib.util
import json
import os
import subprocess  # nosec B404 - fixed argv, never shell=True
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

OFFER = ROOT / ".claude" / "hooks" / "checkpoint-offer.py"
SESSION = "8c765efb-81ea-4397-a80c-620d7b9fc4c3"
SLUG = "8c765efb-81ea-4397-a80c-620d7b9f"
TURN = "11111111-2222-3333-4444-555555555555"

# The three sentences that must appear exactly once per window.
STANDING = ("Never invent work", "Do not touch the unattended switch", "Decide alone")


def _hook():
    spec = importlib.util.spec_from_file_location("checkpoint_offer_brevity", OFFER)
    mod = importlib.util.module_from_spec(spec)
    with contextlib.suppress(SystemExit):
        spec.loader.exec_module(mod)
    return mod


def _env() -> dict:
    env = dict(os.environ)
    for leak in ("CLAUDE_PROJECT_DIR", "WORKSPACE_ROOT", "CLAUDE_HANDOFF_AUTO"):
        env.pop(leak, None)
    env.update({
        "CLAUDE_HANDOFF_SOFT_THRESHOLD": "40",
        "CLAUDE_HANDOFF_HARD_THRESHOLD": "45",
        "CLAUDE_CODE_SESSION_ID": SESSION,
        # 1s with a 1s poll still serves one poll; this file asserts on TEXT,
        # never on timing, so the 4s the contract file needs would be wasted.
        "CLAUDE_HANDOFF_UNATTENDED_WAIT": "1",
        "CLAUDE_HANDOFF_UNATTENDED_POLL": "1",
        "CLAUDE_HANDOFF_UNATTENDED_MAX": "100",
        "CHECKPOINT_TELEGRAM_TARGET": "",
        "OPS_RADAR_TELEGRAM_TARGET": "",
        "ODIN_CADENCE_TELEGRAM_TARGET": "",
    })
    return env


def _reason(tmp: Path, **state_over) -> str:
    """Drive the real hook in a scratch root and return its block message."""
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
    (tmp / ".claude" / "state").mkdir(parents=True, exist_ok=True)
    (tmp / ".claude" / "state" / f"checkpoint-{SLUG}.json").write_text(
        json.dumps(state), encoding="utf-8"
    )
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
    assert proc.stdout.strip(), f"the hook said nothing:\n{proc.stderr}"
    return json.loads(proc.stdout)["reason"]


# --------------------------------------------------------------- which form


def test_the_first_continuation_of_a_session_carries_the_standing_rules(tmp_path):
    reason = _reason(tmp_path)
    for sentence in STANDING:
        assert sentence in reason, f"the first pause dropped {sentence!r}"
    assert "--done" in reason


def test_a_later_window_in_the_same_session_does_not_reprint_the_rules(tmp_path):
    """The rules are session-scoped, not window-scoped.

    `--done` clears the window, so the stretch after it starts at continuation 1
    again. The operator works in short stretches ended with `--done`, which meant
    every single pause he saw was a "continuation 1" carrying the full four
    lines, and he never once reached the repeat form. He asked for the noise to
    stop on 2026-08-22, holding a paste of it.

    The assistant that read the rules in this session still holds them after
    `--done`; only a compaction takes them away, and `rebuilt` already covers
    that. So the flag lives outside `_WINDOW_KEYS` and survives the clear.
    """
    reason = _reason(
        tmp_path,
        unattended_rules_shown=True,
        unattended_last_at="2026-08-20T09:00:00+00:00",
    )
    for sentence in STANDING:
        assert sentence not in reason, (
            f"a new window reprinted {sentence!r}; the assistant read it earlier "
            f"in this same session and nothing removed it"
        )
    assert "--done" in reason, (
        "the repeat dropped the only command that can end a stretch"
    )


def test_the_first_pause_records_that_the_rules_were_shown(tmp_path):
    """Without the flag being written, every pause is the first pause."""
    _reason(tmp_path)
    state = json.loads(
        (tmp_path / ".claude" / "state" / f"checkpoint-{SLUG}.json").read_text(
            encoding="utf-8"
        )
    )
    assert state.get("unattended_rules_shown") is True


def test_the_second_continuation_drops_the_rules_and_keeps_the_command(tmp_path):
    """The window is NOT cleared here: `unattended_turn_id` matches the payload's
    `prompt_id`, which is what makes this the same turn rather than a new one."""
    reason = _reason(
        tmp_path,
        unattended_continuations=1,
        unattended_turn_id=TURN,
        unattended_last_at="2026-08-20T09:00:00+00:00",
        # Written by the pause that printed them. A fixture without it is a
        # session where they were never shown, and the full form is right there.
        unattended_rules_shown=True,
    )
    for sentence in STANDING:
        assert sentence not in reason, (
            f"the repeat form still reprints {sentence!r}; it was read at "
            f"continuation 1 of this same window"
        )
    assert "--done" in reason, (
        "the repeat dropped the only command that can end a stretch"
    )
    assert "continuation 2 of 100" in reason.lower()


def test_a_compaction_inside_the_window_puts_the_full_text_back(tmp_path):
    """The one moment "you already read it" stops being true."""
    reason = _reason(
        tmp_path,
        unattended_continuations=3,
        unattended_turn_id=TURN,
        unattended_last_at="2026-08-20T09:00:00+00:00",
        last_compact_at="2026-08-20T09:30:00+00:00",
    )
    for sentence in STANDING:
        assert sentence in reason, (
            f"after a compaction the assistant lost {sentence!r} with the rest "
            f"of its context, and the repeat form did not restore it"
        )


def test_a_compaction_before_the_window_does_not(tmp_path):
    """The negative half: an OLD compaction must not pin the full text forever."""
    reason = _reason(
        tmp_path,
        unattended_continuations=3,
        unattended_turn_id=TURN,
        unattended_last_at="2026-08-20T09:00:00+00:00",
        last_compact_at="2026-08-20T08:00:00+00:00",
        unattended_rules_shown=True,
    )
    assert "Never invent work" not in reason


# ------------------------------------------------------------ the templates


def test_the_repeat_form_stays_one_line():
    """A cap with a number in it, because prose creep is what this file catches.
    200 leaves room for a real edit and fails on a second sentence."""
    mod = _hook()
    rendered = mod.UNATTENDED_WRAPPER_REPEAT.format(used=62.0, done=4, maximum=100)
    assert len(rendered) < 200, f"the repeat grew to {len(rendered)} chars"
    assert len(rendered.splitlines()) == 1


def test_the_full_form_is_shorter_than_it_was():
    """467 characters on 2026-08-19, 372 after. The cap is the smaller number
    plus room to edit; the older test in test_checkpoint_autonomy_visibility.py
    still holds the 600 ceiling and its sentence list."""
    mod = _hook()
    rendered = mod.UNATTENDED_WRAPPER.format(used=62.0, wait=10, done=1, maximum=100)
    assert len(rendered) < 430, f"the continuation grew back to {len(rendered)} chars"


# -------------------------------------------------------- the rebuilt probe


def test_context_was_rebuilt_answers_yes_when_it_cannot_tell():
    """The two failure directions are not symmetric. Printing the full text when
    it was not needed costs four lines; withholding it can leave an assistant
    unable to name the command that ends the night."""
    mod = _hook()
    assert mod._context_was_rebuilt({"last_compact_at": "nonsense"}, "2026-08-20T09:00:00+00:00")
    assert mod._context_was_rebuilt({"last_compact_at": "2026-08-20T09:00:00+00:00"}, None)
    assert mod._context_was_rebuilt({"last_compact_at": "2026-08-20T09:00:00+00:00"}, "junk")
    # No compaction recorded at all is the one honest NO: nothing rebuilt the
    # context, so the rules the assistant read are still in it.
    assert mod._context_was_rebuilt({}, "2026-08-20T09:00:00+00:00") is False
