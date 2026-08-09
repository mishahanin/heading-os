#!/usr/bin/env python3
"""Append structured trajectory events for /implement runs.

Closes R12 from the 2026-05-27 /scrutinize meta-review. Per Agent-as-a-Judge
(DevAI benchmark, Zhuge et al. 2024), trajectory evaluation lifts agent-
review accuracy from 70% to 90% vs outcome-only. This helper is the
emission side of that pattern. /scrutinize trajectory:<run_id> is the
audit side (see .claude/skills/scrutinize/references/trajectory-evaluation.md).

Four subcommands:

  --new --plan <plan-path>
      Mint a new run_id and create the trajectory JSONL with an opening
      run_start event. Prints the run_id to stdout so /implement can
      capture it and reference it in subsequent --event calls.

      Slug derivation: Path(plan_path).stem with leading YYYY-MM-DD-
      stripped if present. Example:
        plans/2026-05-27-r12-trajectory-evaluation.md
        -> slug=r12-trajectory-evaluation
        -> run_id=2026-05-27_134522_r12-trajectory-evaluation

  --event --run-id <id> --type <event-type> (typed flags | --data-*)
      Append one event record to the trajectory JSONL. The payload comes
      from EITHER typed flags OR one --data-* mode; the two are mutually
      exclusive.

      Typed flags (preferred, single call, no temp file): --step, --title,
      --file (repeatable), --status, --notes, --wave, --step-count,
      --parallel/--no-parallel, --successes, --failures, --check,
      --passed/--failed, --detail, --reason, --what-changed, --scope,
      --artefact, --grade, --iteration, --summary, --plan-status. Only set
      flags contribute a key; type-aware defaults fill step_end
      (files_affected=[], status=ok) and run_end (run_id, trajectory_path,
      plan_status=Implemented). Plain string args avoid JSON quoting, so
      this path is cross-platform safe.

      Escape hatch for an arbitrary payload: exactly one of --data-file
      (writes JSON via the Write tool, passes the path), --data-stdin (bash
      / PowerShell pipe), or --data-json (bash-only / hand-runs only).

  --verify --run-id <id>
      Structurally self-check an existing trajectory: run_start present and
      first, run_end present and last, step_start/step_end pairing,
      wave_start/wave_end pairing, each wave's successes count (an orphan
      wave_end is reconciled against its implicit bracket, not skipped), no
      step outside every bracket in a wave-mode run, and literal
      files_affected paths. Plus a run-level files reconciliation
      (advisory): the current engine working tree is diffed against
      run_start.git_head and any changed file recorded in no step's
      files_affected is flagged; this is meaningful only immediately after the
      run (before any commit / git pull) and degrades to a no-op when git_head
      is "unknown" or git is unavailable. Plus a validation-gate check
      (advisory): a completed run with zero validation_check events is flagged so
      Phase 3 gates are logged as structured events, not only prose. Plus three
      plan-derived advisories (silent when the plan cannot be located): a file
      listed under a plan step's "Files affected" that appears in no step's
      files_affected; a plan whose Implementation Notes declare more deviations
      than the trajectory carries as events; a deviation emitted before its own
      step's step_start. Prints defects and exits 1 on any
      defect, 0 when clean. Read-only; never mutates the audit record.
      /implement calls this in Phase 5 after run_end (advisory).

  --list-files --run-id <id>
      Print the union of every step's files_affected, one literal path per
      line, so the Phase 3 hidden-character scan reads its file list off the
      record instead of assembling it by hand.

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
  5  sequencing violation (step_start opened while another step is open
     outside a parallel wave, or step_end for an unopened step)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils.colors import GREEN, RED, RESET, YELLOW  # noqa: E402
from scripts.utils.workspace import (  # noqa: E402
    get_outputs_dir,
    get_plans_dir,
    get_workspace_root,
)

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


# ============================================================
# Typed-flag payload assembly (single-call emission path)
# ============================================================
# Maps each argparse dest to its payload key. List order defines the
# serialization order of flag-supplied keys. The payload dict is equal
# (same keys and values) to the legacy hand-authored --data-file JSON;
# key ORDER may differ when type-aware defaults are appended below, which
# is fine - the /scrutinize trajectory lens reads payload by key.
_FLAG_TO_PAYLOAD_KEY = [
    ("step", "step"),
    ("title", "title"),
    ("files", "files_affected"),
    ("status", "status"),
    ("notes", "notes"),
    ("wave", "wave"),
    ("step_count", "step_count"),
    ("parallel", "parallel"),
    ("successes", "successes"),
    ("failures", "failures"),
    ("check", "check"),
    ("passed", "passed"),
    ("detail", "detail"),
    ("reason", "reason"),
    ("what_changed", "what_changed"),
    ("scope", "scope"),
    ("artefact", "artefact"),
    ("grade", "grade"),
    ("iteration", "iteration"),
    ("summary", "summary"),
    ("plan_status", "plan_status"),
]

# The set of argparse dests that constitute the typed-flag mode. Shared by
# the mutual-exclusion guard in cmd_event and the payload builder.
TYPED_FLAG_DESTS = [dest for dest, _ in _FLAG_TO_PAYLOAD_KEY]


def build_payload_from_flags(event_type: str, args: argparse.Namespace) -> dict:
    """Assemble an event payload from typed flags, type-awarely.

    Only flags whose value is not None contribute a key. Then per-type
    defaults are applied so the emitted record matches the legacy shape:
      - step_end always carries files_affected (default []) and status (ok)
      - run_end auto-fills run_id, trajectory_path, plan_status (Implemented)
    """
    payload: dict[str, Any] = {}
    for dest, key in _FLAG_TO_PAYLOAD_KEY:
        val = getattr(args, dest, None)
        if val is not None:
            payload[key] = val

    if event_type == "step_end":
        payload.setdefault("files_affected", [])
        payload.setdefault("status", "ok")
    elif event_type == "run_end":
        payload.setdefault("run_id", args.run_id)
        payload.setdefault("trajectory_path", str(trajectory_path(args.run_id)))
        payload.setdefault("plan_status", "Implemented")
    return payload


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
# Emit-time sequencing state (guard support)
# ============================================================
def _read_events(path: Path) -> list[dict]:
    """Tolerant JSONL read: return event dicts, silently skipping bad lines.

    Used by the emit-time sequencing guard to reconstruct open-step state from
    the trajectory written so far. Malformed lines are ignored here (verify
    surfaces them as defects); the guard only needs the well-formed events.
    """
    events: list[dict] = []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return events
    for raw in text.splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            events.append(obj)
    return events


def _open_state(events: list[dict]) -> tuple[set, bool]:
    """Reduce a trajectory's events to (open_step_numbers, in_open_parallel_wave).

    Open steps are tracked with an open-stack per step_number (a number reused
    across waves still reconciles). A parallel wave is "open" while a
    wave_start with payload.parallel is True has no matching wave_end; tracked
    by wave number so a non-parallel wave_end never clears a parallel wave.
    """
    open_counts: dict[Any, int] = {}
    parallel_waves_open: set = set()
    for e in events:
        et = e.get("event_type")
        payload = e.get("payload") or {}
        if et == "step_start":
            sn = e.get("step_number")
            open_counts[sn] = open_counts.get(sn, 0) + 1
        elif et == "step_end":
            sn = e.get("step_number")
            if open_counts.get(sn, 0) > 0:
                open_counts[sn] -= 1
        elif et == "wave_start":
            if payload.get("parallel") is True:
                parallel_waves_open.add(payload.get("wave"))
        elif et == "wave_end":
            parallel_waves_open.discard(payload.get("wave"))
    open_steps = {sn for sn, c in open_counts.items() if c > 0}
    return open_steps, bool(parallel_waves_open)


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

    any_data_mode = bool(args.data_file or args.data_stdin or args.data_json)
    typed_present = any(getattr(args, d, None) is not None for d in TYPED_FLAG_DESTS)
    if any_data_mode and typed_present:
        print(f"{RED}ERROR: typed flags and --data-file/--data-stdin/--data-json "
              f"are mutually exclusive.{RESET}", file=sys.stderr)
        return 2

    if any_data_mode:
        payload = load_data(args)
        if not isinstance(payload, dict):
            print(f"{YELLOW}WARN: payload is not a JSON object; wrapping under 'value' key.{RESET}",
                  file=sys.stderr)
            payload = {"value": payload}
    else:
        payload = build_payload_from_flags(args.type, args)
    step_number = payload.get("step", payload.get("step_number"))

    # Emit-time sequencing guard: reject a mis-ordered step marker the moment
    # it is emitted (exit 5), so a bad marker cannot land silently. Wave-aware:
    # an open parallel wave suspends the guard (its member steps legitimately
    # interleave). This rejects a single --event call, never the run.
    if args.type in ("step_start", "step_end"):
        open_steps, parallel_open = _open_state(_read_events(path))
        if args.type == "step_start" and open_steps and not parallel_open:
            print(f"{RED}ERROR: sequencing violation: cannot open step "
                  f"{step_number} while step(s) {sorted(open_steps)} are still "
                  f"open. Emit their step_end first, or open a parallel "
                  f"wave_start for legitimate interleaving.{RESET}",
                  file=sys.stderr)
            return 5
        if args.type == "step_end" and step_number not in open_steps:
            print(f"{RED}ERROR: sequencing violation: step_end for step "
                  f"{step_number} has no open step_start.{RESET}",
                  file=sys.stderr)
            return 5

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


# ============================================================
# Self-check (--verify)
# ============================================================
_GLOB_CHARS = ("*", "{", "}")

# The plan is the other half of the record. Three of the 2026-08-09 scrutiny
# findings (M1, M2, N1) were all one shape: the run diverged from its own plan
# and only the narrative noticed. A narrative written by the same author that
# diverged is the weakest possible check, so these read the plan file itself.
_PLAN_FILES_HEADING = "**Files affected:**"
_PLAN_DEVIATIONS_HEADING = "### Deviations from Plan"
_BACKTICKED = re.compile(r"`([^`]+)`")
_NUMBERED_ITEM = re.compile(r"^\d+\.\s")


def resolve_plan_path(plan_path: str) -> Path | None:
    """Locate the plan a run_start names. None when it cannot be found.

    The recorded path is relative and the plans live in the DATA overlay, so a
    bare `WORKSPACE_ROOT / plan_path` misses. Every plan-derived check degrades
    to silence when this returns None - a missing plan is not a trajectory
    defect.
    """
    raw = Path(plan_path)
    candidates = [raw, get_plans_dir() / raw.name, WORKSPACE_ROOT / raw]
    for candidate in candidates:
        try:
            if candidate.is_file():
                return candidate
        except OSError:
            continue
    return None


def planned_files(plan_text: str) -> set[str]:
    """Every path listed under a `**Files affected:**` block in the plan."""
    out: set[str] = set()
    lines = plan_text.splitlines()
    for i, line in enumerate(lines):
        if line.strip() != _PLAN_FILES_HEADING:
            continue
        for follow in lines[i + 1:]:
            stripped = follow.strip()
            if not stripped:
                continue
            if not stripped.startswith("- "):
                break
            for token in _BACKTICKED.findall(stripped):
                token = token.strip()
                if "/" in token or "." in token:
                    out.add(token)
    return out


def declared_deviation_count(plan_text: str) -> int:
    """How many deviations the plan's Implementation Notes claim."""
    lines = plan_text.splitlines()
    try:
        start = next(i for i, line in enumerate(lines)
                     if line.strip() == _PLAN_DEVIATIONS_HEADING)
    except StopIteration:
        return 0
    count = 0
    for line in lines[start + 1:]:
        if line.startswith(("### ", "## ")):
            break
        if _NUMBERED_ITEM.match(line):
            count += 1
    return count


def _covers(recorded: str, planned: str) -> bool:
    """Whether a recorded path and a planned path name the same file.

    Suffix-tolerant in both directions: the plan writes a repo-relative path and
    a run may record it with a different prefix (engine vs data overlay). Anchored
    on a path separator so `record.py` never matches `scrutinize_record.py`.
    """
    if recorded == planned:
        return True
    return recorded.endswith("/" + planned) or planned.endswith("/" + recorded)


def _git_changed_files(git_head: str) -> set[str]:
    """Engine working-tree change set since git_head: tracked diff ∪ untracked.

    Returns repo-relative POSIX paths (what git prints and what /implement
    records). Runs in WORKSPACE_ROOT. Returns an empty set on any git failure
    so the run-level reconciliation degrades gracefully to "no defect". Named
    (not inlined) so tests can monkeypatch exactly this seam.
    """
    import subprocess

    changed: set[str] = set()
    try:
        diff = subprocess.run(
            ["git", "diff", "--name-only", git_head],
            cwd=str(WORKSPACE_ROOT), capture_output=True, text=True, timeout=10,
        )
        if diff.returncode != 0:
            return set()
        changed.update(p.strip() for p in diff.stdout.splitlines() if p.strip())
        untracked = subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard"],
            cwd=str(WORKSPACE_ROOT), capture_output=True, text=True, timeout=10,
        )
        if untracked.returncode == 0:
            changed.update(p.strip() for p in untracked.stdout.splitlines() if p.strip())
    except (OSError, subprocess.SubprocessError):
        return set()
    return changed


def verify_trajectory(run_id: str) -> list[str]:
    """Structurally self-check a trajectory JSONL; return defect strings.

    Empty list means clean. The checks are order-tolerant: they assert
    pairing and bracket membership, NOT step-bracket non-overlap, because
    parallel waves legitimately interleave their member steps. The M1
    interleave-ordering judgment stays with /scrutinize.

    Checks: run_start present and first; run_end present and last; every
    step_start paired with a step_end (open-stack per step_number, so a
    number reused across waves still reconciles); every wave_start paired
    with a wave_end; each wave bracket's wave_end.successes equals the
    bracketed count of step_end with status ok/deviation - including an
    ORPHAN wave_end, reconciled against the implicit bracket since the last
    wave boundary; in a run that uses waves at all, no step_end (other than
    the step-0 plan-load marker) sits outside every wave bracket; every
    files_affected entry is a literal path (no glob/shorthand/count token).

    Plus three advisory checks that report without asserting a violation:
    a wave_start whose payload omits step_count/parallel; a timestamp that
    goes backwards (clock or emission skew, not a sequencing fault); and the
    validation-gate check below.

    Plus a run-level files reconciliation (advisory): the current engine
    working tree is diffed against run_start.git_head, and any changed engine
    file absent from every step's files_affected is flagged "(advisory) ...".
    **Scope: the ENGINE tree only.** A run that also writes into the data
    overlay (a plan, a report, an output artifact) has those writes checked by
    nothing here, so a clean reconciliation is not full coverage - `cmd_verify`
    prints that scope explicitly rather than leaving a clean line to imply it.
    Reconciling the overlay too was considered and rejected: it accumulates
    unrelated background churn (daemon outputs, auto-memory, the trajectory
    file itself), which would bury real findings in noise.
    This is meaningful ONLY immediately after the run, against a tree holding
    just this run's changes (before any commit / git pull); re-run later on a
    historical trajectory with a stale-but-valid git_head it will over-flag
    pulled/committed files - expected, and harmless because advisory. It skips
    entirely (no defect) when git_head is "unknown" or any git call fails, so
    verify never becomes environment-fragile. The structural checks above stay
    pure-JSONL and are unaffected by repo state.

    Plus a validation-gate check (advisory): a completed run (run_end present)
    with zero validation_check events is flagged, so Phase 3 gate outcomes are
    logged as structured, machine-auditable events rather than only prose notes.
    """
    path = trajectory_path(run_id)
    defects: list[str] = []
    events: list[dict] = []
    for i, raw in enumerate(path.read_text(encoding="utf-8").splitlines()):
        raw = raw.strip()
        if not raw:
            continue
        try:
            events.append(json.loads(raw))
        except json.JSONDecodeError as exc:
            defects.append(f"line {i + 1}: malformed JSON ({exc})")
    if not events:
        defects.append("trajectory is empty")
        return defects

    types = [e.get("event_type") for e in events]

    # run_start present and first.
    if "run_start" not in types:
        defects.append("run_start event is missing")
    elif types[0] != "run_start":
        defects.append("run_start is not the first event")

    # run_end present and last (a trajectory missing run_end is incomplete).
    if "run_end" not in types:
        defects.append("run_end event is missing (incomplete trajectory)")
    elif types[-1] != "run_end":
        defects.append("run_end is not the last event")

    # step_start / step_end pairing via an open-stack per step number.
    open_steps: dict[Any, int] = {}
    for idx, e in enumerate(events):
        et = e.get("event_type")
        sn = e.get("step_number")
        if et == "step_start":
            open_steps[sn] = open_steps.get(sn, 0) + 1
        elif et == "step_end":
            if open_steps.get(sn, 0) > 0:
                open_steps[sn] -= 1
            else:
                defects.append(
                    f"step_end for step {sn} at position {idx} has no open step_start")
    for sn, count in open_steps.items():
        if count > 0:
            defects.append(
                f"step {sn}: {count} step_start(s) never closed by a step_end")

    # wave_start / wave_end pairing + bracketed successes count.
    #
    # An orphan wave_end is a bracketing defect, but its successes claim is
    # still reconciled - against the IMPLICIT bracket running from the last
    # wave boundary (or run start). The earlier version `continue`d here, so a
    # run whose only wave_end was an orphan had its successes count checked
    # against nothing and passed clean while matching neither the declared
    # membership nor the steps that actually ran (2026-08-09 /scrutinize, M1).
    wave_starts: dict[Any, int] = {}
    wave_spans: list[tuple[int, int]] = []
    saw_wave_event = False
    last_boundary = 0
    for idx, e in enumerate(events):
        et = e.get("event_type")
        payload = e.get("payload") or {}
        if et == "wave_start":
            saw_wave_event = True
            wave_starts[payload.get("wave")] = idx
            if "step_count" not in payload or "parallel" not in payload:
                defects.append(
                    f"(advisory) wave_start for wave {payload.get('wave')} at "
                    f"position {idx} omits step_count/parallel; the wave's shape "
                    f"is unrecoverable from the record")
            last_boundary = idx
        elif et == "wave_end":
            saw_wave_event = True
            w = payload.get("wave")
            start_idx = wave_starts.pop(w, None)
            if start_idx is None:
                defects.append(
                    f"wave_end for wave {w} at position {idx} has no matching wave_start")
                bracket_start, implicit = last_boundary, True
            else:
                bracket_start, implicit = start_idx, False
                wave_spans.append((start_idx, idx))
            bracket = events[bracket_start + 1:idx]
            ok_ends = sum(
                1 for b in bracket
                if b.get("event_type") == "step_end"
                and (b.get("payload") or {}).get("status") in ("ok", "deviation")
            )
            declared = payload.get("successes")
            if declared != ok_ends:
                where = "implicit bracket since the last wave boundary" if implicit \
                    else "bracketed"
                defects.append(
                    f"wave {w}: wave_end.successes={declared} but {where} "
                    f"ok/deviation step_end count={ok_ends}")
            last_boundary = idx
    for w, start_idx in wave_starts.items():
        defects.append(f"wave {w}: wave_start never closed by a wave_end")
        wave_spans.append((start_idx, len(events)))

    # Steps outside every wave bracket. Only meaningful in a run that uses
    # waves at all - a bare sequential run legitimately has none, and the plan
    # format forbids mixing the two shapes. Step 0 (the plan-load marker) sits
    # outside by design and is exempt.
    if saw_wave_event:
        unbracketed = [
            e.get("step_number")
            for idx, e in enumerate(events)
            if e.get("event_type") == "step_end"
            and e.get("step_number") != 0
            and not any(a < idx < b for a, b in wave_spans)
        ]
        if unbracketed:
            defects.append(
                "wave-mode run has step_end(s) outside every wave bracket: "
                + ", ".join(str(s) for s in unbracketed))

    # Timestamp monotonicity (advisory). Emission is sequential, so a record
    # whose timestamp precedes its predecessor is clock or emission skew, not a
    # sequencing violation - it is reported, never treated as one. Timestamps
    # are ISO-8601 UTC to the second, so they compare lexicographically; an
    # event without one is skipped rather than assumed.
    prev_ts, prev_idx = "", -1
    for idx, e in enumerate(events):
        ts = str(e.get("timestamp") or "")
        if not ts:
            continue
        if prev_ts and ts < prev_ts:
            defects.append(
                f"(advisory) event at position {idx} carries timestamp {ts}, "
                f"earlier than position {prev_idx} ({prev_ts})")
        prev_ts, prev_idx = ts, idx

    # files_affected literal-path check (catches the N1 glob/shorthand defect).
    for idx, e in enumerate(events):
        if e.get("event_type") != "step_end":
            continue
        for entry in (e.get("payload") or {}).get("files_affected") or []:
            s = str(entry)
            is_count = s[:1] == "+" and s[1:2].isdigit()
            if any(c in s for c in _GLOB_CHARS) or is_count:
                defects.append(
                    f"step_end at position {idx}: files_affected entry "
                    f"'{s}' is not a literal path (glob/shorthand/count)")

    # Run-level files reconciliation (advisory). Compare the current engine
    # working tree against run_start.git_head; flag any changed engine file
    # recorded in no step's files_affected. Graceful degrade: skip on git_head
    # "unknown" or any git failure (empty change set). See the docstring for
    # the "meaningful only immediately after the run" precondition.
    git_head = ""
    for e in events:
        if e.get("event_type") == "run_start":
            git_head = str((e.get("payload") or {}).get("git_head") or "")
            break
    if git_head and git_head != "unknown":
        changed = _git_changed_files(git_head)
        if changed:
            recorded: set = set()
            for e in events:
                if e.get("event_type") == "step_end":
                    for entry in (e.get("payload") or {}).get("files_affected") or []:
                        recorded.add(str(entry))
            for path_str in sorted(changed - recorded):
                defects.append(
                    f"(advisory) {path_str} was modified in this run but "
                    f"appears in no step's files_affected")

    # Validation-gate logging (advisory; F-6.1 loose end). A completed run
    # (run_end present) that recorded zero validation_check events narrated its
    # Phase 3 gates in prose (step_end notes) instead of structured, machine-
    # auditable events. Surfaces the gap so gate outcomes are deterministically
    # checkable, matching the files-reconciliation advisory style.
    if "run_end" in types and "validation_check" not in types:
        defects.append(
            "(advisory) run has a run_end but zero validation_check events; "
            "Phase 3 gates should emit validation_check events, not only prose notes")

    # Deviation ordering (advisory). A deviation for step N emitted before that
    # step's step_start reads, to anyone consuming the JSONL in order, as a
    # divergence from a step that has not begun. Wave-scoped deviations are
    # exempt by design: a deferred wave never emits a step_start at all.
    seen_starts: set = set()
    for idx, e in enumerate(events):
        et = e.get("event_type")
        sn = e.get("step_number")
        if et == "step_start":
            seen_starts.add(sn)
        elif et == "deviation":
            if (e.get("payload") or {}).get("scope") == "wave":
                continue
            if sn is not None and sn not in seen_starts:
                defects.append(
                    f"(advisory) deviation for step {sn} at position {idx} precedes "
                    f"that step's step_start")

    defects.extend(_plan_reconciliation(events))
    return defects


def _plan_reconciliation(events: list[dict]) -> list[str]:
    """Advisory checks that read the run's plan file. Silent when unavailable.

    Two findings from the 2026-08-09 scrutiny live here. A file the plan lists
    for a step but no step ever records (M1: the run edited the one file its
    step named, and named four others instead). And a run whose plan declares
    more deviations in prose than the trajectory carries as events (M2: two of
    six, one of them inside a step recorded `ok`) - the narrative is what the
    event record exists to be checkable against, so the narrative claiming more
    is the direction that matters.
    """
    defects: list[str] = []
    plan_ref = ""
    for e in events:
        if e.get("event_type") == "run_start":
            plan_ref = str((e.get("payload") or {}).get("plan_path") or "")
            break
    if not plan_ref:
        return defects
    plan_file = resolve_plan_path(plan_ref)
    if plan_file is None:
        return defects
    try:
        plan_text = plan_file.read_text(encoding="utf-8")
    except OSError:
        return defects

    recorded: set[str] = set()
    for e in events:
        if e.get("event_type") == "step_end":
            for entry in (e.get("payload") or {}).get("files_affected") or []:
                recorded.add(str(entry))
    for planned in sorted(planned_files(plan_text)):
        if not any(_covers(r, planned) for r in recorded):
            defects.append(
                f"(advisory) {planned} is listed under a plan step's Files affected "
                f"but appears in no step's files_affected")

    declared = declared_deviation_count(plan_text)
    emitted = sum(1 for e in events if e.get("event_type") == "deviation")
    if declared > emitted:
        defects.append(
            f"(advisory) the plan declares {declared} deviation(s) in its "
            f"Implementation Notes but the trajectory carries {emitted} deviation "
            f"event(s); emit the event at the moment of divergence, not at write-up")
    return defects


def cmd_files(args: argparse.Namespace) -> int:
    """Print the union of every step's files_affected, one literal path per line.

    So the Phase 3 hidden-character scan reads its file list off the record
    instead of being assembled by hand. The 2026-08-09 run scanned 11 files for a
    run that touched 22; nothing had escaped, but the evidence covered half the
    surface it claimed to.
    """
    if not args.run_id:
        print(f"{RED}ERROR: --list-files requires --run-id <id>{RESET}", file=sys.stderr)
        return 2
    path = trajectory_path(args.run_id)
    if not path.exists():
        print(f"{RED}ERROR: trajectory not found: {path}.{RESET}", file=sys.stderr)
        return 3
    out: set[str] = set()
    for e in _read_events(path):
        if e.get("event_type") == "step_end":
            for entry in (e.get("payload") or {}).get("files_affected") or []:
                out.add(str(entry))
    for entry in sorted(out):
        print(entry)
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    if not args.run_id:
        print(f"{RED}ERROR: --verify requires --run-id <id>{RESET}", file=sys.stderr)
        return 2
    path = trajectory_path(args.run_id)
    if not path.exists():
        print(f"{RED}ERROR: trajectory not found: {path}.{RESET}", file=sys.stderr)
        return 3
    defects = verify_trajectory(args.run_id)
    # State the reconciliation's scope on every run, clean or not. A clean line
    # otherwise reads as "every file this run touched is accounted for", which
    # it is not: the files check covers the engine tree only.
    print(f"{YELLOW}scope: files reconciliation covers the engine tree "
          f"({WORKSPACE_ROOT.name}) only; data-overlay writes are not checked.{RESET}",
          file=sys.stderr)
    if not defects:
        print(f"{GREEN}trajectory clean: {path.name}{RESET}")
        return 0
    print(f"{RED}trajectory has {len(defects)} structural defect(s):{RESET}",
          file=sys.stderr)
    for d in defects:
        print(f"{RED}  - {d}{RESET}", file=sys.stderr)
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Emit structured trajectory events for /implement runs.",
    )
    sub = parser.add_mutually_exclusive_group(required=True)
    sub.add_argument("--new", action="store_true",
                     help="Mint a new run_id and write the run_start event.")
    sub.add_argument("--event", action="store_true",
                     help="Append one event to an existing trajectory.")
    sub.add_argument("--verify", action="store_true",
                     help="Structurally self-check an existing trajectory "
                          "(exit 1 on any defect, 0 when clean).")
    sub.add_argument("--list-files", dest="list_files", action="store_true",
                     help="Print the union of every step's files_affected, one "
                          "literal path per line (feeds the Phase 3 scan list).")

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

    # Typed convenience flags (single-call emission, no temp file). Mutually
    # exclusive with the --data-* modes above. All default None so an unset
    # flag contributes no payload key.
    typed = parser.add_argument_group(
        "typed event flags",
        "Build the payload directly from flags instead of --data-file. "
        "Mutually exclusive with --data-file/--data-stdin/--data-json.")
    typed.add_argument("--step", type=int, help="Step number.")
    typed.add_argument("--title", help="Step or event title.")
    typed.add_argument("--file", dest="files", action="append",
                       help="A literal file path touched by the step (repeatable). "
                            "No globs, brace-shorthand, or count strings.")
    typed.add_argument("--status", help="step_end status: ok | issues | deviation.")
    typed.add_argument("--notes", help="Optional free-text note.")
    typed.add_argument("--wave", type=int, help="Wave number.")
    typed.add_argument("--step-count", type=int, dest="step_count",
                       help="wave_start step count.")
    typed.add_argument("--parallel", action=argparse.BooleanOptionalAction,
                       default=None, help="wave_start parallel flag (--parallel/--no-parallel).")
    typed.add_argument("--successes", type=int, help="wave_end successes count.")
    typed.add_argument("--failures", type=int, help="wave_end failures count.")
    typed.add_argument("--check", help="validation_check name.")
    passed_group = typed.add_mutually_exclusive_group()
    passed_group.add_argument("--passed", dest="passed", action="store_const",
                              const=True, default=None,
                              help="validation_check passed (sets passed=true).")
    passed_group.add_argument("--failed", dest="passed", action="store_const",
                              const=False,
                              help="validation_check failed (sets passed=false).")
    typed.add_argument("--detail", help="validation_check one-line detail.")
    typed.add_argument("--reason", help="deviation reason.")
    typed.add_argument("--what-changed", dest="what_changed",
                       help="deviation: what changed vs the plan.")
    typed.add_argument("--scope", help="deviation scope, e.g. 'wave' for a whole-wave deferral.")
    typed.add_argument("--artefact", help="evaluation_result artefact path.")
    typed.add_argument("--grade", help="evaluation_result grade.")
    typed.add_argument("--iteration", type=int, help="evaluation_result iteration.")
    typed.add_argument("--summary", help="run_end one-line summary.")
    typed.add_argument("--plan-status", dest="plan_status",
                       help="run_end plan status (default Implemented).")

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
    if args.verify:
        return cmd_verify(args)
    if args.list_files:
        return cmd_files(args)
    return 2


if __name__ == "__main__":
    sys.exit(main())
