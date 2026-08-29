#!/usr/bin/env python3
"""argparse `type=` callables that refuse a bad value before any work starts.

An argparse type raises `ArgumentTypeError`, and argparse turns that into a
usage message and exit 2. That is the cheapest place to stop a bad value: it
happens before authentication, before any network call, and before a paged
walk has spent its first request.

Why this exists. MEASURED 2026-08-29, `--limit` carried no floor in two paging
CLIs and both compute their page size as `min(PAGE_SIZE, limit - len(sofar))`,
which on the first iteration is just `limit`:

    google-contacts search --limit 0  -> pageSize=0   (People API allows 1-30)
    google-contacts search --limit -3 -> pageSize=-3
    gmail-send list --limit 0         -> maxResults=0

The People API answers `pageSize=0` with an HTTP 400, which the dispatcher
reports as "Bad request -- check your arguments". Accurate, but only by luck,
and only after the credentials were loaded and a request was sent. Gmail's
`maxResults=0` is undefined by the client: if the server substitutes a default
page, `fetch_drafts` returns MORE drafts than the caller asked for and reports
`complete=False`, which is a wrong answer rather than an error.
"""
from __future__ import annotations

import argparse


def positive_int(value: str) -> int:
    """A whole number of at least 1.

    Zero is refused, not clamped. "Fetch me zero items" is a caller mistake
    every time, and silently turning it into 1 hides the mistake in a result
    that looks deliberate.
    """
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        raise argparse.ArgumentTypeError(
            f"expected a whole number, got {value!r}") from None
    if parsed < 1:
        raise argparse.ArgumentTypeError(
            f"must be 1 or more, got {parsed}")
    return parsed
