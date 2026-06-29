#!/usr/bin/env python3
"""Eval-drift daemon -- nightly replay of Langfuse traces against current skills.

Closes P2.2 from the 2026-05-17 workspace audit. Catches the class of regression
where a skill's SKILL.md or model alias quietly drifts away from its eval baseline
and downstream output degrades silently. The daemon:

1. Lists skills under ``.claude/skills/*/evals/cases/`` that have an eval suite.
2. For each one, pulls the last 24h of Langfuse traces whose scope matches the
   skill's @observe wrapper name (best-effort name match).
3. Replays each captured trace input against the current SKILL.md system prompt
   using the same Anthropic client wrapper as ``scripts/run-skill-eval.py``.
4. Runs the existing case-style checks (must_mention / must_not_mention /
   min_words / max_words / hidden_chars_clean) against the replayed output.
5. Compares today's pass rate per skill to a rolling 7-day baseline stored in
   ``datastore/operations/eval-drift/state.json``.
6. Writes a daily Markdown report at
   ``outputs/operations/eval-drift/YYYY-MM-DD.md`` and, if any skill's pass
   rate dropped >5 percentage points vs prior week, marks a regression.

Subcommands:
    daemon  : run forever; APScheduler runs the main task daily at 02:00 local time.
    once    : run a single iteration and exit (smoke + manual).
    status  : print PID, uptime, next scheduled run.
    stop    : signal a running daemon to shut down cleanly (sentinel-file on Windows).

PID file:  .eval-drift/daemon.pid
Log file:  .eval-drift/daemon.log  (rotated by RotatingFileHandler, 1 MB, keep 3)

CLI flags for manual testing:
    --once          run one iteration and exit (alias of `once` subcommand)
    --dry-run       skip API calls, skip report write, skip notifications
    --skill <name>  filter to one skill

Sensitivity-aware: when the session is sensitive (``is_sensitive()`` — the
fail-closed ``SENSITIVE_MODE`` default) this daemon refuses to run. Sensitive
content must never traverse Langfuse / Anthropic via a background schedule.
Mirrors the behavioural gate in ``scripts/utils/observability.py``.

Notification env var (optional):
    EVAL_DRIFT_NOTIFY=none      default; no notification on regression
    EVAL_DRIFT_NOTIFY=telegram  send DM via existing fireside bot path (stub-wired)
    EVAL_DRIFT_NOTIFY=slack     leaves a TODO (no Slack workspace yet)

Exit codes (for `once` and --once):
    0   no regression
    1   one or more skills regressed
    2   setup or environment error
    3   API / Langfuse error
"""
from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import logging
import os
import re
import signal
import sys
import time
from datetime import datetime, timedelta, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

WORKSPACE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WORKSPACE))

from scripts.utils import daemon_heartbeat  # noqa: E402
from scripts.utils import trace  # noqa: E402
from scripts.utils.colors import BOLD, CYAN, GRAY, GREEN, RED, RESET, YELLOW  # noqa: E402
from scripts.utils.healthchecks import ping as hc_ping  # noqa: E402
from scripts.utils.llm_fallback import _is_retriable_anthropic_error  # noqa: E402
from scripts.utils.trace_filter import install_log_factory  # noqa: E402
from scripts.utils.sensitive import is_sensitive  # noqa: E402
from scripts.utils.workspace import get_datastore_dir, get_default_tz, get_default_tz_name, get_outputs_dir, get_workspace_root, load_env  # noqa: E402


class AnthropicAvailabilityError(RuntimeError):
    """Raised when Anthropic is in a retriable-failure state (5xx / 429 /
    connection reset / timeout). The eval-drift daemon catches this at the
    iteration level and aborts the whole run rather than completing with
    partial data or vendor-substituted replays - either path would
    contaminate the regression baseline. The next 24h tick will retry
    naturally.

    Track A decision 2026-05-24: SKIP + re-queue (over fallback-to-Gemini)
    because the daemon's whole purpose is detecting Claude regressions; a
    Gemini-substituted replay produces a baseline number that has nothing
    to do with Claude's behaviour and would silently poison the trend.
    """

# ============================================================
# Configuration
# ============================================================

ROOT = get_workspace_root()
SKILLS_DIR = ROOT / ".claude" / "skills"

# Runtime directory (PID, log, sentinel) - parallels fireside-bot-daemon pattern.
RUNTIME_DIR = ROOT / ".eval-drift"
PID_FILE = RUNTIME_DIR / "daemon.pid"
LOG_FILE = RUNTIME_DIR / "daemon.log"
STARTED_AT_FILE = RUNTIME_DIR / "started_at"
STOP_SENTINEL = RUNTIME_DIR / "stop"

# Durable state lives under datastore/ (one daemon, one concern).
STATE_DIR = get_datastore_dir() / "operations" / "eval-drift"
STATE_FILE = STATE_DIR / "state.json"
ERRORS_LOG = STATE_DIR / "errors.log"

# Report output directory.
REPORT_DIR = get_outputs_dir() / "operations" / "eval-drift"

# Drift / regression thresholds.
PASS_RATE_DROP_PCT = 5.0          # regression if today drops >5pp vs prior 7-day mean
ROLLING_WINDOW_DAYS = 7           # baseline = mean of last 7 days excluding today
TRACE_LOOKBACK_HOURS = 24         # Langfuse query window

# How a daemon iteration is scheduled.
DAILY_HOUR = 2                    # 02:00 local time
DAILY_MINUTE = 0

# Default Anthropic model when a skill's SKILL.md does not declare one.
DEFAULT_MODEL = "claude-haiku-4-5-20251001"
MODEL_ALIAS = {
    "haiku": "claude-haiku-4-5-20251001",
    "sonnet": "claude-sonnet-4-6",
    "opus": "claude-opus-4-7",
}


# ============================================================
# Logging setup
# ============================================================

def _setup_logging() -> logging.Logger:
    """Configure rotating file + stream logger, idempotent."""
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    # R12: mint trace ID + install record factory before any handler.
    trace.mint()
    install_log_factory()
    logger = logging.getLogger("eval-drift-daemon")
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s [%(trace_id)s] %(message)s")
    handler = RotatingFileHandler(LOG_FILE, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(fmt)
    logger.addHandler(handler)
    stream = logging.StreamHandler()
    stream.setFormatter(fmt)
    logger.addHandler(stream)
    return logger


def _log_error(msg: str) -> None:
    """Append a timestamped line to the durable errors log.

    R12: prepend the trace ID so direct errors.log appends correlate with the
    logger-formatted [trace_id] lines in daemon.log for the same run.
    """
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    tid = trace.get() or "-"
    try:
        with ERRORS_LOG.open("a", encoding="utf-8") as fh:
            fh.write(f"{ts} [{tid}] {msg}\n")
    except OSError:
        # Best-effort - never fail the daemon because the log is unwritable.
        pass


# ============================================================
# State Management
# ============================================================

def _atomic_write_json(path: Path, payload: dict) -> None:
    """Write JSON via tmp + os.replace - never leave a torn state file behind."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


def load_state() -> dict:
    """Load durable state - rolling pass-rate history per skill, last_run, errors."""
    if not STATE_FILE.exists():
        return {
            "version": 1,
            "last_run": None,
            "skills": {},
            "errors": [],
        }
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        _log_error(f"state load failed: {e!r}; resetting")
        return {"version": 1, "last_run": None, "skills": {}, "errors": []}


def save_state(state: dict) -> None:
    """Persist state atomically."""
    _atomic_write_json(STATE_FILE, state)


def update_skill_history(state: dict, skill: str, pass_rate: float,
                         passed: int, total: int, ran_at_iso: str) -> None:
    """Append today's pass rate to the skill's rolling window; trim to ROLLING_WINDOW_DAYS + 1.

    The +1 keeps one extra slot so we can detect regression by comparing today's
    rate against the previous window without losing data on the next write.
    """
    skill_state = state["skills"].setdefault(skill, {"history": []})
    skill_state["history"].append({
        "ran_at": ran_at_iso,
        "pass_rate": round(pass_rate, 4),
        "passed": passed,
        "total": total,
    })
    # Keep at most ROLLING_WINDOW_DAYS + 1 entries.
    max_entries = ROLLING_WINDOW_DAYS + 1
    if len(skill_state["history"]) > max_entries:
        skill_state["history"] = skill_state["history"][-max_entries:]
    skill_state["last_pass_rate"] = round(pass_rate, 4)


def compute_baseline(history: list[dict]) -> float | None:
    """Compute the prior-week baseline pass rate (mean of entries before the latest).

    Returns None when there are fewer than 2 entries (no prior week to compare
    against; today cannot be a regression because there is no baseline yet).
    """
    if len(history) < 2:
        return None
    prior = history[:-1]
    rates = [entry["pass_rate"] for entry in prior if "pass_rate" in entry]
    if not rates:
        return None
    return sum(rates) / len(rates)


# ============================================================
# Langfuse Client
# ============================================================

def _sensitive_session() -> bool:
    """Mirror observability.py: refuse to operate during a sensitive session
    (fail-closed SENSITIVE_MODE). Replaces the removed `_secure/` vault check."""
    return is_sensitive()


def _get_langfuse_client():
    """Lazy import of the Langfuse SDK. Returns None when unavailable.

    The langfuse package pulls in numpy transitively which has historically
    failed import on Python 3.14 Windows. Defer the import so that
    --dry-run paths and the daemon's vault-block branch never trigger it.
    """
    try:
        from langfuse import Langfuse  # type: ignore[import-not-found]
    except Exception as e:  # noqa: BLE001 - any import error means "unavailable"
        _log_error(f"langfuse import failed: {e!r}")
        return None

    public_key = os.environ.get("LANGFUSE_PUBLIC_KEY")
    secret_key = os.environ.get("LANGFUSE_SECRET_KEY")
    host = os.environ.get("LANGFUSE_HOST", "https://cloud.langfuse.com")
    if not public_key or not secret_key:
        _log_error("Langfuse keys missing (LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY)")
        return None
    try:
        return Langfuse(public_key=public_key, secret_key=secret_key, host=host)
    except Exception as e:  # noqa: BLE001
        _log_error(f"Langfuse client construction failed: {e!r}")
        return None


def fetch_recent_traces(skill: str, hours: int = TRACE_LOOKBACK_HOURS,
                         dry_run: bool = False) -> list[dict]:
    """Return a list of trace dicts captured by @observe in the last `hours`.

    Each dict has at minimum: ``id``, ``input`` (the user prompt sent to the
    skill), ``output`` (the captured response text). When Langfuse is
    unavailable or dry-run is set, returns ``[]`` so the daemon degrades to
    "no traces, no regression possible".

    Name match: we filter traces by ``name`` containing the skill slug.
    @observe-decorated functions adopt the wrapped function's name by default
    (``call_skill`` in run-skill-eval.py); when that mapping is too loose,
    callers can refine the filter by setting ``LANGFUSE_NAME_FILTER`` in env
    to a regex that must match ``trace.name``.
    """
    if dry_run:
        return []
    client = _get_langfuse_client()
    if client is None:
        return []

    now = datetime.now(timezone.utc)
    from_ts = now - timedelta(hours=hours)
    name_re = os.environ.get("LANGFUSE_NAME_FILTER")
    try:
        name_pattern = re.compile(name_re) if name_re else None
    except re.error:
        name_pattern = None

    # The Langfuse Python SDK exposes a paginated list_traces under
    # ``client.api.trace.list(...)`` per their REST contract. Different
    # SDK versions wrap this slightly differently; we try the documented
    # path first and fall back to ``client.get_traces`` (older alias).
    traces: list[Any] = []
    try:
        api = getattr(client, "api", None)
        if api is not None and hasattr(api, "trace"):
            page = api.trace.list(
                from_timestamp=from_ts.isoformat(),
                to_timestamp=now.isoformat(),
                limit=100,
            )
            # Page object exposes `.data` list per Langfuse contract.
            traces = list(getattr(page, "data", page) or [])
        else:
            # Older SDK fallback.
            traces = list(client.get_traces(from_timestamp=from_ts, to_timestamp=now) or [])  # type: ignore[attr-defined]
    except Exception as e:  # noqa: BLE001
        _log_error(f"Langfuse list_traces failed for {skill}: {e!r}")
        return []

    out: list[dict] = []
    for tr in traces:
        # Normalise to a plain dict regardless of SDK return type.
        def _get(obj: Any, attr: str, default: Any = None) -> Any:
            if isinstance(obj, dict):
                return obj.get(attr, default)
            return getattr(obj, attr, default)

        name = _get(tr, "name", "") or ""
        # Name-based skill filter: either a regex from env, or substring match
        # against the skill slug.
        if name_pattern is not None:
            if not name_pattern.search(str(name)):
                continue
        else:
            if skill not in str(name) and "call_skill" not in str(name):
                continue

        trace_input = _get(tr, "input", None)
        trace_output = _get(tr, "output", None)

        # Inputs in @observe come from the wrapped function's args. For
        # run-skill-eval's ``call_skill(system_prompt, user_input, model)``
        # Langfuse stores args; pull the second positional or the
        # ``user_input`` keyword. Be defensive about shape.
        user_input = _extract_user_input(trace_input)
        if not user_input:
            continue

        out.append({
            "id": _get(tr, "id", ""),
            "name": str(name),
            "input": user_input,
            "output": _coerce_text(trace_output),
            "timestamp": str(_get(tr, "timestamp", "") or ""),
        })
    return out


def _extract_user_input(raw: Any) -> str:
    """Pull the user prompt out of a Langfuse-captured input payload.

    Langfuse stores function call inputs as a dict-of-args. We accept the
    common shapes seen in this workspace and return an empty string when
    nothing usable is present.
    """
    if raw is None:
        return ""
    if isinstance(raw, str):
        return raw
    if isinstance(raw, list):
        # Sometimes captured as positional list; second element is user_input.
        if len(raw) >= 2 and isinstance(raw[1], str):
            return raw[1]
        # Otherwise take the first string.
        for item in raw:
            if isinstance(item, str) and item:
                return item
        return ""
    if isinstance(raw, dict):
        for key in ("user_input", "input", "prompt", "user", "messages"):
            v = raw.get(key)
            if isinstance(v, str) and v:
                return v
        # args/kwargs shape
        args = raw.get("args")
        if isinstance(args, list) and len(args) >= 2 and isinstance(args[1], str):
            return args[1]
        kwargs = raw.get("kwargs")
        if isinstance(kwargs, dict):
            for key in ("user_input", "input", "prompt"):
                v = kwargs.get(key)
                if isinstance(v, str) and v:
                    return v
    return ""


def _coerce_text(raw: Any) -> str:
    """Coerce a Langfuse output payload to plain text for diffing."""
    if raw is None:
        return ""
    if isinstance(raw, str):
        return raw
    if isinstance(raw, list):
        return "\n".join(_coerce_text(x) for x in raw)
    if isinstance(raw, dict):
        # Anthropic-style content blocks
        if "text" in raw and isinstance(raw["text"], str):
            return raw["text"]
        for key in ("output", "content", "response", "text"):
            v = raw.get(key)
            if v is not None:
                return _coerce_text(v)
    try:
        return json.dumps(raw, default=str)[:2000]
    except (TypeError, ValueError):
        return str(raw)[:2000]


# ============================================================
# Eval Replay
# ============================================================

def _load_run_skill_eval():
    """Dynamic import of run-skill-eval.py (hyphenated filename).

    Mirrors fireside-bot-daemon's pattern. Keeps a single source of truth
    for system-prompt loading + check semantics; we don't reimplement them.
    """
    path = ROOT / "scripts" / "run-skill-eval.py"
    if not path.exists():
        raise FileNotFoundError(f"scripts/run-skill-eval.py not found at {path}")
    spec = importlib.util.spec_from_file_location("run_skill_eval", str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def list_skills_with_evals() -> list[str]:
    """Sorted list of skill names that have at least one eval case on disk."""
    if not SKILLS_DIR.exists():
        return []
    out: list[str] = []
    for child in sorted(SKILLS_DIR.iterdir()):
        if not child.is_dir():
            continue
        cases_dir = child / "evals" / "cases"
        if not cases_dir.exists():
            continue
        cases = list(cases_dir.glob("*.json"))
        if cases:
            out.append(child.name)
    return out


def replay_trace(rse_mod: Any, skill_dir: Path, trace: dict,
                 dry_run: bool) -> tuple[str, list[dict]]:
    """Replay one Langfuse trace's input against the current SKILL.md.

    Returns (replayed_output, check_results).

    The case used for checks is constructed on the fly:
    - If the skill has eval cases, we apply the FIRST case's `checks` block
      to the replayed output - this gives drift a comparable baseline to
      run-skill-eval without needing per-trace check overrides.
    - If there are no cases (caller already filtered), we return empty checks.
    """
    system_prompt, frontmatter = rse_mod.load_skill_system_prompt(skill_dir)
    model = rse_mod.resolve_model(frontmatter, None)
    cases = rse_mod.load_cases(skill_dir, None)
    checks = cases[0].get("checks", {}) if cases else {}

    if dry_run:
        # No API call; treat the captured Langfuse output as the replay.
        replayed = trace.get("output", "")
        results = rse_mod.run_checks(replayed, checks, skill_dir) if replayed else []
        return replayed, results

    try:
        output, _usage, _elapsed = rse_mod.call_skill(system_prompt, trace["input"], model)
    except Exception as e:  # noqa: BLE001
        # Track A SKIP policy: if Anthropic is in a retriable-failure state
        # (5xx / 429 / timeout / connection reset), abort the entire run so
        # the regression baseline does not get contaminated by partial or
        # vendor-substituted data. Permanent errors (auth, bad request)
        # still get logged per-trace as before.
        if _is_retriable_anthropic_error(e):
            raise AnthropicAvailabilityError(
                f"anthropic {type(e).__name__}: {e}"
            ) from e
        _log_error(f"replay api error skill={skill_dir.name} trace={trace.get('id')} err={e!r}")
        return "", [{"check": "api_call", "passed": False, "detail": str(e)}]

    results = rse_mod.run_checks(output, checks, skill_dir)
    return output, results


# ============================================================
# Drift Detection
# ============================================================

class SkillDriftResult:
    """One skill's pass/fail aggregate for today's run."""

    __slots__ = ("skill", "traces_seen", "checks_passed", "checks_total",
                 "failed_cases", "baseline", "regression", "errors")

    def __init__(self, skill: str) -> None:
        self.skill = skill
        self.traces_seen: int = 0
        self.checks_passed: int = 0
        self.checks_total: int = 0
        self.failed_cases: list[dict] = []  # [{trace_id, failures, diff}]
        self.baseline: float | None = None
        self.regression: bool = False
        self.errors: list[str] = []

    @property
    def pass_rate(self) -> float:
        if self.checks_total == 0:
            return 1.0  # No checks - vacuously passing; baseline still reasoned about.
        return self.checks_passed / self.checks_total


def detect_regression(today: float, baseline: float | None) -> bool:
    """Regression if today's pass rate dropped >PASS_RATE_DROP_PCT points vs baseline."""
    if baseline is None:
        return False
    return (baseline - today) * 100.0 > PASS_RATE_DROP_PCT


def run_iteration(skill_filter: str | None, dry_run: bool, logger: logging.Logger) -> int:
    """Execute one daemon iteration. Returns regression count."""
    if _sensitive_session():
        logger.warning("sensitive session (SENSITIVE_MODE) - skipping eval-drift run")
        return 0

    load_env()
    state = load_state()
    run_started_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")

    try:
        rse_mod = _load_run_skill_eval()
    except FileNotFoundError as e:
        logger.error("eval-drift abort: %s", e)
        _log_error(f"setup failure: {e!r}")
        return 0

    skills = list_skills_with_evals()
    if skill_filter:
        skills = [s for s in skills if s == skill_filter]
    if not skills:
        logger.info("no skills with evals/ found (filter=%s)", skill_filter)
        return 0

    logger.info("eval-drift start skills=%d dry_run=%s", len(skills), dry_run)
    results: list[SkillDriftResult] = []

    try:
        for skill in skills:
            res = SkillDriftResult(skill)
            skill_dir = SKILLS_DIR / skill
            try:
                traces = fetch_recent_traces(skill, dry_run=dry_run)
            except Exception as e:  # noqa: BLE001
                logger.exception("fetch_recent_traces crashed for %s", skill)
                res.errors.append(f"fetch error: {e!r}")
                traces = []

            res.traces_seen = len(traces)
            for tr in traces:
                try:
                    _output, check_results = replay_trace(rse_mod, skill_dir, tr, dry_run)
                except AnthropicAvailabilityError:
                    # Propagate so the outer try aborts the entire run cleanly.
                    raise
                except Exception as e:  # noqa: BLE001
                    logger.exception("replay crashed skill=%s trace=%s", skill, tr.get("id"))
                    res.errors.append(f"replay error trace={tr.get('id')}: {e!r}")
                    continue

                # Aggregate THIS trace, inside the loop. Previously this block
                # sat at the for-skill indent, so it ran once per skill against
                # whatever check_results/tr the last iteration left bound - and
                # raised NameError on the zero-traces path (the daemon's normal
                # state). One indent deeper fixes both symptoms. [R13]
                passed = sum(1 for r in check_results if r["passed"])
                total = len(check_results)
                res.checks_passed += passed
                res.checks_total += total

                failed = [r for r in check_results if not r["passed"]]
                if failed:
                    res.failed_cases.append({
                        "trace_id": tr.get("id", ""),
                        "timestamp": tr.get("timestamp", ""),
                        "failures": failed,
                    })

            # Per-skill, computed once after the trace loop. Compute baseline
            # from history BEFORE we append today's entry.
            history = state["skills"].get(skill, {}).get("history", [])
            res.baseline = compute_baseline(history)
            res.regression = detect_regression(res.pass_rate, res.baseline)

            update_skill_history(state, skill, res.pass_rate, res.checks_passed,
                                 res.checks_total, run_started_iso)
            results.append(res)
            logger.info("skill=%s traces=%d checks=%d/%d pass_rate=%.3f baseline=%s regression=%s",
                        skill, res.traces_seen, res.checks_passed, res.checks_total,
                        res.pass_rate, res.baseline, res.regression)
    except AnthropicAvailabilityError as exc:
        # SKIP + re-queue policy (Track A 2026-05-24). Do NOT save state, do
        # NOT write a report - either would record a partial baseline that
        # poisons regression detection on the next tick. Next 24h scheduler
        # run will retry the full sweep naturally.
        logger.warning(
            "eval-drift SKIP: anthropic unavailable (%s). Aborting run with "
            "no state update; next 24h tick will retry.", exc,
        )
        _log_error(f"anthropic unavailable - run skipped: {exc!r}")
        return 0

    state["last_run"] = run_started_iso
    if not dry_run:
        save_state(state)

    regression_count = sum(1 for r in results if r.regression)
    if not dry_run:
        write_report(results, run_started_iso)
    if regression_count > 0:
        notify_regressions(results, logger)
    logger.info("eval-drift end skills=%d regressions=%d", len(results), regression_count)
    return regression_count


# ============================================================
# Report Writing
# ============================================================

def _format_pct(rate: float | None) -> str:
    if rate is None:
        return "-"
    return f"{rate * 100:.1f}%"


def write_report(results: list[SkillDriftResult], run_started_iso: str) -> Path:
    """Write the daily Markdown report and return its path."""
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.now(get_default_tz()).strftime("%Y-%m-%d")
    report_path = REPORT_DIR / f"{today}_eval-drift_daily.md"

    lines: list[str] = []
    lines.append(f"# Eval Drift Report - {today}")
    lines.append("")
    lines.append(f"Run started: {run_started_iso}")
    lines.append(f"Skills evaluated: {len(results)}")
    regression_count = sum(1 for r in results if r.regression)
    if regression_count == 0:
        lines.append("Regression flag: **NONE**")
    else:
        lines.append(f"Regression flag: **{regression_count} skill(s) regressed**")
    lines.append("")

    # Summary table
    lines.append("## Summary")
    lines.append("")
    lines.append("| Skill | Traces (24h) | Checks pass/total | Pass rate today | Baseline (prior 7d) | Drop (pp) | Regression |")
    lines.append("|---|---:|---:|---:|---:|---:|:---:|")
    for r in results:
        drop = "-"
        if r.baseline is not None:
            drop = f"{(r.baseline - r.pass_rate) * 100:+.1f}"
        flag = "YES" if r.regression else "no"
        lines.append(
            f"| {r.skill} | {r.traces_seen} | {r.checks_passed}/{r.checks_total} | "
            f"{_format_pct(r.pass_rate)} | {_format_pct(r.baseline)} | {drop} | {flag} |"
        )
    lines.append("")

    # Per-skill detail
    lines.append("## Per-skill detail")
    lines.append("")
    for r in results:
        lines.append(f"### {r.skill}")
        lines.append("")
        lines.append(f"- Traces seen (last 24h): {r.traces_seen}")
        lines.append(f"- Checks passed: {r.checks_passed} / {r.checks_total} ({_format_pct(r.pass_rate)})")
        lines.append(f"- Prior-week baseline: {_format_pct(r.baseline)}")
        lines.append(f"- Regression: {'YES (>5pp drop)' if r.regression else 'no'}")
        if r.errors:
            lines.append("- Errors:")
            for err in r.errors:
                lines.append(f"  - {err}")
        if r.failed_cases:
            lines.append("- Failed cases:")
            for fc in r.failed_cases[:20]:  # cap to keep the report scannable
                lines.append(f"  - trace `{fc['trace_id']}` ({fc.get('timestamp', '')}):")
                for f in fc["failures"][:5]:
                    detail = f.get("detail") or ""
                    lines.append(f"    - {f['check']}: {detail}")
            if len(r.failed_cases) > 20:
                lines.append(f"  - ... and {len(r.failed_cases) - 20} more")
        lines.append("")

    lines.append("---")
    lines.append("Generated by `scripts/eval-drift-daemon.py` (closes P2.2 / 2026-05-17 audit).")
    lines.append("")

    report_path.write_text("\n".join(lines), encoding="utf-8")
    return report_path


# ============================================================
# Notifications
# ============================================================

def notify_regressions(results: list[SkillDriftResult], logger: logging.Logger) -> None:
    """Dispatch regression notifications per EVAL_DRIFT_NOTIFY env var.

    Default: log to errors.log only.
    telegram: stub-wired - logs the intent and a marker line in errors.log so a
              future patch can wire it to fireside-bot's send_dm path. We do NOT
              import telethon here on purpose - the daemon must stay light.
    slack:    TODO; no Slack workspace yet.
    """
    regressed = [r for r in results if r.regression]
    if not regressed:
        return
    mode = os.environ.get("EVAL_DRIFT_NOTIFY", "none").strip().lower()
    summary = ", ".join(
        f"{r.skill} ({_format_pct(r.baseline)} -> {_format_pct(r.pass_rate)})"
        for r in regressed
    )
    _log_error(f"REGRESSION mode={mode} skills=[{summary}]")
    logger.warning("regression detected: %s", summary)
    if mode == "telegram":
        # Intentional stub: wiring lives outside this daemon to keep the
        # "one daemon per concern" rule. Fireside owns the Telegram bot
        # connection; a future patch can post to a dedicated endpoint that
        # fireside polls, OR call its send_message helper directly under
        # an explicit dependency.
        logger.info("telegram notification stub - regression details in errors.log")
    elif mode == "slack":
        logger.info("slack notification not implemented (no Slack workspace yet)")
    # mode == "none" or unrecognised: log-only, already done above.


# ============================================================
# Scheduler
# ============================================================

def is_daemon_alive() -> bool:
    """Check whether a daemon process is currently running per PID file."""
    if not PID_FILE.exists():
        return False
    try:
        pid = int(PID_FILE.read_text().strip())
    except (ValueError, OSError):
        return False
    return _pid_is_running(pid)


def _pid_is_running(pid: int) -> bool:
    """Cross-platform liveness check, mirrors fireside-bot-daemon."""
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        PROCESS_QUERY_LIMITED_INFO = 0x1000
        STILL_ACTIVE = 259
        h = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFO, False, pid)
        if not h:
            return False
        try:
            code = ctypes.c_ulong(0)
            if ctypes.windll.kernel32.GetExitCodeProcess(h, ctypes.byref(code)) == 0:
                return False
            return code.value == STILL_ACTIVE
        finally:
            ctypes.windll.kernel32.CloseHandle(h)
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


async def _run_daemon(logger: logging.Logger, skill_filter: str | None,
                       dry_run: bool) -> None:
    """Long-running daemon: APScheduler + one cron job + graceful shutdown."""
    # Lazy imports - keep `once` and `status` runnable without apscheduler
    # installed if the user has not yet bootstrapped the daemon.
    from apscheduler.schedulers.asyncio import AsyncIOScheduler  # type: ignore[import-not-found]
    from apscheduler.triggers.cron import CronTrigger  # type: ignore[import-not-found]
    from apscheduler.triggers.interval import IntervalTrigger  # type: ignore[import-not-found]

    scheduler = AsyncIOScheduler(timezone=get_default_tz())

    def _job():
        try:
            run_iteration(skill_filter, dry_run, logger)
            # Deadman: the daily run completed (including the SENSITIVE_MODE
            # skip path, which is a healthy no-op). A missed ping means the
            # 02:00 cron never fired. Best-effort, never raises.
            hc_ping("STEWARD_HC_EVAL_DRIFT")
        except Exception:
            logger.exception("scheduled run failed")

    scheduler.add_job(
        _job,
        CronTrigger(timezone=get_default_tz(), hour=DAILY_HOUR, minute=DAILY_MINUTE),
        id="eval-drift-daily",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )

    # R14: dedicated 1-min liveness beat, decoupled from the daily 02:00 work
    # cron. Beating only per work-cycle would advance the heartbeat once a day
    # and force the watchdog grace above 24h, defeating crash detection. One
    # file: .daemon-state/heartbeats/eval-drift.json.
    scheduler.add_job(
        lambda: daemon_heartbeat.beat("eval-drift"),
        IntervalTrigger(minutes=1, timezone=get_default_tz()),
        id="heartbeat",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        next_run_time=datetime.now(get_default_tz()),
    )

    # Clear any stale sentinel from a previous unclean exit.
    if STOP_SENTINEL.exists():
        try:
            STOP_SENTINEL.unlink()
        except OSError:
            pass

    # Atomic PID + start-time writes.
    tmp_pid = PID_FILE.with_suffix(".pid.tmp")
    tmp_pid.write_text(str(os.getpid()))
    os.replace(tmp_pid, PID_FILE)
    tmp_started = STARTED_AT_FILE.with_suffix(".tmp")
    tmp_started.write_text(str(int(time.time())))
    os.replace(tmp_started, STARTED_AT_FILE)

    logger.info("daemon-start pid=%d", os.getpid())
    scheduler.start()

    stop_event = asyncio.Event()

    def _request_stop(*_args):
        logger.info("signal received; shutting down")
        stop_event.set()

    if os.name == "nt":
        async def _sentinel_watcher():
            while not stop_event.is_set():
                if STOP_SENTINEL.exists():
                    logger.info("stop sentinel detected; shutting down")
                    try:
                        STOP_SENTINEL.unlink()
                    except OSError:
                        pass
                    stop_event.set()
                    return
                await asyncio.sleep(1)
        asyncio.get_running_loop().create_task(_sentinel_watcher())
    else:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, _request_stop)

    try:
        await stop_event.wait()
    finally:
        scheduler.shutdown(wait=False)
        for p in (PID_FILE, STARTED_AT_FILE):
            if p.exists():
                try:
                    p.unlink()
                except OSError:
                    pass
        logger.info("daemon-stop")


# ============================================================
# CLI / Main
# ============================================================

def cmd_daemon(args) -> int:
    if is_daemon_alive():
        print("eval-drift-daemon: already running")
        return 1
    logger = _setup_logging()
    try:
        asyncio.run(_run_daemon(logger, args.skill, args.dry_run))
    finally:
        if PID_FILE.exists():
            try:
                PID_FILE.unlink()
            except OSError:
                pass
    return 0


def cmd_once(args) -> int:
    logger = _setup_logging()
    regressions = run_iteration(args.skill, args.dry_run, logger)
    if regressions > 0:
        return 1
    return 0


def cmd_status(args) -> int:
    if not is_daemon_alive():
        print(f"{YELLOW}eval-drift-daemon: NOT RUNNING{RESET}")
        return 0
    pid = int(PID_FILE.read_text().strip())
    uptime_str = "unknown"
    if STARTED_AT_FILE.exists():
        try:
            started_at = int(STARTED_AT_FILE.read_text().strip())
            secs = max(0, int(time.time()) - started_at)
            h, rem = divmod(secs, 3600)
            m, s = divmod(rem, 60)
            uptime_str = f"{h}h {m}m {s}s"
        except (ValueError, OSError):
            pass
    print(f"{GREEN}eval-drift-daemon: RUNNING{RESET} pid={pid} uptime={uptime_str}")
    print(f"schedule: daily {DAILY_HOUR:02d}:{DAILY_MINUTE:02d} local (the configured timezone)")
    return 0


def cmd_stop(args) -> int:
    if not is_daemon_alive():
        print("eval-drift-daemon: NOT RUNNING")
        return 0
    pid = int(PID_FILE.read_text().strip())
    if os.name == "nt":
        RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
        STOP_SENTINEL.write_text(str(pid))
        print(f"eval-drift-daemon: stop sentinel written for pid={pid} (daemon will exit within ~1s)")
    else:
        os.kill(pid, signal.SIGTERM)
        print(f"eval-drift-daemon: SIGTERM sent to pid={pid}")
    return 0


def _shared_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--dry-run", action="store_true",
                        help="Skip API calls, skip state/report writes, skip notifications.")
    parser.add_argument("--skill", default=None,
                        help="Limit to one skill (directory name under .claude/skills/).")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Eval-drift daemon - replay recent Langfuse traces against current skills.",
    )
    # Top-level --once shortcut for muscle-memory.
    parser.add_argument("--once", action="store_true",
                        help="Run a single iteration and exit (equivalent to `once` subcommand).")
    _shared_args(parser)
    sub = parser.add_subparsers(dest="cmd")

    p_daemon = sub.add_parser("daemon", help="Run scheduler forever")
    _shared_args(p_daemon)

    p_once = sub.add_parser("once", help="Run a single iteration and exit")
    _shared_args(p_once)

    sub.add_parser("status", help="Show PID and registered jobs")
    sub.add_parser("stop", help="Signal a running daemon to shut down")

    args = parser.parse_args()

    # Honour --once at the top level (overrides any subcommand).
    if args.once:
        return cmd_once(args)

    if args.cmd is None:
        # Default behaviour: print help. Avoid surprising users with an
        # implicit `daemon` start when they typed bare `python eval-drift-daemon.py`.
        parser.print_help()
        return 0

    dispatch = {
        "daemon": cmd_daemon,
        "once": cmd_once,
        "status": cmd_status,
        "stop": cmd_stop,
    }
    return dispatch[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
