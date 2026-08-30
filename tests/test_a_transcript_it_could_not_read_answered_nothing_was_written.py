#!/usr/bin/env python3
"""`session_scope` exists to keep "I could not tell" from becoming "nothing".

Its module docstring states the invariant in one line: "**Unknown is not
empty.** An unreadable, absent, or malformed transcript returns None, never an
empty set." The code could produce None for absent and unreadable, and could not
produce it for MALFORMED. A transcript that opened fine and contained no
parseable JSON flowed through `_blocks` to `[]` on every line and `files_written`
returned the empty `found` set.

Measured 2026-08-30 on a file holding `this is not json\\n{nope\\n\\n`:
`files_written` answered `set()` where the invariant says None, and `narrow`
then answered `([], 1)` - every candidate dropped as another author's, so a
caller checked NOTHING while believing scope was established. That is the guard
failure the module was written to refuse, reproduced inside the module.

The signal is whether ANY line parsed as JSON, not whether any write was found:
a real session that only read files has parseable lines and no writes, and
`set()` is the correct, honest answer there.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.utils.session_scope import files_written, narrow  # noqa: E402


def _entry(tool, file_path):
    return json.dumps({"message": {"content": [
        {"type": "tool_use", "name": tool, "input": {"file_path": file_path}}]}})


def test_a_malformed_transcript_is_unknown_not_empty(tmp_path):
    """The measured case."""
    t = tmp_path / "t.jsonl"
    t.write_text("this is not json\n{nope\n\n", encoding="utf-8")
    assert files_written(t) is None


def test_narrow_fails_open_over_a_malformed_transcript(tmp_path):
    """The consequence: a caller must degrade to checking everything, not nothing."""
    t = tmp_path / "t.jsonl"
    t.write_text("this is not json\n{nope\n\n", encoding="utf-8")
    candidates = [tmp_path / "a.py", tmp_path / "b.py"]
    assert narrow(candidates, t) == (candidates, 0)


def test_an_absent_transcript_is_still_unknown(tmp_path):
    assert files_written(tmp_path / "never-written.jsonl") is None


def test_a_read_only_session_is_empty_not_unknown(tmp_path):
    """The distinction the flag exists for: parseable lines, no writes."""
    t = tmp_path / "t.jsonl"
    t.write_text(json.dumps({"message": {"content": [
        {"type": "tool_use", "name": "Read",
         "input": {"file_path": str(tmp_path / "x")}}]}}) + "\n", encoding="utf-8")
    assert files_written(t) == set()


def test_an_empty_transcript_is_empty_not_unknown(tmp_path):
    """A file with no content lines has nothing to fail to parse."""
    t = tmp_path / "t.jsonl"
    t.write_text("\n\n  \n", encoding="utf-8")
    assert files_written(t) == set()


def test_a_transcript_with_one_shredded_line_still_reports_its_writes(tmp_path):
    """Partial corruption must not throw away the writes that DID parse."""
    t = tmp_path / "t.jsonl"
    a = tmp_path / "a.py"
    t.write_text(_entry("Write", str(a)) + "\ngarbage {\n", encoding="utf-8")
    assert files_written(t) == {a}


def test_the_writing_tools_are_still_the_only_ones_that_count(tmp_path):
    """The control: without a non-empty positive corpus the above proves little."""
    t = tmp_path / "t.jsonl"
    a, b, c = tmp_path / "a.py", tmp_path / "b.py", tmp_path / "c.py"
    t.write_text("\n".join([
        _entry("Write", str(a)),
        _entry("Edit", str(b)),
        _entry("Grep", str(c)),
    ]) + "\n", encoding="utf-8")
    assert files_written(t) == {a, b}


def test_narrow_still_drops_another_authors_file(tmp_path):
    """The negative case for narrow: fail-open must not become always-open."""
    t = tmp_path / "t.jsonl"
    mine = tmp_path / "mine.py"
    mine.write_text("x", encoding="utf-8")
    theirs = tmp_path / "theirs.py"
    theirs.write_text("x", encoding="utf-8")
    t.write_text(_entry("Write", str(mine)) + "\n", encoding="utf-8")
    assert narrow([mine, theirs], t) == ([mine], 1)


@pytest.mark.parametrize("body", ["null\n", "42\n", '"a string"\n', "[]\n"])
def test_valid_json_that_is_not_a_transcript_record_is_unknown(tmp_path, body):
    """A JSON scalar or array is not a transcript; it must not read as empty."""
    t = tmp_path / "t.jsonl"
    t.write_text(body, encoding="utf-8")
    assert files_written(t) is None
