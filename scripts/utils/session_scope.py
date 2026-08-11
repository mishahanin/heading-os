#!/usr/bin/env python3
"""Which files did THIS session write? The one answer, shared.

A workspace where two Claude Code sessions run against one checkout has one
working tree and two authors. `git status` cannot tell them apart, so any tool
that says "the edits made in this turn" while reading `git diff` is claiming
authorship it never established. On 2026-08-12 the Stop hook did exactly that:
it reported a deliberately-red TDD test written by a parallel session, one
minute old, as a break in the turn that had only read files. The reported lane
was real; the attribution was invented.

The session's own transcript is the only local record of who wrote what. Claude
Code hands `transcript_path` to every hook, and each write arrives there as a
`tool_use` block carrying `input.file_path`. That is the authorship signal.

Two properties this module holds on purpose:

**Unknown is not empty.** An unreadable, absent, or malformed transcript returns
None, never an empty set. A caller that treats "I could not tell" as "nothing
was touched" silently checks nothing and reports success, which is the failure
mode of a guard rather than a miss by one. Callers fail OPEN: no scope means
check everything, exactly as before this module existed.

**Only the writing tools count.** Read, Glob and Grep touch nothing. A file
edited through `Bash` (a heredoc, `sed -i`) carries no `file_path` and is
therefore invisible here, so a caller narrowing by this set can under-cover.
That is a real limit, not an oversight: this workspace's own standards send
edits through the dedicated tools, and a caller must SAY how much it narrowed
rather than quietly shrink. Under-reporting coverage you announce beats
over-claiming coverage you do not have.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

# Tools whose invocation means "this session changed that file on disk".
# Read/Glob/Grep are absent because they are not authorship, and Bash is absent
# because its input carries a command, not a path (see the module docstring).
WRITING_TOOLS = frozenset({"Write", "Edit", "MultiEdit", "NotebookEdit"})

# The environment variable Claude Code exports for the running session, used
# when a caller has no explicit path to hand us.
TRANSCRIPT_ENV = "CLAUDE_TRANSCRIPT_PATH"


def _blocks(line: str):
    """The content blocks of one transcript line, or nothing it cannot parse."""
    try:
        entry = json.loads(line)
    except ValueError:
        return []
    message = entry.get("message")
    if not isinstance(message, dict):
        return []
    content = message.get("content")
    return content if isinstance(content, list) else []


def files_written(transcript: Path | str | None) -> set[Path] | None:
    """Absolute paths this session wrote, or None when that cannot be told.

    None is the honest answer for a missing or unreadable transcript, and it is
    distinct from `set()`, which asserts the session wrote nothing.
    """
    if not transcript:
        return None
    path = Path(transcript)
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None

    found: set[Path] = set()
    for line in raw.splitlines():
        if not line.strip():
            continue
        for block in _blocks(line):
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            if block.get("name") not in WRITING_TOOLS:
                continue
            data = block.get("input")
            if not isinstance(data, dict):
                continue
            target = data.get("file_path")
            if isinstance(target, str) and target:
                found.add(Path(target))
    return found


def current_transcript() -> str | None:
    """The running session's transcript from the environment, if it exported one."""
    value = os.environ.get(TRANSCRIPT_ENV)
    return value or None


def narrow(paths, transcript: Path | str | None) -> tuple[list, int]:
    """`(paths this session wrote, how many were dropped as another author's)`.

    With no usable transcript every path is kept and the drop count is 0, so a
    caller degrades to its pre-scope behaviour instead of going quiet.
    """
    mine = files_written(transcript)
    if mine is None:
        return list(paths), 0
    resolved = {p.resolve() for p in mine}
    kept = [p for p in paths if Path(p).resolve() in resolved]
    return kept, len(list(paths)) - len(kept)
