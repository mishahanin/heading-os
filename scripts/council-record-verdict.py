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
from scripts.utils.workspace import get_outputs_dir  # noqa: E402

COUNCIL_DIR = get_outputs_dir() / "operations" / "council"
VERDICTS_PATH = COUNCIL_DIR / "_verdicts.jsonl"
VALID_CHOICES = {"claude", "gemini", "grok", "kimi", "mix", "reject"}


def latest_verdicts(path: Path) -> dict[str, dict]:
    """Last-write-wins map of verdict_id -> verdict record."""
    if not path.exists():
        return {}
    out: dict[str, dict] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
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
    COUNCIL_DIR.mkdir(parents=True, exist_ok=True)
    with VERDICTS_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


def render_tally(verdicts: dict[str, dict]) -> str:
    """Short summary line printed after every record."""
    if not verdicts:
        return "tally: 0 recorded"
    counts = Counter(v["choice"] for v in verdicts.values())
    parts = [f"{k}={counts.get(k, 0)}" for k in ("claude", "gemini", "grok", "kimi", "mix", "reject")]
    return f"tally: {len(verdicts)} recorded - " + ", ".join(parts)


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
    transcript = COUNCIL_DIR / f"{args.id}.md"
    if not transcript.exists():
        print(f"{YELLOW}WARN: no transcript at {transcript}. Verdict still recorded; "
              f"check --id for typo if this was unexpected.{RESET}", file=sys.stderr)

    rec = append(args.id, args.choice, args.notes)
    print(f"{GREEN}recorded: id={rec['verdict_id']} choice={rec['choice']} "
          f"notes={(rec['notes'][:60] + '...') if len(rec['notes']) > 60 else rec['notes']}{RESET}")
    print(render_tally(latest_verdicts(VERDICTS_PATH)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
