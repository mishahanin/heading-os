#!/usr/bin/env python3
"""The unattended switch: raise, lower, and the window between them.

`raise_unattended` / `lower_unattended` / `clear_unattended_window` /
`mark_unattended_done` are the four functions that decide whether a session
keeps working while the operator sleeps. They had no test naming them, inside a
module measuring 39% line coverage on 2026-08-20.

Two invariants carry the whole design, and both were already broken once in the
history the docstrings record:

  1. **Lower RESTORES, it does not unset.** `raise` remembers whether it was the
     reason `session_auto` became true, and what `session_auto` held before -
     including holding NOTHING, which is a distinct third state that defers to
     `CLAUDE_HANDOFF_AUTO`. The CLI used to write `session_auto = False` on the
     way down, pinning False over a workspace default the operator had set on
     purpose, and calling it "restore".

  2. **A new stretch remembers nothing of the last one.** Every key in
     `_WINDOW_KEYS` describes ONE uninterrupted run, not the switch. Only some
     of them were popped, so `--unattended status` reported this morning's run
     with last night's stop reason for a whole session.

Both are round-trip properties, so both are tested as round trips rather than by
asserting the individual keys a future refactor is free to rename.

The switch itself is the operator's: nothing here lowers `session_unattended`
except an explicit `lower_unattended`, and `mark_unattended_done` in particular
must not - it ends the work, not the mode.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.utils import checkpoint_paths as CP  # noqa: E402


# --------------------------------------------------- invariant 1: restore


@pytest.mark.parametrize(
    "prior,expected_after_lower",
    [
        pytest.param({}, {}, id="absent -> absent (keeps deferring to the env)"),
        pytest.param({"session_auto": False}, {"session_auto": False}, id="False -> False"),
        pytest.param({"session_auto": True}, {"session_auto": True}, id="True -> True"),
    ],
)
def test_raise_then_lower_restores_session_auto_exactly(prior, expected_after_lower):
    state = dict(prior)
    CP.raise_unattended(state)
    assert state["session_auto"] is True, "raise must switch auto on"
    assert state["session_unattended"] is True

    CP.lower_unattended(state)

    assert state["session_unattended"] is False
    got = {k: state[k] for k in ("session_auto",) if k in state}
    assert got == expected_after_lower, (
        f"session_auto was {prior.get('session_auto', '<absent>')!r} before the "
        f"raise and is {state.get('session_auto', '<absent>')!r} after the lower"
    )


def test_lower_leaves_no_bookkeeping_behind():
    """The two keys `raise` writes to make the restore possible are its own.
    Leaving either behind means the NEXT lower restores a stale prior."""
    state = {"session_auto": False}
    CP.raise_unattended(state)
    CP.lower_unattended(state)
    assert "unattended_raised_auto" not in state
    assert "unattended_prior_auto" not in state


def test_lower_without_a_raise_does_not_invent_a_prior():
    """A fuse can lower a mode the CLI never raised. That must not delete or
    fabricate `session_auto`."""
    state = {"session_auto": True, "session_unattended": True}
    CP.lower_unattended(state)
    assert state["session_auto"] is True, "lower unset an auto it never raised"
    assert state["session_unattended"] is False


def test_lowering_twice_is_safe():
    state = {}
    CP.raise_unattended(state)
    CP.lower_unattended(state)
    snapshot = {k: v for k, v in state.items() if k != "session_unattended_at"}
    CP.lower_unattended(state)
    assert {k: v for k, v in state.items() if k != "session_unattended_at"} == snapshot


# ---------------------------------------------- invariant 2: a clean window


def test_raising_clears_every_key_of_the_previous_stretch():
    """The 2026-08-19 defect: only some window keys were popped, so a status
    line reported the new run with the old run's reason."""
    state = dict.fromkeys(CP._WINDOW_KEYS, "left over from last night")
    state["session_unattended"] = False

    CP.raise_unattended(state)

    survivors = [k for k in CP._WINDOW_KEYS if k in state]
    assert not survivors, f"previous stretch survived a raise: {survivors}"


def test_clear_window_never_touches_the_switch():
    """Only the operator lowers the switch. Clearing a window is not that."""
    state = {"session_unattended": True, "unattended_continuations": 7,
             "unattended_stop_reason": "ceiling"}
    CP.clear_unattended_window(state)
    assert state["session_unattended"] is True
    assert "unattended_continuations" not in state
    assert "unattended_stop_reason" not in state


def test_raising_resets_the_continuation_counter():
    """Last night's count must not cut tonight short."""
    state = {"unattended_turn_id": "abc", "unattended_continuations": 40}
    CP.raise_unattended(state)
    assert "unattended_turn_id" not in state
    assert "unattended_continuations" not in state


def test_the_window_key_list_is_not_empty():
    """A list that names nothing clears nothing, and every test above would
    still pass."""
    assert len(CP._WINDOW_KEYS) >= 5
    assert "unattended_stop_reason" in CP._WINDOW_KEYS
    assert "unattended_continuations" in CP._WINDOW_KEYS


# ------------------------------------------------------------- done marker


def test_done_records_the_note_and_leaves_the_mode_alone():
    state = {"session_unattended": True, "session_auto": True}
    CP.mark_unattended_done(state, "  finished the migration  ")
    assert state["unattended_done_note"] == "finished the migration"
    assert state["unattended_done_at"]
    assert state["session_unattended"] is True, "done ended the mode, not the work"
    assert state["session_auto"] is True


@pytest.mark.parametrize("note", ["", "   ", None])
def test_done_never_stores_an_empty_note(note):
    """An empty marker reads as 'no marker' to whoever opens the state file."""
    state = {}
    CP.mark_unattended_done(state, note)
    assert state["unattended_done_note"] == "no note given"


def test_done_is_a_window_key_so_the_next_instruction_clears_it():
    """The operator speaking starts a new stretch, and a done marker describes a
    plan he has just replaced."""
    assert "unattended_done_at" in CP._WINDOW_KEYS
    assert "unattended_done_note" in CP._WINDOW_KEYS
    state = {"session_unattended": True}
    CP.mark_unattended_done(state, "done")
    CP.clear_unattended_window(state)
    assert "unattended_done_at" not in state
    assert "unattended_done_note" not in state
