#!/usr/bin/env python3
"""Tribe Fireside daemon — in-process scheduler replacing 9 Task Scheduler entries.

Subcommands:
    daemon  : run forever (the scheduler). Default for /prime auto-start.
    run J   : execute job J once, out-of-band (smoke test or backfill).
    status  : print PID, uptime, and the names of the registered jobs.
              NOT next-run times: `status` is a separate process with no
              access to the daemon's live scheduler, so it cannot know them.
              The line used to promise them anyway.
    stop    : signal a running daemon to shut down cleanly.

PID file:  .fireside/daemon.pid
Log file:  .fireside/daemon.log  (rotated by RotatingFileHandler, 1 MB, keep 3)
"""
from __future__ import annotations

import argparse
import asyncio
import importlib.util
import logging
import os
import signal
import sys
import time
from argparse import Namespace
from logging.handlers import RotatingFileHandler
from pathlib import Path
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

WORKSPACE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WORKSPACE))

from scripts.utils import daemon_heartbeat  # noqa: E402
from scripts.utils import tracing  # noqa: E402
from scripts.utils.scheduler_defaults import JOB_DEFAULTS  # noqa: E402
from scripts.utils.trace_filter import install_log_factory  # noqa: E402
from scripts.utils.workspace import get_default_tz, get_default_tz_name, load_env  # noqa: E402

# ============================================================
# Configuration
# ============================================================

RUNTIME_DIR = WORKSPACE / ".fireside"
PID_FILE = RUNTIME_DIR / "daemon.pid"
LOG_FILE = RUNTIME_DIR / "daemon.log"
STARTED_AT_FILE = RUNTIME_DIR / "started_at"
STOP_SENTINEL = RUNTIME_DIR / "stop"  # touch to request clean shutdown on Windows

JOB_SPECS: dict[str, dict] = {
    # Note: IntervalTrigger does NOT fire immediately on daemon start — the
    # first poll runs one interval later, so `now + 5s` for the value below.
    # Acceptable: Telegram queues updates server-side, so /start events sent
    # during the gap are picked up at the first poll. (The comment used to say
    # "now + 5min", describing a gap sixty times longer than the trigger. It
    # was left over from a 300-second interval; the interval is the truth.)
    "poll": {"trigger": {"kind": "interval", "seconds": 5}, "critical": True},
    # heartbeat pings FIRESIDE_HC_POLL every minute so the fireside-poll
    # healthchecks.io check stays green in webhook mode (where cmd_poll never
    # runs). 1-min cadence is well under the 5-min check period + grace.
    "heartbeat": {"trigger": {"kind": "interval", "minutes": 1}, "critical": False},
    "health-check": {"trigger": {"kind": "interval", "minutes": 30}, "critical": False},
    "speaker-dms": {"trigger": {"kind": "cron", "hour": 9, "minute": 0}, "critical": True},
    "helmsman-brief": {"trigger": {"kind": "cron", "hour": 10, "minute": 0}, "critical": True},
    "sunday-preview": {"trigger": {"kind": "cron", "day_of_week": "sun", "hour": 18, "minute": 0}, "critical": True},
    "weekly-discrepancy-report": {"trigger": {"kind": "cron", "day_of_week": "sun", "hour": 17, "minute": 0}, "critical": False},
    "email-backup": {"trigger": {"kind": "cron", "day_of_week": "sun", "hour": 19, "minute": 0}, "critical": False},
    "dayof-reminders": {"trigger": {"kind": "cron", "day_of_week": "mon,wed", "hour": 15, "minute": 30}, "critical": True},
    "unpin-weekly": {"trigger": {"kind": "cron", "day_of_week": "wed", "hour": 16, "minute": 0}, "critical": False},
    "topic-nudge": {"trigger": {"kind": "cron", "day_of_week": "sat", "hour": 12, "minute": 0}, "critical": False},
    "topic-digest": {"trigger": {"kind": "cron", "day_of_week": "sun", "hour": 9, "minute": 0}, "critical": False},
    "cycle-end-invite": {"trigger": {"kind": "cron", "hour": 11, "minute": 0}, "critical": False},
    # Daily: rebuild schedule.json when a new cycle config has landed and the old
    # cycle ended. No-op on non-rollover days. Early (07:30) so a rolled schedule
    # is live before speaker-dms (09:00) / helmsman-brief (10:00) read it.
    "cycle-rollover": {"trigger": {"kind": "cron", "hour": 7, "minute": 30}, "critical": False},
}


# ============================================================
# Logging setup
# ============================================================

def _setup_logging() -> logging.Logger:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    # R12: mint this process's trace ID + install the record factory before
    # any handler is built so every line carries [trace_id].
    tracing.mint()
    install_log_factory()
    logger = logging.getLogger("fireside-daemon")
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

    # Attach the SAME handler objects to the scripts.utils.healthchecks logger,
    # so the hc-ping helper's exception/warning lines land in daemon.log too
    # (otherwise its _logger.exception is functionally silent in production).
    #
    # The same OBJECT, not a second RotatingFileHandler on the same path. The
    # comment above already said "the same handlers" while the code below built
    # a new one, giving one file two open handles with independent rotation
    # state: on Windows the rename fails with PermissionError because the other
    # handle holds the file, and on POSIX the handler that did not rotate keeps
    # writing into the renamed `daemon.log.1`, splitting the stream in two.
    hc_logger = logging.getLogger("scripts.utils.healthchecks")
    hc_logger.setLevel(logging.INFO)
    if not hc_logger.handlers:
        hc_logger.addHandler(handler)
        hc_logger.addHandler(stream)

    return logger


# ============================================================
# Dynamic import of fireside-bot.py (hyphen filename)
# ============================================================

def _load_fireside_bot():
    path = WORKSPACE / "scripts" / "fireside-bot.py"
    spec = importlib.util.spec_from_file_location("fireside_bot", str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ============================================================
# PID file management
# ============================================================

def live_daemon_pid() -> int | None:
    """The running daemon's PID, or None. ONE read, then one liveness check.

    `status` and `stop` used to call `is_daemon_alive()` and then re-read the
    PID file as a separate unguarded `int(PID_FILE.read_text())`. A daemon that
    exited in between removed the file, and both subcommands died on an
    unhandled FileNotFoundError traceback. Reading once and handing the value
    back closes that gap.
    """
    try:
        pid = int(PID_FILE.read_text().strip())
    except (ValueError, OSError):
        return None
    return pid if _pid_is_running(pid) else None


def is_daemon_alive() -> bool:
    """Check whether a daemon process is currently running per PID file."""
    return live_daemon_pid() is not None


def _pid_is_running(pid: int) -> bool:
    """Cross-platform: is the given PID alive?"""
    if pid <= 0:
        return False
    if os.name == "nt":
        # On Windows, use ctypes OpenProcess + GetExitCodeProcess.
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
    else:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            # The process EXISTS and belongs to another user. Reporting it dead
            # let `is_daemon_alive()` say "not running", which permits a second
            # daemon start and makes `status` lie.
            return True
        return True


# ============================================================
# Job dispatcher
# ============================================================

class JobDispatcher:
    """Wraps fireside-bot cmd_* calls with per-job try/except + logging."""

    def __init__(self, fireside_bot, logger: logging.Logger):
        self.fb = fireside_bot
        self.logger = logger
        # Map job-name -> cmd_X function
        self._fn_map = {
            "poll": fireside_bot.cmd_poll,
            "heartbeat": fireside_bot.cmd_heartbeat,
            "health-check": fireside_bot.cmd_health_check,
            "speaker-dms": fireside_bot.cmd_speaker_dms,
            "helmsman-brief": fireside_bot.cmd_helmsman_brief,
            "sunday-preview": fireside_bot.cmd_sunday_preview,
            "weekly-discrepancy-report": fireside_bot.cmd_weekly_discrepancy_report,
            "email-backup": fireside_bot.cmd_email_backup,
            "dayof-reminders": fireside_bot.cmd_dayof_reminders,
            "unpin-weekly": fireside_bot.cmd_unpin_weekly,
            "topic-nudge": fireside_bot.cmd_topic_nudge,
            "topic-digest": fireside_bot.cmd_topic_digest,
            "cycle-end-invite": fireside_bot.cmd_cycle_end_invite,
            "cycle-rollover": fireside_bot.cmd_cycle_rollover,
        }

    def dispatch(self, job_name: str) -> None:
        # R14: piggyback the per-daemon liveness beat on the existing 1-min
        # heartbeat job so the watchdog sees fireside in
        # .daemon-state/heartbeats/fireside.json on a fast tick.
        if job_name == "heartbeat":
            daemon_heartbeat.beat("fireside")
        fn = self._fn_map.get(job_name)
        if fn is None:
            self.logger.error("dispatch: unknown job %s", job_name)
            return
        self.logger.info("job-start %s", job_name)
        try:
            fn(Namespace(dry_run=False))
            self.logger.info("job-ok %s", job_name)
        except Exception:
            self.logger.exception("job-fail %s", job_name)


# ============================================================
# Subcommand: daemon
# ============================================================

def make_webhook_death_handler(stop_event: "asyncio.Event", logger: logging.Logger):
    """A done-callback that stops the daemon when the webhook task ends early.

    In webhook mode the `poll` job is deliberately skipped, so this server is
    the ONLY way an update reaches the bot. Nothing used to observe the task:
    it was awaited solely inside the shutdown `finally`, and because the
    variable held a reference asyncio never even logged an unretrieved
    exception. A port already bound, a certificate that expired mid-run, or an
    ASGI error killed the ingress while the `heartbeat` job kept pinging
    healthchecks.io every minute — Telegram POSTed into a dead endpoint,
    retried, and dropped the updates, and every monitor stayed green.

    A monitor that cannot go red is not a monitor.

    Module-level and returning the callback, rather than a closure inside
    `_run_daemon`, so the behaviour can be tested without standing up a
    scheduler, a bot and a TLS listener.
    """
    def _webhook_died(task) -> None:
        if stop_event.is_set():
            return  # ordinary shutdown; the finally block is already running
        try:
            exc = task.exception()
        except asyncio.CancelledError:
            exc = None
        if exc is not None:
            logger.error("webhook-server DIED: %r - the only ingress path is gone; "
                         "shutting the daemon down so the heartbeat stops going green",
                         exc)
        else:
            logger.error("webhook-server exited on its own with no error - the only "
                         "ingress path is gone; shutting the daemon down")
        stop_event.set()

    return _webhook_died


def _remove_own_pid_file(logger: logging.Logger) -> None:
    """Delete the PID and start-time files ONLY when the PID file names us.

    Deleting them unconditionally is what let a start-race loser erase the
    winner's PID file: `status` then reported NOT RUNNING while a live daemon
    kept firing all fourteen jobs, and a third start was permitted on top of
    it. The check and the PID write are not atomic, so the race itself is still
    possible; what this removes is the invisible aftermath.
    """
    try:
        owner = int(PID_FILE.read_text().strip())
    except (OSError, ValueError):
        return
    if owner != os.getpid():
        logger.warning("PID file names %d, not this process (%d); leaving it alone",
                       owner, os.getpid())
        return
    for path in (PID_FILE, STARTED_AT_FILE):
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("could not remove %s: %s", path.name, exc)


def _shutdown_and_clean(scheduler, logger: logging.Logger) -> None:
    """Stop the scheduler and remove THIS process's runtime files.

    One function for both exits. The webhook-config abort used to
    `scheduler.shutdown(); return` and skip the PID and start-time cleanup that
    the normal shutdown path did, so an aborted start left a stale PID file and
    the next `status` reported RUNNING.

    The PID file is removed only when it names US. It was deleted
    unconditionally, so after two daemons raced to start (the check and the
    write are not atomic), the loser's exit erased the WINNER's PID file:
    `status` then said NOT RUNNING while a live daemon kept firing all
    fourteen jobs, and a third start was permitted on top.
    """
    scheduler.shutdown(wait=False)
    _remove_own_pid_file(logger)
    logger.info("daemon-stop")


async def _run_daemon(logger: logging.Logger) -> None:
    load_env()
    fb = _load_fireside_bot()
    dispatcher = JobDispatcher(fb, logger)

    # Webhook mode: when enabled, Telegram POSTs each update to our HTTPS
    # endpoint instead of us polling. The poll job is skipped because Telegram
    # rejects getUpdates with 409 Conflict while a webhook is registered.
    webhook_enabled = os.environ.get("FIRESIDE_WEBHOOK_ENABLED", "").lower() in ("1", "true", "yes")

    scheduler = AsyncIOScheduler(timezone=get_default_tz(),
                                 job_defaults=JOB_DEFAULTS)
    for name, spec in JOB_SPECS.items():
        if name == "poll" and webhook_enabled:
            logger.info("webhook mode: skipping poll cron job")
            continue
        trig = spec["trigger"]
        if trig["kind"] == "interval":
            interval_kwargs = {k: v for k, v in trig.items() if k != "kind"}
            trigger = IntervalTrigger(timezone=get_default_tz(), **interval_kwargs)
        else:
            cron_kwargs = {k: v for k, v in trig.items() if k != "kind"}
            trigger = CronTrigger(timezone=get_default_tz(), **cron_kwargs)
        scheduler.add_job(dispatcher.dispatch, trigger, args=[name], id=name,
                          replace_existing=True, max_instances=1, coalesce=True)

    # I-2: If a previous run left a stop sentinel on disk (e.g. killed
    # mid-shutdown), remove it so the new daemon doesn't immediately self-exit.
    if STOP_SENTINEL.exists():
        try:
            STOP_SENTINEL.unlink()
        except OSError:
            pass

    # M-3: Atomic PID and start-time writes (write-to-tmp + os.replace).
    # I-3: Store wall-clock epoch seconds so cmd_status can compute uptime.
    tmp_pid = PID_FILE.with_suffix(".pid.tmp")
    tmp_pid.write_text(str(os.getpid()))
    os.replace(tmp_pid, PID_FILE)

    tmp_started = STARTED_AT_FILE.with_suffix(".tmp")
    tmp_started.write_text(str(int(time.time())))
    os.replace(tmp_started, STARTED_AT_FILE)

    # Self-heal: regenerate any missing fireside-state files before any job
    # runs. Rebuilds tribe-roster.json from the xlsx if the file is gone;
    # without it every DM is rejected as outsider. Idempotent.
    fb.ensure_state_dir()

    logger.info("daemon-start pid=%d jobs=%d", os.getpid(), len(JOB_SPECS))
    scheduler.start()

    # Webhook server: runs uvicorn as a task in the same asyncio loop as
    # the scheduler. Started AFTER scheduler.start() so cron jobs are live
    # by the time Telegram begins POSTing webhooks to us.
    webhook_server = None
    webhook_task = None
    if webhook_enabled:
        # The WHOLE setup is guarded, not just the missing-credential check.
        # The explicit abort below used to `scheduler.shutdown(); return`
        # outside any try, so it skipped the PID and start-time cleanup that
        # the `finally` further down performs — and `import uvicorn`,
        # `int(FIRESIDE_WEBHOOK_PORT)` and `create_app` could each raise BEFORE
        # reaching that guard, taking the scheduler down with no shutdown at
        # all and leaving a stale PID file behind.
        try:
            import uvicorn  # local import — only needed in webhook mode
            from scripts.fireside_webhook import create_app

            secret = os.environ.get("FIRESIDE_WEBHOOK_SECRET", "")
            host = os.environ.get("FIRESIDE_WEBHOOK_HOST", "0.0.0.0")  # noqa: S104  # nosec B104 — public webhook must bind all interfaces so Telegram can reach it
            port = int(os.environ.get("FIRESIDE_WEBHOOK_PORT", "8443"))
            cert = os.environ.get("FIRESIDE_WEBHOOK_CERT")
            key = os.environ.get("FIRESIDE_WEBHOOK_KEY")
            if not secret or not cert or not key:
                raise RuntimeError(
                    "webhook mode requested but FIRESIDE_WEBHOOK_SECRET/CERT/KEY "
                    "missing in .env"
                )

            app = create_app(fb, secret, logger)
            config = uvicorn.Config(app, host=host, port=port,
                                    ssl_certfile=cert, ssl_keyfile=key,
                                    log_level="warning", access_log=False)
            webhook_server = uvicorn.Server(config)
            webhook_task = asyncio.create_task(webhook_server.serve())
            logger.info("webhook-server listening on %s:%d", host, port)
        except Exception as exc:  # noqa: BLE001 - reported, then a clean abort
            logger.error("webhook setup failed (%r); aborting", exc)
            _shutdown_and_clean(scheduler, logger)
            return

    stop_event = asyncio.Event()

    def _request_stop(*_args):
        logger.info("signal received; shutting down")
        stop_event.set()

    if webhook_task is not None:
        webhook_task.add_done_callback(
            make_webhook_death_handler(stop_event, logger)
        )

    if os.name == "nt":
        # On Windows, signal.signal under asyncio is effectively a no-op (the
        # ProactorEventLoop does not pump Python-level signal handlers from
        # under `await event.wait()`). Clean shutdown on Windows is driven
        # exclusively by the STOP_SENTINEL file. Ctrl-C from a foreground
        # console will hard-kill this process; the `try/finally` in
        # cmd_daemon below cleans up the PID file if that happens.

        # Poll the sentinel file every second.
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
        # M-1: use get_running_loop() — get_event_loop() is deprecated inside
        # async functions and raises a DeprecationWarning in Python 3.10+.
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, _request_stop)

    try:
        await stop_event.wait()
    finally:
        if webhook_server is not None:
            webhook_server.should_exit = True
            if webhook_task is not None:
                try:
                    await asyncio.wait_for(webhook_task, timeout=5)
                except asyncio.TimeoutError:
                    logger.warning("webhook-server did not shut down within 5s; cancelling")
                    webhook_task.cancel()
                except Exception as exc:  # noqa: BLE001 - reported, and cleanup still runs
                    # `wait_for` RE-RAISES whatever killed the task. Only
                    # TimeoutError was caught, so a webhook that died of a bad
                    # certificate took `scheduler.shutdown()`, the PID and
                    # start-time cleanup, and the `daemon-stop` line with it —
                    # the process exited on a traceback leaving a stale PID
                    # file that makes the next `status` report RUNNING.
                    logger.error("webhook-server ended with %r; continuing shutdown", exc)
        _shutdown_and_clean(scheduler, logger)


def cmd_daemon(args) -> None:
    if is_daemon_alive():
        print("fireside-daemon: already running")
        sys.exit(1)
    logger = _setup_logging()
    try:
        asyncio.run(_run_daemon(logger))
    finally:
        # I-1: Belt-and-suspenders: if asyncio.run exits via Ctrl-C or any
        # unhandled exception the _run_daemon finally-block may not have run.
        # Ensure OUR PID file is gone so is_daemon_alive() is correct on the
        # next start. Ownership-checked for the same reason as
        # `_shutdown_and_clean`: this used to delete the file whoever wrote it,
        # so a loser of a start race erased the live winner's PID.
        _remove_own_pid_file(logger)


# ============================================================
# Subcommand: run <job>
# ============================================================

def cmd_run(args) -> None:
    load_env()
    fb = _load_fireside_bot()
    dispatcher = JobDispatcher(fb, _setup_logging())
    dispatcher.dispatch(args.job)


# ============================================================
# Subcommand: status
# ============================================================

def cmd_status(args) -> None:
    pid = live_daemon_pid()
    if pid is None:
        print("fireside-daemon: NOT RUNNING")
        return
    # I-3: Compute human-readable uptime from the wall-clock epoch stored at start.
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
    print(f"fireside-daemon: RUNNING pid={pid} uptime={uptime_str}")
    print(f"jobs registered: {', '.join(JOB_SPECS.keys())}")


# ============================================================
# Subcommand: stop
# ============================================================

def cmd_stop(args) -> None:
    pid = live_daemon_pid()
    if pid is None:
        print("fireside-daemon: NOT RUNNING")
        return
    if os.name == "nt":
        # On Windows, CTRL_BREAK_EVENT propagates to the entire console process
        # group and kills the caller too. Use a sentinel file instead: the daemon
        # polls STOP_SENTINEL every second and shuts down cleanly when it appears.
        RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
        STOP_SENTINEL.write_text(str(pid))
        # "requested", not "will exit": nothing here confirms the daemon
        # consumed the sentinel, and a wedged event loop never will.
        print(f"fireside-daemon: stop requested for pid={pid} via sentinel; "
              f"run `status` to confirm it exited")
    else:
        # The check-then-kill gap is real and cannot be closed without a lock:
        # if the daemon died uncleanly and the OS recycled its PID, this
        # targets a stranger. What CAN be handled is the ordinary race, where
        # the process is simply gone by now.
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            print(f"fireside-daemon: pid={pid} already gone")
            return
        except PermissionError:
            print(f"fireside-daemon: pid={pid} is not ours to signal", file=sys.stderr)
            sys.exit(1)
        print(f"fireside-daemon: SIGTERM sent to pid={pid}")


# ============================================================
# Entry point
# ============================================================

def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("daemon", help="Run the scheduler forever")
    runp = sub.add_parser("run", help="Execute one job out-of-band")
    runp.add_argument("job", choices=list(JOB_SPECS.keys()))
    sub.add_parser("status", help="Show PID and registered jobs")
    sub.add_parser("stop", help="Signal a running daemon to shut down")
    args = parser.parse_args()
    {"daemon": cmd_daemon, "run": cmd_run, "status": cmd_status, "stop": cmd_stop}[args.cmd](args)


if __name__ == "__main__":
    main()
