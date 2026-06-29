#!/usr/bin/env python3
"""Append structured trajectory events for /implement runs.

Closes R12 from the 2026-05-27 /scrutinize meta-review. Per Agent-as-a-Judge
(DevAI benchmark, Zhuge et al. 2024), trajectory evaluation lifts agent-
review accuracy from 70% to 90% vs outcome-only. This helper is the
emission side of that pattern. /scrutinize trajectory:<run_id> is the
audit side (see .claude/skills/scrutinize/references/trajectory-evaluation.md).

Two subcommands:

  --new --plan <plan-path>
      Mint a new run_id and create the trajectory JSONL with an opening
      run_start event. Prints the run_id to stdout so /implement can
      capture it and reference it in subsequent --event calls.

      Slug derivation: Path(plan_path).stem with leading YYYY-MM-DD-
      stripped if present. Example:
        plans/2026-05-27-r12-trajectory-evaluation.md
        -> slug=r12-trajectory-evaluation
        -> run_id=2026-05-27_134522_r12-trajectory-evaluation

  --event --run-id <id> --type <event-type>
            [--data-file <path> | --data-stdin | --data-json <json-string>]
      Append one event record to the trajectory JSONL. Exactly one of
      --data-file, --data-stdin, --data-json must be provided.

      Cross-platform safety: /implement MUST use --data-file (writes JSON
      via the Write tool, passes the path). --data-stdin is OK for bash /
      PowerShell pipelines. --data-json is bash-only / hand-runs only.

Event types: run_start, step_start, step_end, validation_check,
              evaluation_result, deviation, wave_start, wave_end, run_end.

Each event record: {timestamp, event_type, step_number, payload}.

Atomic append discipline: the JSONL is shared-state in wave-mode
parallel /implement runs. POSIX uses O_APPEND on file open (line writes
under PIPE_BUF are atomic). Windows uses msvcrt.locking with retry.

The trajectory is a verbatim audit record: never mutate, never sanitize.
Hidden-character checking happens at READ time in the /scrutinize
trajectory lens, emitted at LOW severity (advisory).

All timestamps (in run_id and in event records) are UTC for cross-
machine consistency.

Usage:
  run_id=$(python scripts/implement-trajectory-log.py --new \\
             --plan plans/2026-05-27-r12-trajectory-evaluation.md)
  python scripts/implement-trajectory-log.py --event \\
    --run-id $run_id --type step_start --data-file /tmp/event.json

Exit codes:
  0  ok
  2  bad args (missing required, mutually-exclusive violation)
  3  filesystem error (cannot write, locking timeout)
  4  JSON parse error on supplied data
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils.colors import GREEN, RED, RESET, YELLOW  # noqa: E402
from scripts.utils.workspace import get_outputs_dir, get_workspace_root  # noqa: E402

WORKSPACE_ROOT = get_workspace_root()
TRAJECTORY_DIR = get_outputs_dir() / "operations" / "implement"

EVENT_TYPES = {
    "run_start",
    "step_start",
    "step_end",
    "validation_check",
    "evaluation_result",
    "deviation",
    "wave_start",
    "wave_end",
    "run_end",
}

# Atomic-append lock retry parameters (Windows path only).
_LOCK_RETRY_DELAY_S = 0.05
_LOCK_RETRY_MAX_ATTEMPTS = 40  # ~2s total


# ============================================================
# Run ID minting
# ============================================================
def derive_slug(plan_path: str) -> str:
    """Path(plan_path).stem with leading YYYY-MM-DD- stripped if present.

    Examples:
      plans/2026-05-27-r12-trajectory-evaluation.md -> r12-trajectory-evaluation
      plans/refactor-foo.md                          -> refactor-foo
      docs/some-plan-name                            -> some-plan-name
    """
    stem = Path(plan_path).stem
    # Strip YYYY-MM-DD- (10 chars + 1 hyphen = 11 chars) if it matches the pattern
    if len(stem) >= 11 and stem[4] == "-" and stem[7] == "-" and stem[10] == "-":
        date_part = stem[:10]
        if all(c.isdigit() or c == "-" for c in date_part):
            return stem[11:] or "untitled"
    return stem or "untitled"


def mint_run_id(plan_path: str) -> str:
    """Generate run_id = YYYY-MM-DD_HHMMSS_<slug> (UTC).

    All timestamps in trajectory artefacts are UTC for cross-machine consistency.
    A run minted at 17:25 local (UTC+4) and a run minted at 13:25 London (UTC+0)
    should produce identical run_id prefixes so their event records sort coherently.
    """
    now = datetime.now(timezone.utc)
    timestamp = now.strftime("%Y-%m-%d_%H%M%S")
    slug = derive_slug(plan_path)
    return f"{timestamp}_{slug}"


# ============================================================
# Atomic append (cross-platform)
# ============================================================
def _append_jsonl_posix(path: Path, record: dict) -> None:
    """POSIX path: O_APPEND ensures atomicity for line writes < PIPE_BUF."""
    line = json.dumps(record, ensure_ascii=False) + "\n"
    fd = os.open(str(path), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        os.write(fd, line.encode("utf-8"))
    finally:
        os.close(fd)


def _append_jsonl_windows(path: Path, record: dict) -> None:
    """Windows path: msvcrt.locking on a per-write basis with retry."""
    import msvcrt

    line = json.dumps(record, ensure_ascii=False) + "\n"
    data = line.encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.touch()

    last_err: Exception | None = None
    for attempt in range(_LOCK_RETRY_MAX_ATTEMPTS):
        try:
            with open(path, "ab") as f:
                try:
                    msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, len(data))
                except OSError as exc:
                    last_err = exc
                    time.sleep(_LOCK_RETRY_DELAY_S)
                    continue
                try:
                    f.write(data)
                    f.flush()
                finally:
                    try:
                        msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, len(data))
                    except OSError:
                        pass  # best-effort unlock
                return
        except OSError as exc:
            last_err = exc
            time.sleep(_LOCK_RETRY_DELAY_S)
    raise OSError(
        f"failed to acquire lock on {path} after "
        f"{_LOCK_RETRY_MAX_ATTEMPTS} attempts: {last_err}"
    )


def append_event(path: Path, record: dict) -> None:
    """Append one JSON record as a JSONL line, atomic under concurrent writes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        _append_jsonl_windows(path, record)
    else:
        _append_jsonl_posix(path, record)


# ============================================================
# Event records
# ============================================================
def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def trajectory_path(run_id: str) -> Path:
    return TRAJECTORY_DIR / f"_trajectory_{run_id}.jsonl"


def write_run_start(run_id: str, plan_path: str) -> Path:
    path = trajectory_path(run_id)
    if path.exists():
        # Refuse to overwrite an existing trajectory - this would clobber audit history.
        raise FileExistsError(f"trajectory already exists: {path}")
    try:
        import subprocess
        git_head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(WORKSPACE_ROOT),
            capture_output=True, text=True, timeout=3,
        ).stdout.strip() or "unknown"
    except (OSError, subprocess.SubprocessError):
        git_head = "unknown"

    record = {
        "timestamp": now_iso(),
        "event_type": "run_start",
        "step_number": 0,
        "payload": {
            "run_id": run_id,
            "plan_path": plan_path,
            "slug": derive_slug(plan_path),
            "workspace_root": str(WORKSPACE_ROOT),
            "git_head": git_head,
        },
    }
    append_event(path, record)
    return path


# ============================================================
# Data ingestion (three input modes - cross-platform safety)
# ============================================================
def load_data(args: argparse.Namespace) -> Any:
    """Load the event data payload from one of three input modes.

    Exactly one of --data-file, --data-stdin, --data-json must be set.
    """
    supplied = sum(1 for v in (args.data_file, args.data_stdin, args.data_json) if v)
    if supplied == 0:
        print(f"{RED}ERROR: one of --data-file, --data-stdin, --data-json is required.{RESET}",
              file=sys.stderr)
        sys.exit(2)
    if supplied > 1:
        print(f"{RED}ERROR: --data-file, --data-stdin, --data-json are mutually exclusive.{RESET}",
              file=sys.stderr)
        sys.exit(2)

    raw: str
    if args.data_file:
        try:
            raw = Path(args.data_file).read_text(encoding="utf-8")
        except OSError as exc:
            print(f"{RED}ERROR: cannot read --data-file {args.data_file}: {exc}{RESET}",
                  file=sys.stderr)
            sys.exit(3)
    elif args.data_stdin:
        raw = sys.stdin.read()
    else:
        raw = args.data_json

    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        print(f"{RED}ERROR: data payload is not valid JSON: {exc}{RESET}",
              file=sys.stderr)
        sys.exit(4)


# ============================================================
# CLI
# ============================================================
def cmd_new(args: argparse.Namespace) -> int:
    if not args.plan:
        print(f"{RED}ERROR: --new requires --plan <plan-path>{RESET}", file=sys.stderr)
        return 2
    run_id = mint_run_id(args.plan)
    try:
        path = write_run_start(run_id, args.plan)
    except FileExistsError as exc:
        print(f"{RED}ERROR: {exc}{RESET}", file=sys.stderr)
        return 3
    except OSError as exc:
        print(f"{RED}ERROR: cannot write trajectory: {exc}{RESET}", file=sys.stderr)
        return 3
    # run_id to stdout for capture by /implement; informational line to stderr.
    print(run_id)
    print(f"{GREEN}trajectory: {path}{RESET}", file=sys.stderr)
    return 0


def cmd_event(args: argparse.Namespace) -> int:
    if args.type not in EVENT_TYPES:
        print(f"{RED}ERROR: --type must be one of {sorted(EVENT_TYPES)}{RESET}",
              file=sys.stderr)
        return 2
    path = trajectory_path(args.run_id)
    if not path.exists():
        print(f"{RED}ERROR: trajectory not found: {path}. "
              f"Did you call --new first?{RESET}", file=sys.stderr)
        return 3
    payload = load_data(args)
    if not isinstance(payload, dict):
        print(f"{YELLOW}WARN: payload is not a JSON object; wrapping under 'value' key.{RESET}",
              file=sys.stderr)
        payload = {"value": payload}
    step_number = payload.get("step", payload.get("step_number", None))
    record = {
        "timestamp": now_iso(),
        "event_type": args.type,
        "step_number": step_number,
        "payload": payload,
    }
    try:
        append_event(path, record)
    except OSError as exc:
        print(f"{RED}ERROR: append failed: {exc}{RESET}", file=sys.stderr)
        return 3
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Emit structured trajectory events for /implement runs.",
    )
    sub = parser.add_mutually_exclusive_group(required=True)
    sub.add_argument("--new", action="store_true",
                     help="Mint a new run_id and write the run_start event.")
    sub.add_argument("--event", action="store_true",
                     help="Append one event to an existing trajectory.")

    parser.add_argument("--plan", help="Plan file path (required with --new).")
    parser.add_argument("--run-id", help="Existing run_id (required with --event).")
    parser.add_argument("--type", help=f"Event type. One of: {sorted(EVENT_TYPES)}.")
    parser.add_argument("--data-file",
                        help="Path to a JSON file holding the event payload. "
                             "REQUIRED for /implement automated calls (cross-platform).")
    parser.add_argument("--data-stdin", action="store_true",
                        help="Read JSON payload from stdin. Bash/PowerShell pipe-friendly.")
    parser.add_argument("--data-json",
                        help="Inline JSON payload. Bash-only / hand-runs only. "
                             "/implement MUST NOT use this mode.")

    args = parser.parse_args(argv)

    if args.new:
        return cmd_new(args)
    if args.event:
        if not args.run_id:
            print(f"{RED}ERROR: --event requires --run-id <id>{RESET}", file=sys.stderr)
            return 2
        if not args.type:
            print(f"{RED}ERROR: --event requires --type <event-type>{RESET}", file=sys.stderr)
            return 2
        return cmd_event(args)
    return 2


if __name__ == "__main__":
    sys.exit(main())
