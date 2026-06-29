#!/usr/bin/env python3
"""Sync-Exchange daemon — in-process scheduler for Exchange calendar + email sync.

Owns ONE responsibility: run `python scripts/sync-exchange.py --calendar --emails`
every 2 hours, plus once at boot. Does not share code with the Fireside daemon;
duplication is intentional — one daemon per concern.

Subcommands:
    daemon  : run forever (the scheduler). Default for /prime auto-start.
    run J   : execute job J once, out-of-band (smoke test or backfill).
    status  : print PID, uptime, next scheduled run for each job.
    stop    : signal a running daemon to shut down cleanly.

PID file:  .sync-exchange/daemon.pid
Log file:  .sync-exchange/daemon.log  (rotated by RotatingFileHandler, 1 MB, keep 3)
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal
import subprocess
import sys
import time
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

WORKSPACE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WORKSPACE))

from scripts.utils import daemon_heartbeat  # noqa: E402
from scripts.utils import trace  # noqa: E402
from scripts.utils.trace_filter import install_log_factory  # noqa: E402
from scripts.utils.workspace import get_default_tz, get_default_tz_name, load_env  # noqa: E402

# ============================================================
# Configuration
# ============================================================

RUNTIME_DIR = WORKSPACE / ".sync-exchange"
PID_FILE = RUNTIME_DIR / "daemon.pid"
LOG_FILE = RUNTIME_DIR / "daemon.log"
STARTED_AT_FILE = RUNTIME_DIR / "started_at"
STOP_SENTINEL = RUNTIME_DIR / "stop"

# Single job spec - this daemon owns one task.
# next_run_time=NOW makes the first sync fire immediately on scheduler.start();
# IntervalTrigger then takes over for the 2h cadence.
JOB_SPECS: dict[str, dict] = {
    "sync-exchange": {
        "interval_hours": 2,
        "fire_at_start": True,
        "subprocess": [
            sys.executable,
            str(WORKSPACE / "scripts" / "sync-exchange.py"),
            "--calendar",
            "--emails",
        ],
        "timeout_s": 600,
        "critical": False,
    },
}


# ============================================================
# Logging setup
# ============================================================

def _setup_logging() -> logging.Logger:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    # R12: mint trace ID + install record factory before any handler.
    trace.mint()
    install_log_factory()
    logger = logging.getLogger("sync-exchange-daemon")
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


# ============================================================
# PID file management
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
    """Cross-platform: is the given PID alive?"""
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
    else:
        try:
            os.kill(pid, 0)
            return True
        except (ProcessLookupError, PermissionError):
            return False


# ============================================================
# Job dispatcher
# ============================================================

class JobDispatcher:
    """Runs the configured subprocess job with timeout + structured logging."""

    def __init__(self, logger: logging.Logger):
        self.logger = logger

    def dispatch(self, job_name: str) -> None:
        spec = JOB_SPECS.get(job_name)
        if spec is None:
            self.logger.error("dispatch: unknown job %s", job_name)
            return
        cmd = spec["subprocess"]
        timeout = spec.get("timeout_s", 600)
        self.logger.info("job-start %s", job_name)
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=str(WORKSPACE),
            )
            if result.returncode == 0:
                self.logger.info("job-ok %s (exit=0)", job_name)
            else:
                stderr_tail = (result.stderr or "")[-500:]
                self.logger.error(
                    "job-fail %s exit=%d stderr=%s",
                    job_name, result.returncode, stderr_tail.strip(),
                )
        except subprocess.TimeoutExpired:
            self.logger.error("job-timeout %s after %ds", job_name, timeout)
        except Exception:
            self.logger.exception("job-fail %s (exception)", job_name)


# ============================================================
# Subcommand: daemon
# ============================================================

async def _run_daemon(logger: logging.Logger) -> None:
    load_env()
    dispatcher = JobDispatcher(logger)

    scheduler = AsyncIOScheduler(timezone=get_default_tz())
    for name, spec in JOB_SPECS.items():
        trigger = IntervalTrigger(hours=spec["interval_hours"], timezone=get_default_tz())
        kwargs = dict(
            id=name,
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        if spec.get("fire_at_start"):
            # Force the first run to fire immediately when the scheduler starts.
            # IntervalTrigger by itself waits one full interval before firing.
            kwargs["next_run_time"] = datetime.now(get_default_tz())
        scheduler.add_job(dispatcher.dispatch, trigger, args=[name], **kwargs)

    # R14: dedicated 1-min liveness beat, decoupled from the 2-hour sync work
    # cadence so the watchdog can detect a crashed daemon within ~minutes
    # rather than ~hours. One file: .daemon-state/heartbeats/sync-exchange.json.
    scheduler.add_job(
        lambda: daemon_heartbeat.beat("sync-exchange"),
        IntervalTrigger(minutes=1, timezone=get_default_tz()),
        id="heartbeat",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        next_run_time=datetime.now(get_default_tz()),
    )

    # Remove any stale stop sentinel left by a previous abrupt shutdown.
    if STOP_SENTINEL.exists():
        try:
            STOP_SENTINEL.unlink()
        except OSError:
            pass

    # Atomic PID + start-time writes (write-to-tmp + os.replace).
    tmp_pid = PID_FILE.with_suffix(".tmp")
    tmp_pid.write_text(str(os.getpid()))
    os.replace(tmp_pid, PID_FILE)

    tmp_started = STARTED_AT_FILE.with_suffix(".tmp")
    tmp_started.write_text(str(int(time.time())))
    os.replace(tmp_started, STARTED_AT_FILE)

    logger.info("daemon-start pid=%d jobs=%d", os.getpid(), len(JOB_SPECS))
    scheduler.start()

    stop_event = asyncio.Event()

    def _request_stop(*_args):
        logger.info("signal received; shutting down")
        stop_event.set()

    if os.name == "nt":
        # Windows: clean shutdown driven by STOP_SENTINEL file (polled every 1s).
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
        if PID_FILE.exists():
            try:
                PID_FILE.unlink()
            except OSError:
                pass
        if STARTED_AT_FILE.exists():
            try:
                STARTED_AT_FILE.unlink()
            except OSError:
                pass
        logger.info("daemon-stop")


def cmd_daemon(args) -> None:
    if is_daemon_alive():
        print("sync-exchange-daemon: already running")
        sys.exit(1)
    logger = _setup_logging()
    try:
        asyncio.run(_run_daemon(logger))
    finally:
        if PID_FILE.exists():
            try:
                PID_FILE.unlink()
            except OSError:
                pass


# ============================================================
# Subcommand: run <job>
# ============================================================

def cmd_run(args) -> None:
    load_env()
    dispatcher = JobDispatcher(_setup_logging())
    dispatcher.dispatch(args.job)


# ============================================================
# Subcommand: status
# ============================================================

def cmd_status(args) -> None:
    if not is_daemon_alive():
        print("sync-exchange-daemon: NOT RUNNING")
        return
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
    print(f"sync-exchange-daemon: RUNNING pid={pid} uptime={uptime_str}")
    print(f"jobs registered: {', '.join(JOB_SPECS.keys())}")


# ============================================================
# Subcommand: stop
# ============================================================

def cmd_stop(args) -> None:
    if not is_daemon_alive():
        print("sync-exchange-daemon: NOT RUNNING")
        return
    pid = int(PID_FILE.read_text().strip())
    if os.name == "nt":
        RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
        STOP_SENTINEL.write_text(str(pid))
        print(f"sync-exchange-daemon: stop sentinel written for pid={pid} (daemon will exit within ~1s)")
    else:
        os.kill(pid, signal.SIGTERM)
        print(f"sync-exchange-daemon: SIGTERM sent to pid={pid}")


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
