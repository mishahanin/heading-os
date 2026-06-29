#!/usr/bin/env python3
"""Bridge daemon entry point.

Usage:
  python scripts/bridge-daemon.py --start
  python scripts/bridge-daemon.py --rotate-token
  python scripts/bridge-daemon.py --health
"""
import argparse
import logging
import os
import socket
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WORKSPACE_ROOT))
from scripts.utils.workspace import get_default_tz, get_default_tz_name, load_env
from scripts.utils.paths import get_data_root

from scripts.bridge_daemon._atomic import atomic_write_text
from scripts.bridge_daemon.app import build_app
from scripts.bridge_daemon.auth import get_or_create_token
from scripts.bridge_daemon.config import (
    ConfigState,
    list_snapshots,
    load_config,
    revert_config,
    revert_config_to,
    snapshot_config,
)
from scripts.bridge_daemon.error_tracker import install_handler as install_error_tracker
from scripts.bridge_daemon.heartbeat import write_heartbeat
from scripts.bridge_daemon.refreshers import email as r_email
from scripts.bridge_daemon.refreshers import inflight as r_inflight
from scripts.bridge_daemon.refreshers import pulse as r_pulse
from scripts.bridge_daemon.scheduler import build_scheduler
from scripts.bridge_daemon.state import State
from scripts.bridge_daemon.watcher import start_observer
from scripts.utils import daemon_heartbeat
from scripts.utils import trace
from scripts.utils.trace_filter import install_log_factory

LOG_PATH = WORKSPACE_ROOT / ".daemon-state" / "bridge.log"


# ============================================================
# Scheduled jobs (subprocess-isolated tick handlers + registration)
# ============================================================
def _run_llm_fit_report(workspace_root: Path) -> None:
    """Track B weekly report. Runs llm-fit-report.py as a subprocess so a
    crash in the renderer or langfuse SDK does not propagate into the daemon
    process. Cross-platform: pure Python invocation, no shell. Cadence is
    Sundays 03:00 local per CEO decision 2026-05-24."""
    import subprocess
    script = workspace_root / "scripts" / "llm-fit-report.py"
    if not script.exists():
        logging.warning("llm_fit_report: producer script missing at %s; skipping", script)
        return
    try:
        result = subprocess.run(
            [sys.executable, str(script), "--days", "7"],
            cwd=str(workspace_root),
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
        if result.returncode == 0:
            logging.info("llm_fit_report: ok (%s)", result.stdout.strip().splitlines()[-1] if result.stdout else "")
        else:
            logging.warning(
                "llm_fit_report: exited %d; stderr=%s",
                result.returncode, result.stderr.strip()[:500],
            )
    except subprocess.TimeoutExpired:
        logging.warning("llm_fit_report: timed out after 180s")
    except OSError as e:
        logging.warning("llm_fit_report: subprocess failed: %s", e)


def _prime_all_components(state) -> None:
    """Phase 1.5 (extended 2026-05-20): prime EVERY component so the
    dashboard has a non-None data_time on first paint.

    The original boot-list only covered the 9 components that had explicit
    Watchdog mappings at the time. Later additions (approvals, calendar,
    crm, prime, status, conversations, threads, signals, critical,
    inflight, investors) were never added to the boot-prime list, so
    their data_time stayed null until a Watchdog event fired - which
    never happens for calendar/crm/conversations/threads/signals (no
    watcher path mapping) and only occasionally for the rest. Result:
    the freshness indicator showed '-' on those pages even though their
    source data is computed fresh on every request.

    Iterating over state.COMPONENTS is self-maintaining: future
    components added to that tuple get primed automatically without
    needing to update this function.
    """
    from scripts.bridge_daemon.state import COMPONENTS as _ALL
    for component in _ALL:
        state.bump(component)


def _cold_sweep_job(workspace_root: Path, state, data_root: Path | None = None) -> None:
    """R2 scheduled Cold-Sweep pre-pass (in-process, no self-HTTP).

    Imports the CEO-only core lazily, builds cards, appends them under the
    queue lock via the shared helper (the daemon stays the single writer), and
    bumps so the browser re-fetches. Non-fatal on any error.

    Reads CRM + writes the action queue under ``data_root`` (CEO content). When
    a caller omits it, the fail-safe fallback is ``get_data_root()`` (the real
    data root in both two-repo and in-tree modes), never the engine root.
    """
    if data_root is None:
        data_root = get_data_root()
    try:
        from scripts import cold_sweep_core
        from scripts.bridge_daemon.sources import action_queue as aq
        cards = cold_sweep_core.run(data_root)
        if cards:
            aq.append_cards(data_root, cards)
            state.bump("action_queue")
        logging.info("cold_sweep: built %d candidate card(s)", len(cards))
    except Exception:
        logging.exception("cold_sweep job failed (non-fatal)")


def _sweep_non_gated_cards(data_root: Path, aq) -> int:
    """R3 tier routing: dispose autonomous cards and auto-apply notify cards
    in-process under the queue lock, before the send executor runs.

    Routing (tier resolved from config/tool-risk.json via tool_risk.tier_for):

    - ``autonomous`` display-only types (``note``, ``alert``) -> left in the
      active queue for the CEO to read and manually dismiss. These carry no
      executable action; they are surfaced read-only. (CEO decision 2026-06-04:
      Cold-Sweep deposits cold/drop recommendations as ``note`` cards, so
      auto-disposing them would hide advice the CEO meant to read. Notes are
      surfaced, not swept.)
    - ``notify`` ``pipeline_update`` -> auto-apply (status ``applied``). The
      reversible ``prev_value`` the producer (R4, future) stamps on the card is
      preserved so ``undo_card`` can revert it; the daemon never invents
      pipeline state here.

    ``gated`` cards (email_send) are untouched - they flow through the send
    executor below only once the CEO has approved them.

    Returns the count of cards applied (for the bump decision).
    """
    from scripts.utils import tool_risk
    swept = 0
    snapshot = aq.list_action_queue(data_root)
    for card in snapshot.get("items", []):
        if card.get("status") != "pending":
            continue
        aid = card.get("id")
        atype = card.get("action_type")
        if not aid or not atype:
            continue
        tier = tool_risk.tier_for(atype)
        if tier == tool_risk.AUTONOMOUS:
            # Display-only autonomous types (note, alert) are surfaced read-only
            # and left for the CEO to dismiss - never auto-disposed.
            continue
        elif tier == tool_risk.NOTIFY:
            # Auto-apply. prev_value (if the producer supplied it) stays on the
            # card so undo_card can revert; we do not synthesise it.
            aq.apply_status(data_root, aid, "applied", event="auto_apply")
            swept += 1
    return swept


def _executor_job(workspace_root: Path, state, data_root: Path | None = None) -> None:
    """Non-gated sweep job (the send-executor spawn was REMOVED 2026-06-27).

    The synchronous terminal ``action-queue.py approve`` is now the SOLE send
    path; the daemon NO LONGER SENDS. This slimmed job only sweeps the queue
    in-process for non-gated cards: autonomous ``note`` cards are disposed,
    autonomous ``alert`` cards are left for the CEO, and notify ``pipeline_update``
    cards are auto-applied (with ``prev_value`` preserved for undo). It never
    spawns the send executor and never transitions a gated send. Non-fatal on any
    error - a sweep failure must never take the daemon down.

    Queue reads/writes use ``data_root`` (CEO content). When a caller omits it the
    fail-safe fallback is ``get_data_root()``, never the engine root."""
    if data_root is None:
        data_root = get_data_root()
    try:
        from scripts.bridge_daemon.sources import action_queue as aq

        swept = _sweep_non_gated_cards(data_root, aq)
        if swept:
            state.bump("action_queue")
            logging.info("action_queue sweep: applied %d non-gated card(s)", swept)
    except Exception:
        logging.exception("action_queue sweep job failed (non-fatal)")


def _watchdog_job(workspace_root: Path) -> None:
    """R14 watchdog tick: classify each daemon's per-daemon liveness beat and
    route a deduped, severity-tiered alert on a missed beat. Runs the importable
    ``watchdog_core.check_once`` in-process (the same logic the standalone
    ``scripts/daemon-watchdog.py`` CLI wraps for the console path).

    Known residual (scrutiny M4): because the watchdog runs INSIDE the bridge
    daemon, a bridge-down event kills this push path with it; bridge-down
    detection reverts to the ``daemon-fleet-health.py`` pull. Non-fatal on any
    error - a watchdog failure must never take the daemon down."""
    try:
        from scripts import watchdog_core
        report = watchdog_core.check_once(workspace_root)
        if report.get("alerts_fired"):
            logging.info("watchdog: %d alert(s) fired; verdict=%s",
                         report["alerts_fired"], report.get("verdict"))
    except Exception:
        logging.exception("watchdog job failed (non-fatal)")


def _critique_job(workspace_root: Path, max_per_tick: int, model: str | None,
                  data_root: Path | None = None) -> None:
    """R5b advisory pre-approval critique sweep (config-gated, bounded, never sends).

    Reads + annotates the action queue under ``data_root`` (CEO content); when a
    caller omits it the fail-safe fallback is ``get_data_root()``, never the engine root.

    For each pending ``email_send`` card that is ``ready_for_review`` and not yet
    critiqued, run one bounded model call (``draft_critique.critique_draft``) and
    stamp the advisory result via ``aq.annotate_card`` - which is structurally
    incapable of changing ``status``. The R3 ``gated`` invariant is untouched: a
    critiqued card still requires the CEO approve click before the executor
    sends. Bounded to ``max_per_tick`` model calls per tick. The recipient is the
    card's ``to`` field (no ``recipient`` key exists on cards). Non-fatal on any
    error - a critique failure must never take the daemon down."""
    if data_root is None:
        data_root = get_data_root()
    try:
        from scripts.bridge_daemon.sources import action_queue as aq
        from scripts.utils import draft_critique
        snapshot = aq.list_action_queue(data_root)
        done = 0
        for card in snapshot.get("items", []):
            if done >= max_per_tick:
                break
            if card.get("status") != "pending":
                continue
            if card.get("action_type") != "email_send":
                continue
            if card.get("draft_status") != "ready_for_review":
                continue  # no body to critique yet
            if card.get("critique"):
                continue  # already critiqued -> idempotent, never re-spend
            aid = card.get("id")
            if not aid:
                continue
            # One bounded call per card; recipient comes from the card's `to`.
            done += 1  # count the attempt so max_per_tick bounds model calls
            result = draft_critique.critique_draft(
                card.get("subject"), card.get("draft_body"), card.get("to"), model=model,
            )
            if result is not None:
                aq.annotate_card(data_root, aid, critique=result)
        if done:
            logging.info("critique: attempted %d card(s) this tick (max %d)", done, max_per_tick)
    except Exception:
        logging.exception("critique job failed (non-fatal)")


def _register_spine_jobs(sched, cfg: dict, workspace_root: Path, state,
                         data_root: Path | None = None) -> None:
    """R2 (scrutiny H1): register the Cold-Sweep + executor + watchdog jobs only
    when config-enabled AND their CEO-only core is present. The shared daemon
    ships to execs, where these flags default off and the core is absent, so the
    jobs must never be scheduled there - they self-disable with a single INFO
    log."""
    if data_root is None:
        data_root = get_data_root()
    daemon_cfg = cfg.get("daemon") if isinstance(cfg.get("daemon"), dict) else {}
    cs = daemon_cfg.get("cold_sweep") if isinstance(daemon_cfg.get("cold_sweep"), dict) else {}
    ex = daemon_cfg.get("action_queue") if isinstance(daemon_cfg.get("action_queue"), dict) else {}
    ex = ex.get("executor") if isinstance(ex.get("executor"), dict) else {}
    wd = daemon_cfg.get("watchdog") if isinstance(daemon_cfg.get("watchdog"), dict) else {}
    crit = daemon_cfg.get("critique") if isinstance(daemon_cfg.get("critique"), dict) else {}

    if bool(cs.get("enabled", False)):
        if (workspace_root / "scripts" / "cold_sweep_core.py").exists():
            from apscheduler.triggers.cron import CronTrigger
            from zoneinfo import ZoneInfo
            sched.add_job(
                _cold_sweep_job,
                CronTrigger(hour=6, minute=30, timezone=get_default_tz()),
                id="cold_sweep_daily", max_instances=1, coalesce=True,
                args=[workspace_root, state, data_root],
            )
            logging.info("cold_sweep: scheduled daily 06:30 local")
        else:
            logging.info("cold_sweep: enabled but core module absent; not scheduled")
    else:
        logging.info("cold_sweep: disabled (daemon.cold_sweep.enabled=false)")

    if bool(ex.get("enabled", False)):
        # Slimmed 2026-06-27: this job now ONLY sweeps non-gated cards; the send
        # executor spawn was removed (synchronous terminal approve is the sole
        # send path). Guarded on the always-present sweep helper, not the
        # (retained but no-longer-spawned) executor script.
        sched.add_job(
            _executor_job, "interval", minutes=2,
            id="action_queue_executor", max_instances=1, coalesce=True,
            args=[workspace_root, state, data_root],
        )
        logging.info("action_queue non-gated sweep: scheduled every 2 min (daemon no longer sends)")
    else:
        logging.info("action_queue sweep: disabled (daemon.action_queue.executor.enabled=false)")

    if bool(wd.get("enabled", False)):
        if (workspace_root / "scripts" / "watchdog_core.py").exists():
            sched.add_job(
                _watchdog_job, "interval", minutes=2,
                id="daemon_watchdog", max_instances=1, coalesce=True,
                args=[workspace_root],
            )
            logging.info("daemon watchdog: scheduled every 2 min")
        else:
            logging.info("daemon watchdog: enabled but core module absent; not scheduled")
    else:
        logging.info("daemon watchdog: disabled (daemon.watchdog.enabled=false)")

    # R5b pre-approval critique sweep. Corporate util (no CEO-only core), but
    # default OFF fleet-wide via the config flag; CEO prove-out enables it in
    # .daemon-state/config.yaml. Self-disables if draft_critique cannot import
    # (e.g. anthropic/observability absent on an exec workspace).
    if bool(crit.get("enabled", False)):
        try:
            from scripts.utils import draft_critique  # noqa: F401
            _critique_ok = True
        except Exception:
            _critique_ok = False
        if _critique_ok:
            max_per_tick = int(crit.get("max_per_tick", 3) or 3)
            model = crit.get("model") or None
            sched.add_job(
                _critique_job, "interval", minutes=2,
                id="critique", max_instances=1, coalesce=True,
                args=[workspace_root, max_per_tick, model, data_root],
            )
            logging.info("critique: scheduled every 2 min (max_per_tick=%d)", max_per_tick)
        else:
            logging.info("critique: enabled but draft_critique import failed; not scheduled")
    else:
        logging.info("critique: disabled (daemon.critique.enabled=false)")


# ============================================================
# Daemon lifecycle & port management
# ============================================================
def _pick_port(start: int) -> int:
    """Find the first free TCP port in [start, start+50). Raises if none."""
    for p in range(start, start + 50):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", p)) != 0:
                return p
    raise RuntimeError(f"no free port in range {start}..{start + 50}")


def _verify_port_free(port: int) -> int:
    """Assert that an explicit port is free; raise RuntimeError otherwise.

    Used by the --port CLI override (Phase S) so a CEO request for a
    specific port fails fast instead of silently falling back to the
    auto-pick range.
    """
    if not (1 <= port <= 65535):
        raise RuntimeError(f"port {port} out of range (1..65535)")
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        if s.connect_ex(("127.0.0.1", port)) == 0:
            raise RuntimeError(f"port {port} is already in use")
    return port


def start_daemon(explicit_port: int | None = None):
    """Start the bridge daemon: load token + config, pick a port, start observer +
    scheduler, then run uvicorn on 127.0.0.1. Cleans up on exit or exception.

    Phase S: when explicit_port is set, skip the auto-pick range and bind
    exactly that port. Fails fast if it's busy. Without explicit_port,
    auto-pick from cfg["port_range_start"] (default 31415, scanning +50).
    """
    import uvicorn
    # R12: mint a trace ID for this daemon's process tree and install the
    # record factory before any logging so every line (and every subprocess
    # this daemon spawns) carries the same [trace_id].
    trace.mint()
    install_log_factory()
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    # Rotating handler: 1 MB per file, 3 backups (~4 MB total cap).
    # Workspace convention - matches scripts/sync-exchange-daemon.py.
    handler = RotatingFileHandler(
        LOG_PATH, maxBytes=1_000_000, backupCount=3, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s [%(trace_id)s] %(message)s"))
    root = logging.getLogger()
    # Clear any pre-existing handlers (defense vs. test contamination).
    for h in list(root.handlers):
        root.removeHandler(h)
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    # Phase J: attach the error tracker to root so every WARNING+ record
    # feeds heartbeat.json's recent_error_count + last_error fields.
    install_error_tracker(root)

    observer = None
    sched = None
    try:
        # Phase B / spec 3.6: ConfigState owns the merged config in memory
        # and exposes reconcile() for the 60-second mtime check. build_app
        # and the heartbeat job both read cfg_state.config at call time so
        # /push-updates of corporate/daemon/config.yaml propagate without
        # a daemon restart (cadence changes still need a restart - they
        # are baked into APScheduler at sched.start()).
        cfg_state = ConfigState(WORKSPACE_ROOT)
        cfg = cfg_state.config
        # F-M11: wire alert's AQ-append callable now that bridge_daemon is fully
        # loaded, breaking the circular import that alert.py previously had.
        from scripts.bridge_daemon.sources import action_queue as _aq
        import scripts.utils.alert as _alert_mod
        _alert_mod.init(_aq.append_cards)
        # Phase 1.154: snapshot the merged config on every boot so
        # --revert-config has a prior version to roll back to. Keeps
        # the last 3 snapshots in .daemon-state/config-history/.
        try:
            snap = snapshot_config(WORKSPACE_ROOT, cfg)
            logging.info(f"config snapshot written: {snap.name}")
        except Exception as e:
            logging.warning(f"config snapshot failed (non-fatal): {e}")
        token = get_or_create_token(WORKSPACE_ROOT)
        state = State()
        _prime_all_components(state)
        user_slug = cfg.get("user_slug", "misha")
        if explicit_port is not None:
            port = _verify_port_free(explicit_port)
            logging.info(f"using explicit port {port} (from --port flag)")
        else:
            port = _pick_port(cfg["port_range_start"])
        atomic_write_text(WORKSPACE_ROOT / ".daemon-state" / "port", str(port), mode=0o644)
        os.environ["BRIDGE_PORT"] = str(port)
        # HEADING OS engine/data split: data (outputs/crm/threads/knowledge/pipeline)
        # resolves under data_root; engine paths (.claude/skills, .daemon-state cache)
        # stay on WORKSPACE_ROOT. On transitional ceo-main the two are identical, so
        # all wiring below is a no-op; a post-cutover engine clone reads its data sibling.
        data_root = get_data_root()
        logging.info("data_root: %s (in-tree=%s)", data_root, data_root == WORKSPACE_ROOT)
        observer = start_observer(WORKSPACE_ROOT, state, interval=0.5, data_root=data_root)
        def _reconcile_config():
            """Phase B / spec 3.6 reconciliation tick. Stats both config
            layers; on mtime change, reloads + logs the new version field.
            Logged warning instead of exception so a transient read fault
            doesn't kill the daemon."""
            try:
                if cfg_state.reconcile():
                    new_v = cfg_state.config.get("version", "unversioned")
                    logging.info(f"config reloaded: version={new_v} count={cfg_state.reload_count}")
            except OSError as e:
                logging.warning(f"config reconcile failed (non-fatal): {e}")

        jobs = {
            "email": lambda: r_email.refresh(WORKSPACE_ROOT, state),
            "inflight": lambda: r_inflight.refresh(WORKSPACE_ROOT, state),
            # Phase 2 (2026-05-24): pulse refresher computes the full payload
            # off the request path and writes .daemon-state/pulse-snapshot.json.
            # Endpoint reads from snapshot, so per-request latency dropped from
            # ~7s (WSL /mnt/c rglob over outputs/) to ~5ms.
            "pulse": lambda: r_pulse.refresh(WORKSPACE_ROOT, state, cfg_state, data_root=data_root),
            # Phase 1.152: heartbeat writer (spec section 3.7). Default
            # cadence is set in scheduler.py via config; falls back to
            # 60s when 'heartbeat' isn't in config.refresh. Reads
            # cfg_state.config at call time so it picks up the version
            # bump after a reconcile.
            # R14: write the rich bridge heartbeat (fleet-health back-compat) AND
            # emit the per-daemon liveness beat on the same 60s tick so the
            # watchdog sees the bridge in .daemon-state/heartbeats/bridge.json.
            "heartbeat": lambda: (
                write_heartbeat(
                    WORKSPACE_ROOT, str(cfg_state.config.get("version", "unversioned"))
                ),
                daemon_heartbeat.beat(
                    "bridge", config_version=str(cfg_state.config.get("version", "unversioned"))
                ),
            ),
            # Phase B: config reconciliation tick.
            "config_reconcile": _reconcile_config,
        }
        sched = build_scheduler(cfg, jobs)
        # Track B (2026-05-24): weekly LLM-fit report every Sunday 03:00
        # local time. Cron trigger (not interval) so the cadence is calendar-
        # aligned regardless of when the daemon last booted. Subprocess so
        # the report renderer crashes don't propagate into the daemon.
        from apscheduler.triggers.cron import CronTrigger
        from zoneinfo import ZoneInfo
        sched.add_job(
            _run_llm_fit_report,
            CronTrigger(day_of_week="sun", hour=3, minute=0, timezone=get_default_tz()),
            id="llm_fit_report_weekly",
            max_instances=1,
            coalesce=True,
            args=[WORKSPACE_ROOT],
        )
        # R2 (2026-06-03): config-gated Cold-Sweep + Action-Queue executor jobs.
        # Self-disabling on exec workspaces (flags default off, core absent).
        _register_spine_jobs(sched, cfg, WORKSPACE_ROOT, state, data_root)
        # Phase 1.152: write the first heartbeat immediately on boot so a
        # fleet-health reader sees the daemon alive within the first
        # second instead of waiting for the 60s tick.
        write_heartbeat(WORKSPACE_ROOT, str(cfg_state.config.get("version", "unversioned")))
        sched.start()
        # Prime the pulse snapshot only when no previous snapshot exists
        # (cold boot / first install). On warm restarts we serve the
        # prior snapshot - it's at most one refresh interval (60s) stale
        # and the first scheduled tick will overwrite it shortly. This
        # cuts warm restart latency from ~8s (sync prime) to ~0s while
        # keeping the cold-boot guarantee that the first /pulse hits
        # cache rather than the inline-compute fallback.
        snapshot = r_pulse.snapshot_path(WORKSPACE_ROOT)
        if snapshot.exists():
            logging.info(f"pulse snapshot present ({snapshot.name}); skipping sync prime")
        else:
            try:
                r_pulse.refresh(WORKSPACE_ROOT, state, cfg_state, data_root=data_root)
            except Exception:
                logging.exception("initial pulse prime failed (non-fatal; endpoint will fall back to inline compute)")
        logging.info(f"bridge daemon starting on port {port}")
        app = build_app(WORKSPACE_ROOT, state, token, user_slug, cfg_state=cfg_state, data_root=data_root)
        uvicorn.run(app, host="127.0.0.1", port=port, log_config=None)
    except Exception:
        logging.exception("bridge daemon failed during startup or runtime")
        raise
    finally:
        if sched is not None:
            sched.shutdown(wait=False)
        if observer is not None:
            observer.stop()
            observer.join()


# ============================================================
# Token rotation, status & health surfaces
# ============================================================
def rotate_token():
    """Rotate the on-disk auth token. WARNING: a running daemon retains the OLD
    token in memory until restarted - this CLI only rewrites the file."""
    token_file = WORKSPACE_ROOT / ".daemon-state" / "token"
    if token_file.exists():
        token_file.unlink()
    new = get_or_create_token(WORKSPACE_ROOT)
    print(f"new token written to {token_file}")
    print(f"preview: {new[:16]}...")
    print()
    print("WARNING: a running daemon still holds the old token in memory.")
    print("Restart the daemon (Ctrl+C and re-run --start) for the new token to take effect.")


def _read_heartbeat_fallback():
    """Read .daemon-state/heartbeat.json. Returns the parsed dict or None."""
    import json
    hb = WORKSPACE_ROOT / ".daemon-state" / "heartbeat.json"
    if not hb.exists():
        return None
    try:
        return json.loads(hb.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def show_status():
    """Print a one-line grep-friendly summary of the local daemon state.

    Phase W: combines .daemon-state/port + heartbeat.json (pid, uptime,
    version, config_loaded_version, last_heartbeat) into a single line
    so cron / shell pipelines can grep for fields without running both
    --health and reading the heartbeat manually. No HTTP call, no auth.

    Output format (tab-separated for easy `cut -f`):
      port=PORT  pid=PID  uptime=Ns  version=V  config_v=CV  last_hb=ISO

    Exit codes:
      0 - status available (port file or heartbeat readable)
      1 - neither port nor heartbeat exists (daemon never started)
    """
    port_file = WORKSPACE_ROOT / ".daemon-state" / "port"
    hb = _read_heartbeat_fallback()
    port = port_file.read_text().strip() if port_file.exists() else "-"

    if hb is None and port == "-":
        print("daemon not started (no port file, no heartbeat.json)", file=sys.stderr)
        sys.exit(1)

    fields = [
        f"port={port}",
        f"pid={hb.get('pid', '-') if hb else '-'}",
        f"uptime={hb.get('uptime_s', '-') if hb else '-'}s",
        f"version={hb.get('version', '-') if hb else '-'}",
        f"config_v={hb.get('config_loaded_version', '-') if hb else '-'}",
        f"sessions={hb.get('active_sessions', '-') if hb else '-'}",
        f"errors={hb.get('recent_error_count', '-') if hb else '-'}",
        f"last_hb={hb.get('last_heartbeat', '-') if hb else '-'}",
    ]
    print("  ".join(fields))


def check_health():
    """Probe the running daemon's /health endpoint and pretty-print the JSON.

    Phase 1.161: when the live probe fails, fall back to reading
    heartbeat.json so the CEO still gets diagnostic info (last
    heartbeat, version, config_loaded_version, active_sessions) when
    the daemon has died but the on-disk state survives. Exit code:
    0 if live probe succeeded, 1 if fell back to heartbeat, 2 if
    neither could be read.
    """
    import json
    import urllib.error
    import urllib.request
    port_file = WORKSPACE_ROOT / ".daemon-state" / "port"
    if not port_file.exists():
        # No port file -> daemon has never run, OR was uninstalled.
        # Heartbeat fallback may still work if a previous run left one.
        hb = _read_heartbeat_fallback()
        if hb is not None:
            print("# WARNING: no .daemon-state/port file - daemon not running.", file=sys.stderr)
            print("# Showing last heartbeat from disk:", file=sys.stderr)
            print(json.dumps(hb, indent=2))
            sys.exit(1)
        print("bridge daemon not running (no .daemon-state/port file, no heartbeat.json)", file=sys.stderr)
        sys.exit(2)
    port_str = port_file.read_text().strip()
    if not port_str.isdigit() or not (1 <= int(port_str) <= 65535):
        print(f"corrupted port file: {port_str!r}", file=sys.stderr)
        sys.exit(2)
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port_str}/health", timeout=2) as r:
            print(json.dumps(json.loads(r.read()), indent=2))
            return
    except (urllib.error.URLError, ConnectionRefusedError, OSError) as e:
        # Fall back to the on-disk heartbeat.
        hb = _read_heartbeat_fallback()
        if hb is not None:
            print(f"# WARNING: bridge daemon not reachable on port {port_str} ({e}).", file=sys.stderr)
            print(f"# Daemon may have crashed - showing last heartbeat from disk:", file=sys.stderr)
            print(json.dumps(hb, indent=2))
            sys.exit(1)
        print(f"bridge daemon not reachable on port {port_str}: {e}", file=sys.stderr)
        print("(no heartbeat.json fallback either - daemon likely never started)", file=sys.stderr)
        sys.exit(2)


def revert_to_prior_config(target_name: str | None = None):
    """Restore a config snapshot to the per-user override path.

    Without target_name: restores the most-recent prior snapshot (index 1).
    With target_name: restores that specific snapshot by filename.

    Daemon must be restarted to apply.
    Phase 1.154 (--revert-config) + 1.159 (--revert-to).
    """
    snaps = list_snapshots(WORKSPACE_ROOT)
    if not snaps:
        print(
            "no snapshots on disk yet. Start the daemon at least once "
            "to write a snapshot.",
            file=sys.stderr,
        )
        sys.exit(1)
    print("Available snapshots (newest first):")
    for i, s in enumerate(snaps):
        if target_name:
            marker = " <- will restore" if s.name == target_name else ""
        else:
            marker = " <- current boot" if i == 0 else (" <- will restore" if i == 1 else "")
        print(f"  [{i}] {s.name}{marker}")
    print()
    try:
        if target_name:
            restored = revert_config_to(WORKSPACE_ROOT, target_name)
        else:
            restored = revert_config(WORKSPACE_ROOT)
    except RuntimeError as e:
        print(f"revert failed: {e}", file=sys.stderr)
        sys.exit(1)
    print(f"Restored {restored.name} -> .daemon-state/config.yaml")
    print()
    print("WARNING: a running daemon still holds the OLD config in memory.")
    print("Restart the daemon (Ctrl+C and re-run --start) for the revert to take effect.")


# ============================================================
# CLI entry point
# ============================================================
def main():
    """CLI entry point - dispatches to start_daemon / rotate_token / check_health / revert_config."""
    # Load .env first so HEADING_OS_TZ (and other runtime config) is present even when
    # the daemon is launched by systemd/launchd with no inherited environment. Without
    # this, get_default_tz_name() falls back to UTC and the dashboard renders the wrong
    # time-of-day greeting, tz label, and meeting countdowns. Mirrors the other daemons.
    load_env(WORKSPACE_ROOT)
    from scripts.bridge_daemon.version import __version__ as _DAEMON_VERSION
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", action="store_true")
    ap.add_argument("--rotate-token", action="store_true")
    ap.add_argument("--health", action="store_true")
    ap.add_argument("--revert-config", action="store_true",
                    help="restore the most-recent prior config snapshot (index 1)")
    ap.add_argument("--revert-to", metavar="SNAPSHOT",
                    help="restore a specific snapshot by filename (use --revert-config to see available names)")
    ap.add_argument("--port", type=int, metavar="PORT",
                    help="bind to a specific port instead of auto-picking from port_range_start..+50")
    ap.add_argument("--status", action="store_true",
                    help="one-line summary of local daemon state (port + pid + uptime + heartbeat fields)")
    ap.add_argument("--version", action="version", version=f"bridge-daemon {_DAEMON_VERSION}")
    args = ap.parse_args()
    if args.rotate_token:
        rotate_token()
        return
    if args.health:
        check_health()
        return
    if args.status:
        show_status()
        return
    if args.revert_to:
        revert_to_prior_config(target_name=args.revert_to)
        return
    if args.revert_config:
        revert_to_prior_config()
        return
    if args.start or len(sys.argv) == 1 or args.port is not None:
        start_daemon(explicit_port=args.port)
        return
    ap.print_help()


if __name__ == "__main__":
    main()
