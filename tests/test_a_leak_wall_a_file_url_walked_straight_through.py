#!/usr/bin/env python3
"""A private path written as a `file://` URL passed the public-repo leak wall.

`canopus_note._LEAK` is the wall between a private absolute path and a note this
PUBLIC repository commits. Its POSIX alternative refuses a slash preceded by
another slash, deliberately: that is what keeps `https://example.com/page` out
of the guard. The comment says so. What the comment did not say is that the
third slash of a `file://` URL is exactly the same shape, so the whole class
walked through.

MEASURED 2026-09-01 against the shipped pattern:

    text                                            _LEAK.search
    file:///home/operator/private/plan.md           None
    see [the plan](file:///home/operator/p.md)      None
    went ..\\up\\one                                 None
    /home/operator/private/plan.md                  matched
    C:\\Users\\operator                               matched

A markdown link to a local document is an ordinary way to write a path down, and
`write_note` would have committed either of the first two into this repository.
The third is the Windows spelling of the parent-directory escape, which the
comment above the pattern already claimed to cover while the pattern knew only
`../`.

The fix adds `file://` as its own alternative (a `file://` URL is never a public
web address, so it costs no false positive that `https://` does not already
avoid) and widens the parent escape to `(?<!\\.)\\.\\.[\\\\/]`, where the lookbehind
keeps an ellipsis followed by a backslash from reading as one.

Two mutation results are recorded here because they are why this file exists
rather than a line in the older one. Against
`tests/test_a_path_that_walked_past_the_guard_in_backticks.py` plus
`tests/test_canopus_note.py` (45 passed), deleting the `file://` alternative:
45 passed, SURVIVED. Deleting the parent-escape alternative: 45 passed,
SURVIVED, because every parent-escape case in that file (`../up/one`) is also
caught by the POSIX branch, which accepts a slash preceded by a dot. Only a
`../` with nothing after it distinguishes the two, and no case had that shape.
Both mutations fail here.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.utils.canopus_note import NoteError, _LEAK, write_note  # noqa: E402

VALID = {
    "value": "the slice earns its keep",
    "approval_sha": "abc1234",
    "contract": "tests/test_example.py",
    "plan_digest": "sha256:" + "a" * 64,
    "scrutinize_plan": "clean",
    "scrutinize_built": "clean",
    "undo": "revert the commit",
}


def _fields(slug: str, **over) -> dict:
    return {**VALID, "slug": slug, **over}


# ============================================================
# The file:// class
# ============================================================

@pytest.mark.parametrize("text", [
    "file:///home/operator/private/plan.md",
    "see [the plan](file:///home/operator/p.md)",
    "opened `file:///home/operator/x/y.md` yesterday",
    "file://server/share/plan.md",
    "FILE reference: file:///tmp/scratch/notes.md",
])
def test_a_file_url_is_a_path_and_is_refused(text):
    """Every one of these passed before. The note is committed to a public
    repository, so each was one commit from publishing an operator path."""
    assert _LEAK.search(text), f"leak guard missed: {text!r}"


# ============================================================
# The Windows spelling of the parent escape
# ============================================================

@pytest.mark.parametrize("text", [
    "went ..\\up\\one",
    "the contract sits at ..\\..\\plans\\x.md",
    "..\\sibling",
])
def test_a_backslash_parent_escape_is_refused(text):
    assert _LEAK.search(text), f"leak guard missed: {text!r}"


def test_a_bare_parent_escape_is_the_case_only_that_branch_catches():
    """The case that makes the parent-escape alternative load-bearing.

    Every other `../` case in the older file is ALSO matched by the POSIX
    branch, which accepts a slash preceded by a dot, so deleting the whole
    alternative changed no result there. A `../` with no segment after it has
    nothing for the POSIX tail to match, so it distinguishes them.
    """
    assert _LEAK.search("kept one level up in ../")
    assert _LEAK.search("it lives in ..\\ and nowhere else")


# ============================================================
# The other direction: ordinary prose and public URLs stay clean
# ============================================================

@pytest.mark.parametrize("text", [
    "see https://example.com/page for detail",
    "http://localhost:1234/api/list",
    "ftp://host/dir/file.txt",
    "a ratio 3/4 in prose",
    "and/or",
    "TCP/IP",
    "24/7 coverage",
    "a range of 1..5 inclusive",
    "the ellipsis ... and more",
    # An ellipsis immediately followed by a backslash. Without the `(?<!\.)`
    # lookbehind the widened parent escape fires on the last two dots of this.
    "profile the file...\\home",
    "versions 1.2..1.3",
    "the word profile is not a path",
])
def test_the_widening_refuses_no_ordinary_writing(text):
    """A guard that fires on `24/7` gets switched off, and a switched-off wall
    guards nothing. Both new alternatives have to be free of that."""
    assert not _LEAK.search(text), f"false positive on: {text!r}"


# ============================================================
# End to end through the public API
# ============================================================

def test_a_note_carrying_a_file_url_is_refused(tmp_path):
    with pytest.raises(NoteError, match="carries a path"):
        write_note(tmp_path, "demo",
                   _fields("demo", value="see file:///home/operator/p.md"))


def test_a_note_body_carrying_a_backslash_escape_is_refused(tmp_path):
    with pytest.raises(NoteError, match="carries a path"):
        write_note(tmp_path, "demo",
                   _fields("demo", body="the contract moved to ..\\plans\\x.md"))


def test_a_clean_note_still_writes(tmp_path):
    """The guard must not have become a wall."""
    assert write_note(tmp_path, "demo", _fields("demo")).exists()
