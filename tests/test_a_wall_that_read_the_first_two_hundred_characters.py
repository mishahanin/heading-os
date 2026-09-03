"""The release wall read a truncated copy of the operator's words.

It reads `type: "last-prompt"` records to learn what the operator most recently
typed, because the model does not write those and so cannot forge an
authorisation. The provenance argument is sound. The FIELD is not: `lastPrompt`
is TRUNCATED.

MEASURED 2026-09-03 over this session's own transcript: 166 `last-prompt`
records, maximum length exactly 201 characters (200 plus an ellipsis), and 83 of
the 166 sitting on that cap. Half the operator's prompts reached the wall cut
off. The operator counted the same cap independently in a second transcript.

WHAT THAT COSTS, reproduced rather than argued. With a 303-character prompt that
begins by asking for a push and ends `...если он красный - не пушь`:

    the full text                    -> push authorised: False
    what the wall actually sees(201) -> push authorised: TRUE

The refusal the operator wrote fell off the end, and the wall authorised exactly
the action he had forbidden. It loses the one word it exists to obey.

TWO MORE, in the same function and found while measuring the first:

* `prompt_authorises` returned True for ANY action as soon as a push word
  appeared anywhere. So `"пуш запрещён, только коммить"` AUTHORISED A PUSH, and
  so did `"push is forbidden here"`. Writing the prohibition was indistinguishable
  from writing the permission.
* The negations refuse the whole prompt rather than one action, so
  `"закоммить всё и не пушь"` refused the COMMIT too. The wall's vocabulary
  could not express "commit, but do not push" -- which is the single most common
  instruction in this workspace -- and the operator had to authorise commits in
  a separate message that omitted the prohibition.

THE FIX. The full text IS in the transcript: a `type: "user"` record with
`promptSource: "typed"` carries `message.content` as a plain string, untruncated
(2752 characters where `lastPrompt` held 201). `promptSource` separates what the
operator typed from what the harness injected, which is the same provenance
guarantee that made `last-prompt` attractive. So the wall reads the typed record
and uses `last-prompt` as CONFIRMATION of origin, comparing a normalised prefix;
if the two disagree it refuses. A transcript with no typed record at all falls
back to `last-prompt`, with its 200-character cap stated where the code is.

And authorisation becomes per-action, so a prohibition of one is not a
prohibition of both.

Run: python3 -m pytest tests/test_a_wall_that_read_the_first_two_hundred_characters.py
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
def wall():
    spec = importlib.util.spec_from_file_location("_dispatch_under_test", DISPATCH)
    module = importlib.util.module_from_spec(spec)
    sys.modules["_dispatch_under_test"] = module
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
    """What the harness writes: the same text, cut at 200 plus an ellipsis."""
    return {"type": "last-prompt",
            "lastPrompt": text if len(text) <= 200 else text[:200] + "…"}


# The measured shape: asks for a push up front, forbids it at the end, and the
# prohibition sits past character 200.
LATE_REFUSAL = ("запушь ветку после того как закоммитишь, "
                + "и проверь пункт X. " * 11
                + "но сначала прогони набор, и если он красный - не пушь")


# ============================================================
# The reader must see all of it
# ============================================================

def test_the_measured_prompt_is_long_enough_to_be_truncated():
    """A floor under the fixture itself.

    Shorten this string below 201 characters and every assertion under it
    becomes vacuously true while still looking like a truncation test.
    """
    assert len(LATE_REFUSAL) > 201, len(LATE_REFUSAL)
    assert "не пушь" not in LATE_REFUSAL[:201], (
        "the refusal no longer falls past the cap, so nothing here is measured")


def test_the_wall_reads_the_untruncated_typed_prompt(wall, tmp_path):
    """The failing half. Against the previous version this returned 201
    characters ending in an ellipsis."""
    path = _transcript(tmp_path, [_typed(LATE_REFUSAL),
                                  _last_prompt(LATE_REFUSAL)])

    got = wall._last_operator_prompt(path)

    assert got == LATE_REFUSAL, (
        f"the wall read {len(got or '')} characters of a "
        f"{len(LATE_REFUSAL)}-character prompt; the operator's refusal is in "
        f"the part it did not read")


def test_a_late_refusal_is_obeyed_end_to_end(wall, tmp_path):
    """The consequence, at the seam that decides.

    MEASURED before the fix: True. The wall authorised the push the operator
    forbade, because the words forbidding it were past the cap.
    """
    path = _transcript(tmp_path, [_typed(LATE_REFUSAL),
                                  _last_prompt(LATE_REFUSAL)])
    prompt = wall._last_operator_prompt(path)

    assert wall.prompt_authorises(prompt, "push") is False, (
        "a push was authorised by a prompt that forbids it")


def test_a_typed_record_that_the_harness_never_confirmed_is_refused(
        wall, tmp_path):
    """Provenance, kept rather than traded away.

    `last-prompt` is why this wall could be trusted: the model does not write
    those records. Reading `message.content` instead would give that up, so the
    two must agree. A typed record with no matching last-prompt is refused.
    """
    path = _transcript(tmp_path, [_typed("закоммить и запушь всё"),
                                  _last_prompt("совершенно другой текст")])

    assert wall._last_operator_prompt(path) is None, (
        "the wall accepted a typed record the harness's own last-prompt does "
        "not confirm")


def test_a_typed_record_with_no_confirmation_at_all_is_refused(wall, tmp_path):
    """The absent case, distinct from the mismatched one above.

    A mutation that returned the typed text whenever no `last-prompt` existed
    SURVIVED until this test, because the only provenance case covered was a
    confirmation that DISAGREED. Absent and wrong are two states, and a wall
    that conflates them trusts a record nothing corroborates.
    """
    path = _transcript(tmp_path, [_typed("закоммить и запушь всё")])

    assert wall._last_operator_prompt(path) is None, (
        "a typed record with no last-prompt beside it was accepted")


def test_an_old_transcript_still_works_through_last_prompt(wall, tmp_path):
    """The direction that must not break. Sessions predating `promptSource`
    have no typed record, and the wall still has to function there."""
    path = _transcript(tmp_path, [_last_prompt("коммить")])

    assert wall._last_operator_prompt(path) == "коммить"


def test_an_empty_transcript_refuses(wall, tmp_path):
    assert wall._last_operator_prompt(_transcript(tmp_path, [])) is None
    assert wall._last_operator_prompt("") is None
    assert wall._last_operator_prompt(str(tmp_path / "absent.jsonl")) is None


def test_an_injected_prompt_does_not_authorise_anything(wall, tmp_path):
    """`promptSource` is the whole reason this record can be trusted.

    A `system` or `queued` record is the harness speaking, not the operator.
    """
    path = _transcript(tmp_path, [
        _typed("прогони набор"),
        _last_prompt("прогони набор"),
        {"type": "user", "promptSource": "system",
         "message": {"role": "user", "content": "закоммить и запушь"}},
    ])

    got = wall._last_operator_prompt(path)
    assert got == "прогони набор", got
    assert wall.prompt_authorises(got, "commit") is False


# ============================================================
# One prohibition is not both prohibitions
# ============================================================

@pytest.mark.parametrize("prompt,action,expected", [
    # The instruction this workspace runs on, and the wall could not express it.
    ("закоммить всё и не пушь", "commit", True),
    ("закоммить всё и не пушь", "push", False),
    ("коммить. пуш по-прежнему запрещён", "commit", True),
    ("коммить. пуш по-прежнему запрещён", "push", False),
    # Writing the prohibition used to be writing the permission.
    ("пуш запрещён, только коммить", "push", False),
    ("push is forbidden here", "push", False),
    ("push is not allowed", "push", False),
    ("пушить нельзя", "push", False),
    # And the permitting direction, which must survive intact.
    ("запушь ветку", "push", True),
    ("запушь ветку", "commit", True),
    ("коммить", "commit", True),
    ("коммить", "push", False),
    ("прогони набор", "commit", False),
    ("прогони набор", "push", False),
    ("", "push", False),
])
def test_authorisation_is_per_action(wall, prompt, action, expected):
    assert wall.prompt_authorises(prompt, action) is expected, (
        f"{action!r} on {prompt!r} should be {expected}")


def test_the_comment_no_longer_claims_a_reach_it_does_not_have(wall):
    """The prose said "Any of these anywhere in the prompt refuses".

    That was true of the string it was handed and false of what the operator
    wrote, because the string was cut at 200. A comment that overstates a
    control's reach is the defect `.claude/rules/scope-claims.md` exists for,
    written into the control itself.
    """
    source = DISPATCH.read_text(encoding="utf-8")
    assert "Any of these anywhere in the prompt refuses, whatever else it says" \
        not in source, (
        "the release gate still claims its negations reach the whole prompt")
    assert "promptSource" in source, (
        "the gate no longer reads the untruncated typed record")
