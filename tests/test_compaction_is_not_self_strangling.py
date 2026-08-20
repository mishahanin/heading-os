#!/usr/bin/env python3
"""A submitted compaction must end the Stop, and must not read as the operator.

`HA.submit_compact` does not compact anything. It queues the literal `/compact`
into the session's own input through HERDR, and the harness runs a queued prompt
when the CURRENT TURN ENDS. Two consequences follow, and both were live defects
until 2026-08-20.

**The block strangles the request.** Printing a `{"decision": "block"}` is what
stops a turn from ending. A Stop that submits the compaction and then blocks has
guaranteed its own request will never run, and the next Stop - on the next 5%
bucket - queues another one behind it. Measured on this session:
`compact_requests` held two entries, 07:41:02 at bucket 55 and 08:07:10 at
bucket 60, both to pane w39:p1, neither carrying an error, while
`compact_history` still ended at the previous day's `trigger=auto` boundary. The
mechanism could not compact itself in auto mode, by construction, and recorded
success twice while doing it.

**The request reads as the operator.** The harness records the queueing as an
ordinary `queue-operation` / `enqueue`, which is exactly the signal
`_queue_pending` and `_operator_spoke` use to tell that the operator pressed
Enter mid-turn. The two hook-submitted enqueues had no matching `remove` -
because the removal happens at the turn boundary the block was preventing - so
`_queue_pending` returned True permanently. The hook was reading its own request
as a message waiting from the operator, and would hand the turn back on every
pause because of it.

The two are one bug seen from both ends: the hook cannot tell its own voice from
his.
"""
from __future__ import annotations

import contextlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
HOOK = ROOT / ".claude" / "hooks" / "checkpoint-offer.py"

from scripts.utils import herdr_agent as HA  # noqa: E402

SESSION = "sess-compact-0001"


def _hook():
    spec = importlib.util.spec_from_file_location("checkpoint_offer_selfvoice", HOOK)
    mod = importlib.util.module_from_spec(spec)
    with contextlib.suppress(SystemExit):
        spec.loader.exec_module(mod)
    return mod


def _enqueue(content: str, session: str = SESSION) -> str:
    return json.dumps({
        "type": "queue-operation",
        "operation": "enqueue",
        "sessionId": session,
        "content": content,
    }, ensure_ascii=False)


# ------------------------------------------------ the hook's own voice


def test_our_own_compact_submission_is_not_counted_as_a_queued_message(tmp_path):
    mod = _hook()
    transcript = tmp_path / "t.jsonl"
    transcript.write_text(_enqueue(HA.COMPACT_COMMAND) + "\n", encoding="utf-8")
    assert mod._queue_pending(transcript, SESSION) is False, (
        "the hook counted its own /compact submission as a message from the "
        "operator; the enqueue never clears, so the miscount is permanent"
    )


def test_a_real_queued_message_is_still_counted(tmp_path):
    """The negative half. Suppressing our own voice must not deafen the hook."""
    mod = _hook()
    transcript = tmp_path / "t.jsonl"
    transcript.write_text(_enqueue("stop and look at this") + "\n", encoding="utf-8")
    assert mod._queue_pending(transcript, SESSION) is True


def test_the_delta_reader_ignores_our_voice_too(tmp_path):
    mod = _hook()
    assert mod._operator_spoke(_enqueue(HA.COMPACT_COMMAND) + "\n", SESSION) is False
    assert mod._operator_spoke(_enqueue("actually, wait") + "\n", SESSION) is True


def test_a_compact_typed_by_the_operator_is_indistinguishable_and_that_is_accepted():
    """Stated rather than pretended away.

    The suppression keys on the literal content, so an operator who types
    `/compact` himself mid-turn is also ignored. That costs him one grace period;
    the alternative - counting it - costs the mechanism its only path to a
    boundary. HERDR gives the hook no marker of its own, so there is nothing
    finer to key on today.
    """
    assert HA.COMPACT_COMMAND == "/compact"


# --------------------------------------- the Stop must end on a submission


def test_the_hook_returns_zero_when_it_submitted_a_compaction():
    """Source guard. Driving this end to end needs a live HERDR and a real turn
    boundary; what can be held here is that the decision exists and reads the
    submission result."""
    text = HOOK.read_text(encoding="utf-8")
    code = "\n".join(ln for ln in text.splitlines() if not ln.strip().startswith("#"))
    assert "submitted = _request_compaction(" in code, (
        "main() discards whether the compaction was submitted"
    )
    assert "if submitted:\n        return 0" in code, (
        "main() blocks after submitting, which prevents the turn boundary the "
        "queued /compact needs"
    )


def test_request_compaction_reports_its_outcome():
    """Every early return says False, the tail says True. A function that
    returned None on some paths would make `if submitted` silently false."""
    mod = _hook()
    import inspect

    source = inspect.getsource(mod._request_compaction)
    body = "\n".join(ln for ln in source.splitlines()
                     if not ln.strip().startswith("#"))
    assert "-> bool:" in body
    bare = [ln for ln in body.splitlines() if ln.strip() == "return"]
    assert not bare, f"{len(bare)} bare `return` left; each reads as None"
    assert body.rstrip().endswith("return True")


@pytest.mark.parametrize("used,auto,unattended", [(30.0, True, False)])
def test_it_returns_false_when_the_threshold_is_not_crossed(used, auto, unattended, tmp_path):
    """A behavioural check on the cheapest gate, so the contract above is not
    held only by its own shape."""
    mod = _hook()
    state = {"session_auto": auto, "session_unattended": unattended,
             "soft_threshold": 40, "hard_threshold": 45}
    assert mod._request_compaction(
        {"session_id": SESSION}, state, tmp_path / "s.json", tmp_path, used
    ) is False
