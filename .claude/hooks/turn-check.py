#!/usr/bin/env python3
"""turn-check.py - Claude Code Stop hook. Refuses to let a turn end on a break.

A thin wrapper. All the logic is `scripts/turn-check.py`, which is a normal CLI
anyone can run with the browser and the harness both closed, per
`.claude/rules/console-first.md`. This file only translates its exit code into
the Stop-hook protocol.

Behaviour:
  clean, cached, or nothing changed -> silent, exit 0
  a lane failed                     -> {"decision": "block"} with the failure text

Scope. The payload's `transcript_path` is forwarded to the checker, which uses
it to narrow the changed set to files THIS session wrote. Without that the hook
reports another session's uncommitted work as a break in this turn, which it did
on 2026-08-12 over a parallel session's deliberately-red TDD test.

Anti-loop: bails on `stop_hook_active`, so a genuinely stuck failure blocks the
turn once and then lets the operator take over. A hook that can block forever is
worse than one that misses.

Never fatal. The check is a warning system; if it cannot run at all (missing
interpreter, unreadable tree) the turn ends normally rather than being held
hostage by the thing meant to protect it.
"""
import json
import subprocess
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

WORKSPACE = Path(__file__).resolve().parent.parent.parent
CHECKER = WORKSPACE / "scripts" / "turn-check.py"

# Long enough for a handful of matched test files, short enough that nobody
# learns to dread the end of a turn.
BUDGET_SECONDS = 90

REASON = """\
`scripts/turn-check.py` failed on the uncommitted Python edits in this turn \
({lane} lane). This is the fast check, not the full suite, so a failure here \
is almost always real.

{body}

Fix it, or say explicitly why it is being left. Re-run with:

    python scripts/turn-check.py

Full suite when you want the whole picture: `python scripts/run-tests.py`."""


def interpreter() -> str:
    """Prefer the project venv; the checker imports workspace modules."""
    venv = WORKSPACE / ".venv" / "bin" / "python"
    return str(venv) if venv.exists() else sys.executable


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:  # noqa: BLE001 - malformed payload must not hold the turn
        return 0

    # A payload that is valid JSON but not an object still reaches `.get`.
    # `[]`, `"x"`, `3` and `null` all parse, then raise an uncaught
    # AttributeError. Swept 2026-08-23 across every stdin hook: six crashed on
    # all four shapes. Same defect checkpoint-inject.py fixed on 2026-08-20;
    # the sweep is how the rest were found.
    if not isinstance(payload, dict):
        return 0

    if payload.get("stop_hook_active"):
        return 0

    if not CHECKER.is_file():
        return 0

    command = [interpreter(), str(CHECKER), "--json",
               "--timeout", str(BUDGET_SECONDS - 10)]
    transcript = payload.get("transcript_path")
    if isinstance(transcript, str) and transcript:
        command += ["--session-transcript", transcript]

    try:
        proc = subprocess.run(
            command,
            cwd=str(WORKSPACE), capture_output=True, text=True, timeout=BUDGET_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired):
        return 0

    try:
        result = json.loads(proc.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return 0

    if result.get("status") != "fail":
        return 0

    body = "\n".join(result.get("failures") or []) or "(no detail reported)"
    reason = REASON.format(lane=result.get("lane", "unknown"), body=body)
    print(json.dumps({"decision": "block", "reason": reason}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
