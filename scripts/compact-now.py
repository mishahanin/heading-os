#!/usr/bin/env python3
"""
compact-now.py - ask Claude Code to compact THIS session, from the terminal.

Claude Code exposes no in-process way to run `/compact`, so this submits the
literal text to the terminal that hosts the session through HERDR. The harness
parses it exactly as it would from the keyboard. See
`scripts/utils/herdr_agent.py` for the mechanism and the evidence behind it.

This CLI exists because `.claude/rules/console-first.md` requires every
capability to be drivable from a terminal with no browser and no model turn. The
Stop hook uses the same seam; neither depends on the other.

Usage:
    python scripts/compact-now.py --dry-run       # resolve and print, submit nothing
    python scripts/compact-now.py                 # submit
    python scripts/compact-now.py --session <id>  # override self-resolution

Exit codes:
    0  submitted (or resolved, under --dry-run)
    1  HERDR is unreachable
    2  HERDR does not host this session
    3  no session could be resolved
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils import checkpoint_paths as CP  # noqa: E402
from scripts.utils.colors import BOLD, GRAY, GREEN, RED, RESET, YELLOW  # noqa: E402
from scripts.utils.herdr_agent import (  # noqa: E402
    COMPACT_COMMAND,
    HERDR_BIN,
    HerdrUnavailable,
    resolve_pane,
    submit_compact,
)
from scripts.utils.workspace import get_workspace_root  # noqa: E402


def _resolve_session(explicit: str | None, project: Path) -> str | None:
    if explicit:
        return explicit
    from_env = CP.session_id()
    if from_env != "session":
        return from_env
    return CP.newest_session_id(project)


def _agent_status(payload: dict) -> str | None:
    """HERDR's `agent_status` for the pane, or None when it did not say.

    The submission has already happened by the time this is read, so a shape
    HERDR did not use to send is a missing status line, never a failed compact.
    The chain this replaces was `(payload.get("result") or {}).get("agent")`,
    which guards a MISSING key and lets a present-but-wrong one through: `or {}`
    keeps a non-empty list, and the `.get` after it raises AttributeError past
    every HerdrUnavailable handler in this file. `herdr_agent.agents()` closed
    the same hole for the `agent list` call on 2026-08-19.
    """
    result = payload.get("result")
    agent = result.get("agent") if isinstance(result, dict) else None
    status = agent.get("agent_status") if isinstance(agent, dict) else None
    return status if isinstance(status, str) else None


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Submit /compact to this session's own terminal via HERDR."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="resolve the pane and print the command without submitting it",
    )
    parser.add_argument(
        "--session",
        metavar="ID",
        help="session id to act on; defaults to this session, "
        "then to the newest transcript in this workspace",
    )
    args = parser.parse_args()

    project = get_workspace_root()
    session = _resolve_session(args.session, project)
    if not session:
        print(
            f"{RED}No session could be resolved.{RESET} "
            f"No transcript found under {CP.transcript_dir(project)}. "
            "Pass --session <id>.",
            file=sys.stderr,
        )
        return 3

    try:
        pane = resolve_pane(session)
    except HerdrUnavailable as exc:
        print(f"{RED}{HERDR_BIN} is unreachable:{RESET} {exc}", file=sys.stderr)
        return 1

    if pane is None:
        print(
            f"{YELLOW}{HERDR_BIN} does not host session {session}.{RESET} "
            "Nothing was submitted; the native auto-compact remains the only "
            "path for this session.",
            file=sys.stderr,
        )
        return 2

    argv = [HERDR_BIN, "agent", "prompt", pane, COMPACT_COMMAND]
    if args.dry_run:
        print(f"{BOLD}session{RESET} {session}")
        print(f"{BOLD}pane{RESET}    {pane}")
        print(f"{BOLD}command{RESET} {' '.join(argv)}")
        print(f"{GRAY}dry run - nothing was submitted{RESET}")
        return 0

    try:
        payload = submit_compact(pane)
    except HerdrUnavailable as exc:
        print(f"{RED}Submission failed:{RESET} {exc}", file=sys.stderr)
        return 1

    status = _agent_status(payload)
    print(f"{GREEN}Submitted {COMPACT_COMMAND} to {pane}.{RESET}")
    if status == "working":
        print(
            f"{GRAY}Agent is working, so the prompt is queued and runs when the "
            f"current turn ends.{RESET}"
        )
    elif status:
        print(f"{GRAY}Agent status: {status}.{RESET}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
