#!/usr/bin/env python3
"""Record one CEO verdict on a /council consultation.

Track C invocation: after every /council run the model asks the CEO which
answer landed best, then calls this script ONCE to append the verdict.
The CEO never opens a file - this is the only verdict-writing path.

Verdicts are appended to outputs/operations/council/_verdicts.jsonl
(last-write-wins per verdict_id so the CEO can revise by recording again).
council-aggregate.py reads this JSONL and renders the aggregate file.

Usage:
  python scripts/council-record-verdict.py \\
    --id 2026-05-22_council_151429_always-on-assistant \\
    --choice gemini \\
    --notes "more concrete sequencing, surfaced Series B timing risk first"

Choice values: claude | gemini | grok | kimi | mix | reject
- claude / gemini / grok / kimi: that single model's answer landed best
- kimi: Kimi's answer landed best
- mix: took useful pieces from multiple; no single winner
- reject: none of the answers moved the decision; used something else

Exit codes:
  0 ok (verdict appended; tally printed to stdout)
  2 argument error
  3 transcript file for --id not found (verdict still written; warning)

Tests: tests/test_a_closing_fence_that_only_half_read_crlf.py
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WORKSPACE_ROOT))

from scripts.utils.colors import GREEN, RESET, YELLOW  # noqa: E402
from scripts.utils.jsonl_lines import jsonl_lines  # noqa: E402
from scripts.utils.workspace import get_outputs_dir  # noqa: E402

def council_dir() -> Path:
    """Resolved at call time, never at import.

    `get_outputs_dir()` reads `HEADING_OS_DATA` on every call, so it follows
    the environment for a caller that asks after the environment moved. As a
    module-level constant it asked once, during its own import, and stored the
    answer, so a test that imported this module and then repointed the root
    still got the operator's real overlay. The `mkdir` below is not among the
    primitives `tests/conftest.py` wraps, so a stray directory in that overlay
    drew no refusal.
    """
    return get_outputs_dir() / "operations" / "council"


def verdicts_path() -> Path:
    return council_dir() / "_verdicts.jsonl"
# Ordered, so the tally below reads the same way every time AND is derived from
# the same set argparse validates against. A second hand-written tuple lived in
# `render_tally`; a seventh choice added here would have been accepted on the
# command line, written to the ledger, counted in the total, and then left out
# of the per-choice breakdown, so the parts would quietly stop summing to the
# whole.
VALID_CHOICES = ("claude", "gemini", "grok", "kimi", "mix", "reject")


def latest_verdicts(path: Path) -> dict[str, dict]:
    """Last-write-wins map of verdict_id -> verdict record."""
    if not path.exists():
        return {}
    out: dict[str, dict] = {}
    # The twin of the reader in `scripts/council-aggregate.py`, and now the same
    # function: both read THIS file, both spelled the read
    # `read_text(encoding="utf-8").splitlines()`, and both carried the two
    # defects `scripts/utils/jsonl_lines.py` documents. A shared reader, because
    # the previous fix to this pair -- the `isinstance(rec, dict)` guard below --
    # reached one of them and not the other, twice running.
    try:
        lines = list(jsonl_lines(path))
    except OSError as exc:
        print(f"{YELLOW}WARN: {path} could not be read ({exc}); "
              f"treating the ledger as empty{RESET}", file=sys.stderr)
        return {}
    for line in lines:
        if line is None:
            # Named, never silent: a dropped verdict changes the tally printed
            # after every record.
            print(f"{YELLOW}WARN: skipped an undecodable line in {path}; "
                  f"that verdict is not counted{RESET}", file=sys.stderr)
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        # `json.loads` answers with any JSON value, and the handler above
        # catches only a decode failure. A ledger line of `null`, `[]` or `42`
        # parses fine and then raises AttributeError on `.get` -- after the new
        # verdict has already been appended, and on every run afterwards until
        # someone hand-edits the file. `council-models-notify.py` carries this
        # exact guard with a comment explaining it; the fix had not reached
        # here.
        if not isinstance(rec, dict):
            continue
        vid = rec.get("verdict_id")
        if vid:
            out[vid] = rec
    return out


def append(verdict_id: str, choice: str, notes: str) -> dict:
    """Append a new verdict line; return the record written."""
    rec = {
        "verdict_id": verdict_id,
        "choice": choice,
        "notes": notes or "",
        "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    council_dir().mkdir(parents=True, exist_ok=True)
    with verdicts_path().open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


def render_tally(verdicts: dict[str, dict]) -> str:
    """Short summary line printed after every record."""
    if not verdicts:
        return "tally: 0 recorded"
    counts = Counter(v.get("choice") for v in verdicts.values())
    parts = [f"{k}={counts.get(k, 0)}" for k in VALID_CHOICES]
    named = sum(counts.get(k, 0) for k in VALID_CHOICES)
    tail = f", other={len(verdicts) - named}" if named != len(verdicts) else ""
    return f"tally: {len(verdicts)} recorded - " + ", ".join(parts) + tail


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Record one /council verdict.")
    parser.add_argument("--id", required=True,
                        help="verdict_id (transcript filename stem, e.g. "
                             "2026-05-22_council_151429_always-on-assistant)")
    parser.add_argument("--choice", required=True, choices=sorted(VALID_CHOICES),
                        help="CEO's chosen winner: claude / gemini / grok / kimi / mix / reject")
    parser.add_argument("--notes", default="",
                        help="Optional CEO comment about WHY (free text)")
    args = parser.parse_args(argv)

    # Soft-warn if the transcript file doesn't exist - the verdict is still
    # recorded (CEO might be backfilling from memory before the file lands),
    # but flag it so a typo'd id doesn't silently rot in the JSONL.
    transcript = council_dir() / f"{args.id}.md"
    missing = not transcript.exists()
    if missing:
        print(f"{YELLOW}WARN: no transcript at {transcript}. Verdict still recorded; "
              f"check --id for typo if this was unexpected.{RESET}", file=sys.stderr)

    rec = append(args.id, args.choice, args.notes)
    print(f"{GREEN}recorded: id={rec['verdict_id']} choice={rec['choice']} "
          f"notes={(rec['notes'][:60] + '...') if len(rec['notes']) > 60 else rec['notes']}{RESET}")
    print(render_tally(latest_verdicts(verdicts_path())))
    # Exit 3, as the docstring has always contracted: "transcript file for --id
    # not found (verdict still written; warning)". It returned 0, so a wrapper
    # using the exit code to catch a typo'd id — precisely the failure the
    # docstring describes — could not. The verdict is still appended; the code
    # says the id was not resolvable, not that the write failed.
    return 3 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
