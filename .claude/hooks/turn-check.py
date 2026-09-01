#!/usr/bin/env python3
"""turn-check.py - Claude Code Stop hook. Refuses to let a turn end on a break.

A thin wrapper. All the logic is `scripts/turn-check.py`, which is a normal CLI
anyone can run with the browser and the harness both closed, per
`.claude/rules/console-first.md`. This file only translates its exit code into
the Stop-hook protocol.

Behaviour:
  clean, cached, or nothing changed -> silent, exit 0
  a lane failed                     -> {"decision": "block"} with the failure text
  no verdict reachable at all       -> one line on stderr, exit 0, never silent

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
#
# Raised from 90 on 2026-08-31, together with `-n auto` in the lane itself. At 90
# this hook REFUSED five turns in a row: each re-run with a longer cap came back
# clean, so the operator paid the wait and learned nothing, which is the exact
# outcome the number above was chosen to avoid. A budget too small to ever finish
# is not caution; it converts a check into a toll.
#
# MEASURED that day, on the real changed set, on a machine loaded by five
# parallel agents: 78 test files and 1932 tests finished in 45.2s with `-n auto`.
# 150 leaves roughly 3x headroom over that, and the matched set only reaches this
# size during a fix campaign; an ordinary turn touches a handful of files and
# still returns in seconds. The flag alone was not enough to rely on, because its
# measured speed-up is 1.37x and worker start-up is a fixed cost.
BUDGET_SECONDS = 150

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


def stderr_tail(text: str, keep: int = 3) -> str:
    """The last few stderr lines, joined onto one line.

    One line because this goes to a Stop hook's stderr on a path where nothing
    is otherwise printed, and a screenful of traceback there is how a warning
    gets muted. The last lines are the ones that name the exception.
    """
    lines = [ln.strip() for ln in (text or "").strip().splitlines() if ln.strip()]
    return " | ".join(lines[-keep:]) or "(nothing on stderr)"


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
        # Announced for the same reason as the three degradations below. A
        # checker that is not on disk passes every turn forever, and a control
        # whose absence looks exactly like its success is the shape SEC-007
        # refuses. Renaming or moving the script is how this arrives.
        print(f"turn-check: checker not found at {CHECKER}", file=sys.stderr)
        return 0

    command = [interpreter(), str(CHECKER), "--json",
               "--timeout", str(BUDGET_SECONDS - 10)]
    transcript = payload.get("transcript_path")
    if isinstance(transcript, str) and transcript:
        command += ["--session-transcript", transcript]

    try:
        proc = subprocess.run(
            command,
            cwd=str(WORKSPACE), capture_output=True, text=True,
            # `errors="replace"`, or one byte of the checker's output ends the
            # turn. Strict decoding raises `UnicodeDecodeError` from inside
            # `subprocess.run` itself, before this call returns, and that error
            # is a `ValueError`: the handler below catches neither it nor
            # anything it derives from. The checker prints file paths and a
            # failing test's own output, so a byte that is not UTF-8 is
            # reachable, not theoretical.
            #
            # The sibling fix landed in `scripts/turn-check.py` (four call
            # sites) on 2026-09-01 and this file, the WRAPPER the harness
            # actually invokes on every Stop, was not looked at. Guard:
            # `tests/test_three_hooks_one_byte_could_end_a_session.py`.
            errors="replace", timeout=BUDGET_SECONDS,
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
        # The same one line, and the same reason, as the branch above. This
        # branch is reached when the checker died before printing anything: an
        # empty stdout leaves `splitlines()` an empty list and `[-1]` raises
        # IndexError. MEASURED 2026-08-31 with CHECKER pointed at a three-line
        # stub that wrote a traceback to stderr and exited 1: the hook exited 0
        # and emitted nothing on either stream, which is byte-for-byte what a
        # clean tree looks like. A syntax error in the checker or in one of its
        # imports, a missing pytest, and an OOM kill all land here, so the
        # Stop-hook control could be gone permanently with nobody told.
        print(f"turn-check: no usable result from the checker (exit "
              f"{proc.returncode}): {stderr_tail(proc.stderr)}", file=sys.stderr)
        return 0

    # A last stdout line that parses to `[]`, `"x"`, `3` or `null` reaches
    # `result.get` and raises AttributeError, which exits the hook 1 with a
    # traceback and blocks nothing. MEASURED the same day with a stub printing
    # `[]`. The real checker only ever emits an object, so this is the same
    # defensive guard the stdin payload already carries thirty lines above, for
    # the same reason: the shape arrives from another process.
    if not isinstance(result, dict):
        print(f"turn-check: the checker returned a "
              f"{result.__class__.__name__}, not an object (exit "
              f"{proc.returncode})", file=sys.stderr)
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
    # First, because it is the one exclusion that reports a WIDENING rather than
    # a drop, and it makes the count below it meaningless: with no scope, every
    # candidate is kept and `skipped_foreign` is 0 by construction. The two
    # never appear together.
    #
    # `session_scope.files_written` answers None for an absent, unreadable or
    # malformed transcript, and for any one unreadable subagent sidecar out of
    # the hundred-odd a busy session writes. `narrow` collapsed that None into a
    # zero drop, so the message below asserted the failure belonged to this turn
    # and named no exclusion at all. MEASURED 2026-08-31 over a malformed
    # transcript: another session's deliberately-red file blocked the turn and
    # was attributed to this one, which is the 2026-08-12 incident verbatim.
    # Obligation 3 of `.claude/rules/scope-claims.md` is to widen back to
    # everything AND say the state is unknown; only the widening was built.
    if result.get("scope_unknown"):
        exclusions.append("the attribution above (session scope could not be "
                          "established, so every uncommitted file was checked, "
                          "including files another session wrote)")
    foreign = result.get("skipped_foreign") or 0
    if foreign:
        # A disjunction, because that is what the scope establishes: a dropped
        # file carries no Write/Edit/MultiEdit/NotebookEdit call in this
        # session's transcript OR its subagent sidecars. It is another
        # session's, or an edit this session made through Bash, which records a
        # command and never a path. See `_foreign_note` in the checker.
        exclusions.append(f"{foreign} changed file(s) written by another session, "
                          f"or edited here through Bash, not checked")
    contract = result.get("skipped_contract") or 0
    if contract:
        exclusions.append(f"{contract} frozen-contract file(s) not run: red by "
                          f"design until the slice implements them")
    slow = result.get("deselected_slow") or 0
    if slow < 0:
        # The checker's sentinel for "the count could not be read at all".
        # `DESELECTED_UNKNOWN` is -1 in `scripts/turn-check.py`, returned when
        # the lane ran under `-n auto`: xdist prints no deselection summary, so
        # the number is unavailable while the exclusion is still happening. The
        # checker's own renderer (`_slow_note`) has always said that in words;
        # this hook read the value as a count and printed "-1 slow test(s) not
        # run here" to the operator. MEASURED 2026-09-01 by driving the real
        # hook over a failing result carrying `deselected_slow: -1`: that
        # sentence appeared verbatim in the block message. A negative count is
        # not a smaller claim, it is a nonsense one, and the honest answer here
        # is the state rather than the number
        # (obligation 3, `.claude/rules/scope-claims.md`).
        #
        # Reached whenever a campaign-sized changed set fails: the parallel lane
        # starts at 20 matched files, which the 2026-08-31 run passed on its way
        # from 74 to 111.
        exclusions.append("an unknown number of slow test(s) not run here: the "
                          "parallel lane reports no deselection count, so run "
                          "`python scripts/run-tests.py` for those")
    elif slow:
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
