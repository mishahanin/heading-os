"""APScheduler boot. Registers refresh jobs based on config cadences."""
import logging
import math
from typing import Callable

from apscheduler.schedulers.background import BackgroundScheduler

from scripts.utils.scheduler_defaults import JOB_DEFAULTS

logger = logging.getLogger(__name__)


def build_scheduler(cfg: dict, jobs: dict[str, Callable]) -> BackgroundScheduler:
    # job_defaults, not per-job arguments. scripts/bridge-daemon.py adds five
    # more jobs to this same scheduler object, and while the safe values lived
    # only on the add_job call below, those five silently kept APScheduler's 1
    # second grace. The reasoning now lives with the constant.
    sched = BackgroundScheduler(job_defaults=JOB_DEFAULTS)
    refresh_cfg = cfg.get("refresh", {}) or {}
    default = _coerce_interval(refresh_cfg.get("default", 60), "default", 60)
    for component, fn in jobs.items():
        interval = _coerce_interval(refresh_cfg.get(component, default), component, default)
        sched.add_job(fn, "interval", seconds=interval, id=f"refresh_{component}")
    return sched


MIN_INTERVAL_S = 5


def _coerce_interval(value, component: str, fallback: float) -> float:
    """A refresh interval from config, or `fallback` when it is unusable.

    Config values used to flow straight into `seconds=`, so a single typo in
    `.daemon-state/config.yaml` -- `pulse: "60"` as a string, or `pulse: 0` --
    made `add_job` raise during boot and the daemon never started. A refresh
    cadence is not worth refusing to run over, and a silent substitution is not
    worth making either, so the bad value is named in the log.
    """
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        logger.warning("refresh interval for %r is %r, which is not a number; "
                       "using %s s", component, value, fallback)
        return fallback
    if not math.isfinite(seconds):
        # `float()` accepts nan and inf, and YAML parses `.nan` / `.inf` into
        # them, so a config typo reached `add_job(seconds=nan)` and raised
        # during boot -- the exact failure this function exists to eliminate.
        # NaN also slipped the floor check below, because every comparison
        # against NaN is False.
        logger.warning("refresh interval for %r is %r, which is not a finite "
                       "number; using %s s", component, value, fallback)
        return fallback
    if seconds < MIN_INTERVAL_S:
        logger.warning("refresh interval for %r is %s s, below the %s s floor; "
                       "using the floor", component, seconds, MIN_INTERVAL_S)
        return MIN_INTERVAL_S
    return seconds
