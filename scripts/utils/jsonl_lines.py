#!/usr/bin/env python3
"""One JSONL reader, decoded a line at a time, for append-only ledgers.

Written 2026-09-01, after the SAME two defects were found in both readers of
`outputs/council/_verdicts.jsonl` -- `scripts/council-aggregate.py` and
`scripts/council-record-verdict.py`. Both spelled the read
``path.read_text(encoding="utf-8").splitlines()``, and that one expression
carried both:

1. **The whole file decoded at once, with no handler.** ``UnicodeDecodeError``
   is a ``ValueError``, so it matched neither reader's ``json.JSONDecodeError``
   clause and raised out of the caller. On an APPEND-ONLY ledger that nothing
   prunes, one non-UTF-8 byte is therefore permanent: every later run dies the
   same way until someone edits the file by hand.

2. **``str.splitlines()`` breaks on eight characters JSONL does not** -- U+000B,
   U+000C, U+001C, U+001D, U+001E, U+0085, U+2028, U+2029. A writer using
   ``json.dumps(..., ensure_ascii=False)`` puts three of those on the line raw,
   so a value pasted from a web page was shredded into fragments that no longer
   parsed, and the ``JSONDecodeError`` clause dropped every fragment in silence.
   MEASURED 2026-09-01: a ledger holding one valid verdict whose ``notes`` field
   contained a single U+2028 read back as ``{}``.

The reason there were two sites to fix is that the earlier fix to this same pair
of readers -- the ``isinstance(rec, dict)`` guard, whose own story is in
`tests/test_a_verdict_ledger_line_that_was_not_a_record.py` -- reached one
reader and not the other, twice running. So this is one implementation rather
than a third copy of the same four lines.

It also keeps `scripts/council-aggregate.py` free of ``read_bytes()``. That file
is in the frontmatter-reader registry of
`tests/test_ten_regexes_that_spelled_the_fence_themselves.py`, whose
`test_no_reader_in_the_set_can_receive_a_cr` refuses any byte-level read in
those files because a CR could then reach a frontmatter pattern. That guard is
right about the file: `parse_transcript` there still reads transcripts with
universal-newline ``read_text``, and must. Only the LEDGER read moved here, and
`bytes.splitlines()` already treats ``\\r\\n`` as one break.
"""
from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

__all__ = ["jsonl_lines"]


def jsonl_lines(path: Path) -> Iterator[str | None]:
    """Yield each non-empty line of `path`, or None for one that will not decode.

    The caller reads the whole file (these ledgers are small) and then decodes
    line by line, so a single bad line costs that line and nothing around it --
    the same policy the callers already apply to a line that will not parse as
    JSON.

    `None` rather than a silently dropped line, because a dropped record changes
    a count the operator reads as a measurement, and
    `.claude/rules/scope-claims.md` requires a narrowed read to say what it left
    out. Each caller names the file in its own vocabulary, so the message is
    theirs and only the signal is here.

    Splits with `bytes.splitlines()`, which breaks on ``\\n`` and ``\\r`` only.
    That is the whole point: the `str` method it replaces breaks on eight more
    characters, and JSONL says a record ends at a newline.

    Raises `OSError` if the file cannot be read at all. That is a different fact
    from "a line was corrupt" and the callers report it differently.
    """
    for raw_line in path.read_bytes().splitlines():
        try:
            line = raw_line.decode("utf-8").strip()
        except UnicodeDecodeError:
            yield None
            continue
        if line:
            yield line
