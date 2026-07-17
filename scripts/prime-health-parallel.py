#!/usr/bin/env python3
"""Run /prime's read-only health checks in parallel and emit aggregated output.

Replaces the previous serial chain of subprocess invocations the /prime skill
executed. The checks are defined in the CHECKS registry and rendered in
DISPLAY_ORDER (crm-health, knowledge-health, memory file scan, email-intel
state read, thread.py archive-scan, fireside-pulse, sync-exchange health, and
the read-only odin-cadence nudge). Each runs in its own thread via
concurrent.futures.ThreadPoolExecutor(max_workers=8). Output blocks are emitted
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

from scripts.utils.workspace import get_outputs_dir, get_workspace_root  # noqa: E402


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
    from scripts.utils.memory_health import compute_memory_defects

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
    if stale:
        issues.append(f"{len(stale)} memory files >45 days old (review recommended)")
    if orphans:
        issues.append(f"{len(orphans)} orphan file(s) not linked from MEMORY.md")

    if issues:
        body = (
            f"Memory: {files_count} files, {lines}/200 lines. Issues: "
            + "; ".join(issues)
        )
    else:
        body = f"Memory: {files_count} files, {lines}/200 lines. All healthy."

    return {
        "status": "ok",
        "output": body,
        "file_count": files_count,
        "memory_md_lines": lines,
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
    except (OSError, json.JSONDecodeError) as exc:
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
    if tasks_path.exists():
        try:
            for line in tasks_path.read_text(encoding="utf-8", errors="ignore").splitlines():
                stripped = line.strip()
                if stripped.startswith("- [ ]") and "P1" in stripped:
                    p1_open += 1
        except OSError:
            pass

    if hours_ago > 20:
        body = (
            f"Email Intelligence: Last run {hours_ago:.1f} hours ago. "
            f"Run `/email-intel` to catch up."
        )
    else:
        body = f"Email Intelligence: Last run {hours_ago:.1f} hours ago. Status: {data.get('last_run_status', 'unknown')}."

    if p1_open:
        body += f" Pending P1 tasks: {p1_open}."

    return {
        "status": "ok",
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
    return {"status": "ok" if proc.returncode == 0 else "failed",
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
    return {"status": "ok" if proc.returncode == 0 else "failed",
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
    return {
        "status": "ok" if proc.returncode == 0 else "error",
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
    out = proc.stdout.strip()
    # "all clear" -> omit the panel; only surface when something is actually due.
    if "all clear" in out:
        out = ""
    return {
        "status": "ok" if proc.returncode == 0 else "error",
        "output": out,
        "stderr": proc.stderr.strip(),
        "omit_if_empty": True,
    }


def run_reminders_due(workspace_root: Path) -> dict[str, Any]:
    """Read-only: surface due + upcoming durable reminders as a /prime backstop.

    Never mutates the store, never marks fired. ceo-only surface via outputs/;
    omit_if_empty keeps the brief clean when nothing is due or upcoming.
    """
    from datetime import date as _date

    try:
        from scripts.utils import reminders_store as rs
        today = _date.today()
        due = rs.due_records(today)
        upcoming = rs.upcoming(today, days=7)
    except Exception as exc:  # noqa: BLE001 - boundary; reported inline
        return {"status": "error", "output": f"reminders check failed: {exc}",
                "omit_if_empty": True}
    lines = []
    for r in due:
        lines.append(f"DUE: {r['message']}" + (f"  -> {r['command']}" if r.get("command") else ""))
    for r in upcoming:
        when = r["when"] if r["kind"] == "once" else "recurring"
        lines.append(f"upcoming ({when}): {r['message']}")
    return {"status": "ok", "output": "\n".join(lines), "omit_if_empty": True}


def run_dream_shadow(workspace_root: Path) -> dict[str, Any]:
    """Read the latest dream-shadow report and surface one line when it lists
    prune/merge candidates, nothing otherwise.

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
    except OSError as exc:
        return {"status": "error", "output": f"dream-shadow report unreadable: {exc}",
                "omit_if_empty": True}

    prune_match = re.search(r"## Prune Candidates.*?:\s*(\d+)", text)
    prune_n = int(prune_match.group(1)) if prune_match else 0
    merge_section = re.search(r"## Merge Candidates.*?\n\n(.*?)(?:\n---|\Z)", text, re.DOTALL)
    merge_n = 0
    if merge_section:
        merge_n = len(re.findall(r"^- .+<->.+$", merge_section.group(1), re.MULTILINE))

    if prune_n == 0 and merge_n == 0:
        return {"status": "ok", "output": "", "omit_if_empty": True}
    return {
        "status": "ok",
        "output": (
            f"Dream-shadow: {prune_n} prune candidates, {merge_n} merge "
            "candidates -- run `/dream` to review."
        ),
        "omit_if_empty": True,
    }


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
            res = {
                "status": "error",
                "output": f"{type(exc).__name__}: {exc}",
                "traceback": traceback.format_exc(),
            }
        res.setdefault("elapsed_ms", round((time.perf_counter() - t0) * 1000, 1))
        return key, res

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(_wrap, k) for k in CHECKS]
        for fut in as_completed(futures):
            key, res = fut.result()
            results[key] = res

    return results


def render_text(results: dict[str, dict[str, Any]]) -> str:
    """Format aggregated results in the order /prime expects."""
    lines: list[str] = []
    for key in DISPLAY_ORDER:
        banner = SECTION_BANNERS[key]
        res = results.get(key, {"status": "missing", "output": "(no result)"})
        body = res.get("output", "").rstrip()
        # Optional sections (e.g. odin_cadence) render nothing when empty -- no
        # banner, no "(no output)" line. Keeps an up-to-date / exec workspace clean.
        if not body and res.get("omit_if_empty"):
            continue
        lines.append(banner)
        if not body:
            body = "(no output)"
        lines.append(body)
        if res.get("status") == "error":
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
