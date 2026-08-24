"""Shape guards for JSON and JSONL the daemon did not write.

Every file these guards protect is described by its own module as
pipeline-written and hand-editable. `json.JSONDecodeError` catches a file that
is not JSON. It does not catch a file that is valid JSON of the WRONG SHAPE,
and that is the case that reaches production: a bare `[]` where an object was
expected, a `"ts": null` in one log line, an `"undo": 1` typed instead of
`true`. Each one raises somewhere far from the read, and each takes a whole
endpoint down over a single bad line.

The three guards here were each written once, in one module, after one
incident, and then not applied to the eight siblings with the identical read.
The 2026-08-24 audit found all three classes still live:

- `list_conversations` and five sites in `sources/inbox.py` called `.get` on a
  `json.loads` result that had never been shape-checked.
- `sources/critical.py` coerced a non-string `ts` before sorting; the two inbox
  footers with the same sort did not.
- `sources/critical.py` read a tombstone by truthiness after `"undo": 1`
  resurrected an item; nine other readers still compared `is True`.

One home, so the next fix lands everywhere at once.
"""
from __future__ import annotations

from typing import Any


def as_mapping(value: Any) -> dict:
    """`value` when it is a dict, `{}` otherwise.

    Wrap every `json.loads` of a file the daemon does not own. `.get` on a list
    is an `AttributeError`, which no `except (json.JSONDecodeError, OSError)`
    catches, so it leaves the caller's guard intact and 500s the endpoint.
    """
    return value if isinstance(value, dict) else {}


def entry_ts(entry: dict, key: str = "ts") -> str:
    """A log entry's timestamp as a sortable string, whatever the log holds.

    `entry.get("ts", "")` returns the VALUE when the key is present, so a
    hand-edited `"ts": null` comes back as None and `sorted()` raises
    `'<' not supported between instances of 'NoneType' and 'str'`. One bad row
    then hides every good one.
    """
    value = entry.get(key)
    return value if isinstance(value, str) else ""


def is_undo(entry: dict) -> bool:
    """True when this log entry is a tombstone.

    Truthiness, not `is True`. A tombstone hand-edited to `"undo": 1` failed the
    identity test and was replayed as an ACTIVE entry, resurrecting an item the
    operator had unmarked. In the investor send log it was worse: the tombstone
    itself became a send mark with empty fields, so the firm rendered as sent
    with a blank date.
    """
    return bool(entry.get("undo"))
