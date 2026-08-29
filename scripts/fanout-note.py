#!/usr/bin/env python3
"""Record why a stretch of work is serial, and clear the fan-out budget.

The second door out of `check_fanout_first` (`.claude/hooks/_dispatch.py`).
The first door is dispatching an Agent or a Workflow, which is the outcome the
rule wants. This one is for the case the rule cannot see: work that genuinely
IS a single dependency chain.

WHY IT WRITES A LOG. A wall with a silent escape teaches nothing and cannot be
audited: the operator would have no way to tell a session that thought about
fanning out from one that clicked past the question. `check_graph_first` used
to carry a refusal counter with exactly that shape, and it was removed on
2026-08-29 for exactly that reason. Here the escape is kept, because "this is
serial" is sometimes true and a wall that refused it would be wrong -- so
instead of hiding the escape, every use of it leaves a dated claim the operator
can read back and disagree with.

Usage:
    python scripts/fanout-note.py "one file, edited in sequence; nothing to split"
    python scripts/fanout-note.py --show          # what has been claimed so far
    python scripts/fanout-note.py --show --limit 5
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils.argtypes import positive_int  # noqa: E402
from scripts.utils.atomic import atomic_write_text  # noqa: E402
from scripts.utils.colors import GRAY, GREEN, RESET, YELLOW  # noqa: E402
from scripts.utils.workspace import get_workspace_root  # noqa: E402

ROOT = get_workspace_root()
STATE_DIR = ROOT / ".claude" / "state" / "fanout"
LOG = STATE_DIR / "serial-claims.jsonl"

# A reason has to say something. Refusing a blank keeps the escape from
# degrading into a keystroke: the point of this door is the sentence, not the
# reset.
MIN_REASON = 15


def record(reason: str, session: str | None = None) -> Path:
    reason = reason.strip()
    if len(reason) < MIN_REASON:
        raise ValueError(
            f"a reason of at least {MIN_REASON} characters, please; "
            f"got {len(reason)}. Name the dependency that makes this serial.")
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    entry = {
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "session": session or "-",
        "reason": reason,
    }
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return LOG


def clear_budgets() -> int:
    """Empty every session's path budget. Returns how many were cleared.

    The hook ALSO clears on seeing this script's name in a Bash command, which
    is what makes the reset immediate. This pass is the belt: it covers a run
    from outside the hook (a terminal the operator drives themselves), where no
    PreToolUse payload exists to react to.
    """
    if not STATE_DIR.is_dir():
        return 0
    cleared = 0
    for marker in STATE_DIR.glob("*.json"):
        atomic_write_text(marker, "[]")
        cleared += 1
    return cleared


def show(limit: int) -> int:
    if not LOG.is_file():
        print(f"{GRAY}no serial claims recorded{RESET}")
        return 0
    lines = [ln for ln in LOG.read_text(encoding="utf-8").splitlines() if ln.strip()]
    print(f"{len(lines)} serial claim(s); newest {min(limit, len(lines))}:")
    for line in lines[-limit:]:
        try:
            e = json.loads(line)
        except ValueError:
            print(f"  {YELLOW}unreadable entry{RESET} {line[:70]}")
            continue
        print(f"  {GRAY}{e.get('at', '?')}{RESET}  {e.get('reason', '')}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Record why a stretch of work is serial, clearing the "
                    "fan-out budget.")
    ap.add_argument("reason", nargs="?",
                    help="why this work cannot be split across agents")
    ap.add_argument("--session", help="session id, for the log")
    ap.add_argument("--show", action="store_true", help="print recorded claims")
    ap.add_argument("--limit", type=positive_int, default=10,
                    help="how many claims --show prints (default 10)")
    args = ap.parse_args(argv)

    if args.show:
        return show(args.limit)

    if not args.reason:
        ap.error("a reason is required (or use --show)")

    try:
        path = record(args.reason, args.session)
    except ValueError as exc:
        print(f"{YELLOW}{exc}{RESET}", file=sys.stderr)
        return 2

    cleared = clear_budgets()
    print(f"{GREEN}recorded{RESET}; fan-out budget cleared "
          f"({cleared} session marker(s))")
    print(f"  {GRAY}{path}{RESET}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
