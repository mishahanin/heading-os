#!/usr/bin/env python3
"""JSONL is split on newlines only. `str.splitlines()` splits on eight more.

`scripts/utils/session_scope.files_written()` read a session transcript with
`read_text().splitlines()`. That looks equivalent to iterating the file and is
not: `str.splitlines()` also breaks on U+000B, U+000C, U+001C, U+001D, U+001E,
U+0085, U+2028 and U+2029. A JSONL record carrying any of them inside a string
value was cut into fragments, neither of which parsed, so every `tool_use` block
on that record became invisible.

Not hypothetical. This workspace's largest transcript (88 MB) holds 11 U+2028,
9 U+2029 and 2 U+0085, and switching to file iteration recovered a write the old
reader had dropped. `files_written` is what `scripts/turn-check.py` uses to tell
this session's edits from a parallel session's, so a dropped write is a write
attributed to nobody and a turn check that silently skips it.

The streaming rewrite also took peak RSS on that file from 795 MB to 19 MB, but
that was the smaller half.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.utils.session_scope import files_written  # noqa: E402

# Every character `str.splitlines()` treats as a line break and a file handle
# does not. Pinned as data so a Python release that adds one is visible here.
# Written as backslash-u escapes, never as the characters themselves. U+2028
# and U+2029 are hidden characters under `.claude/rules/hidden-chars.md`, and
# `scripts/sanitize-text.py --scan` reported both on this line on 2026-09-01:
# a sanitiser pass over this file would have emptied two of the eight cases
# below into no-ops. `test_the_premise_holds...` does catch that, so the
# failure would be loud rather than silent, but the escape costs nothing and
# removes the trap.
EXTRA_BREAKS = ("\x0b", "\x0c", "\x1c", "\x1d", "\x1e", "\x85",
                "\u2028", "\u2029")

# Of those eight, only three can reach a transcript line intact.
# `json.dumps` escapes every C0 control (U+000B through U+001E) as a
# `\uXXXX` ASCII sequence, so `splitlines()` never sees the character and
# those five records survive the OLD reader too. U+0085, U+2028 and U+2029
# are written literally, and they are exactly the three the live 88 MB
# transcript contains: 2, 11 and 9 of them.
#
# Named rather than left implicit, because five of the eight parameter cases
# below pass against the broken reader as well. They document that JSON
# escaping protects those characters; they are not guards, and reading them
# as guards is how a suite comes to look stronger than it is.
LOAD_BEARING = ("\x85", "\u2028", "\u2029")


def _record(target: str, poison: str = "") -> str:
    return json.dumps({
        "type": "assistant",
        "message": {"content": [{
            "type": "tool_use",
            "name": "Write",
            "input": {"file_path": target, "content": f"before{poison}after"},
        }]},
    }, ensure_ascii=False)


@pytest.mark.parametrize("ch", EXTRA_BREAKS, ids=lambda c: f"U+{ord(c):04X}")
def test_a_record_carrying_a_soft_break_is_still_read(tmp_path, ch):
    transcript = tmp_path / "t.jsonl"
    transcript.write_text(_record("/w/poisoned.py", ch) + "\n", encoding="utf-8")

    found = files_written(transcript)

    assert found == {Path("/w/poisoned.py")}, (
        f"a record containing U+{ord(ch):04X} was lost; splitlines() cut it in two"
    )


def test_the_premise_holds_str_splitlines_really_does_break_on_these():
    """If a Python release stops splitting on one of these, this file's reason
    for existing has changed and the list should be re-derived rather than
    quietly kept."""
    for ch in EXTRA_BREAKS:
        assert len(f"a{ch}b".splitlines()) == 2, (
            f"U+{ord(ch):04X} no longer splits; re-derive EXTRA_BREAKS"
        )


def test_ordinary_records_are_unaffected(tmp_path):
    transcript = tmp_path / "t.jsonl"
    transcript.write_text(
        _record("/w/a.py") + "\n" + _record("/w/b.py") + "\n\n",
        encoding="utf-8")
    assert files_written(transcript) == {Path("/w/a.py"), Path("/w/b.py")}


def test_an_unreadable_transcript_is_unknown_not_empty(tmp_path):
    """`None` means "cannot tell" and `set()` asserts the session wrote nothing.
    A caller that reads the second as the first checks nothing and reports a
    clean pass."""
    assert files_written(tmp_path / "missing.jsonl") is None
    assert files_written(None) is None


def test_it_does_not_load_the_whole_file(tmp_path):
    """The memory half, held by shape rather than by a byte count: a reader that
    materialises the file is one `read_text` away from 795 MB again."""
    source = (ROOT / "scripts" / "utils" / "session_scope.py").read_text(encoding="utf-8")
    body = source[source.index("def files_written("):source.index("def current_transcript(")]
    # Comments stripped first. The block above this function EXPLAINS both
    # forbidden calls by name, so scanning the raw text matches the explanation
    # rather than the code - a detector that fires on its own documentation
    # teaches the next reader to weaken it.
    code = "\n".join(ln for ln in body.splitlines() if not ln.strip().startswith("#"))
    assert ".read_text(" not in code, "files_written materialises the transcript"
    assert ".splitlines()" not in code, "files_written splits with str.splitlines()"
    assert "for line in handle" in code, "files_written no longer streams"


def test_the_three_that_can_actually_reach_a_line_are_covered():
    """The guard on the guard: LOAD_BEARING must stay a real subset, and every
    one of its members must still survive a round trip through `json.dumps`
    unescaped. If a future json release escapes U+2028, that case stops being a
    risk and stops being a test worth counting."""
    assert set(LOAD_BEARING) <= set(EXTRA_BREAKS)
    for ch in LOAD_BEARING:
        assert ch in json.dumps({"x": f"a{ch}b"}, ensure_ascii=False), (
            f"U+{ord(ch):04X} is now escaped by json.dumps and can no longer "
            "reach a transcript line intact"
        )
    for ch in set(EXTRA_BREAKS) - set(LOAD_BEARING):
        assert ch not in json.dumps({"x": f"a{ch}b"}, ensure_ascii=False), (
            f"U+{ord(ch):04X} now survives json.dumps unescaped; it belongs in "
            "LOAD_BEARING"
        )


# ============================================================
# The Stop hook reads the same transcript, the same way
# ============================================================

OFFER_HOOK = ROOT / ".claude" / "hooks" / "checkpoint-offer.py"


def _offer():
    import contextlib
    import importlib.util

    spec = importlib.util.spec_from_file_location("checkpoint_offer_lines", OFFER_HOOK)
    mod = importlib.util.module_from_spec(spec)
    with contextlib.suppress(SystemExit):
        spec.loader.exec_module(mod)
    return mod


def _queue_op(operation: str, session: str, poison: str = "") -> str:
    return json.dumps({
        "type": "queue-operation",
        "operation": operation,
        "sessionId": session,
        "text": f"before{poison}after",
    }, ensure_ascii=False)


@pytest.mark.parametrize("ch", LOAD_BEARING, ids=lambda c: f"U+{ord(c):04X}")
def test_a_queued_message_is_seen_even_when_its_record_carries_a_soft_break(tmp_path, ch):
    """The consequential half.

    `_queue_pending` decides whether the operator has typed something the screen
    no longer shows - pressing Enter mid-turn queues the message and clears the
    input line. A record cut on U+2028 parses as neither half, the enqueue is
    never counted, and the hook continues over a message already waiting.
    """
    mod = _offer()
    session = "sess-0001"
    transcript = tmp_path / "t.jsonl"
    transcript.write_text(_queue_op("enqueue", session, ch) + "\n", encoding="utf-8")

    assert mod._queue_pending(transcript, session) is True, (
        f"an enqueue carrying U+{ord(ch):04X} was not counted"
    )


def test_the_queue_balance_still_works_on_ordinary_records(tmp_path):
    mod = _offer()
    session = "sess-0002"
    transcript = tmp_path / "t.jsonl"
    transcript.write_text(
        _queue_op("enqueue", session) + "\n" + _queue_op("dequeue", session) + "\n",
        encoding="utf-8")
    assert mod._queue_pending(transcript, session) is False


@pytest.mark.parametrize("ch", LOAD_BEARING, ids=lambda c: f"U+{ord(c):04X}")
def test_operator_text_in_a_delta_survives_a_soft_break(tmp_path, ch):
    """`_operator_spoke` reads a byte DELTA rather than the whole file, so it
    splits a string instead of iterating a handle - and `splitlines()` there had
    the same defect for the same reason."""
    mod = _offer()
    session = "sess-0003"
    delta = _queue_op("enqueue", session, ch) + "\n"
    assert mod._operator_spoke(delta, session) is True


def test_neither_reader_uses_splitlines():
    """Both readers, held by shape. Comments are stripped first: the blocks above
    them name `splitlines()` in the explanation."""
    text = OFFER_HOOK.read_text(encoding="utf-8")
    code = "\n".join(ln for ln in text.splitlines() if not ln.strip().startswith("#"))
    for fn in ("_queue_pending", "_operator_spoke"):
        start = code.index(f"def {fn}(")
        end = code.index("\ndef ", start + 1)
        body = code[start:end]
        assert ".splitlines()" not in body, (
            f"{fn} splits with str.splitlines(), which breaks JSONL records on "
            "U+0085 / U+2028 / U+2029"
        )
