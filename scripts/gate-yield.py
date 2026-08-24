#!/usr/bin/env python3
"""A2 — report what each gate has caught, and whether that can yet be judged.

    python scripts/gate-yield.py
    python scripts/gate-yield.py --json

Reads the A1 denial log and writes nothing. Every mechanism is judged over the
window of the source it is recorded in, because judging a young mechanism over an
old window is how something gets called dead before it has had a day to speak.

It flags. It never recommends a removal, and it cannot: `render` is held to a
forbidden-verb list by its own test. What happens to a flagged mechanism is the
operator's decision and nobody else's.

Tests: tests/test_a_spawn_that_reported_a_daemon_it_never_confirmed.py
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ENGINE_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ENGINE_ROOT))

from scripts.utils.gate_yield import (  # noqa: E402
    SOURCE_DENIALS,
    read_sources,
    render,
    summarise,
)
from scripts.utils.workspace import get_default_tz  # noqa: E402


def _earliest(rows) -> str:
    """The first timestamp a source carries, which is when it began recording.

    A source with no rows has no window rather than an infinite one: reporting
    "0 catches in 20000 days" from an epoch default is the confident-looking
    lie this whole report exists to avoid.
    """
    # Compared as MOMENTS, not as strings. A lexicographic sort of ISO-8601 is
    # chronological only while every stamp shares one offset and one precision:
    # "2026-01-02T00:00:00+05:00" is four hours EARLIER than
    # "2026-01-01T23:00:00+00:00" and sorts later, and a "Z" suffix sorts against
    # "+00:00" by character. The window start decides whether a mechanism is
    # judged "too young to tell" or "old enough, flag it" -- the exact
    # confident-looking misjudgment this report exists to avoid.
    #
    # An unparseable stamp keeps its string ordering rather than being dropped:
    # losing a row here would shorten the window and make a gate look younger
    # than it is, which fails in the direction that hides a finding.
    stamps = [str(r.get("ts")) for r in rows if r.get("ts")]
    if not stamps:
        return ""

    def _moment(s: str):
        try:
            parsed = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            return (1, s)          # unparseable sorts after every real moment
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return (0, parsed.timestamp())

    return min(stamps, key=_moment)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="gate-yield",
        description="What each gate has caught, and whether the window is long "
                    "enough to judge it.")
    # NOT "the working tree to report over". Every source this command has is
    # the workspace-global denial log, which `read_sources` documents as never
    # having been read from a caller's root -- so the flag could not change one
    # byte of the output, and its help text promised a scope it does not have.
    # Kept, because the lifecycle ledger it once selected may return; renamed to
    # what it is.
    parser.add_argument("--root", default=str(ENGINE_ROOT),
                        help="ACCEPTED AND INERT. Reserved for a future "
                             "root-scoped source; the only source today is the "
                             "workspace-global denial log, so passing this "
                             "changes nothing in the report")
    parser.add_argument("--json", dest="as_json", action="store_true",
                        help="the summary as JSON")
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    sources = read_sources(root)
    now = datetime.now(tz=get_default_tz()).isoformat()
    summary = summarise(
        denials=sources["denials"],
        since={SOURCE_DENIALS: _earliest(sources["denials"])},
        now=now)
    summary["missing_sources"] = sources["missing"]

    if args.as_json:
        print(json.dumps(summary, indent=2))
        return 0

    print(render(summary, now=now), end="")
    if sources["missing"]:
        # Named, never folded into a zero: an absent log and an empty one are
        # different facts, and only one of them says anything about a gate.
        print(f"\n  NOT READ: {', '.join(sources['missing'])} "
              f"(absent, which is not the same as empty)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
