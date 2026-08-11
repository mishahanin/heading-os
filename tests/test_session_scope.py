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

from scripts.utils.session_scope import (WRITING_TOOLS, files_written,  # noqa: E402
                                         narrow)


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
