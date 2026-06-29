#!/usr/bin/env python3
"""ops_signals.py - pure, dir-parameterized state computation for ops-radar.

One function per signal that the ops-radar detector aggregates. Each returns a
flat dict of the shape:

    {key, value, threshold, due, severity, tier, summary}

`summary` is a COUNTS-ONLY one-liner (no content, no PII) safe to put on the
Telegram wire. `severity` is one of SEVERITY_ORDER (ok < warn < high < critical)
and drives the ack "band" comparison and the crunch critical-floor. `tier` is
"A" (machine-domain, auto-healable) or "B" (sovereign manual action, nudge-only).

Design split (per plan Decision 8 + testability): the expensive / non-
deterministic measurement (git plumbing, an ollama probe, a subprocess) is kept
separate from the PURE classifier that turns measured primitives into the signal
dict. The classifiers (`classify_backup`, `classify_ollama`, `classify_cold_sweep`,
`classify_publish`, `classify_index`, `classify_weekly_review`, `classify_odin`)
are unit-tested in isolation; the measurement wrappers (`backup_state`,
`ollama_state`, ...) call them after gathering primitives.

READ ONLY. No function here mutates workspace state. Consumed by
scripts/ops-radar.py.
"""
from __future__ import annotations

import json
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

# ============================================================
# Severity + thresholds
# ============================================================

# Ordered weakest -> strongest. The crunch critical-floor is severity == "critical".
SEVERITY_ORDER = ["ok", "warn", "high", "critical"]


def severity_rank(sev: str) -> int:
    """Numeric rank of a severity label (unknown -> 0)."""
    return SEVERITY_ORDER.index(sev) if sev in SEVERITY_ORDER else 0


# Tier-B (sovereign manual) thresholds.
BACKUP_UNCOMMITTED_HOURS = 24      # uncommitted work sitting this long is due
BACKUP_HIGH_HOURS = 48             # ... escalates to high
BACKUP_CRITICAL_HOURS = 72         # ... pierces crunch (imminent data-loss floor)
WEEKLY_REVIEW_DAYS = 7             # days since last review file -> due
WEEKLY_REVIEW_HIGH_DAYS = 14
COLD_SWEEP_RED = 5                 # red-debt contact count -> due
COLD_SWEEP_HIGH = 12
PUBLISH_PENDING = 1                # >=1 corporate-routed change since last BUILD -> due (approximate, v1)

# Tier-A (machine-domain, auto-heal) thresholds.
INDEX_STALE_DAYS = 2               # index older than this (build age) -> rebuild
AUTOHEAL_ESCALATE = 2              # consecutive auto-heal failures before surfacing in the nudge

# Default local embedder host (mirrors config/memory-index.yaml default).
OLLAMA_HOST = "http://localhost:11434"
EMBED_MODEL_PREFIX = "bge-m3"


# ============================================================
# Tier-B: backup (git, both repos)
# ============================================================

def classify_backup(uncommitted: int, oldest_age_hours: float, ahead: int) -> dict:
    """Pure: turn measured git primitives into the backup signal dict.

    due when uncommitted work has sat >= BACKUP_UNCOMMITTED_HOURS, OR any commit
    is unpushed (ahead > 0). Severity escalates with the age of the oldest
    uncommitted change; >= BACKUP_CRITICAL_HOURS is the crunch-piercing floor.
    """
    due = (uncommitted > 0 and oldest_age_hours >= BACKUP_UNCOMMITTED_HOURS) or ahead > 0
    if uncommitted > 0 and oldest_age_hours >= BACKUP_CRITICAL_HOURS:
        severity = "critical"
    elif uncommitted > 0 and oldest_age_hours >= BACKUP_HIGH_HOURS:
        severity = "high"
    elif due:
        severity = "warn"
    else:
        severity = "ok"
    return {
        "key": "backup",
        "value": {
            "uncommitted": uncommitted,
            "oldest_age_hours": round(oldest_age_hours, 1),
            "ahead": ahead,
        },
        "threshold": BACKUP_UNCOMMITTED_HOURS,
        "due": due,
        "severity": severity,
        "tier": "B",
        "summary": (
            f"backup: {uncommitted} uncommitted "
            f"({oldest_age_hours:.0f}h old), {ahead} unpushed"
        ),
    }


def _run_git(repo: Path, args: list[str]) -> tuple[int, str]:
    """Run git in `repo`, return (returncode, stdout). Never raises."""
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=str(repo),
            capture_output=True,
            text=True,
            timeout=20,
        )
    except (OSError, subprocess.SubprocessError):
        return 1, ""
    return proc.returncode, proc.stdout


def _repo_uncommitted(repo: Path) -> tuple[int, float]:
    """Return (uncommitted_count, oldest_age_hours) for one git repo.

    oldest_age_hours = now minus the OLDEST mtime among the dirty paths (how long
    work has been sitting). Paths that cannot be stat'd (deletions) are skipped.
    """
    rc, out = _run_git(repo, ["status", "--porcelain"])
    if rc != 0 or not out.strip():
        return 0, 0.0
    entries = [ln for ln in out.splitlines() if ln.strip()]
    now = time.time()
    oldest_mtime = None
    for ln in entries:
        # porcelain line: "XY <path>" (path starts at col 3); rename uses " -> ".
        path = ln[3:].strip()
        if " -> " in path:
            path = path.split(" -> ", 1)[1].strip()
        fp = repo / path
        try:
            mt = fp.stat().st_mtime
        except OSError:
            continue
        if oldest_mtime is None or mt < oldest_mtime:
            oldest_mtime = mt
    age_hours = (now - oldest_mtime) / 3600.0 if oldest_mtime is not None else 0.0
    return len(entries), age_hours


def _repo_ahead(repo: Path) -> int:
    """Commits on HEAD not on the upstream (or origin/main fallback)."""
    rc, out = _run_git(repo, ["rev-list", "--count", "@{u}..HEAD"])
    if rc != 0:
        rc, out = _run_git(repo, ["rev-list", "--count", "origin/main..HEAD"])
    if rc != 0:
        return 0
    try:
        return int(out.strip() or "0")
    except ValueError:
        return 0


def backup_state(engine_root: Path, data_root: Path) -> dict:
    """Measure git backup debt across BOTH repos, then classify.

    Aggregates the engine clone and the data overlay: total uncommitted, the
    oldest sitting change across both, and total unpushed commits.
    """
    repos = [engine_root]
    if data_root.resolve() != engine_root.resolve():
        repos.append(data_root)
    total_uncommitted = 0
    oldest_age = 0.0
    total_ahead = 0
    for repo in repos:
        if not (repo / ".git").exists():
            continue
        n, age = _repo_uncommitted(repo)
        total_uncommitted += n
        oldest_age = max(oldest_age, age)
        total_ahead += _repo_ahead(repo)
    return classify_backup(total_uncommitted, oldest_age, total_ahead)


# ============================================================
# Tier-B: weekly review (fs)
# ============================================================

def classify_weekly_review(days_since: int | None) -> dict:
    """Pure: days since the newest weekly-review file -> signal dict.

    None means no review has ever been written (treated as due, high)."""
    if days_since is None:
        due, severity = True, "high"
        value = "never"
    else:
        due = days_since >= WEEKLY_REVIEW_DAYS
        if days_since >= WEEKLY_REVIEW_HIGH_DAYS:
            severity = "high"
        elif due:
            severity = "warn"
        else:
            severity = "ok"
        value = days_since
    return {
        "key": "weekly_review",
        "value": value,
        "threshold": WEEKLY_REVIEW_DAYS,
        "due": due,
        "severity": severity,
        "tier": "B",
        "summary": (
            "weekly-review: never run" if days_since is None
            else f"weekly-review: {days_since}d since last"
        ),
    }


def weekly_review_state(outputs_dir: Path, now: float | None = None) -> dict:
    """Days since the newest file mtime under outputs/operations/weekly-review/."""
    review_dir = outputs_dir / "operations" / "weekly-review"
    now = time.time() if now is None else now
    newest = None
    if review_dir.is_dir():
        for p in review_dir.rglob("*"):
            if not p.is_file():
                continue
            try:
                mt = p.stat().st_mtime
            except OSError:
                continue
            if newest is None or mt > newest:
                newest = mt
    days_since = None if newest is None else int((now - newest) // 86400)
    return classify_weekly_review(days_since)


# ============================================================
# Tier-B: cold-sweep (crm-health red debt)
# ============================================================

def classify_cold_sweep(red_count: int) -> dict:
    """Pure: red-debt contact count -> signal dict."""
    due = red_count >= COLD_SWEEP_RED
    if red_count >= COLD_SWEEP_HIGH:
        severity = "high"
    elif due:
        severity = "warn"
    else:
        severity = "ok"
    return {
        "key": "cold_sweep",
        "value": red_count,
        "threshold": COLD_SWEEP_RED,
        "due": due,
        "severity": severity,
        "tier": "B",
        "summary": f"cold-sweep: {red_count} red-debt contacts",
    }


def cold_sweep_state(engine_root: Path) -> dict:
    """Count red-health contacts via crm-health.py --json, then classify.

    Degrades to red_count=0 (not due) when crm-health is absent or unreadable -
    a missing CRM is not a cold-sweep emergency.
    """
    script = engine_root / "scripts" / "crm-health.py"
    red = 0
    if script.exists():
        try:
            proc = subprocess.run(
                ["python3", str(script), "--json"],
                cwd=str(engine_root),
                capture_output=True,
                text=True,
                timeout=60,
            )
            if proc.returncode == 0:
                data = json.loads(proc.stdout)
                red = sum(1 for c in data if c.get("health") == "red")
        except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
            red = 0
    return classify_cold_sweep(red)


# ============================================================
# Tier-B: publish-to-fleet (approximate, v1)
# ============================================================

def classify_publish(pending: int) -> dict:
    """Pure: count of corporate-routed pending changes -> signal dict."""
    due = pending >= PUBLISH_PENDING
    severity = "warn" if due else "ok"
    return {
        "key": "publish",
        "value": pending,
        "threshold": PUBLISH_PENDING,
        "due": due,
        "severity": severity,
        "tier": "B",
        "summary": f"publish-to-fleet: {pending} corporate change(s) pending",
    }


def publish_state(engine_root: Path) -> dict:
    """Approximate pending-publish count via publish-corporate.py --dry-run.

    v1 approximation (Open Q 3): parse the dry-run pending count; exact per-file
    diff is deferred to v2. Degrades to 0 (not due) when the script is absent or
    the dry-run fails - publish debt is advisory, never an emergency.
    """
    script = engine_root / "scripts" / "publish-corporate.py"
    pending = 0
    if script.exists():
        try:
            proc = subprocess.run(
                ["python3", str(script), "--dry-run", "--json"],
                cwd=str(engine_root),
                capture_output=True,
                text=True,
                timeout=60,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                data = json.loads(proc.stdout)
                if isinstance(data, dict):
                    pending = int(
                        data.get("pending")
                        or data.get("changed")
                        or len(data.get("files", []))
                    )
        except (OSError, subprocess.SubprocessError, json.JSONDecodeError, ValueError):
            pending = 0
    return classify_publish(pending)


# ============================================================
# Tier-B: Odin cadence (wrapper over odin-cadence.py --json)
# ============================================================

def classify_odin(cadence: dict) -> dict:
    """Pure: odin-cadence.py --json result -> signal dict."""
    nudge = bool(cadence.get("nudge"))
    total = cadence.get("unharvested_total", 0)
    clusters = cadence.get("reflect_clusters", 0)
    stale = cadence.get("stale_clusters", 0)
    if stale >= 1:
        severity = "high"
    elif nudge:
        severity = "warn"
    else:
        severity = "ok"
    return {
        "key": "odin_cadence",
        "value": {"unharvested": total, "clusters": clusters, "stale": stale},
        "threshold": cadence.get("min_entries", 0),
        "due": nudge,
        "severity": severity,
        "tier": "B",
        "summary": (
            f"odin: {total} un-harvested, {clusters} clusters"
            + (f" ({stale} stale)" if stale else "")
        ),
    }


def classify_queue(ready: int, failed: int) -> dict:
    """Pure: Action Queue drafts awaiting the CEO -> signal dict (Tier B).

    due when >= 1 draft is ready_for_review OR >= 1 card is send_failed. A failed
    send escalates to high (it needs attention, not just a nudge)."""
    due = ready >= 1 or failed >= 1
    severity = "high" if failed >= 1 else ("warn" if due else "ok")
    summary = f"queue: {ready} draft(s) ready" + (f" ({failed} failed)" if failed else "")
    return {
        "key": "queue",
        "value": {"ready": ready, "failed": failed},
        "threshold": 1,
        "due": due,
        "severity": severity,
        "tier": "B",
        "summary": summary,
    }


def queue_state(data_root: Path) -> dict:
    """Count Action Queue cards awaiting the CEO (ready_for_review) and failed
    sends, then classify. Reads the queue store under the DATA root; degrades to
    zero (not due) when the store is absent or unreadable."""
    qpath = data_root / "outputs" / "operations" / "action-queue" / "queue.json"
    ready = failed = 0
    try:
        data = json.loads(qpath.read_text(encoding="utf-8"))
        for c in data.get("actions", []):
            status = c.get("status")
            if status == "send_failed":
                failed += 1
            elif status in ("pending", "approved") and c.get("draft_status") == "ready_for_review":
                ready += 1
    except (OSError, json.JSONDecodeError):
        pass
    return classify_queue(ready, failed)


def odin_cadence_state(engine_root: Path) -> dict:
    """Run odin-cadence.py --json (reused compute), then classify."""
    script = engine_root / "scripts" / "odin-cadence.py"
    cadence: dict = {}
    if script.exists():
        try:
            proc = subprocess.run(
                ["python3", str(script), "--json"],
                cwd=str(engine_root),
                capture_output=True,
                text=True,
                timeout=60,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                cadence = json.loads(proc.stdout)
        except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
            cadence = {}
    return classify_odin(cadence)


# ============================================================
# Tier-A: ollama (probe)
# ============================================================

def classify_ollama(reachable: bool, model_present: bool) -> dict:
    """Pure: ollama reachability + bge-m3 presence -> signal dict (Tier A)."""
    due = (not reachable) or (not model_present)
    if not reachable:
        severity, summary = "high", "ollama: unreachable"
    elif not model_present:
        severity, summary = "high", f"ollama: up but {EMBED_MODEL_PREFIX} missing"
    else:
        severity, summary = "ok", "ollama: up"
    return {
        "key": "ollama",
        "value": {"reachable": reachable, "model_present": model_present},
        "threshold": None,
        "due": due,
        "severity": severity,
        "tier": "A",
        "summary": summary,
    }


def ollama_state(host: str | None = None, timeout: int = 3) -> dict:
    """Probe the local ollama endpoint for reachability + the embed model.

    Read-only HTTP GET to /api/tags. Unreachable host -> due (Tier-A heal will
    try to restart it). The host is injectable for tests (point at a dead port
    to deterministically exercise the unreachable path).
    """
    host = host or OLLAMA_HOST
    url = f"{host.rstrip('/')}/api/tags"
    reachable = False
    model_present = False
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        reachable = True
        models = body.get("models", []) or []
        for m in models:
            name = (m.get("name") or m.get("model") or "") if isinstance(m, dict) else str(m)
            if name.startswith(EMBED_MODEL_PREFIX):
                model_present = True
                break
    except (urllib.error.URLError, OSError, json.JSONDecodeError, ValueError):
        reachable = False
    return classify_ollama(reachable, model_present)


# ============================================================
# Tier-A: memory-index freshness (fs)
# ============================================================

def classify_index(build_age_days: int | None, sources_newer: bool) -> dict:
    """Pure: index build age + whether sources are newer than the last build.

    None build age means the index was never built (due, high). Otherwise due
    when sources changed since the last build OR the build is older than
    INDEX_STALE_DAYS.
    """
    if build_age_days is None:
        return {
            "key": "memory_index",
            "value": "absent",
            "threshold": INDEX_STALE_DAYS,
            "due": True,
            "severity": "high",
            "tier": "A",
            "summary": "memory-index: never built",
        }
    due = sources_newer or build_age_days >= INDEX_STALE_DAYS
    if sources_newer or build_age_days >= INDEX_STALE_DAYS * 3:
        severity = "high"
    elif due:
        severity = "warn"
    else:
        severity = "ok"
    return {
        "key": "memory_index",
        "value": {"build_age_days": build_age_days, "sources_newer": sources_newer},
        "threshold": INDEX_STALE_DAYS,
        "due": due,
        "severity": severity,
        "tier": "A",
        "summary": (
            f"memory-index: {build_age_days}d old"
            + (", sources newer" if sources_newer else "")
        ),
    }


# Source dirs whose *.md mtime indicates "content changed since last build".
# Relative to the DATA root (content store) and ENGINE root (code store).
_DATA_SOURCE_DIRS = ("knowledge", "threads", "context")
_ENGINE_SOURCE_DIRS = (".claude/skills", ".claude/rules")


def _newest_mtime(base: Path, rel_dirs: tuple[str, ...]) -> float | None:
    newest = None
    for rel in rel_dirs:
        d = base / rel
        if not d.is_dir():
            continue
        for p in d.rglob("*.md"):
            try:
                mt = p.stat().st_mtime
            except OSError:
                continue
            if newest is None or mt > newest:
                newest = mt
    return newest


def index_freshness_state(engine_root: Path, data_root: Path, now: float | None = None) -> dict:
    """Compare newest indexed source vs the last successful build (index.db mtime).

    The content index is .memory-index/index.db under the DATA root; its mtime is
    the proxy for the last successful build (the build writes it on success and
    fails loud otherwise). Sources newer than that mtime -> the index is stale.
    """
    now = time.time() if now is None else now
    index_db = data_root / ".memory-index" / "index.db"
    try:
        build_mtime = index_db.stat().st_mtime
    except OSError:
        return classify_index(None, False)
    build_age_days = int((now - build_mtime) // 86400)
    newest_data = _newest_mtime(data_root, _DATA_SOURCE_DIRS)
    newest_engine = _newest_mtime(engine_root, _ENGINE_SOURCE_DIRS)
    newest_source = max((m for m in (newest_data, newest_engine) if m is not None), default=None)
    sources_newer = newest_source is not None and newest_source > build_mtime
    return classify_index(build_age_days, sources_newer)
