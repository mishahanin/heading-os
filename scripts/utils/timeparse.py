#!/usr/bin/env python3
"""One ISO-8601 timestamp parser for the whole engine — always tz-aware.

Usage:

    from scripts.utils.timeparse import parse_iso

    dt = parse_iso(rec.get("ts"))          # aware datetime, or None
    if dt is not None and dt >= cutoff:    # cutoff is aware; never a TypeError
        ...

Why this module exists (2026-08-20). Six files had each hand-rolled their own
`_parse_iso`, and two of the six — `bridge_daemon/sources/ops.py` and
`bridge_daemon/sources/inbox.py` — returned whatever `fromisoformat` gave them,
so an offset-less timestamp came back NAIVE while every cutoff they were
compared against was aware. Measured on the pre-fix tree: one `usage.jsonl` line
with ts `2026-08-19T10:00:00` made `ops.read_telemetry_summary()` raise
`TypeError: can't compare offset-naive and offset-aware datetimes`, and its
caller (`bridge_daemon/app.py`, `settings_ops`) has no guard, so the Settings
endpoint 500s. The same shape sat in `inbox.py`'s band sort against an aware
`_epoch`. Today's writers always emit `+00:00`, so both were one hand-edited or
externally-appended line away.

The convention this encodes is the workspace DTZ rule: a SERIALIZED timestamp
is UTC, so a stored timestamp that arrived without an offset is read as UTC
rather than as local time. That keeps whole comparison graphs aware and makes
the naive/aware `TypeError` unreachable by construction.

Deliberately NOT folded in here: the date-only readers
(`bridge_daemon/sources/library.py` and `.../tasks.py`, both `_parse_iso_date`)
return a `date`, not a `datetime`. Dates carry no tzinfo, so their comparison
graphs are already safe; merging them into this function would silently turn
every downstream date comparison into a datetime comparison. Leave them alone.
"""
from __future__ import annotations

from datetime import datetime, timezone

__all__ = ["parse_iso"]


def parse_iso(value) -> datetime | None:
    """Parse an ISO-8601 timestamp into an AWARE datetime; None if unparseable.

    A value with no UTC offset is read as UTC, per the DTZ convention for
    serialized timestamps. Anything unparseable — garbage, None, a non-string —
    returns None rather than raising, because every caller is a reader of
    append-only state it does not control and must skip a bad row, not die on it.
    """
    if not value or not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt
