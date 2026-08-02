#!/usr/bin/env python3
"""A2 — report what each gate has caught, and whether that can yet be judged.

    python scripts/gate-yield.py
    python scripts/gate-yield.py --json

Reads two logs and writes nothing: the Canopus lifecycle ledger
(`.canopus/history.jsonl`) and the A1 denial log. Every mechanism gets its own
observation window, taken from the source it is recorded in, because the two
sources did not start on the same day and judging a young mechanism over an old
window is how something gets called dead before it has had a day to speak.

It flags. It never recommends a removal, and it cannot: `render` is held to a
forbidden-verb list by its own test. What happens to a flagged mechanism is the
operator's decision and nobody else's.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ENGINE_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ENGINE_ROOT))

from scripts.utils.gate_yield import (  # noqa: E402
    SOURCE_DENIALS,
    SOURCE_LIFECYCLE,
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
    stamps = sorted(str(r.get("ts")) for r in rows if r.get("ts"))
    return stamps[0] if stamps else ""


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="gate-yield",
        description="What each gate has caught, and whether the window is long "
                    "enough to judge it.")
    parser.add_argument("--root", default=str(ENGINE_ROOT),
                        help="working tree root (default: this script's own repository)")
    parser.add_argument("--json", dest="as_json", action="store_true",
                        help="the summary as JSON")
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    sources = read_sources(root)
    now = datetime.now(tz=get_default_tz()).isoformat()
    summary = summarise(
        ledger=sources["ledger"],
        denials=sources["denials"],
        since={SOURCE_LIFECYCLE: _earliest(sources["ledger"]),
               SOURCE_DENIALS: _earliest(sources["denials"])},
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
