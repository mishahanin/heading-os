#!/usr/bin/env python3
"""`_queue_pending` caught a failing OPEN and let a failing READ escape.

The function decides whether the operator has typed something the screen has not
shown yet. It runs on EVERY Stop, and it streams the session transcript, which
the harness is appending to at the same moment. The largest measured here is
88 MB.

Until 2026-09-02 the guard read:

    try:
        handle = path.open(...)
    except OSError:
        return True
    with handle:
        for line in handle:      # <- unguarded

`errors="replace"` covers decoding and nothing else. A read that starts fine can
still raise part way down: a truncation while the writer is mid-record, an I/O
error, a filesystem hiccup on a file that crosses the WSL2 to Windows boundary.
An unhandled raise inside a Stop hook is not an ordinary traceback; it is the
one place a crash costs the operator the turn.

The direction of the answer matters as much as catching it. Both failure paths
return True, meaning "he may have typed something", so the hook stands down and
lets him speak. The opposite default talks over an instruction already sent, and
this file pins the direction so a later edit cannot quietly invert it.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
OFFER = ROOT / ".claude" / "hooks" / "checkpoint-offer.py"
SESSION = "read-error-session"


@pytest.fixture(scope="module")
def mod():
    spec = importlib.util.spec_from_file_location("offer_read_guard_mod", OFFER)
    module = importlib.util.module_from_spec(spec)
    sys.modules["offer_read_guard_mod"] = module
    spec.loader.exec_module(module)
    return module


def _record(operation: str, content: str | None = None) -> str:
    entry = {"type": "queue-operation", "operation": operation,
             "sessionId": SESSION}
    if content is not None:
        entry["content"] = content
    return json.dumps(entry)


class _HandleThatDiesMidRead:
    """Yields `good` lines, then raises OSError, exactly like a truncated read."""

    def __init__(self, good: list[str]):
        self._good = good
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.closed = True
        return False

    def __iter__(self):
        for line in self._good:
            yield line
        raise OSError(5, "Input/output error")


class _PathThatOpensThenDies:
    def __init__(self, good: list[str]):
        self._good = good
        self.handle = _HandleThatDiesMidRead(good)

    def open(self, *args, **kwargs):
        return self.handle


class _PathThatCannotOpen:
    def open(self, *args, **kwargs):
        raise OSError(13, "Permission denied")


def test_a_read_that_dies_part_way_down_does_not_escape_the_hook(mod):
    """The founding case. Before the fix this raised OSError out of a Stop hook."""
    path = _PathThatOpensThenDies([_record("enqueue", "hello") + "\n"])

    answer = mod._queue_pending(path, SESSION)

    assert answer is True, (
        "a transcript that could not be finished must read as 'he may have "
        "typed something', so the hook stands down rather than talking over him")


def test_the_handle_is_still_closed_when_the_read_dies(mod):
    """The `with` must not be skipped by the new guard.

    Wrapping the block in a `try` that sits OUTSIDE the `with` is what keeps
    this true; wrapping only the `for` and returning from inside would too, but
    a later edit could move the `with` and leak a handle on every failed Stop.
    """
    path = _PathThatOpensThenDies([_record("enqueue", "hello") + "\n"])

    mod._queue_pending(path, SESSION)

    assert path.handle.closed, "the transcript handle leaked on the error path"


def test_a_failing_open_still_answers_the_same_way(mod):
    """The guard that already existed. Both paths must agree, or the answer
    depends on WHERE the filesystem failed, which the caller cannot know."""
    assert mod._queue_pending(_PathThatCannotOpen(), SESSION) is True


def test_the_guard_does_not_swallow_a_real_answer(mod, tmp_path):
    """The negative case.

    A `try` around a loop is the shape that turns a working counter into a
    constant. These two assertions fail if the guard ever starts eating the
    normal path, and they disagree with each other, so a stub returning one
    fixed value cannot satisfy both.
    """
    balanced = tmp_path / "balanced.jsonl"
    balanced.write_text(_record("enqueue", "hi") + "\n"
                        + _record("remove", "hi") + "\n", encoding="utf-8")
    assert mod._queue_pending(balanced, SESSION) is False

    waiting = tmp_path / "waiting.jsonl"
    waiting.write_text(_record("enqueue", "hi") + "\n", encoding="utf-8")
    assert mod._queue_pending(waiting, SESSION) is True


def test_lines_read_before_the_error_are_not_credited_as_a_complete_count(mod):
    """A partial read must not be reported as a finished one.

    Here the good lines balance to zero pending, so a guard that returned
    `pending > 0` over the truncated prefix would answer False and let the hook
    speak over the operator. It must answer True on the strength of the error
    alone.
    """
    path = _PathThatOpensThenDies([_record("enqueue", "hi") + "\n",
                                   _record("remove", "hi") + "\n"])

    assert mod._queue_pending(path, SESSION) is True, (
        "a truncated transcript that happens to balance is not a balanced "
        "transcript; the count is unfinished and must not be trusted")
