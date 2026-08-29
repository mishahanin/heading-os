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

# A lane killed by the wall clock reached no verdict about anything. Until
# 2026-08-29 it was rendered with REASON above, so the operator was told a
# check had failed and that "a failure here is almost always real", when in
# fact nothing had been measured. MEASURED that day by forcing
# `subprocess.TimeoutExpired`: the result carried `"status": "fail"` and
# `"tests_run": 2` with zero tests actually run. Both halves are fixed; this is
# the half the operator reads.
UNMEASURED_REASON = """\
`scripts/turn-check.py` did not finish the {lane} lane on the uncommitted \
Python edits in this turn. This is NOT a failure: nothing was measured, so \
nothing about those edits is known yet.

{body}

Re-run with a longer cap, or run the files yourself:

    python scripts/turn-check.py --timeout 300

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
    except (OSError, subprocess.TimeoutExpired) as exc:
        # One line, then degrade. Returning 0 silently made a checker that
        # times out on EVERY turn look exactly like a clean tree, which is the
        # shape SEC-007 exists to refuse: a control whose failure is
        # indistinguishable from its success.
        print(f"turn-check: checker unavailable ({exc.__class__.__name__}): {exc}",
              file=sys.stderr)
        return 0

    try:
        result = json.loads(proc.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return 0

    if result.get("status") != "fail":
        return 0

    body = "\n".join(result.get("failures") or []) or "(no detail reported)"
    unmeasured = result.get("unmeasured") or 0
    template = UNMEASURED_REASON if unmeasured else REASON
    reason = template.format(lane=result.get("lane", "unknown"), body=body)

    # Name what the check left out. `scripts/turn-check.py` reports three
    # exclusion counts and prints all three in its own human renderer, "because
    # silence here is how a narrowed check starts reading as a complete one".
    # This hook read only `lane` and `failures` and then asserted the run covered
    # "the uncommitted Python edits in this turn" - the one message the operator
    # actually reads, with every exclusion dropped. Fixing the named failure then
    # looked like a green turn over a set that was never checked.
    exclusions = []
    foreign = result.get("skipped_foreign") or 0
    if foreign:
        exclusions.append(f"{foreign} changed file(s) written by another session, "
                          f"not checked")
    contract = result.get("skipped_contract") or 0
    if contract:
        exclusions.append(f"{contract} frozen-contract file(s) not run: red by "
                          f"design until the slice implements them")
    slow = result.get("deselected_slow") or 0
    if slow:
        exclusions.append(f"{slow} slow test(s) not run here: run "
                          f"`python scripts/run-tests.py` for those")
    if unmeasured:
        exclusions.append(f"{unmeasured} matched file(s) left unmeasured: the "
                          f"lane did not finish, so nothing about them is known")
    if exclusions:
        reason += ("\n\nNot covered by this check: " + "; ".join(exclusions) + ".")

    print(json.dumps({"decision": "block", "reason": reason}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
