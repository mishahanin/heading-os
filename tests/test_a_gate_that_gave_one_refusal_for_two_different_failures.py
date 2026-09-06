#!/usr/bin/env python3
"""Two unrelated failures shared one sentence, and it named neither.

The release gate reads the operator's words from two records the harness writes
for one turn: the full `type: "user"` / `promptSource: "typed"` text, and the
capped `type: "last-prompt"` copy that confirms where it came from. When it
cannot produce a prompt it refuses, which is right. Until this change it said
the same thing in two states that have nothing in common:

  * the transcript is absent, unreadable, or holds no prompt record at all --
    a machine problem;
  * both records exist but do not yet agree, because the harness has written the
    full one and not the capped one for THIS turn -- a timing state that clears
    by itself.

MEASURED 2026-09-06, and this is why it matters. Over 982 turns holding a tool
call, taken from the 25 largest transcripts on this machine, the turn's
`last-prompt` record is written AFTER its first `tool_use` record in 515 of them
(52.4%). So a commit issued as the first tool call of a turn lands in that
window about half the time, and the refusal it gets back describes an unreadable
file. Establishing what had really happened cost the previous yard a trip
through the reader and then through the raw JSONL of a live transcript. Two
sentences answer it in seconds.

WHAT DID NOT CHANGE, stated because a refusal that becomes friendlier is exactly
the shape that quietly becomes weaker: both states still refuse, and for the
same reason. A full typed record with nothing to confirm it is precisely what a
forged one looks like, and the transcript is writable from Bash. Nothing here
accepts a prompt that the previous version rejected; only the sentence differs.

Both directions:

* the two states now produce DIFFERENT refusals (they were identical);
* the unreadable case keeps the literal `cannot read`, which
  `test_an_unreadable_transcript_refuses` pins;
* a matching pair is still accepted, so the split did not close the normal path;
* `_last_operator_prompt` still returns `str | None`, because six assertions in
  two other files compare its result with `is None` and `==`.

Run: .venv/bin/python -m pytest \\
     tests/test_a_gate_that_gave_one_refusal_for_two_different_failures.py -q
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
DISPATCH = ROOT / ".claude" / "hooks" / "_dispatch.py"


@pytest.fixture(scope="module")
def gate():
    spec = importlib.util.spec_from_file_location("_dispatch_two_refusals",
                                                  DISPATCH)
    module = importlib.util.module_from_spec(spec)
    sys.modules["_dispatch_two_refusals"] = module
    spec.loader.exec_module(module)
    return module


def _transcript(tmp_path: Path, records: list[dict]) -> str:
    path = tmp_path / "session.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n",
                    encoding="utf-8")
    return str(path)


def _typed(text: str) -> dict:
    return {"type": "user", "promptSource": "typed",
            "message": {"role": "user", "content": text}}


def _last_prompt(text: str) -> dict:
    return {"type": "last-prompt",
            "lastPrompt": text if len(text) <= 200 else text[:200] + "…"}


def _payload(command: str, transcript: str | None) -> dict:
    return {"tool_name": "Bash", "tool_input": {"command": command},
            "transcript_path": transcript}


def _reason(decision) -> str:
    assert decision is not None, "the gate permitted a commit it cannot vouch for"
    assert decision["decision"] == "block"
    assert decision["_policy_deny"] is True
    return decision["reason"]


# The race as it actually arrives: the operator asked for the merge, the full
# record is on disk, and the capped copy still belongs to the previous turn.
THIS_TURN = "сливай фикс и удаляй ярд"
LAST_TURN = "прогони набор и покажи что осталось"


# ============================================================
# The two states, told apart
# ============================================================

def test_a_transcript_that_cannot_be_read_says_so(gate, tmp_path):
    for transcript in (None, "", str(tmp_path / "absent.jsonl")):
        reason = _reason(gate.check_release_gate(
            _payload("git commit -m x", transcript)))
        assert "cannot read" in reason, reason
        assert "do not yet agree" not in reason, (
            "an absent transcript was reported as a disagreement between two "
            "records that were never read")


def test_a_transcript_holding_no_prompt_record_is_the_same_failure(gate,
                                                                   tmp_path):
    """A file with records but no prompt in it is still "could not read it"."""
    path = _transcript(tmp_path, [{"type": "assistant"}, {"type": "attachment"}])
    reason = _reason(gate.check_release_gate(_payload("git commit -m x", path)))
    assert "cannot read" in reason, reason


def test_the_race_is_reported_as_a_race(gate, tmp_path):
    """The failing half. Against the previous version this said `cannot read`.

    The capped record for this turn has not been written yet, which is the
    state 515 of 982 measured turns are in at their first tool call.
    """
    path = _transcript(tmp_path, [_typed(THIS_TURN)])
    reason = _reason(gate.check_release_gate(_payload("git commit -m x", path)))

    assert "cannot read" not in reason, (
        "the timing state is still described as an unreadable file, which is "
        "the defect this file exists to remove")
    assert "do not yet agree" in reason, reason
    assert "no `last-prompt` record for this turn has been written yet" in reason


def test_a_capped_record_from_an_earlier_turn_is_named_as_one(gate, tmp_path):
    """The other half of the same state, and it reads differently on purpose.

    "nothing confirms it yet" and "what confirms it belongs to an earlier turn"
    are different observations, and a reader who is told the second one knows
    the transcript is intact.
    """
    path = _transcript(tmp_path, [_last_prompt(LAST_TURN), _typed(THIS_TURN)])
    reason = _reason(gate.check_release_gate(_payload("git commit -m x", path)))

    assert "cannot read" not in reason, reason
    assert "belongs to an earlier turn" in reason, reason


def test_the_two_refusals_are_not_the_same_text(gate, tmp_path):
    """The defect itself, asserted directly rather than through its symptoms."""
    unreadable = _reason(gate.check_release_gate(
        _payload("git commit -m x", str(tmp_path / "absent.jsonl"))))
    racing = _reason(gate.check_release_gate(
        _payload("git commit -m x", _transcript(tmp_path, [_typed(THIS_TURN)]))))
    assert unreadable != racing, (
        "one sentence still covers both failures, so the next reader pays the "
        "same trip through the raw transcript")


def test_the_race_refusal_carries_what_to_do_about_it(gate, tmp_path):
    """A wall that refuses without a next step is a wall people work around.

    The remedy is measured, not invented: in 357 of the 496 racing turns the
    record written immediately after the first `tool_use` is the tool RESULT,
    and the capped record follows it. So letting any tool call complete clears
    it MOST of the time.

    And the refusal has to say "most", because it is not always true. MEASURED
    2026-09-06 on this wall's own first live refusal: the operator's word sat in
    the typed record, several tool calls had already completed in that turn, and
    the two capped records written afterwards both still carried the previous
    prompt. A remedy stated without its limit is the same defect as a refusal
    stated without its cause, one level up, so the sentence carries both and
    names the fresh turn as the fallback.
    """
    path = _transcript(tmp_path, [_typed(THIS_TURN)])
    reason = _reason(gate.check_release_gate(_payload("git commit -m x", path)))
    assert "let a tool call complete" in reason, reason
    assert "It does not always" in reason, (
        "the remedy is stated as if it always works, and it does not")
    assert "a fresh turn" in reason, reason
    assert "Do not edit the transcript" in reason, reason


# ============================================================
# The wall itself is unchanged
# ============================================================

def test_a_matching_pair_is_still_accepted(gate, tmp_path):
    """The normal path, which no refusal may have swallowed."""
    path = _transcript(tmp_path, [_typed(THIS_TURN), _last_prompt(THIS_TURN)])
    assert gate.check_release_gate(_payload("git commit -m x", path)) is None, (
        "an authorised commit is now refused, so the split closed the door it "
        "was supposed to leave open")


def test_a_prompt_that_authorises_nothing_still_refuses(gate, tmp_path):
    """Neither new branch may be reachable by a prompt that simply said no."""
    path = _transcript(tmp_path, [_typed("не коммить, просто покажи диф"),
                                  _last_prompt("не коммить, просто покажи диф")])
    reason = _reason(gate.check_release_gate(_payload("git commit -m x", path)))
    assert "did not ask for" in reason, reason


def test_the_reader_still_returns_a_string_or_none(gate, tmp_path):
    """Six assertions in two other files compare this result with `is None`."""
    ok = _transcript(tmp_path, [_typed(THIS_TURN), _last_prompt(THIS_TURN)])
    assert gate._last_operator_prompt(ok) == THIS_TURN
    assert gate._last_operator_prompt(str(tmp_path / "absent.jsonl")) is None
    assert gate._last_operator_prompt(
        _transcript(tmp_path, [_typed(THIS_TURN)])) is None


# ============================================================
# The codes, asked of the resolver directly
# ============================================================

@pytest.mark.parametrize("typed,capped,expected_prompt,code_attr", [
    (THIS_TURN, THIS_TURN, THIS_TURN, "_PROMPT_OK"),
    (None, THIS_TURN, THIS_TURN, "_PROMPT_OK"),          # a pre-`promptSource` session
    (None, None, None, "_PROMPT_NO_RECORDS"),
    (THIS_TURN, None, None, "_PROMPT_UNCONFIRMED"),
    (THIS_TURN, LAST_TURN, None, "_PROMPT_MISMATCH"),
])
def test_every_state_has_its_own_code(gate, typed, capped, expected_prompt,
                                      code_attr):
    """One code per state, so the caller branches on a fact and not on a guess.

    The `(None, None)` row is the negative anchor between the two families: it
    is a file that was READ and held nothing, which belongs with the unreadable
    ones and not with the race.
    """
    prompt, code = gate._reconcile_detail(typed, capped)
    assert prompt == expected_prompt
    assert code == getattr(gate, code_attr), (typed, capped, code)


def test_the_codes_are_distinct_values(gate):
    """A floor: four aliases of one string would satisfy every row above."""
    codes = {gate._PROMPT_OK, gate._PROMPT_NO_TRANSCRIPT, gate._PROMPT_NO_RECORDS,
             gate._PROMPT_UNCONFIRMED, gate._PROMPT_MISMATCH}
    assert len(codes) == 5, codes


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-q"]))
