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

**A session is its parent transcript PLUS its subagents' sidecars.** Claude Code
records a dispatched agent's tool calls in
`<transcript-dir>/<session-id>/subagents/agent-*.jsonl`, and the parent
transcript never contains them. Reading only the file the hook hands over
therefore attributes every subagent write to nobody. MEASURED 2026-08-30 on this
workspace: of 80 changed files, the parent transcript alone kept 43 and dropped
37 as another author's - and all 37 were written by three of that same session's
own subagents. Truly foreign: zero. The union over the parent and its 106
sidecars kept all 80. A guard silently skipping 46% of the changed set is the
`.claude/rules/scope-claims.md` defect exactly: a narrowed check reading as a
complete one.
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


def _blocks(line: str) -> tuple[bool, list]:
    """`(the line parsed as JSON, its content blocks)`.

    The two answers are separate because "no blocks" and "no JSON" are the same
    empty list and must not be. `files_written` needs the first flag to tell a
    transcript that recorded no writes from one it could not read at all.
    """
    try:
        entry = json.loads(line)
    except ValueError:
        return False, []
    if not isinstance(entry, dict):
        return False, []
    message = entry.get("message")
    if not isinstance(message, dict):
        return True, []
    content = message.get("content")
    return True, (content if isinstance(content, list) else [])


def files_written(transcript: Path | str | None) -> set[Path] | None:
    """Absolute paths this session wrote, or None when that cannot be told.

    None is the honest answer for a missing or unreadable transcript, and it is
    distinct from `set()`, which asserts the session wrote nothing.

    "This session" spans the parent transcript AND the sidecars its subagents
    wrote (see the module docstring). A session that dispatched no agent has no
    sidecar directory, which is the ordinary case and changes nothing here.

    Unknown propagates from either layer. A sidecar that cannot be read or holds
    no parseable JSON makes the write set unknowable, so the answer is None and
    the caller widens back to everything - the same rule the parent transcript
    already obeyed. A corrupt LINE is a different thing and keeps the existing
    policy unchanged: `_scan` skips it and still collects its siblings.
    """
    if not transcript:
        return None
    path = Path(transcript)
    found = _scan(path)
    if found is None:
        return None
    for sidecar in _subagent_transcripts(path):
        theirs = _scan(sidecar)
        if theirs is None:
            return None
        found |= theirs
    return found


# `_scan` and `_subagent_transcripts` sit BELOW their caller on purpose.
# `tests/test_session_scope_line_splitting.py` guards the memory and
# line-splitting properties by slicing this source between the two definitions
# that bracket them, so the streaming loop has to stay inside that region to
# keep being guarded. Hoisting either helper above its caller would leave that
# assertion measuring a slice with no loop in it, which passes nothing.
#
# Do not name either bracketing marker verbatim in this file's prose. The slice
# is found by a plain substring search, so a comment quoting the closing marker
# IS the closing marker and truncates the guarded region to nothing - measured
# here 2026-08-30, on the first draft of this very comment.


def _subagent_transcripts(transcript: Path) -> list[Path]:
    """This session's subagent sidecars, newest layout, sorted for determinism.

    Derived from the transcript path handed in, never from the environment or a
    fixed root: `<dir>/<session-id>.jsonl` implies `<dir>/<session-id>/subagents/`.
    That derivation IS the session scoping - a sidecar under a different session
    id lives under a different directory and is never reached, so widening the
    parent's view does not widen it to another author's work.

    A missing directory is the normal case for a session that dispatched no
    agent, and answers with no files rather than an error.
    """
    directory = transcript.parent / transcript.stem / "subagents"
    try:
        return sorted(directory.glob("agent-*.jsonl"))
    except OSError:
        return []


def _scan(path: Path) -> set[Path] | None:
    """One transcript file's writes, or None when that file could not be told.

    The whole reader, unchanged, factored out of `files_written` so the parent
    transcript and each subagent sidecar are read by ONE policy rather than two.
    A second copy is the one that stops being fixed.
    """
    # Streamed, never `read_text().splitlines()`, for TWO reasons - and the
    # second is a correctness bug, not a resource one.
    #
    # Memory: that shape held the whole file AND the list of lines it was split
    # into, so peak ran to roughly two copies. Measured 2026-08-20 on this
    # workspace's largest transcript, 88 MB: 795 MB peak RSS, against 19 MB
    # streamed. `checkpoint-precompact.py` calls this inside a 20-second
    # PreCompact budget, so time was never the problem - memory was, on a hook
    # that runs while the session is already at its ceiling.
    #
    # Correctness: `str.splitlines()` breaks on eight characters a file handle
    # does not - U+000B, U+000C, U+001C, U+001D, U+001E, U+0085, U+2028, U+2029.
    # A JSONL record carrying any of them inside a string was shredded into
    # fragments that no longer parsed, so every `tool_use` block on that record
    # was invisible. The same transcript holds 11 U+2028, 9 U+2029 and 2 U+0085,
    # and streaming recovered a write the old reader had silently dropped. A
    # missed write here is a write `turn-check.py` attributes to nobody.
    found: set[Path] = set()
    try:
        handle = path.open(encoding="utf-8", errors="replace")
    except OSError:
        return None
    # A MALFORMED transcript is the third case the docstring promises None for,
    # and until 2026-08-30 the code could not produce it: every line failed to
    # parse, `found` stayed empty, and the caller was handed `set()`. Measured
    # on a file holding "this is not json\n{nope\n\n": `files_written` answered
    # `set()` where the module's stated invariant says None, and `narrow` then
    # answered `([], 1)` - every candidate dropped as another author's, so the
    # caller checked NOTHING while believing scope was established. That is the
    # exact "I could not tell read as nothing was touched" failure this module
    # exists to refuse.
    #
    # The signal is whether ANY line parsed as JSON, not whether any write was
    # found: a real session that only read files has parseable lines and no
    # writes, and `set()` is the right, honest answer there.
    saw_content = False
    parsed_any = False
    with handle:
        for line in handle:
            if not line.strip():
                continue
            saw_content = True
            parsed, blocks = _blocks(line)
            parsed_any = parsed_any or parsed
            for block in blocks:
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
    if saw_content and not parsed_any:
        return None
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
    # Materialise ONCE. `paths` was walked twice: the comprehension below
    # consumed it, and `len(list(paths))` then measured the exhausted remainder.
    # Given a generator the drop count came back NEGATIVE (measured 2026-08-26:
    # a list argument answered `([a.py], 1)` and the identical contents as a
    # generator answered `([a.py], -1)`). The only caller today,
    # `scripts/turn-check.py`, passes a real list, so this was not reachable in
    # the tree; the signature says `paths`, not `list[Path]`, and the drop count
    # is what a caller prints to say what it did NOT check.
    items = list(paths)
    mine = files_written(transcript)
    if mine is None:
        return items, 0
    resolved = {p.resolve() for p in mine}
    kept = [p for p in items if Path(p).resolve() in resolved]
    return kept, len(items) - len(kept)
