#!/usr/bin/env python3
"""Run /prime's read-only health checks in parallel and emit aggregated output.

Replaces the previous serial chain of subprocess invocations the /prime skill
executed. The checks are defined in the CHECKS registry and rendered in
DISPLAY_ORDER -- see those two objects below for the live set, which is the
only place it is stated. This sentence used to enumerate eight checks by name
while the registry held twelve; four (ops_radar, reminders_due, dream_shadow,
updates) had been added and never written down, which is how a check gets
added in one place and missed in another. The checks run concurrently on a
concurrent.futures.ThreadPoolExecutor(max_workers=8) -- a BOUNDED pool, so with
twelve checks at least four of them run on a reused pool thread; this line said
"each runs in its own thread" until 2026-08-30. Output blocks are emitted
in the same fixed order /prime expects so the CEO-facing brief stays unchanged.

A single failing health check never blocks the others: the script captures
the exception, reports it inline in that check's block, and continues. Exit
code is always 0 from this helper (per-check failure is informational only).

Usage:
    python scripts/prime-health-parallel.py
    python scripts/prime-health-parallel.py --json    # machine-readable
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

# Workspace import bootstrap (per development-standards.md)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils.workspace import (  # noqa: E402
    get_default_tz,
    get_outputs_dir,
    get_workspace_root,
)


# ============================================================
# Configuration
# ============================================================

# Fixed display order for /prime output blocks. Threads run concurrently;
# results are rendered serially in this order after all threads finish.
DISPLAY_ORDER = [
    "crm_health",
    "knowledge_health",
    "memory_health",
    "email_intel_status",
    "active_threads_archive_scan",
    "fireside_health",
    "sync_exchange_health",
    "odin_cadence",
    "ops_radar",
    "reminders_due",
    "dream_shadow",
    "updates",
]

# Section banner for each block (matches /prime numbering for legibility)
SECTION_BANNERS = {
    "crm_health": "### 2.5 Relationship Radar",
    "knowledge_health": "### 2.7 Knowledge Base Health",
    "memory_health": "### 2.9 Memory Health",
    "email_intel_status": "### 2.10 Email Intelligence Status",
    "active_threads_archive_scan": "### 2.11 Active Threads -- archive scan",
    "fireside_health": "### 2.12 Fireside Daemon",
    "sync_exchange_health": "### 2.13 Sync-Exchange Daemon",
    "odin_cadence": "### 2.14 Odin Cadence",
    "ops_radar": "### 2.15 Ops-Radar",
    "reminders_due": "### 2.16 Durable Reminders",
    "dream_shadow": "### 2.17 Dream-Shadow",
    "updates": "### 2.18 Component Updates",
}

# Per-check timeout (seconds). Real budget for /prime parallel block.
CHECK_TIMEOUT = 60


# ============================================================
# Health Check Implementations
# ============================================================

def run_crm_health(workspace_root: Path) -> dict[str, Any]:
    """Invoke scripts/crm-health.py and capture its stdout."""
    script = workspace_root / "scripts" / "crm-health.py"
    if not script.exists():
        return {"status": "missing", "output": f"crm-health.py not found at {script}"}
    proc = subprocess.run(
        [sys.executable, str(script)],
        cwd=str(workspace_root),
        capture_output=True,
        text=True,
        timeout=CHECK_TIMEOUT,
    )
    return {
        "status": "ok" if proc.returncode == 0 else "error",
        "exit_code": proc.returncode,
        "output": proc.stdout.strip(),
        "stderr": proc.stderr.strip(),
    }


def run_knowledge_health(workspace_root: Path) -> dict[str, Any]:
    """Invoke scripts/knowledge-health.py and capture its stdout."""
    script = workspace_root / "scripts" / "knowledge-health.py"
    if not script.exists():
        return {"status": "missing", "output": f"knowledge-health.py not found at {script}"}
    proc = subprocess.run(
        [sys.executable, str(script)],
        cwd=str(workspace_root),
        capture_output=True,
        text=True,
        timeout=CHECK_TIMEOUT,
    )
    return {
        "status": "ok" if proc.returncode == 0 else "error",
        "exit_code": proc.returncode,
        "output": proc.stdout.strip(),
        "stderr": proc.stderr.strip(),
    }


def run_memory_health(workspace_root: Path) -> dict[str, Any]:
    """Scan the persistent memory directory and report file/line counts.

    Inlined (no subprocess) - reads from the Claude Code memory dir under the
    user's ~/.claude project tree.
    """
    # Claude Code names each project dir by replacing every non-alphanumeric
    # char in the workspace path with "-". Derive the slug from workspace_root
    # so this resolves correctly under WSL/Linux, Windows, and exec machines,
    # rather than hardcoding one platform's encoding.
    projects_dir = Path.home() / ".claude" / "projects"
    slug = re.sub(r"[^a-zA-Z0-9]", "-", str(workspace_root))
    memory_dir = projects_dir / slug / "memory"
    if not memory_dir.is_dir() and projects_dir.is_dir():
        # Fallback: drive-letter case or other platform quirks. Match any
        # project dir whose slug equals workspace_root's case-insensitively.
        for cand in projects_dir.iterdir():
            if cand.name.lower() == slug.lower() and (cand / "memory").is_dir():
                memory_dir = cand / "memory"
                break
    # Objective defect computation is shared with scripts/memory-hygiene.py via
    # scripts/utils/memory_health.compute_memory_defects (dir-parameterized).
    from scripts.utils.memory_health import MEMORY_BUDGET_LINES, compute_memory_defects

    data = compute_memory_defects(memory_dir)
    if data["status"] == "missing":
        return {
            "status": "missing",
            "output": f"Memory: directory not found ({memory_dir}). Memory system inactive.",
        }

    files_count = data["file_count"]
    lines = data["memory_md_lines"]
    stale = data["stale"]
    orphans = data["orphans"]

    issues = []
    # `compute_memory_defects` has always returned this flag and this panel has
    # always dropped it, so an index over its own printed budget was reported as
    # "All healthy" - with the number that refutes the claim sitting in the same
    # sentence, two words to its left. `scripts/memory-hygiene.py` reads the same
    # field from the same helper and counts it as a defect in five places; the
    # panel the operator sees at EVERY session start was the honest tool's silent
    # twin.
    if data.get("over_budget"):
        issues.append(f"MEMORY.md is over its {MEMORY_BUDGET_LINES}-line budget")
    if stale:
        issues.append(f"{len(stale)} memory files >45 days old (review recommended)")
    # The index state comes first, because it explains the orphan count rather
    # than adding to it: an absent MEMORY.md makes every fact file unreferenced.
    if not data.get("index_readable", True):
        issues.append(f"MEMORY.md was NOT read ({data.get('index_problem')})")
    if orphans:
        issues.append(f"{len(orphans)} orphan file(s) not linked from MEMORY.md")

    # The budget in the printed line is the constant the flag above is computed
    # from, not a literal beside it. A hardcoded `/200` here would keep printing
    # 200 after someone moved the budget, which is how the number and the verdict
    # came apart in the first place.
    counts = f"Memory: {files_count} files, {lines}/{MEMORY_BUDGET_LINES} lines."
    body = counts + (" Issues: " + "; ".join(issues) if issues else " All healthy.")

    return {
        "status": "ok",
        "output": body,
        "file_count": files_count,
        "memory_md_lines": lines,
        "over_budget": bool(data.get("over_budget")),
        "stale": stale,
        "orphans": orphans,
    }


def run_email_intel_status(workspace_root: Path) -> dict[str, Any]:
    """Read outputs/operations/email-intelligence/state.json and summarise."""
    state_path = get_outputs_dir() / "operations" / "email-intelligence" / "state.json"
    if not state_path.exists():
        return {
            "status": "ok",
            "output": (
                "Email Intelligence: Never run. Use `/email-intel` to process "
                "yesterday's emails."
            ),
        }

    import datetime

    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        # `ValueError`, not `json.JSONDecodeError`. `read_text(encoding="utf-8")`
        # raises `UnicodeDecodeError` on non-UTF-8 bytes, which is a SIBLING of
        # JSONDecodeError under ValueError, so the narrower tuple missed one of
        # the two ways a state file is unreadable. MEASURED 2026-09-01: a
        # state.json of `b"\xff\xfe\x00binary"` raised out of this check, and
        # `run_all._wrap` then reported the whole panel as an exception instead
        # of the named "state.json unreadable" line this branch exists to print.
        return {
            "status": "error",
            "output": f"Email Intelligence: state.json unreadable ({exc}).",
        }

    last_run = data.get("last_run")
    if not last_run:
        return {
            "status": "ok",
            "output": (
                "Email Intelligence: Never run. Use `/email-intel` to process "
                "yesterday's emails."
            ),
        }

    try:
        last = datetime.datetime.fromisoformat(last_run.replace("Z", "+00:00"))
    except ValueError:
        return {
            "status": "ok",
            "output": f"Email Intelligence: last_run={last_run} (unparseable)",
        }

    now = datetime.datetime.now(datetime.timezone.utc)
    if last.tzinfo is None:
        last = last.replace(tzinfo=datetime.timezone.utc)
    hours_ago = (now - last).total_seconds() / 3600

    # Check pending P1 tasks
    tasks_path = state_path.parent / "tasks.md"
    p1_open = 0
    tasks_note = ""
    if tasks_path.exists():
        try:
            for line in tasks_path.read_text(encoding="utf-8", errors="ignore").splitlines():
                stripped = line.strip()
                if stripped.startswith("- [ ]") and "P1" in stripped:
                    p1_open += 1
        except OSError as exc:
            # UNKNOWN is not ZERO. A bare `pass` here reported `p1_open = 0`,
            # so an unreadable tasks.md told the operator there were no pending
            # P1 tasks when the count was simply not available -- a silent
            # failure on a priority signal.
            tasks_note = f" tasks.md unreadable ({exc}); P1 count UNKNOWN."
            p1_open = None

    if hours_ago > 20:
        body = (
            f"Email Intelligence: Last run {hours_ago:.1f} hours ago. "
            f"Run `/email-intel` to catch up."
        )
    else:
        body = f"Email Intelligence: Last run {hours_ago:.1f} hours ago. Status: {data.get('last_run_status', 'unknown')}."

    if p1_open:
        body += f" Pending P1 tasks: {p1_open}."
    body += tasks_note

    return {
        # An unreadable tasks.md is a partial answer, not an ok one.
        "status": "ok" if p1_open is not None else "error",
        "output": body,
        "last_run_hours_ago": hours_ago,
        "p1_open": p1_open,
    }


def run_threads_archive_scan(workspace_root: Path) -> dict[str, Any]:
    """Invoke `python scripts/thread.py archive-scan` and capture output."""
    script = workspace_root / "scripts" / "thread.py"
    if not script.exists():
        return {
            "status": "skipped",
            "output": "[threads] archive-scan unavailable - script missing",
        }
    try:
        proc = subprocess.run(
            [sys.executable, str(script), "archive-scan"],
            cwd=str(workspace_root),
            capture_output=True,
            text=True,
            timeout=CHECK_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return {
            "status": "skipped",
            "output": "[threads] archive-scan unavailable - timeout",
        }

    if proc.returncode != 0:
        # Suppress panel on failure per /prime contract; emit single inform line.
        return {
            "status": "skipped",
            "output": "[threads] archive-scan unavailable - skipping panel",
            "exit_code": proc.returncode,
            "stderr": proc.stderr.strip(),
        }

    return {
        "status": "ok",
        "output": proc.stdout.strip() or "(no archive candidates)",
    }


def run_fireside_health(workspace_root: Path) -> dict[str, Any]:
    """Invoke scripts/fireside-pulse.py and capture its stdout (includes auto-start)."""
    script = workspace_root / "scripts" / "fireside-pulse.py"
    if not script.exists():
        return {"status": "missing", "output": f"fireside-pulse.py not found at {script}"}
    # venv layout differs per OS: 'Scripts/python.exe' on Windows, 'bin/python' on POSIX.
    if sys.platform == "win32":
        venv_py = workspace_root / "scripts" / ".venv-fireside" / "Scripts" / "python.exe"
    else:
        venv_py = workspace_root / "scripts" / ".venv-fireside" / "bin" / "python"
    py = str(venv_py) if venv_py.exists() else sys.executable
    proc = subprocess.run(
        [py, str(script)],
        cwd=str(workspace_root),
        capture_output=True,
        text=True,
        timeout=CHECK_TIMEOUT,
    )
    # "error", not "failed": one failure word across every check, so the
    # renderer and the checks cannot disagree about which one means trouble.
    return {"status": "ok" if proc.returncode == 0 else "error",
            "output": proc.stdout or proc.stderr or "(no output)"}


def run_sync_exchange_health(workspace_root: Path) -> dict[str, Any]:
    """Invoke scripts/sync-exchange-pulse.py and capture its stdout (includes auto-spawn)."""
    script = workspace_root / "scripts" / "sync-exchange-pulse.py"
    if not script.exists():
        return {"status": "missing", "output": f"sync-exchange-pulse.py not found at {script}"}
    proc = subprocess.run(
        [sys.executable, str(script)],
        cwd=str(workspace_root),
        capture_output=True,
        text=True,
        timeout=CHECK_TIMEOUT,
    )
    # "error", not "failed": one failure word across every check, so the
    # renderer and the checks cannot disagree about which one means trouble.
    return {"status": "ok" if proc.returncode == 0 else "error",
            "output": proc.stdout or proc.stderr or "(no output)"}


def run_odin_cadence(workspace_root: Path) -> dict[str, Any]:
    """Run scripts/odin-cadence.py --quiet if present (ceo-only). Inert on execs.

    Existence-guarded: the cadence script is ceo-only, so on an exec workspace it
    is absent -- the check no-ops and `omit_if_empty` drops the section entirely,
    leaking no Odin feature reference into the exec-facing brief. On the CEO
    workspace, --quiet prints a one-line nudge ONLY on a genuine collect/reflect
    cadence signal; when up to date it prints nothing, so the section is omitted.
    Read-only (counts, never content) by construction of odin-cadence.py.
    """
    script = workspace_root / "scripts" / "odin-cadence.py"
    if not script.exists():
        return {"status": "skipped", "output": "", "omit_if_empty": True}
    proc = subprocess.run(
        [sys.executable, str(script), "--quiet"],
        cwd=str(workspace_root),
        capture_output=True,
        text=True,
        timeout=CHECK_TIMEOUT,
    )
    if proc.returncode != 0:
        # A non-zero child exit with EMPTY stdout used to vanish completely.
        # `render_text` honours `omit_if_empty` BEFORE it ever consults
        # `status`, so the banner, the failure and the captured stderr were all
        # dropped, and session boot rendered a clean brief over a check that had
        # crashed. odin-cadence.py only ever `return 0`s, so a non-zero exit is
        # an uncaught exception: the traceback goes to stderr and stdout is
        # empty, which is exactly the shape that disappeared.
        #
        # The repo's own precedent is a non-empty `output` plus
        # `omit_if_empty: False` on the error path (run_dream_shadow,
        # run_reminders_due, run_updates); the same shape is already pinned for
        # dream_shadow by tests/test_a_scan_that_never_ran_reported_nothing_to_do.py.
        return {
            "status": "error",
            "output": f"odin-cadence.py exited {proc.returncode}",
            "stderr": proc.stderr.strip(),
            "omit_if_empty": False,
        }
    return {
        "status": "ok",
        "output": proc.stdout.strip(),
        "stderr": proc.stderr.strip(),
        "omit_if_empty": True,
    }


def run_ops_radar(workspace_root: Path) -> dict[str, Any]:
    """Run scripts/ops-radar.py (default detailed view) if present (ceo-only).

    Existence-guarded: the radar is ceo-only, so on an exec workspace it is
    absent -- the check no-ops and `omit_if_empty` drops the section. On the CEO
    workspace the default (no-arg) run renders the detailed due-items view and is
    READ-ONLY (no heal, no state write) -- it respects ack/crunch suppression via
    assess(). When nothing is due ops-radar prints an "all clear" line; we map
    that to empty output so the section is omitted from the brief.
    """
    script = workspace_root / "scripts" / "ops-radar.py"
    if not script.exists():
        return {"status": "skipped", "output": "", "omit_if_empty": True}
    proc = subprocess.run(
        [sys.executable, str(script)],
        cwd=str(workspace_root),
        capture_output=True,
        text=True,
        timeout=CHECK_TIMEOUT,
    )
    if proc.returncode != 0:
        # Same shape as run_odin_cadence above, for the same reason: a crashed
        # child writes its traceback to stderr and leaves stdout empty, and
        # `omit_if_empty` then erased the whole section before `render_text`
        # looked at the status.
        return {
            "status": "error",
            "output": f"ops-radar.py exited {proc.returncode}",
            "stderr": proc.stderr.strip(),
            "omit_if_empty": False,
        }
    out = proc.stdout.strip()
    # "all clear" -> omit the panel; only surface when something is actually due.
    #
    # Tested on the FIRST LINE, not anywhere in the output. `"all clear" in out`
    # searched every line of the detailed view, so a single due item whose
    # summary carried that phrase blanked the entire panel -- the brief then said
    # nothing at all while something was in fact due. render_detailed emits the
    # all-clear sentence as the whole output, and any other run opens with
    # "ops-radar: N item(s) due".
    first_line = out.splitlines()[0] if out else ""
    if first_line.endswith("all clear - nothing due."):
        out = ""
    return {
        "status": "ok",
        "output": out,
        "stderr": proc.stderr.strip(),
        "omit_if_empty": True,
    }


def run_reminders_due(workspace_root: Path) -> dict[str, Any]:
    """Read-only: surface durable reminders that are DUE, as a /prime backstop.

    Due only, deliberately. This check used to also list everything falling
    inside a 7-day lookahead, which meant a reminder the operator had dated for
    a specific day reached them up to a week early, every session until then --
    the exact noise the date was chosen to avoid. A reminder dated D is for D.
    `scripts/reminders-notify.py`, the Telegram path, has always been due-only;
    this brings the /prime backstop in line with it.

    Never mutates the store, never marks fired. ceo-only surface via outputs/;
    omit_if_empty keeps the brief clean when nothing is due.
    """
    from datetime import datetime as _dt

    try:
        from scripts.utils import reminders_store as rs
        today = _dt.now(get_default_tz()).date()
        due = rs.due_records(today)
    except Exception as exc:  # noqa: BLE001 - boundary; reported inline
        return {"status": "error", "output": f"reminders check failed: {exc}",
                "omit_if_empty": True}
    lines = [
        f"DUE: {r['message']}" + (f"  -> {r['command']}" if r.get("command") else "")
        for r in due
    ]
    return {"status": "ok", "output": "\n".join(lines), "omit_if_empty": True}


def run_dream_shadow(workspace_root: Path) -> dict[str, Any]:
    """Read the latest dream-shadow report and surface one line when it lists
    merge candidates, nothing otherwise.

    Read-only: never runs scripts/dream-shadow.py itself -- that is the
    nightly timer's job (scripts/install-dream-shadow-timer.sh). This check
    only reads whatever report already exists under
    outputs/operations/dream/. Existence-guarded: if no report has been
    written yet (timer not installed / first run pending), the check is
    silently skipped, matching the odin_cadence "renders nothing when empty"
    pattern.
    """
    try:
        report_dir = get_outputs_dir() / "operations" / "dream"
        reports = sorted(report_dir.glob("*_dream-shadow_report.md"))
    except Exception as exc:  # noqa: BLE001 - boundary; reported inline
        return {"status": "error", "output": f"dream-shadow check failed: {exc}",
                "omit_if_empty": True}
    if not reports:
        return {"status": "skipped", "output": "", "omit_if_empty": True}

    latest = reports[-1]
    try:
        text = latest.read_text(encoding="utf-8")
    except (OSError, ValueError) as exc:
        # `ValueError`, for the reason the two handlers above record.
        # `UnicodeDecodeError` is a ValueError, not an OSError, so a report of
        # non-UTF-8 bytes escaped the branch whose message is literally
        # "unreadable". MEASURED 2026-09-01: the check raised, `run_all._wrap`
        # caught it, and the panel reported a generic exception instead of the
        # named line. The report is written by `scripts/dream-shadow.py` on a
        # nightly timer, so a torn write is how it becomes undecodable.
        return {"status": "error", "output": f"dream-shadow report unreadable: {exc}",
                "omit_if_empty": True}

    # Dormancy is deliberately NOT surfaced here: it is informational, it
    # proposes nothing, and on the first runs after reinforcement shipped it
    # lists nearly every aged file. It lives in the report for the operator to
    # read when they want it. Merge candidates DO need a nudge — each one is a
    # decision waiting on them.
    merge_section = re.search(r"## Merge Candidates.*?\n\n(.*?)(?:\n---|\Z)", text, re.DOTALL)
    body = merge_section.group(1) if merge_section else ""

    # A scan that COULD NOT RUN is not a scan that found nothing. When the
    # embedder is unavailable, dream-shadow writes one `- UNAVAILABLE: ...`
    # bullet instead of pair lines; it carries no `<->`, so the count below read
    # 0 and this check returned `status: ok` with empty output and
    # `omit_if_empty`. The embedder could be down every night and session boot
    # would never say a word. Surfaced by its marker, not by a count.
    #
    # What makes this render is `omit_if_empty: False` plus a non-empty output,
    # NOT the status string: `render_text` only consults the status to decide
    # whether to append stderr. So adding "warn" to NON_FAILURE_STATUSES later
    # cannot silently re-hide this line.
    unavailable = re.search(r"^- UNAVAILABLE: *(.*)$", body, re.MULTILINE)
    if unavailable:
        return {
            "status": "warn",
            "output": (f"Dream-shadow: merge scan did not run "
                       f"({unavailable.group(1).strip() or 'no reason recorded'}) — "
                       f"consolidation is not being detected."),
            "omit_if_empty": False,
        }

    merge_n = len(re.findall(r"^- .+<->.+$", body, re.MULTILINE))
    if merge_n == 0:
        return {"status": "ok", "output": "", "omit_if_empty": True}
    return {
        "status": "ok",
        "output": f"Dream-shadow: {merge_n} merge candidates — run `/dream` to review.",
        "omit_if_empty": True,
    }


def run_updates(workspace_root: Path) -> dict[str, Any]:
    """Read the update-manager state and surface waiting `notify` updates and any
    failed auto-apply. Read-only: never runs `update-manager check` (the daily
    timer's job). Silent when everything is current or no state exists yet.
    """
    try:
        state_file = get_outputs_dir() / "operations" / "updates" / "state.json"
    except Exception as exc:  # noqa: BLE001 - boundary; reported inline
        return {"status": "error", "output": f"updates check failed: {exc}",
                "omit_if_empty": True}
    if not state_file.exists():
        return {"status": "skipped", "output": "", "omit_if_empty": True}
    try:
        state = json.loads(state_file.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        # `ValueError`, for the reason `run_email_intel_status` above records:
        # `UnicodeDecodeError` is a sibling of `json.JSONDecodeError`, not a
        # subclass, so the narrower tuple caught only half of "unreadable".
        return {"status": "error", "output": f"updates state unreadable: {exc}",
                "omit_if_empty": True}

    lines: list[str] = []
    # `.get` with a fallback on every field. The state file is written by a
    # DIFFERENT component (update-manager), so any version skew between writer
    # and reader used to turn one entry missing one of four keys into a KeyError
    # that took the whole updates section down -- hiding every other valid
    # update behind a traceback. The statuses below are update-manager's
    # vocabulary, not this script's check statuses; they are unrelated words
    # that happen to look alike.
    for name, e in state.get("components", {}).items():
        if not isinstance(e, dict):
            lines.append(f"{name}: malformed entry ({type(e).__name__}), skipped")
            continue
        display = e.get("display", name)
        current = e.get("current", "?")
        latest = e.get("latest", "?")
        tier = e.get("tier", "?")
        status = e.get("status")
        if status == "waiting":
            lines.append(
                f"{display} {current}->{latest} "
                f"({tier} - apply: update-manager apply {name})"
            )
        elif status == "failed":
            lines.append(
                f"{display}: auto-apply FAILED (rolled back, "
                f"{e.get('fail_count', 0)}x) - check logs"
            )
        elif status == "observed-stale":
            lines.append(f"{display} {current}->{latest} (observed - self-updates)")
    if not lines:
        return {"status": "ok", "output": "", "omit_if_empty": True}
    return {"status": "ok", "output": "Updates waiting:\n  " + "\n  ".join(lines),
            "omit_if_empty": True}


# Map check key -> (callable, friendly label)
CHECKS = {
    "crm_health": (run_crm_health, "CRM health"),
    "knowledge_health": (run_knowledge_health, "Knowledge health"),
    "memory_health": (run_memory_health, "Memory health"),
    "email_intel_status": (run_email_intel_status, "Email Intelligence status"),
    "active_threads_archive_scan": (run_threads_archive_scan, "Threads archive scan"),
    "fireside_health": (run_fireside_health, "Fireside daemon health"),
    "sync_exchange_health": (run_sync_exchange_health, "Sync-Exchange daemon health"),
    "odin_cadence": (run_odin_cadence, "Odin cadence nudge"),
    "ops_radar": (run_ops_radar, "Ops-radar detector"),
    "reminders_due": (run_reminders_due, "Durable reminders"),
    "dream_shadow": (run_dream_shadow, "Dream-shadow worklist"),
    "updates": (run_updates, "Component updates"),
}


# ============================================================
# Aggregation & Output
# ============================================================

def run_all(workspace_root: Path) -> dict[str, dict[str, Any]]:
    """Dispatch all checks to a ThreadPoolExecutor and collect results."""
    results: dict[str, dict[str, Any]] = {}

    def _wrap(key: str) -> tuple[str, dict[str, Any]]:
        fn, _label = CHECKS[key]
        t0 = time.perf_counter()
        try:
            res = fn(workspace_root)
        except subprocess.TimeoutExpired as exc:
            res = {
                "status": "error",
                "output": f"timeout after {exc.timeout}s",
            }
        except Exception as exc:  # noqa: BLE001 - boundary; reported inline
            # The traceback goes to STDERR, not into the result dict. `--json`
            # prints the dict, and a formatted traceback carries absolute paths
            # -- home directory, username, workspace layout -- into output that
            # is designed to be pasted elsewhere. Text mode never rendered it
            # anyway.
            print(traceback.format_exc(), file=sys.stderr)
            res = {
                "status": "error",
                "output": f"{type(exc).__name__}: {exc}",
            }
        res.setdefault("elapsed_ms", round((time.perf_counter() - t0) * 1000, 1))
        return key, res

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(_wrap, k) for k in CHECKS]
        for fut in as_completed(futures):
            key, res = fut.result()
            results[key] = res

    return results


# The statuses that are NOT a failure. Everything else is, including a value
# added later that nobody thought to list here.
NON_FAILURE_STATUSES = frozenset({"ok", "skipped", "missing"})


def render_text(results: dict[str, dict[str, Any]]) -> str:
    """Format aggregated results in the order /prime expects."""
    lines: list[str] = []
    for key in DISPLAY_ORDER:
        banner = SECTION_BANNERS[key]
        res = results.get(key, {"status": "missing", "output": "(no result)"})
        body = res.get("output", "").rstrip()
        # Optional sections (e.g. odin_cadence) render nothing when empty -- no
        # banner, no "(no output)" line. Keeps an up-to-date / exec workspace clean.
        #
        # A FAILING check is never omitted, whatever it asks for. `omit_if_empty`
        # is a quiet-when-healthy switch, and read on its own it also silenced
        # two checks whose child had crashed with empty stdout. Both are fixed at
        # their source above; this second gate is what stops the next check added
        # here from reintroducing the same disappearance. It changes nothing for
        # a check that already reports its failure in `output`.
        if not body and res.get("omit_if_empty") \
                and res.get("status") in NON_FAILURE_STATUSES:
            continue
        lines.append(banner)
        if not body:
            body = "(no output)"
        lines.append(body)
        # Anything that is NOT a known-good status is a failure, so its stderr
        # is shown. Keying on `== "error"` alone meant that when fireside and
        # sync-exchange still reported `"failed"`, their diagnostics were
        # silently dropped -- exactly the two daemon checks where the stderr is
        # the whole point. Both were since changed to `"error"` (see the two
        # `"error", not "failed"` comments above) and no check in this file
        # returns `"failed"` any more; the `"failed"` at `run_updates` is
        # update-manager's own vocabulary, not a check status. This comment read
        # as a description of live code until 2026-08-30, which invited someone
        # to "fix" a check back to a word nothing here returns. Deriving the
        # failure set from the good one means a NEW status string cannot hide a
        # diagnostic by accident, whatever it is called.
        if res.get("status") not in NON_FAILURE_STATUSES:
            stderr = res.get("stderr", "").strip()
            if stderr:
                lines.append(f"[stderr] {stderr}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


# ============================================================
# CLI Entry Point
# ============================================================

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run /prime's read-only health checks in parallel.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON instead of the formatted text block.",
    )
    args = parser.parse_args()

    workspace_root = get_workspace_root()
    t0 = time.perf_counter()
    results = run_all(workspace_root)
    elapsed = round((time.perf_counter() - t0) * 1000, 1)

    if args.json:
        print(json.dumps(
            {"elapsed_ms": elapsed, "results": results},
            indent=2,
            default=str,
        ))
    else:
        print(render_text(results))

    # Always exit 0 - per-check failures are reported inline, not propagated.
    return 0


if __name__ == "__main__":
    sys.exit(main())
