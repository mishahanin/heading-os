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
from scripts.utils.operator_identity import operator_slug
from scripts.utils.paths import get_data_root

from scripts.bridge_daemon._atomic import atomic_write_text
# build_app is imported lazily inside start_daemon() (F-2.1: it pulls in fastapi,
# which must not import at module scope so bridge-daemon.py stays collectable).
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
from scripts.bridge_daemon.refreshers import mail as r_mail
from scripts.bridge_daemon.refreshers import inflight as r_inflight
from scripts.bridge_daemon.refreshers import pulse as r_pulse
from scripts.bridge_daemon.scheduler import build_scheduler
from scripts.bridge_daemon.state import State
from scripts.bridge_daemon.watcher import start_observer
from scripts.utils import daemon_heartbeat
from scripts.utils import tracing
from scripts.utils.trace_filter import install_log_factory
from scripts.utils.clone_guard import require_main_clone

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
            # `.splitlines()[-1]` on a whitespace-only stdout is an IndexError:
            # `"\n"` is truthy, `.strip()` empties it, and the list is empty. It
            # escaped both `except` clauses and turned a SUCCESSFUL report into a
            # scheduler job error, feeding the heartbeat error count with noise.
            lines = (result.stdout or "").strip().splitlines()
            logging.info("llm_fit_report: ok (%s)", lines[-1] if lines else "")
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


# The tier alone used to decide this, so ANY type `config/tool-risk.json` maps
# to `notify` -- today's, or one a later config edit adds -- was flipped to
# `applied` by the daemon with nothing executed and, absent a `prev_value` the
# producer never stamps yet, nothing for `undo_card` to revert either. A config
# edit could therefore mutate CEO-facing queue state on its own. `tiered-risk.md`
# lets the ledger RAISE friction freely and never lower it, so the code carries
# its own allowlist and an unlisted notify type simply waits for the CEO.
_AUTO_APPLY_TYPES = frozenset({"pipeline_update"})


def _sweep_non_gated_cards(data_root: Path, aq) -> int:
    """R3 tier routing: auto-apply the notify types listed in
    ``_AUTO_APPLY_TYPES``, in-process under the queue lock.

    Routing (tier resolved from config/tool-risk.json via tool_risk.tier_for):

    - ``autonomous`` display-only types (``note``, ``alert``) -> left in the
      active queue for the CEO to read and manually dismiss. These carry no
      executable action; they are surfaced read-only. (CEO decision 2026-06-04:
      Cold-Sweep deposits cold/drop recommendations as ``note`` cards, so
      auto-disposing them would hide advice the CEO meant to read. Notes are
      surfaced, not swept.) The summary line above said this function DISPOSED
      them until 2026-08-24, which is the opposite of the routing below. No
      branch tests for this tier: the dispatch below matches ``notify`` only, so
      an autonomous card falls through it untouched. The explicit branch that
      used to say so was deleted the same day as provably behaviour-neutral.
    - ``notify`` ``pipeline_update`` -> auto-apply (status ``applied``). The
      reversible ``prev_value`` the producer (R4, future) stamps on the card is
      preserved so ``undo_card`` can revert it; the daemon never invents
      pipeline state here.
    - any OTHER ``notify`` type -> left pending. It reaches the CEO instead of
      being applied by a daemon that knows nothing about what it means.

    ``gated`` cards (email_send) are untouched. There is no send executor here
    and no "below" for one to run in: the spawn was REMOVED 2026-06-27 and the
    terminal ``action-queue.py approve`` is the sole send path. This docstring
    claimed otherwise until 2026-08-24, pointing readers at a component that
    does not exist.

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
        # Only the notify tier is dispatched. Every other tier falls through
        # untouched, which is the disposition both of them need: a gated send
        # belongs to the terminal approve path alone (lethal-trifecta), and
        # autonomous display-only types are surfaced read-only for the CEO to
        # dismiss - never auto-disposed.
        #
        # An explicit `tier == AUTONOMOUS -> continue` branch stood here until
        # 2026-08-24. Mutation testing could not kill it, and the reason was not
        # a missing test: neither arm below can fire on a non-notify tier, so
        # the branch and the fall-through disposed of an autonomous card
        # identically, and no test can separate two paths that do the same
        # thing. It was dead code wearing the CEO decision of 2026-06-04 as a
        # label. That decision is now held by something that can actually fail:
        # the tier check on the arm below, plus
        # `test_no_auto_apply_type_resolves_outside_notify`, which resolves the
        # allowlist through the real ledger instead of restating it.
        if tier == tool_risk.NOTIFY and atype in _AUTO_APPLY_TYPES:
            # Auto-apply. prev_value (if the producer supplied it) stays on the
            # card so undo_card can revert; we do not synthesise it.
            aq.apply_status(data_root, aid, "applied", event="auto_apply")
            swept += 1
        elif tier == tool_risk.NOTIFY:
            logging.warning(
                "action-queue card %s is notify-tier %r, which this daemon does "
                "not auto-apply; leaving it pending for the CEO", aid, atype)
    return swept


def _executor_job(workspace_root: Path, state, data_root: Path | None = None) -> None:
    """Non-gated sweep job (the send-executor spawn was REMOVED 2026-06-27).

    The synchronous terminal ``action-queue.py approve`` is now the SOLE send
    path; the daemon NO LONGER SENDS. This slimmed job only sweeps the queue
    in-process for non-gated cards: autonomous types (``note``, ``alert``) are
    surfaced read-only and LEFT for the CEO to dismiss -- never auto-disposed,
    per the CEO decision of 2026-06-04 -- and notify ``pipeline_update`` cards
    are auto-applied (with ``prev_value`` preserved for undo). This paragraph
    said ``note`` cards were disposed until 2026-08-23, which is the opposite of
    the branch in ``_sweep_non_gated_cards`` and of that decision. It never
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
            logging.info("watchdog: %d alert(s) raised; verdict=%s",
                         report["alerts_fired"], report.get("verdict"))
        # Separate line, and a WARNING: an alert that reached no channel beyond
        # the log is the daemon telling this log that nothing else was told.
        if report.get("alerts_undelivered"):
            logging.warning(
                "watchdog: %d of %d alert(s) reached no channel but the log; "
                "check the Telegram credentials and the action-queue path",
                report["alerts_undelivered"], report.get("alerts_fired", 0))
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
            try:
                max_per_tick = int(crit.get("max_per_tick", 3) or 3)
            except (ValueError, TypeError):
                # Every other config read in this function is coerced
                # defensively; this one was bare. `.daemon-state/config.yaml` is
                # hand-editable, so `max_per_tick: "lots"` raised ValueError and
                # `max_per_tick: [3]` raised TypeError, straight out of
                # `_register_spine_jobs` into `start_daemon`'s `except
                # Exception: ... raise`. The WHOLE daemon then failed to boot
                # over one optional knob on an optional, default-off feature: no
                # observer, no scheduler, no uvicorn. Four docstrings in this
                # file promise these spine jobs self-disable and that a failure
                # "must never take the daemon down".
                logging.warning(
                    "critique: max_per_tick=%r is not a number; using 3",
                    crit.get("max_per_tick"))
                max_per_tick = 3
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
def _bind_listener(port: int) -> socket.socket:
    """Bind and listen on 127.0.0.1:port, returning the OPEN socket.

    Binding, not probing. `connect_ex` answers "is someone accepting here",
    which is a weaker question than "can I have this port": it misses a socket
    bound but not listening, and it misses a bind to a different interface. It
    also cannot HOLD the port -- and holding is the point, see `_pick_port`.

    Raises OSError when the port is unavailable. No SO_REUSEADDR: a permissive
    rebind is exactly what must fail here.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", port))
        sock.listen(128)
    except OSError:
        sock.close()
        raise
    return sock


def _pick_port(start: int) -> tuple[int, socket.socket]:
    """First free TCP port in [start, start+50), with the socket HELD OPEN.

    The socket comes back with the number because the two used to be separate
    moments. The port was probed free, written to `.daemon-state/port` and
    `BRIDGE_PORT`, and only bound later inside `uvicorn.run` -- so anything
    that grabbed it in between (a second `--start` racing boot, an unrelated
    dev server) left the daemon dead and the port file confidently advertising
    a port it never held. `--health` then probed a stranger.

    The caller passes this socket straight to uvicorn, so nothing can take the
    port between the check and the bind: there is no longer an "in between".
    """
    for p in range(start, start + 50):
        try:
            return p, _bind_listener(p)
        except OSError:
            continue
    # `start + 49`, the last port the loop actually probes. The message said
    # `start + 50`, which `range(start, start + 50)` never reaches, so an
    # operator freeing "the last port in the range" freed one that was never
    # tried. The docstring's half-open `[start, start+50)` was right all along.
    raise RuntimeError(
        f"no free port in range {start}..{start + 49} (50 ports probed)")


def _is_bridge_health_payload(payload) -> bool:
    """True when a decoded /health body is THIS daemon's, not merely some JSON.

    `_live_daemon_port` answers a narrower question on purpose: is the port
    occupied? Any HTTP answer settles that, and for the singleton guard it is
    the right question - launching a second daemon onto a port somebody else
    holds fails either way.

    `check_health` asks a different question and printed the first answer as if
    it were the second: an unrelated local server on the port named in a STALE
    `.daemon-state/port` had its response printed as this daemon's health, exit
    0. The /health route returns a known shape, so identity is checkable, and
    `.claude/rules/scope-claims.md` says a tool states the coverage its method
    established. This is that method.

    `pid` and `version` together: `ok` alone is too common a key to identify
    anything, and a bare 200 from a static file server carries neither.
    """
    return (isinstance(payload, dict)
            and payload.get("ok") is True
            and isinstance(payload.get("pid"), int)
            and isinstance(payload.get("version"), str))


def _live_daemon_port(timeout: float = 2.0) -> int | None:
    """The port SOMETHING is bound to and answering HTTP on, or None.

    Deliberately not "the port the bridge daemon answers on": a 500 counts, and
    so does an unrelated server. That is the right test for the singleton guard
    in `start_daemon`, which only needs to know the port is taken. A caller that
    needs to know it is THIS daemon calls `_bridge_answers` as well.

    `_pick_port` exists so a boot survives a busy port, which also means a
    second `--start` succeeds on the next one. Both instances then share the
    singleton `.daemon-state/port` and `heartbeat.json`: the second overwrote
    the port file with its own port, and its exit unlinked that file while the
    FIRST daemon was still bound and serving. `--health` then reported "daemon
    not running" for a daemon answering fine, and `--status` printed `port=-`.

    A stale file whose port nothing answers returns None, so a crashed daemon
    never blocks the next start.
    """
    import urllib.error
    import urllib.request
    port_file = WORKSPACE_ROOT / ".daemon-state" / "port"
    try:
        port_str = port_file.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        # `UnicodeDecodeError` is a `ValueError`, so `except OSError` never saw
        # it. The port file is written by a live daemon, so a torn write is the
        # ordinary corruption here, and the docstring above promises None for a
        # file that does not name a live port. MEASURED 2026-09-01 with a port
        # file of `b"3141\xff5"`: a raw UnicodeDecodeError out of this function,
        # which is the SINGLETON GUARD - so `--start` died on the check that
        # exists to stop a second daemon, instead of answering it.
        #
        # `scripts/daemon-fleet-health.py` already carried this class at its own
        # three reads. This file, which writes the state that file reads, had
        # fallen behind it.
        return None
    if not port_str.isdigit() or not (1 <= int(port_str) <= 65535):
        return None
    port = int(port_str)
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health",
                                    timeout=timeout):
            return port
    except urllib.error.HTTPError:
        # A 500 IS an answer, and this function's job is "is something bound
        # and serving on that port?". HTTPError subclasses URLError, so the
        # handler below swallowed every non-2xx and reported the daemon absent
        # -- which made `start_daemon` launch a SECOND one. The singleton guard
        # failed open in the degraded state where a duplicate hurts most:
        # two schedulers, two critique sweeps, two sets of alerts.
        return port
    except (urllib.error.URLError, OSError):
        return None


def _acquire_start_lock():
    """An exclusive lock held for this process's whole life, or None.

    `_live_daemon_port()` alone is check-then-act with a long gap. It reads
    `.daemon-state/port`, which is not written until well after the check, and
    `_pick_port` holds each probed socket open -- so two `--start` processes
    launched together do not even collide on a port. They bind DIFFERENT ones,
    both write the shared port file (last one wins), and both run schedulers:
    the action-queue sweep, the watchdog and the critique sweep each run twice,
    which is duplicate alerts and duplicate model spend. `--health` only ever
    sees one of them. Scripted or service launches make the overlap ordinary;
    two hand-typed commands rarely hit it, which is why it survived.

    The lock closes the gap because it is taken and held, not sampled. The file
    is never unlinked: removing a flocked path lets the next process lock a
    different inode with the same name, which is the same race wearing the
    lock's clothes.

    Returns the open file object -- the caller must keep the reference, since
    closing it releases the lock. None means someone else holds it.
    """
    try:
        import fcntl
    except ImportError:                       # pragma: no cover - Windows
        # No flock here. The `_live_daemon_port` check below still runs, so
        # this is exactly the pre-lock behaviour, not a new hole -- but say so
        # rather than let a Windows operator read the lock as protection.
        print("note: no fcntl on this platform, so a concurrent --start is "
              "guarded only by the liveness probe.", file=sys.stderr)
        return _NO_LOCK
    lock_path = WORKSPACE_ROOT / ".daemon-state" / "start.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("a+")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return None
    return handle


# A truthy sentinel for the platform with no flock, so the caller's "did I get
# the lock?" test does not have to know why.
_NO_LOCK = object()


def _verify_port_free(port: int) -> tuple[int, socket.socket]:
    """Claim an explicit port or raise; returns it with the socket held open.

    Used by the --port CLI override (Phase S) so a CEO request for a
    specific port fails fast instead of silently falling back to the
    auto-pick range.
    """
    if not (1 <= port <= 65535):
        raise RuntimeError(f"port {port} out of range (1..65535)")
    try:
        return port, _bind_listener(port)
    except OSError as exc:
        raise RuntimeError(f"port {port} is already in use") from exc


def _configure_logging(log_path: Path | None = None) -> logging.Logger:
    """Configure the root logger for the daemon process and return it.

    Extracted from start_daemon() on 2026-08-20 so the logger levels have a
    test seam - start_daemon() itself ends in uvicorn.run() and cannot be
    called from a test.
    """
    log_path = LOG_PATH if log_path is None else log_path
    install_log_factory()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    # Rotating handler: 1 MB per file, 3 backups (~4 MB total cap).
    # Workspace convention - matches scripts/sync-exchange-daemon.py.
    handler = RotatingFileHandler(
        log_path, maxBytes=1_000_000, backupCount=3, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s [%(trace_id)s] %(message)s"))
    root = logging.getLogger()
    # Clear any pre-existing handlers (defense vs. test contamination).
    for h in list(root.handlers):
        root.removeHandler(h)
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    # 2026-08-20: APScheduler logs every job start and every successful
    # completion at INFO. Measured over the four retained bridge.log files
    # (16,277 lines): 13,780 of them - 84.7% - were APScheduler job-lifecycle
    # chatter. Success shouted as loudly as failure, so a job that STOPPED
    # firing read exactly like one that fires every minute, and the log
    # carried no signal. WARNING keeps genuine scheduler failures (missed
    # runs, job exceptions, executor errors) - they still propagate to root,
    # so they reach both the file and install_error_tracker below, which
    # feeds heartbeat.json's recent_error_count from WARNING+ records.
    # The daemon's own lines are untouched: pulse/mail/config log on the root
    # logger or on scripts.bridge_daemon.* loggers, none of which descend
    # from 'apscheduler'.
    logging.getLogger("apscheduler").setLevel(logging.WARNING)
    # Phase J: attach the error tracker to root so every WARNING+ record
    # feeds heartbeat.json's recent_error_count + last_error fields.
    install_error_tracker(root)
    return root


def start_daemon(explicit_port: int | None = None):
    """Start the bridge daemon: load token + config, pick a port, start observer +
    scheduler, then run uvicorn on 127.0.0.1. Cleans up on exit or exception.

    Phase S: when explicit_port is set, skip the auto-pick range and bind
    exactly that port. Fails fast if it's busy. Without explicit_port,
    auto-pick from cfg["port_range_start"] (scanning +50). The 31415 default
    comes from DEFAULTS in scripts/bridge_daemon/config.py, which load_config
    merges under every override -- not from this function, which subscripts
    the key directly.
    """
    import uvicorn
    from scripts.bridge_daemon.app import build_app

    # Single instance. Before this check a second --start bound the next port,
    # took over the shared port file, and on exit deleted it out from under the
    # daemon that was still serving. Checked before logging is configured so the
    # refusal is one line on the operator's terminal, not a log entry.
    # The lock comes FIRST and is kept in a local for the life of this call, so
    # a concurrent --start is refused during the whole boot, not just at this
    # instant. The liveness probe stays behind it: it produces the useful
    # message, and it still catches a daemon started before the lock existed.
    start_lock = _acquire_start_lock()          # noqa: F841 - held, not used
    if start_lock is None:
        running = _live_daemon_port()
        where = (f" It is serving on 127.0.0.1:{running}."
                 if running is not None else
                 " Another --start is mid-boot.")
        print("bridge daemon start is already in progress or running."
              f"{where} Refusing to start a second one: the two share "
              ".daemon-state/port and heartbeat.json, both run schedulers, "
              "and whichever exits first would leave the survivor "
              "unreachable to --health.", file=sys.stderr)
        raise SystemExit(1)

    already = _live_daemon_port()
    if already is not None:
        # "something is serving", not "the daemon is running": this probe
        # accepts any HTTP answer, and the refusal is correct either way,
        # because the port is taken whoever holds it.
        print(f"something is already serving on 127.0.0.1:{already}, the port "
              f"in .daemon-state/port. Run --health to see whether it is this "
              f"daemon. Refusing to start a second one: the two would share "
              f".daemon-state/port and heartbeat.json, and whichever exits "
              f"first would leave the survivor unreachable to --health.",
              file=sys.stderr)
        raise SystemExit(1)

    # R12: mint a trace ID for this daemon's process tree and install the
    # record factory before any logging so every line (and every subprocess
    # this daemon spawns) carries the same [trace_id].
    tracing.mint()
    _configure_logging()

    observer = None
    sched = None
    port = None          # bound below; referenced by the cleanup clause
    listener = None      # the held listening socket; handed to uvicorn
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
        # cfg["user_slug"] is resolved through the operator seam in load_config;
        # the defensive fallback also routes through it (never a bare literal).
        user_slug = cfg.get("user_slug") or operator_slug()
        if explicit_port is not None:
            port, listener = _verify_port_free(explicit_port)
            logging.info(f"using explicit port {port} (from --port flag)")
        else:
            port, listener = _pick_port(cfg["port_range_start"])
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
            "email": lambda: r_mail.refresh(WORKSPACE_ROOT, state),
            "inflight": lambda: r_inflight.refresh(WORKSPACE_ROOT, state),
            # Phase 2 (2026-05-24): pulse refresher computes the full payload
            # off the request path and writes .daemon-state/pulse-snapshot.json.
            # Endpoint reads from the snapshot. The ~7s figure that justified
            # this was WSL /mnt/c; the same walk is 68 ms on today's ext4
            # (2026-08-20). See refreshers/pulse.py before re-deciding sizes.
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
        # `Server.run(sockets=[...])` rather than `uvicorn.run(host, port)`:
        # the listener is already bound, so uvicorn adopts it instead of racing
        # for the same port a second time. host/port stay on the Config for the
        # sake of the log line uvicorn prints.
        server = uvicorn.Server(
            uvicorn.Config(app, host="127.0.0.1", port=port, log_config=None)
        )
        server.run(sockets=[listener])
    except Exception:
        logging.exception("bridge daemon failed during startup or runtime")
        raise
    finally:
        # The listener is uvicorn's once handed over, but a failure between the
        # bind and that handover leaves it held by a process that is exiting;
        # close it so a retry can take the port back immediately.
        if listener is not None:
            try:
                listener.close()
            except OSError:
                logging.warning("listening socket close failed during cleanup",
                                exc_info=True)
        # Every cleanup step is guarded on its own. `sched` is bound long before
        # `sched.start()`, and on a scheduler that never started APScheduler
        # 3.11.3 raises SchedulerNotRunningError -- from inside the finally,
        # where it REPLACED the real startup failure. The operator was then told
        # "Scheduler is not running", which is true and useless, while the
        # heartbeat error or malformed job that actually killed the boot was
        # gone. The observer pair had the same shape: `stop()` raising both
        # skipped `join()` and hid the original.
        #
        # Guarded, not silent: a cleanup that fails is still a fact worth a line.
        if sched is not None:
            try:
                sched.shutdown(wait=False)
            except Exception:                     # noqa: BLE001 - see above
                logging.warning("scheduler shutdown failed during cleanup",
                                exc_info=True)
        if observer is not None:
            try:
                observer.stop()
            except Exception:                     # noqa: BLE001 - see above
                logging.warning("watchdog observer stop failed during cleanup",
                                exc_info=True)
            try:
                observer.join()
            except Exception:                     # noqa: BLE001 - see above
                logging.warning("watchdog observer join failed during cleanup",
                                exc_info=True)
        # Drop the port file on the way out. It is written BEFORE uvicorn binds
        # -- the scheduler start, the pulse prime and build_app all run in
        # between -- so a startup that failed after that write left a file
        # naming a port nothing listens on, and `--status` treats a present file
        # as a live daemon while treating absence as "not running". Removing it
        # here makes a failed start indistinguishable from no start.
        #
        # This comment used to say the probe-then-bind race between two daemons
        # was still open and "needs binding the socket first and handing uvicorn
        # the fd". That is exactly what this file now does: `_pick_port` returns
        # a BOUND, listening socket and `server.run(sockets=[listener])` adopts
        # it, so there is no gap left to race. The residual multi-instance
        # hazard was the port file itself, which `_live_daemon_port` now guards.
        try:
            port_file = WORKSPACE_ROOT / ".daemon-state" / "port"
            # `errors="replace"` so a torn port file cannot raise out of a
            # SHUTDOWN path. An undecodable file never equals `str(port)`, so
            # this daemon leaves it alone, which is the safe direction: it does
            # not own a file it cannot read.
            if (port is not None and port_file.exists()
                    and port_file.read_text(
                        encoding="utf-8", errors="replace").strip() == str(port)):
                port_file.unlink()
        except OSError:
            logging.warning("could not remove the stale port file", exc_info=True)


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
    # The last 4, not the FIRST 16. A 16-character prefix of a live bearer token
    # is enough of the secret to matter, and stdout is terminal scrollback, tmux
    # history, and any provisioning log that captured the command -- none of
    # which have the 0600 the token file gets. 4 trailing characters still let
    # the operator confirm the rotation happened.
    print(f"ends with: ...{new[-4:]}")
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
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        # `UnicodeDecodeError` is a SIBLING of `json.JSONDecodeError` under
        # `ValueError`, not a subclass, and it is raised by `read_text` before
        # `json.loads` is handed anything. The heartbeat is rewritten on a timer
        # by a live daemon, so a half-written file is the ordinary corruption.
        #
        # MEASURED 2026-09-01 on a heartbeat holding one 0xff byte: this
        # function raised instead of returning None, and took `show_status` and
        # `check_health` with it - the two commands an operator runs precisely
        # when the daemon is sick, both answering a traceback rather than the
        # diagnostic they exist to print. `scripts/daemon-fleet-health.py`
        # already caught this at its own `_read_heartbeat`, naming the same
        # reason; this reader had fallen behind it.
        return None


def show_status():
    """Print a one-line grep-friendly summary of the local daemon state.

    Phase W: combines .daemon-state/port + heartbeat.json (pid, uptime,
    version, config_loaded_version, last_heartbeat) into a single line
    so cron / shell pipelines can grep for fields without running both
    --health and reading the heartbeat manually. No HTTP call, no auth.

    Output format, tab-separated for `cut -f`, in this field order:
      port  pid  uptime  version  config_v  sessions  errors  last_hb

    The separator was two spaces until 2026-08-24, under this same docstring
    promising `cut -f`: with no tab in the line every `cut -fN` returned the
    whole line, or with `-s` nothing at all. The documented field list also
    stopped at `last_hb` and omitted `sessions` and `errors`, so even the
    positions it implied were wrong.

    Exit codes:
      0 - status available (port file or heartbeat readable)
      1 - neither port nor heartbeat exists (daemon never started)
    """
    port_file = WORKSPACE_ROOT / ".daemon-state" / "port"
    hb = _read_heartbeat_fallback()
    # `errors="replace"`, and the encoding stated. A bare `read_text()` decodes
    # with the LOCALE encoding and raises on a byte it cannot handle, so an
    # undecodable port file killed this whole status line - measured 2026-09-01
    # with `b"3141\xff5"`. Replacing keeps the existing "print whatever is in
    # the file" behaviour (a decodable `garbage` already printed as
    # `port=garbage`) and simply extends it to bytes that are not text.
    port = (port_file.read_text(encoding="utf-8", errors="replace").strip()
            if port_file.exists() else "-")

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
    print("\t".join(fields))


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
    # `errors="replace"` for the same reason as `show_status`, and it needs no
    # new branch: an undecodable port file becomes a string that is not all
    # digits, so the `corrupted port file` arm below - which already falls back
    # to the heartbeat - is the one that runs. Before this it was a raw
    # UnicodeDecodeError out of `--health`, measured 2026-09-01.
    port_str = port_file.read_text(encoding="utf-8", errors="replace").strip()
    if not port_str.isdigit() or not (1 <= int(port_str) <= 65535):
        # Exit 2 is documented as "neither could be read", and this branch used
        # to take it without ever trying the heartbeat -- losing the last known
        # pid, version and uptime in the one scenario (a corrupt state file)
        # where they are worth the most. Every other failure path here falls
        # back first; this one now does too.
        print(f"corrupted port file: {port_str!r}", file=sys.stderr)
        hb = _read_heartbeat_fallback()
        if hb is not None:
            print("# Showing last heartbeat from disk:", file=sys.stderr)
            print(json.dumps(hb, indent=2))
            sys.exit(1)
        sys.exit(2)
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port_str}/health", timeout=2) as r:
            payload = json.loads(r.read())
            # A 200 on the port named in the port file proves something is
            # bound there. It does not prove it is this daemon, and this
            # function's contract is the daemon's health, so the shape is
            # checked: `/health` returns ok/pid/version, and until 2026-08-28 an
            # unrelated local server answering on a STALE port was printed as
            # the daemon's health with exit 0. The comment ten lines down
            # already described that exact scenario for the UNREADABLE body; a
            # readable body from the same wrong process went through.
            if not _is_bridge_health_payload(payload):
                print(f"# WARNING: something answers on port {port_str}, but it "
                      f"is not this daemon (no ok/pid/version in its /health).",
                      file=sys.stderr)
                print("# The port file is stale, or another process took the "
                      "port. Showing what answered:", file=sys.stderr)
                print(json.dumps(payload, indent=2))
                # This branch took exit 1 without ever reading the heartbeat,
                # and 1 is documented as "fell back to heartbeat". A foreign
                # process on the named port is the daemon-probably-dead case the
                # fallback was written for, and it was the one case that lost
                # the last known pid, version, uptime and `last_heartbeat` - the
                # field that says WHEN the real daemon died. Every other failure
                # path here falls back first; the corrupt-port-file branch above
                # carries a comment saying so.
                hb = _read_heartbeat_fallback()
                if hb is not None:
                    print("# Showing last heartbeat from disk:", file=sys.stderr)
                    print(json.dumps(hb, indent=2))
                sys.exit(1)
            print(json.dumps(payload, indent=2))
            return
    except (urllib.error.URLError, ConnectionRefusedError, OSError,
            ValueError) as e:
        # ValueError covers the two ways a 200 can still be unreadable, and
        # neither was caught: `json.JSONDecodeError` subclasses it, and so does
        # the `UnicodeDecodeError` from decoding a non-UTF-8 body. The stale-port
        # case this function otherwise handles carefully produces exactly that
        # input -- the port file survives, a DIFFERENT process now holds the
        # port and answers 200 with its own content -- and `--health` died with
        # a traceback instead of the 0/1/2 its docstring promises, skipping the
        # heartbeat fallback that would have shown when the real daemon died.
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
    require_main_clone(__file__)
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
