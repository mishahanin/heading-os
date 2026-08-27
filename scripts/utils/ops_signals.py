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
dict. The classifiers (`classify_backup`, `classify_ollama`, `classify_ollama_accel`,
`classify_cold_sweep`,
`classify_publish`, `classify_index`, `classify_weekly_review`, `classify_odin`)
are unit-tested in isolation; the measurement wrappers (`backup_state`,
`ollama_state`, ...) call them after gathering primitives.

READ ONLY. No function here mutates workspace state. Consumed by
scripts/ops-radar.py.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import yaml

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

# Fallback endpoint for `ollama_state` when this machine pins no host. It is NOT
# "the local daemon by policy" any more: on 2026-08-23 the ollama inside WSL was
# uninstalled and every model moved to the Windows side, so a monitor hardwired
# to localhost would have gone red forever and driven Tier-A to keep restarting
# a unit that no longer exists. `ollama_state` now probes whatever
# `config/ollama-hosts.yaml` says serves this machine, and falls back here only
# on a machine that pins nothing.
OLLAMA_HOST = "http://localhost:11434"

# The model name is NOT a constant here. It was `EMBED_MODEL_PREFIX = "bge-m3"`
# until 2026-08-22, which made this monitor report on a model the workspace may
# have stopped using: change `model:` in config/memory-index.yaml and the radar
# would keep announcing that `bge-m3` is missing while the index embedded happily
# with the new one. A monitor that hardcodes what it monitors is the one copy of
# a literal that produces a WRONG answer rather than a merely wasteful one.
# Read per call from the single source: `embeddings.index_embed_model()`, which
# reads the config and probes nothing.


# ============================================================
# Tier-B: backup (git, both repos)
# ============================================================

def classify_backup(uncommitted: int, oldest_age_hours: float | None, ahead: int,
                    unreadable: int = 0) -> dict:
    """Pure: turn measured git primitives into the backup signal dict.

    due when uncommitted work has sat >= BACKUP_UNCOMMITTED_HOURS, OR any commit
    is unpushed (ahead > 0). Severity escalates with the age of the oldest
    uncommitted change; >= BACKUP_CRITICAL_HOURS is the crunch-piercing floor.

    Two arguments carry "I could not measure this", and both escalate rather
    than reassure:

    `oldest_age_hours=None` means dirty paths exist and NONE of them could be
    stat'd. That is the ordinary case for deletions: `git status --porcelain`
    lists " D f1.txt" and the file is gone, so the old code skipped every entry,
    left `oldest_mtime` at None and reported 0.0 hours. Three files deleted a
    week ago read as "3 uncommitted (0h old)" and not due, because due needs an
    age >= BACKUP_UNCOMMITTED_HOURS. Measured 2026-08-26.

    `unreadable` counts repos whose `git status` itself failed. That returned
    (0, 0.0) and summed into a clean total, so a repo git could not read at all
    reported as a repo with nothing to back up.
    """
    age_known = oldest_age_hours is not None
    age = oldest_age_hours if age_known else 0.0
    stale = uncommitted > 0 and (not age_known or age >= BACKUP_UNCOMMITTED_HOURS)
    due = stale or ahead > 0 or unreadable > 0
    if uncommitted > 0 and age_known and age >= BACKUP_CRITICAL_HOURS:
        severity = "critical"
    elif (unreadable > 0
          or (uncommitted > 0 and not age_known)
          or (uncommitted > 0 and age >= BACKUP_HIGH_HOURS)):
        severity = "high"
    elif due:
        severity = "warn"
    else:
        severity = "ok"
    age_text = f"{age:.0f}h old" if age_known else "age unknown"
    summary = (f"backup: {uncommitted} uncommitted ({age_text}), {ahead} unpushed")
    if unreadable:
        summary += f", {unreadable} repo(s) git could not read"
    return {
        "key": "backup",
        "value": {
            "uncommitted": uncommitted,
            "oldest_age_hours": round(age, 1) if age_known else None,
            "ahead": ahead,
            "unreadable": unreadable,
        },
        "threshold": BACKUP_UNCOMMITTED_HOURS,
        "due": due,
        "severity": severity,
        "tier": "B",
        "summary": summary,
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


def _repo_uncommitted(repo: Path) -> tuple[int | None, float | None]:
    """Return (uncommitted_count, oldest_age_hours) for one git repo.

    Two None values, and each means something different from zero:

    - count None: `git status` itself failed, so nothing was measured. This used
      to be folded in with "clean" and returned (0, 0.0), which is how a repo
      with an unreachable gitdir reported "0 uncommitted". Reproduced 2026-08-26
      with a `.git` file pointing at a directory that does not exist: git exits
      128 and prints nothing on stdout.
    - age None: dirty paths exist and none of them could be stat'd. Deletions
      are the common case, and reporting 0.0 hours for them says "just now",
      which is the reading that keeps the signal quiet.

    oldest_age_hours = now minus the OLDEST mtime among the dirty paths (how long
    work has been sitting).
    """
    # `-z`, not plain porcelain. Git C-QUOTES any path holding a space, a
    # non-ASCII byte or a backslash: `?? "caf\303\251.md"`, quotes and octal
    # escapes included. Those bytes are not the filename, so `stat()` raised
    # FileNotFoundError and the path was dropped from the oldest-mtime scan
    # entirely - understating backup debt in exactly the way this function's
    # docstring says must not happen. `-z` suppresses quoting and separates
    # records with NUL, so nothing needs decoding.
    rc, out = _run_git(repo, ["status", "--porcelain", "-z"])
    if rc != 0:
        return None, None
    # Under -z a rename is TWO NUL-separated fields for ONE entry: "R  <new>"
    # then "<old>". Consume the second so it is neither counted as a change nor
    # stat'd as a path that no longer exists.
    fields = [f for f in out.split("\0") if f]
    if not fields:
        return 0, 0.0
    entries: list[str] = []
    i = 0
    while i < len(fields):
        record = fields[i]
        entries.append(record)
        if record[:1] in ("R", "C"):
            i += 1  # skip the source path that follows
        i += 1
    now = time.time()
    oldest_mtime = None
    for record in entries:
        # porcelain record: "XY <path>"; the path starts at column 3.
        path = record[3:].strip()
        fp = repo / path
        try:
            mt = fp.stat().st_mtime
        except OSError:
            continue
        if oldest_mtime is None or mt < oldest_mtime:
            oldest_mtime = mt
    age_hours = (now - oldest_mtime) / 3600.0 if oldest_mtime is not None else None
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
    oldest_age: float | None = 0.0
    total_ahead = 0
    unreadable = 0
    for repo in repos:
        if not (repo / ".git").exists():
            continue
        n, age = _repo_uncommitted(repo)
        if n is None:
            # git could not read this repo at all. Counted and reported; the old
            # code summed its (0, 0.0) into the totals and said nothing.
            unreadable += 1
            total_ahead += _repo_ahead(repo)
            continue
        total_uncommitted += n
        if age is None:
            oldest_age = None  # unknown wins: it can only be older, never newer
        elif oldest_age is not None:
            oldest_age = max(oldest_age, age)
        total_ahead += _repo_ahead(repo)
    return classify_backup(total_uncommitted, oldest_age, total_ahead, unreadable)


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
    """Days since the newest file mtime under outputs/operations/reviews/.

    This must match where the /weekly-review skill actually saves reviews
    (outputs/operations/reviews/YYYY-MM-DD-weekly-review.md). A prior mismatch
    (this read operations/weekly-review/, which the skill never wrote) made the
    signal report "never run" even when reviews existed."""
    review_dir = outputs_dir / "operations" / "reviews"
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

    Degrades to red_count=0 (not due) when crm-health is absent, unreadable, or
    emits something other than a list of contact dicts - a missing CRM is not a
    cold-sweep emergency, and neither is a changed output shape.

    That last clause was false until 2026-08-23. The comprehension below calls
    `c.get(...)` on whatever the JSON parsed to, and the except clause caught
    only OSError, SubprocessError and JSONDecodeError. A dict, a list of
    strings, a bare null - all parse cleanly and then raise AttributeError or
    TypeError, killing the whole ops-radar run. The monitor that exists to
    surface silent failures died on the shape of what it monitors.
    `publish_state`, twenty lines below, already guards with `isinstance`.
    """
    script = engine_root / "scripts" / "crm-health.py"
    red = 0
    if script.exists():
        try:
            proc = subprocess.run(
                [sys.executable, str(script), "--json"],
                cwd=str(engine_root),
                capture_output=True,
                text=True,
                timeout=60,
            )
            if proc.returncode == 0:
                data = json.loads(proc.stdout)
                if isinstance(data, list):
                    red = sum(1 for c in data
                              if isinstance(c, dict) and c.get("health") == "red")
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
    """Pending-publish count via `publish-corporate.py --preview --json`.

    Degrades to 0 (not due) when the script is absent or the preview fails -
    publish debt is advisory, never an emergency.

    The argv used to read `--dry-run --json`, and `publish-corporate.py` has
    never defined either flag: its only options are --preview / --copy /
    --verify / --bump-build (a required mutually exclusive group), --summary,
    --structural and --files-changed. Every run exited 2 on the argparse error,
    so `proc.returncode == 0` was unreachable, `pending` never left 0, and this
    signal could not fire whatever the fleet owed. Measured 2026-08-27 against a
    tree with real corporate changes: `due=False`, threshold 1.
    `tests/test_ops_signals.py` exercises only the pure classifier, which takes
    an already-computed integer and so cannot notice that the integer is always
    zero. `--preview --json` now exists, and a test asserts this argv parses.
    """
    script = engine_root / "scripts" / "publish-corporate.py"
    pending = 0
    if script.exists():
        try:
            proc = subprocess.run(
                [sys.executable, str(script), "--preview", "--json"],
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
    except (OSError, json.JSONDecodeError):
        return classify_queue(ready, failed)

    # Valid JSON of the WRONG SHAPE was not handled, and only OSError and
    # JSONDecodeError were caught. Five payloads written to the real queue path
    # each took the signal down with an uncaught exception: `[]`, `null` and
    # `"str"` raised AttributeError on `.get`; `{"actions": ["oops"]}` raised
    # AttributeError on a string card; `{"actions": null}` raised TypeError on
    # iteration. Measured 2026-08-26. This runs inside the ops radar, so one
    # malformed queue file took out every other signal beside it.
    actions = data.get("actions") if isinstance(data, dict) else None
    if not isinstance(actions, list):
        return classify_queue(ready, failed)
    for c in actions:
        if not isinstance(c, dict):
            continue
        status = c.get("status")
        if status == "send_failed":
            failed += 1
        elif status in ("pending", "approved") and c.get("draft_status") == "ready_for_review":
            ready += 1
    return classify_queue(ready, failed)


def odin_cadence_state(engine_root: Path) -> dict:
    """Run odin-cadence.py --json (reused compute), then classify."""
    script = engine_root / "scripts" / "odin-cadence.py"
    cadence: dict = {}
    if script.exists():
        try:
            proc = subprocess.run(
                [sys.executable, str(script), "--json"],
                cwd=str(engine_root),
                capture_output=True,
                text=True,
                timeout=60,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                parsed = json.loads(proc.stdout)
                # A helper that prints `null`, a list or a number is valid JSON
                # and is not a cadence report. Without this, `classify_odin`
                # raised AttributeError on `.get` and took the radar with it.
                cadence = parsed if isinstance(parsed, dict) else {}
        except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
            cadence = {}
    return classify_odin(cadence)


# ============================================================
# Tier-A: ollama (probe)
# ============================================================

def classify_ollama(reachable: bool, model_present: bool | None,
                    model: str | None = None) -> dict:
    """Pure: ollama reachability + embed-model presence -> signal dict (Tier A).

    `model` names the tag in the summary line. The CALLER supplies it, because
    this function is pure and reading the config here would end that. Omitted
    means the generic wording, which is right for a test that is asserting on
    reachability and has no opinion about the tag.

    `model_present=None` means NOT CHECKED, and is not the same as False. Since
    2026-08-23 the embed model is deliberately absent from the local daemon -
    embedding is pinned to the Windows side and `bge-m3` was removed here so the
    pin cannot be defeated by accident. A monitor that kept asserting its
    presence would report a permanent, false, unfixable failure; presence on the
    host that DOES need it is `ollama_accel_state`'s job.
    """
    due = (not reachable) or (model_present is False)
    named = model or "the embed model"
    if not reachable:
        severity, summary = "high", "ollama: unreachable"
    elif model_present is False:
        severity, summary = "high", f"ollama: up but {named} missing"
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


def classify_ollama_accel(configured: bool, reachable: bool,
                          model_present: bool | None = None) -> dict:
    """Pure: accelerated-host configuration + reachability -> signal dict (Tier B).

    Not configured is the normal state on most machines and is never due. A host
    that IS configured and does not answer is due at `high`, not `warn`: until
    2026-08-23 work continued on the local daemon and the cost was only speed,
    but embedding is now pinned here, so this host being down means recall and
    every rebuild stop. `model_present` is None when it could not be determined
    (host down, or the tags endpoint failed) and is never guessed.

    Tier B, not A, and deliberately. The accelerated daemon lives outside this
    OS (on a WSL2 workspace it is the Windows side), so nothing here can restart
    it; a Tier-A signal would wait for an auto-heal that cannot exist and stay
    invisible forever, since `select_candidates` only surfaces Tier A after two
    failed heals.
    """
    due = configured and (not reachable or model_present is False)
    if not configured:
        severity, summary = "ok", "ollama-accel: not configured"
    elif not reachable:
        # No longer "running on the local daemon": since 2026-08-23 embedding is
        # pinned here, so this host being down means nothing embeds at all.
        severity, summary = "high", "ollama-accel: pinned embed host down -- nothing can embed"
    elif model_present is False:
        severity, summary = "high", "ollama-accel: up but the embed model is not pulled there"
    else:
        severity, summary = "ok", "ollama-accel: up"
    return {
        "key": "ollama_accel",
        "value": {"configured": configured, "reachable": reachable,
                  "model_present": model_present},
        "threshold": None,
        "due": due,
        "severity": severity,
        "tier": "B",
        "summary": summary,
    }


def ollama_accel_state(engine_root: Path, timeout: int = 3) -> dict:
    """Probe the accelerated ollama host the memory index is configured to use.

    Reads the SAME preference the index reads, in the same order - `host` from
    `config/memory-index.yaml`, else the `HEADING_OS_OLLAMA_EMBED_HOST`
    environment variable, else `embed:` in the machine-local
    `config/ollama-hosts.yaml` - so this reports on the endpoint that is
    actually used, not on a second opinion about which one it should be. A
    preference that resolves to the local daemon is not an accelerated host and
    counts as not configured.

    Keeping this list in step with `index_embed_target` is load-bearing: when the
    pin moved out of the tracked config on 2026-08-23, a monitor still reading
    only that file would have reported "not configured" on a machine that was
    pinned, and gone blind to exactly the outage it exists for.

    Scope: this is the index's EMBEDDING endpoint. Generation (`chronicle.py`,
    `census-submodel-bench.py`) resolves `generate:` from the same machine file
    and may point elsewhere; this signal says nothing about it.
    """
    import yaml

    from scripts.utils import yamlio
    from scripts.utils.ollama_host import (
        LOCAL_HOST,
        host_candidates,
        machine_hosts,
        probe,
    )

    cfg: dict = {}
    config_path = engine_root / "config" / "memory-index.yaml"
    try:
        with open(config_path, encoding="utf-8") as fh:
            cfg = yamlio.safe_load(fh) or {}
    except (OSError, yaml.YAMLError):
        cfg = {}

    preference = (
        cfg.get("host")
        or os.environ.get("HEADING_OS_OLLAMA_EMBED_HOST", "")
        or machine_hosts("embed", root=engine_root)
    )

    # `host_candidates`, not `candidate_url`: since 2026-08-23 the pin may name
    # several ports on the same machine, and reading only the first entry would
    # call a healthy second one "not configured" - a monitor blind in exactly the
    # case the list was added for.
    candidates = [c for c in host_candidates(preference) if c != LOCAL_HOST]
    if not candidates:
        return classify_ollama_accel(False, False)

    for candidate in candidates:
        if probe(candidate, timeout=timeout):
            return classify_ollama_accel(
                True, True, model_present=_embed_model_present(candidate, timeout)
            )
    return classify_ollama_accel(True, False)


def _embed_model_present(host: str, timeout: int = 3) -> bool | None:
    """Whether the configured embed model is pulled on `host`. None if unknown.

    Asked of the PINNED host and nowhere else. Embedding happens there or it does
    not happen, so a missing tag there is a real outage - unlike its deliberate
    absence on the local daemon. None (the tags endpoint hiccuped) is reported as
    unknown rather than as missing: a monitor that guesses is worse than one that
    abstains.
    """
    from scripts.utils.embeddings import index_embed_model

    wanted = index_embed_model()
    # The host comes from config/ollama-hosts.yaml, so its scheme is operator
    # input, not a literal. A `file:` host would make urlopen read a local path
    # and report its contents as an ollama tag list.
    tags_url = f"{host.rstrip('/')}/api/tags"
    if not tags_url.startswith(("http://", "https://")):
        return None
    try:
        with urllib.request.urlopen(tags_url, timeout=timeout) as resp:  # noqa: S310 - scheme guarded above
            body = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, json.JSONDecodeError, ValueError):
        return None
    for m in body.get("models", []) or []:
        name = (m.get("name") or m.get("model") or "") if isinstance(m, dict) else str(m)
        if name.startswith(wanted):
            return True
    return False


def ollama_hosts_in_use(engine_root: Path | None = None) -> list[str]:
    """Every address this machine expects an ollama at. Never empty.

    The union of the `embed` and `generate` pins, in that order, falling back to
    `OLLAMA_HOST` when the machine pins nothing. Probes nothing itself.

    Two pins rather than one because they are allowed to differ, and a monitor
    that watched only the embed host would miss a summarizer pointed elsewhere.
    """
    from scripts.utils.embeddings import index_embed_preference
    from scripts.utils.ollama_host import host_candidates, machine_hosts

    root = engine_root
    preferences = [index_embed_preference(root=root), machine_hosts("generate", root=root)]
    seen: list[str] = []
    for preference in preferences:
        for candidate in host_candidates(preference):
            if candidate not in seen:
                seen.append(candidate)
    return seen or [OLLAMA_HOST]


def ollama_state(host: str | None = None, timeout: int = 3) -> dict:
    """Is an ollama answering for this workspace at all? Tier A.

    Read-only HTTP GET to /api/version against each address in
    `ollama_hosts_in_use()`; the first to answer makes the signal green.
    Unreachable everywhere -> due, and Tier-A heal tries to start one. `host` is
    injectable for tests (point at a dead port to exercise the unreachable path).

    It watched `http://localhost:11434` until 2026-08-23, when the ollama inside
    WSL was uninstalled and every model moved to the Windows side. A monitor left
    pointing at localhost would have reported a permanent outage of a daemon that
    was deliberately removed - the mirror image of the defect fixed the day
    before, where it asserted an embed model whose absence was also deliberate.

    It still does not check for the embed model: presence of the model where it
    IS required is `ollama_accel_state`'s job, on the host that must have it.
    """
    hosts = [host] if host else ollama_hosts_in_use()
    reachable = False
    for candidate in hosts:
        url = f"{candidate.rstrip('/')}/api/version"
        if not url.startswith(("http://", "https://")):
            continue          # same operator-input scheme guard as above
        try:
            with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310 - scheme guarded above
                json.loads(resp.read().decode("utf-8"))
            reachable = True
            break
        except (urllib.error.URLError, OSError, json.JSONDecodeError, ValueError):
            continue
    return classify_ollama(reachable, None)


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


# Fallback source dirs, used ONLY when the indexer's own config cannot be read.
#
# These three were the whole watch list, hand-written beside an indexer that
# ingests fourteen layers. Everything else it indexes went unwatched: a new CRM
# contact, a new auto-memory, a new deliverable under outputs/, a new reference
# file, a new plan, a new linkedin archive entry, a new datastore extract, a new
# chronicle entry, a new skill or rule. Each was indexed and none of them could
# ever make this signal say the index was stale. Measured 2026-08-26 against a
# synthetic data root: four files written after the build (crm/contacts/,
# auto-memory/, outputs/research/, reference/) left `sources_newer` False and
# `severity` ok; one file under knowledge/ flipped it. The watch list is now
# DERIVED from the same config the builder reads, so a layer added there is
# watched here without anyone remembering to.
_FALLBACK_DATA_DIRS = ("knowledge", "threads", "context")
_FALLBACK_ENGINE_DIRS = (".claude/skills", ".claude/rules")

_INDEX_CONFIG_REL = "config/memory-index.yaml"


def _expand_braces(pattern: str) -> list[str]:
    """Expand one `{a,b}` group at a time. pathlib globbing has no braces."""
    start = pattern.find("{")
    if start == -1:
        return [pattern]
    end = pattern.find("}", start)
    if end == -1:
        return [pattern]
    pre, body, post = pattern[:start], pattern[start + 1:end], pattern[end + 1:]
    out = []
    for option in body.split(","):
        out.extend(_expand_braces(pre + option.strip() + post))
    return out


def _index_source_globs(engine_root: Path) -> list[str] | None:
    """Every `glob:` the indexer's layer config declares, braces expanded.

    None when the config cannot be read or declares no globs - the callers below
    then fall back to the hand-written dirs AND say they narrowed, rather than
    reporting a full sweep they did not run.
    """
    path = Path(engine_root) / _INDEX_CONFIG_REL
    try:
        cfg = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return None
    patterns: list[str] = []
    for layer in cfg.get("layers", []) or []:
        # A commit corpus layer carries `source: git-log` and no glob; the
        # builder reads git, so there is no file mtime to compare against.
        glob = (layer or {}).get("glob")
        if isinstance(glob, str) and glob:
            patterns.extend(_expand_braces(glob))
    return patterns or None


def _newest_mtime(base: Path, rel_dirs) -> float | None:
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


def _newest_by_glob(base: Path, patterns) -> float | None:
    """Newest mtime among the files the indexer would actually ingest."""
    newest = None
    base = Path(base)
    for pattern in patterns:
        try:
            matches = base.glob(pattern)
        except (OSError, ValueError):
            continue
        for p in matches:
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
    patterns = _index_source_globs(engine_root)
    if patterns is None:
        # Say what was NOT swept. A narrowed check that prints like a complete
        # one is the defect this whole function was rewritten for.
        print(f"[ops_signals] {_INDEX_CONFIG_REL} unreadable; the staleness check "
              f"covers only {', '.join(_FALLBACK_DATA_DIRS + _FALLBACK_ENGINE_DIRS)}",
              file=sys.stderr)
        newest_data = _newest_mtime(data_root, _FALLBACK_DATA_DIRS)
        newest_engine = _newest_mtime(engine_root, _FALLBACK_ENGINE_DIRS)
    else:
        # Each glob is tried against BOTH roots: the config does not say which
        # store a layer lives in, and a pattern that matches nothing under one
        # root simply yields nothing.
        newest_data = _newest_by_glob(data_root, patterns)
        newest_engine = _newest_by_glob(engine_root, patterns)
    newest_source = max((m for m in (newest_data, newest_engine) if m is not None), default=None)
    sources_newer = newest_source is not None and newest_source > build_mtime
    return classify_index(build_age_days, sources_newer)


# ============================================================
# Router accuracy (F-6.2) - Tier B
# ============================================================

# Point thresholds (rates are 0-1 fractions, scaled *100 to points like eval-drift).
ROUTER_ACCURACY_DROP_PCT = 10.0    # a skill dropping > this many points vs baseline is due (warn)
ROUTER_ACCURACY_HIGH_PCT = 20.0    # ... a bigger single-skill drop escalates to high
ROUTER_ACCURACY_BASELINE_N = 7     # rolling-baseline window (prior records), mirrors eval-drift


def classify_router_accuracy(latest: dict | None, baseline: dict | None) -> dict:
    """Pure: compare the latest router-accuracy record against a rolling baseline.

    `latest` / `baseline` are record-shaped dicts {overall_rate, per_skill:{name:rate}}
    with rates as 0-1 fractions; `baseline` is the per-skill mean of the prior window.
    Point-scaled like eval-drift: drop_pts = (baseline_rate - latest_rate) * 100. due
    when any skill dropped > ROUTER_ACCURACY_DROP_PCT points OR the aggregate overall_rate
    dropped > that; a single skill dropping > ROUTER_ACCURACY_HIGH_PCT or an aggregate drop
    escalates to high. Not due when there is no baseline (< 2 records). Tier B - a sovereign
    manual nudge (the CEO investigates a routing regression), never machine-auto-healable."""
    worst_skill = None
    worst_drop = 0.0
    overall_drop = 0.0
    if latest and baseline:
        lp = latest.get("per_skill") or {}
        bp = baseline.get("per_skill") or {}
        for name, brate in bp.items():
            lrate = lp.get(name)
            if brate is not None and lrate is not None:
                drop = (brate - lrate) * 100.0
                if drop > worst_drop:
                    worst_drop = drop
                    worst_skill = name
        lo = latest.get("overall_rate")
        bo = baseline.get("overall_rate")
        if lo is not None and bo is not None:
            overall_drop = (bo - lo) * 100.0

    # No measurement at all is a reason to raise, not a reason to say ok. Measured
    # 2026-08-03: this returned `due=False, severity="ok"` while the job producing
    # the trend had never run once on any host, so the Tier-B alert described as
    # "waiting on output" reported healthy for the whole of that time. A signal
    # whose absence of data reads as good news cannot detect the failure that
    # matters most, which is the producer being dead. A PRESENT latest with no
    # baseline stays not-due: that is a trend legitimately forming.
    if latest is None:
        return {
            "key": "router_accuracy",
            "value": {"worst_skill": None, "worst_drop_pts": 0.0, "overall_drop_pts": 0.0},
            "threshold": ROUTER_ACCURACY_DROP_PCT,
            "due": True,
            "severity": "warn",
            "tier": "B",
            "summary": ("router-accuracy: no measurement on record - the nightly "
                        "run is not producing data"),
        }

    due = worst_drop > ROUTER_ACCURACY_DROP_PCT or overall_drop > ROUTER_ACCURACY_DROP_PCT
    if worst_drop > ROUTER_ACCURACY_HIGH_PCT or overall_drop > ROUTER_ACCURACY_DROP_PCT:
        severity = "high"
    elif due:
        severity = "warn"
    else:
        severity = "ok"

    if worst_skill:
        summary = (
            f"router-accuracy: {worst_skill} -{worst_drop:.0f}pt vs baseline"
            + (f", overall -{overall_drop:.0f}pt" if overall_drop > 0 else "")
        )
    elif latest is None:
        summary = "router-accuracy: no trend data"
    elif baseline is None:
        summary = "router-accuracy: baseline forming (no comparable prior run)"
    else:
        summary = "router-accuracy: stable"

    return {
        "key": "router_accuracy",
        "value": {
            "worst_skill": worst_skill,
            "worst_drop_pts": round(worst_drop, 1),
            "overall_drop_pts": round(overall_drop, 1),
        },
        "threshold": ROUTER_ACCURACY_DROP_PCT,
        "due": due,
        "severity": severity,
        "tier": "B",
        "summary": summary,
    }


def _read_trend_records(trend_path: Path, limit: int) -> list[dict]:
    """Return up to the last `limit` parsed JSONL OBJECTS; [] if absent/unreadable.

    Non-object lines are dropped. `123` and `"abc"` are valid JSON, so they used
    to survive the JSONDecodeError filter and reach every `.get` downstream:
    `router_accuracy_state` raised AttributeError on an int. Measured
    2026-08-26. A trend file is appended to by a nightly job, and a truncated or
    interleaved write is exactly how a stray scalar line gets there.
    """
    try:
        lines = trend_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    records: list[dict] = []
    for line in lines[-limit:]:
        line = line.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            records.append(parsed)
    return records


def router_accuracy_state(data_root: Path) -> dict:
    """Read the router-accuracy trend under the DATA root, build a rolling baseline
    (per-skill mean of the prior up-to-N records MEASURED BY THE SAME JUDGE MODEL),
    and classify. Degrades to not-due when the trend is absent, has < 2 records, or
    carries no prior run on the current judge. The trend lives under the datastore
    (get_datastore_dir() == data_root/datastore), written by router-accuracy-nightly.py."""
    trend_path = data_root / "datastore" / "operations" / "router-accuracy" / "trend.jsonl"
    records = _read_trend_records(trend_path, ROUTER_ACCURACY_BASELINE_N + 1)
    # A refusal shares the file with the measurements so a silent night is
    # visible, which is the whole point of writing it. It carries no `per_skill`,
    # so counting it as a data point would rebuild the sibling daemon's 0/0
    # baseline in a new place: a trend of pure refusals must read as no data.
    records = [r for r in records if r.get("status") != "refused"]
    if len(records) < 2:
        return classify_router_accuracy(records[-1] if records else None, None)
    latest = records[-1]
    # Compare like with like: the judge is the measuring instrument, and the harness
    # resolves a model FAMILY, so the instrument replaces itself without anyone
    # touching the router. Measured 2026-08-10, the night the Sonnet family advanced
    # a release: 32 of 69 skills "dropped", almost all by exactly one case, and the
    # Tier-B alert named /voss at -38pt while no commit had touched .claude/skills or
    # .claude/rules for two days. A baseline built across a model change measures the
    # models, not the routing.
    prior = [r for r in records[:-1] if r.get("model") == latest.get("model")]
    if not prior:
        return classify_router_accuracy(latest, None)
    sums: dict[str, float] = {}
    counts: dict[str, int] = {}
    overall_sum = 0.0
    overall_n = 0
    for rec in prior:
        ov = rec.get("overall_rate")
        if ov is not None:
            overall_sum += ov
            overall_n += 1
        for name, rate in (rec.get("per_skill") or {}).items():
            if rate is not None:
                sums[name] = sums.get(name, 0.0) + rate
                counts[name] = counts.get(name, 0) + 1
    baseline = {
        "overall_rate": (overall_sum / overall_n) if overall_n else None,
        "per_skill": {name: sums[name] / counts[name] for name in sums},
    }
    return classify_router_accuracy(latest, baseline)
