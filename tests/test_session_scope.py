"""Whose edits are they? The property that keeps one session out of another's.

`scripts/utils/session_scope.py` exists because on 2026-08-12 the Stop hook
blocked a turn over a deliberately-red TDD test that a PARALLEL session had
written one minute earlier. The lane failure was genuine; the attribution was
not, and `git` cannot supply attribution because a working tree has one status
and two authors.

The two properties worth more than the parsing are both about the honest answer
to "I do not know": an unreadable transcript must return None rather than an
empty set, and a caller given None must widen back to everything rather than
quietly check nothing.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

from scripts.utils.session_scope import (WRITING_TOOLS, files_written,  # noqa: E402
                                         narrow, narrow_with_scope)


def _transcript(tmp_path: Path, blocks: list[dict], name="t.jsonl") -> Path:
    """A transcript shaped the way Claude Code writes one."""
    path = tmp_path / name
    lines = [json.dumps({"message": {"content": [b]}}) for b in blocks]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _write(tool: str, target: str) -> dict:
    return {"type": "tool_use", "name": tool, "input": {"file_path": target}}


def test_a_written_file_is_attributed_to_the_session(tmp_path):
    path = _transcript(tmp_path, [_write("Edit", "/repo/scripts/a.py")])
    assert files_written(path) == {Path("/repo/scripts/a.py")}


def test_every_writing_tool_counts(tmp_path):
    blocks = [_write(tool, f"/repo/{tool}.py") for tool in sorted(WRITING_TOOLS)]
    written = files_written(_transcript(tmp_path, blocks))
    assert len(written) == len(WRITING_TOOLS), written


def test_the_writing_set_is_exactly_these_four_tools():
    """The assertion above cannot see a tool being dropped.

    It builds its transcript FROM `WRITING_TOOLS` and then compares against
    `len(WRITING_TOOLS)`, so removing a name removes both the input and the
    expectation and the count still matches. MEASURED 2026-09-01: with
    `NotebookEdit` deleted from the frozenset, `test_session_scope.py`,
    `test_session_scope_line_splitting.py`, `test_turn_check.py`,
    `test_a_scope_that_disowned_its_own_subagents_writes.py` and
    `test_a_transcript_it_could_not_read_answered_nothing_was_written.py` all
    stayed green.

    What that costs: a file written through NotebookEdit is attributed to
    nobody, so `scripts/turn-check.py` drops it as a parallel session's work and
    reports a clean pass over an unexamined edit. That is the 2026-08-12
    misattribution this module exists to prevent, arrived at silently.

    Pinned as a literal, and the non-writers pinned beside it, because the
    module docstring gives a REASON for each absence: Read, Glob and Grep are
    not authorship, and Bash carries a command rather than a path.
    """
    assert frozenset({"Write", "Edit", "MultiEdit", "NotebookEdit"}) == WRITING_TOOLS
    for not_a_writer in ("Read", "Glob", "Grep", "Bash", "Task", "WebFetch"):
        assert not_a_writer not in WRITING_TOOLS


def test_reading_a_file_is_not_writing_it(tmp_path):
    """The distinction the whole module turns on: this session read the parallel
    session's test, and reading is not authorship."""
    path = _transcript(tmp_path, [
        _write("Read", "/repo/tests/test_written_by_someone_else.py"),
        {"type": "tool_use", "name": "Grep", "input": {"pattern": "x"}},
        {"type": "tool_use", "name": "Bash", "input": {"command": "ls"}},
    ])
    assert files_written(path) == set()


def test_an_unreadable_transcript_answers_unknown_not_empty(tmp_path):
    """The failure that would silently disable the check.

    `set()` asserts the session wrote nothing, which narrows every caller to
    zero files and reports a clean pass over an unexamined tree. None says the
    question could not be answered, and callers widen on it.
    """
    assert files_written(tmp_path / "absent.jsonl") is None
    assert files_written(None) is None


def test_a_malformed_line_is_skipped_without_losing_the_rest(tmp_path):
    """A transcript is appended to live, so its last line can be half-written."""
    path = tmp_path / "t.jsonl"
    path.write_text(
        json.dumps({"message": {"content": [_write("Write", "/repo/good.py")]}})
        + "\n{ this is not json\n",
        encoding="utf-8",
    )
    assert files_written(path) == {Path("/repo/good.py")}


def test_a_file_path_that_is_not_a_string_is_skipped_rather_than_raising(tmp_path):
    """A transcript is JSON written by another process, so `file_path` can be
    any JSON type. `files_written` guards it with `isinstance(target, str)` and
    nothing reached that guard: MEASURED 2026-09-01, deleting the isinstance and
    leaving `if target:` kept `test_session_scope.py`,
    `test_session_scope_line_splitting.py`, `test_turn_check.py`,
    `test_a_scope_that_disowned_its_own_subagents_writes.py` and
    `test_a_transcript_it_could_not_read_answered_nothing_was_written.py` green.

    What the mutant costs: `Path(["/repo/a.py"])` raises TypeError out of
    `files_written`, out of `narrow`, and out of the Stop hook that called it.
    The good record on the same line still has to survive, or a single bad block
    would cost the whole transcript.
    """
    path = tmp_path / "t.jsonl"
    path.write_text(
        json.dumps({"message": {"content": [
            {"type": "tool_use", "name": "Edit", "input": {"file_path": ["/repo/a.py"]}},
            {"type": "tool_use", "name": "Edit", "input": {"file_path": 7}},
            {"type": "tool_use", "name": "Edit", "input": {"file_path": None}},
            _write("Edit", "/repo/real.py"),
        ]}}) + "\n",
        encoding="utf-8",
    )
    assert files_written(path) == {Path("/repo/real.py")}


# --- the subagent sidecar directory obeys the same "unknown is not empty" rule


def _session_with_a_subagent(tmp_path) -> tuple[Path, Path]:
    """A parent transcript plus one sidecar, in the layout Claude Code writes."""
    parent = _transcript(tmp_path, [_write("Edit", "/repo/parent.py")], name="sess.jsonl")
    sidecar_dir = tmp_path / "sess" / "subagents"
    sidecar_dir.mkdir(parents=True)
    (sidecar_dir / "agent-1.jsonl").write_text(
        json.dumps({"message": {"content": [_write("Write", "/repo/theirs.py")]}}) + "\n",
        encoding="utf-8")
    return parent, sidecar_dir


def test_a_readable_sidecar_directory_is_still_a_known_answer(tmp_path):
    """The anti-vacuity jaw for the test below. Without it, a
    `_subagent_transcripts` that answered None unconditionally would satisfy the
    unknown case and disable the whole subagent union.
    """
    parent, _ = _session_with_a_subagent(tmp_path)
    assert files_written(parent) == {Path("/repo/parent.py"), Path("/repo/theirs.py")}


def test_a_session_that_dispatched_no_agent_is_empty_not_unknown(tmp_path):
    """The ordinary case: no sidecar directory at all is not an unknown answer."""
    parent = _transcript(tmp_path, [_write("Edit", "/repo/parent.py")], name="sess.jsonl")
    assert files_written(parent) == {Path("/repo/parent.py")}


def test_a_sidecar_directory_that_cannot_be_listed_answers_unknown(tmp_path):
    """The gap `Path.glob` hid, and the reason the reader is `os.listdir` now.

    `Path.glob`'s selector catches `PermissionError` itself and yields nothing,
    so the `except OSError` beneath it was unreachable and an unlistable
    directory was indistinguishable from a session that dispatched no agent.

    MEASURED 2026-09-01 against the shipped reader, with the directory at mode
    000: `files_written` answered `{parent.py}` and
    `narrow_with_scope([parent.py, theirs.py], t)` answered
    `([parent.py], 1, True)` - the subagent's own file dropped as another
    author's, with the third value asserting scope WAS established.
    """
    import os

    parent, sidecar_dir = _session_with_a_subagent(tmp_path)
    os.chmod(sidecar_dir, 0o000)
    try:
        try:
            os.listdir(sidecar_dir)
        except PermissionError:
            pass
        else:
            pytest.skip("this process can list a mode-000 directory (root?), so "
                        "the unreadable case cannot be produced here")

        assert files_written(parent) is None, (
            "an unlistable sidecar directory read as 'this session dispatched "
            "no agent'")
        kept, dropped, known = narrow_with_scope(
            [Path("/repo/parent.py"), Path("/repo/theirs.py")], parent)
        assert (dropped, known) == (0, False), (
            "the caller was told scope was established over a directory the "
            f"reader could not list: kept={kept} dropped={dropped} known={known}")
    finally:
        os.chmod(sidecar_dir, 0o700)


def test_narrow_keeps_only_this_session_and_counts_what_it_dropped(tmp_path):
    mine = tmp_path / "mine.py"
    theirs = tmp_path / "theirs.py"
    for f in (mine, theirs):
        f.write_text("X = 1\n", encoding="utf-8")
    path = _transcript(tmp_path, [_write("Edit", str(mine))])

    kept, dropped = narrow([mine, theirs], path)
    assert kept == [mine]
    assert dropped == 1, "a dropped file that is not counted is a silent gap"


def test_narrow_fails_open_when_the_scope_is_unknown(tmp_path):
    """No transcript means pre-scope behaviour: check everything, drop nothing.

    A guard that goes quiet on missing metadata is worse than one that over-runs.
    """
    paths = [tmp_path / "a.py", tmp_path / "b.py"]
    assert narrow(paths, None) == (paths, 0)
    assert narrow(paths, tmp_path / "absent.jsonl") == (paths, 0)


# ---------------------------------------------------------------------------
# Five properties the module states and nothing measured
#
# MEASURED 2026-09-01 by mutation, against test_session_scope.py,
# test_session_scope_line_splitting.py, test_turn_check.py,
# test_a_scope_that_disowned_its_own_subagents_writes.py,
# test_a_transcript_it_could_not_read_answered_nothing_was_written.py,
# test_a_gate_that_reported_a_scope_it_never_assembled.py and
# test_checkpoint_session_scope.py together - all seven green under each of the
# mutants below.
# ---------------------------------------------------------------------------


def test_a_transcript_holding_a_bad_utf8_byte_is_read_not_dropped(tmp_path):
    """`errors="replace"` is load-bearing and `except OSError` cannot cover it.

    A `UnicodeDecodeError` is a `ValueError`. It is raised inside the READ, one
    line at a time, so it escapes the `except OSError` around the `open` as well
    as the loop below it, out of `files_written` and out of the Stop hook that
    called it. MEASURED 2026-09-01 with `errors="replace"` deleted: this file,
    the line-splitting file and five neighbours all stayed green.

    A transcript acquires a lone 0x80 the ordinary way - a tool result carrying
    a fragment of binary output, or a write interleaved mid-character.
    """
    path = tmp_path / "t.jsonl"
    good = json.dumps({"message": {"content": [_write("Write", "/repo/kept.py")]}})
    path.write_bytes(good.encode("utf-8") + b"\n"
                     + b'{"message": {"content": [{"x": "\x80\xff"}]}}\n')

    assert files_written(path) == {Path("/repo/kept.py")}


def test_only_a_tool_use_block_is_a_write(tmp_path):
    """The type check, which no case reached: every fixture above is a real
    `tool_use`. A block that merely CARRIES the shape - a name in
    `WRITING_TOOLS` and an `input.file_path` - is not an invocation, and
    counting one attributes a file to a session that only saw it echoed back.
    """
    path = tmp_path / "t.jsonl"
    path.write_text(json.dumps({"message": {"content": [
        {"type": "tool_result", "name": "Edit",
         "input": {"file_path": "/repo/echoed.py"}},
        {"type": "text", "name": "Write",
         "input": {"file_path": "/repo/quoted.py"}},
        _write("Edit", "/repo/real.py"),
    ]}}) + "\n", encoding="utf-8")

    assert files_written(path) == {Path("/repo/real.py")}


def test_a_message_that_is_not_a_mapping_is_skipped_rather_than_raising(tmp_path):
    """`entry["message"]` is JSON written by another process and can be any type.

    `_blocks` guards it with `isinstance(message, dict)` and nothing reached the
    guard. Without it, `"a string".get("content")` raises AttributeError out of
    `files_written`. The record on the next line still has to survive, or one
    odd entry costs the whole transcript.
    """
    path = tmp_path / "t.jsonl"
    path.write_text(
        json.dumps({"message": "a plain string"}) + "\n"
        + json.dumps({"message": ["a", "list"]}) + "\n"
        + json.dumps({"message": None}) + "\n"
        + json.dumps({"message": {"content": [_write("Edit", "/repo/real.py")]}}) + "\n",
        encoding="utf-8")

    assert files_written(path) == {Path("/repo/real.py")}


def test_the_drop_count_is_never_negative_over_a_generator(tmp_path):
    """`paths` was walked twice, so a generator answered a NEGATIVE drop count.

    The docstring on `narrow_with_scope` records the measurement and the fix;
    nothing held it. The signature says `paths`, and the drop count is the
    number a caller PRINTS to say what it did not check, so a negative one is a
    sentence about coverage that cannot be true.
    """
    mine = tmp_path / "mine.py"
    theirs = tmp_path / "theirs.py"
    for f in (mine, theirs):
        f.write_text("X = 1\n", encoding="utf-8")
    path = _transcript(tmp_path, [_write("Edit", str(mine))])

    kept, dropped, known = narrow_with_scope((p for p in (mine, theirs)), path)
    assert (kept, dropped, known) == ([mine], 1, True)
    assert narrow((p for p in (mine, theirs)), path) == ([mine], 1)


def test_a_candidate_spelled_differently_is_still_matched(tmp_path):
    """Both sides of the comparison resolve, and they have to stay symmetrical.

    The write set is resolved and the candidates are resolved, so a candidate
    written with a `.` or a `..` segment - the shape `ROOT / relative` produces
    for a caller that assembled it by hand - still matches. Dropping the resolve
    from EITHER side alone silently reclassifies the file as another author's,
    which is the misattribution the module exists to refuse.
    """
    mine = tmp_path / "mine.py"
    mine.write_text("X = 1\n", encoding="utf-8")
    path = _transcript(tmp_path, [_write("Edit", str(mine))])

    awkward = tmp_path / "sub" / ".." / "mine.py"
    kept, dropped, known = narrow_with_scope([awkward], path)
    assert (kept, dropped, known) == ([awkward], 0, True)
