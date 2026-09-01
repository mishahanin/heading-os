"""Four guards on the /email-intel deferred-commit seam that nothing bound.

`tests/test_email_intel_state_commit.py` proves the seam itself: `--json` writes
no state, `--commit-state` replays it, the 500-id cap holds. Underneath that
sits a set of type and shape guards, each written after a specific incident and
each carrying a comment saying so, and four of them had no case at all.

MEASURED 2026-09-01 by mutating `scripts/email-intelligence.py` and running every
test file in the repo that names it and touches this seam
(`test_email_intel_state_commit.py`, `test_a_mail_run_that_reports_what_it_missed.py`,
`test_a_digest_that_burned_the_mail_it_never_read.py`,
`test_a_mail_run_that_called_a_failed_analysis_complete.py`,
`test_four_bounds_that_nobody_was_told_about.py`, 179 tests):

    making `last_inbox_datetime` unconditional         179 passed
    deleting the non-dict run-object refusal           179 passed
    deleting the non-dict state_commit refusal         179 passed
    dropping `strict=True` from build_output's zip     179 passed

The first is the sharpest, because its TWIN is tested. The sibling line two
rows below guards `last_sent_datetime` behind `sent_count`, and
`test_commit_state_leaves_sent_stamp_alone_when_nothing_was_sent` pins it. The
inbox half of the identical pair had nothing, which is the same one-side-only
shape the rest of this repo keeps finding. An inbox stamp advanced by a run that
read no inbox mail is a cutoff moved past messages nobody looked at.

The two refusals matter because of where the input comes from. `--commit-state`
reads a FILE, so the shape is whatever is on disk. `main` catches
`ValueError / OSError / JSONDecodeError` and prints a clean "Commit failed";
`data.get(...)` on a list raises `AttributeError`, which walks straight past that
handler and out as a traceback. VERIFIED 2026-09-01: with the guards, both cases
raise `ValueError` naming the offending type.

No Exchange, no network, no send: nothing here constructs a mail client.

Run: .venv/bin/python -m pytest tests/test_a_commit_that_stamped_an_inbox_it_never_read.py -q
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_spec = importlib.util.spec_from_file_location(
    "email_intelligence_commit_shapes", ROOT / "scripts" / "email-intelligence.py"
)
ei = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ei)

CUTOFF = "2026-08-08T19:00:00+00:00"


def _payload(**over) -> dict:
    """A commit block in the shape `build_output` emits. Addresses invented."""
    base = {
        "message_ids": ["<a@example.invalid>"],
        "conversations": [{"id": "conv-1", "topic": "Northwind demo"}],
        "inbox_count": 0,
        "sent_count": 0,
        "noise_filtered": 0,
        "cutoff": CUTOFF,
    }
    base.update(over)
    return base


@pytest.fixture
def state(tmp_path):
    return ei.StateManager(path=tmp_path / "state.json")


# ============================================================
# 1. the guarded stamp whose twin was the only one tested
# ============================================================

def test_the_inbox_stamp_is_left_alone_when_no_inbox_mail_was_read(state):
    """The mirror of `test_commit_state_leaves_sent_stamp_alone_when_nothing_was_sent`.

    `last_inbox_datetime` is the cutoff the NEXT fetch starts from. Advancing it
    on a run that read nothing means the window skips forward over mail that was
    never fetched, and the only symptom is mail that quietly never arrives in a
    digest.
    """
    ei.commit_state(state, _payload(inbox_count=0))

    assert state.data["last_inbox_datetime"] is None


def test_the_inbox_stamp_does_advance_when_inbox_mail_was_read(state):
    """Vacuity guard: a stamp that never advances is not a stamp."""
    ei.commit_state(state, _payload(inbox_count=3))

    assert state.data["last_inbox_datetime"] == CUTOFF


def test_the_two_stamps_are_independent(state):
    """A sent-only run must move the sent stamp and not the inbox one, which is
    the case a shared `if payload.get("cutoff")` would silently break."""
    ei.commit_state(state, _payload(inbox_count=0, sent_count=2))

    assert state.data["last_sent_datetime"] == CUTOFF
    assert state.data["last_inbox_datetime"] is None


# ============================================================
# 2. the two refusals on a file the caller did not produce
# ============================================================

def test_a_run_file_holding_an_array_is_refused_as_a_value_error(tmp_path):
    """`main` catches ValueError and prints "Commit failed". An AttributeError
    from `.get` on a list is not caught anywhere and arrives as a traceback."""
    path = tmp_path / "run.json"
    path.write_text(json.dumps([{"state_commit": {}}]), encoding="utf-8")

    with pytest.raises(ValueError, match="not a run object"):
        ei.commit_state_from_file(
            path, state=ei.StateManager(path=tmp_path / "s.json"))


def test_a_state_commit_block_that_is_an_array_is_refused_the_same_way(tmp_path):
    """One level in, the same shape, the same handler. The comment on this line
    names the AttributeError explicitly; nothing exercised it."""
    path = tmp_path / "run.json"
    path.write_text(json.dumps({"state_commit": ["a", "b"]}), encoding="utf-8")

    with pytest.raises(ValueError, match="state_commit is a list"):
        ei.commit_state_from_file(
            path, state=ei.StateManager(path=tmp_path / "s.json"))


def test_neither_refusal_wrote_any_state(tmp_path):
    """A refusal that half-committed would be worse than the traceback it
    replaced. `commit_state` runs after both checks, so nothing should land."""
    path = tmp_path / "run.json"
    path.write_text(json.dumps({"state_commit": ["a"]}), encoding="utf-8")
    state_path = tmp_path / "s.json"

    with pytest.raises(ValueError):
        ei.commit_state_from_file(path, state=ei.StateManager(path=state_path))

    assert not state_path.exists(), "a refused commit still wrote state.json"


# ============================================================
# 3. the zip that must not silently drop the tail
# ============================================================

def _conversation(cid: str) -> dict:
    """A conversation carrying every key `build_output` reads.

    Complete on purpose. A thin stub raises `KeyError` on the FIRST pair and the
    run never reaches the point where a short `analyses` list runs out, so the
    test would pass on an exception that has nothing to do with the guard.
    Names and addresses are invented.
    """
    return {
        "id": cid,
        "topic": f"Northwind demo ({cid})",
        "direction": "incoming",
        "message_count": 1,
        "participants": ["dana.quill@example.invalid"],
        "latest_datetime": CUTOFF,
        "is_internal": False,
        "raw_emails": [{
            "message_id": f"<{cid}@example.invalid>",
            "sender_name": "Dana Quill",
            "sender_email": "dana.quill@example.invalid",
            "to": [{"email": "operator@example.invalid", "name": "Operator"}],
            "cc": [],
            "subject": "Northwind demo",
            "body_preview": "hello",
            "datetime": CUTOFF,
            "direction": "incoming",
        }],
    }


def test_a_short_analysis_list_stops_the_run_instead_of_dropping_threads():
    """`strict=True` is the whole guard. Without it a short `analyses` silently
    drops the trailing conversations: the run reports "N conversations
    processed" and the digest shows fewer, with nothing naming which are gone.
    """
    conversations = [_conversation("conv-1"), _conversation("conv-2")]

    with pytest.raises(ValueError):
        ei.build_output(conversations=conversations,
                        analyses=[{"priority": "P1", "summary": "s"}],
                        run_info={})


def test_the_matched_pair_of_that_same_fixture_does_build():
    """The control for the fixture above. Without this, the refusal test could
    be passing on a malformed conversation rather than on the zip.
    """
    out = ei.build_output(
        conversations=[_conversation("conv-1"), _conversation("conv-2")],
        analyses=[{"priority": "P1", "summary": "s"},
                  {"priority": "P2", "summary": "t"}],
        run_info={})

    assert [c["id"] for c in out["conversations"]] == ["conv-1", "conv-2"]


def test_a_long_analysis_list_is_refused_in_the_other_direction():
    """`strict=True` is symmetric, and a test on one side only would pass over a
    `zip` truncating from the other end."""
    with pytest.raises(ValueError):
        ei.build_output(conversations=[],
                        analyses=[{"priority": "P1", "summary": "s"}],
                        run_info={})


def test_matched_lists_still_build_an_output():
    """Vacuity guard: a `strict=True` that refused everything would pass both
    tests above and produce no digest at all."""
    out = ei.build_output(conversations=[], analyses=[], run_info={})

    assert out["conversations"] == []
    assert "state_commit" not in out


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
