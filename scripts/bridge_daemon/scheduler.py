"""APScheduler boot. Registers refresh jobs based on config cadences."""
from typing import Callable

from apscheduler.schedulers.background import BackgroundScheduler

from scripts.utils.scheduler_defaults import JOB_DEFAULTS


def build_scheduler(cfg: dict, jobs: dict[str, Callable]) -> BackgroundScheduler:
    # job_defaults, not per-job arguments. scripts/bridge-daemon.py adds five
    # more jobs to this same scheduler object, and while the safe values lived
    # only on the add_job call below, those five silently kept APScheduler's 1
    # second grace. The reasoning now lives with the constant.
    sched = BackgroundScheduler(job_defaults=JOB_DEFAULTS)
    for component, fn in jobs.items():
        interval = cfg.get("refresh", {}).get(component, cfg.get("refresh", {}).get("default", 60))
        sched.add_job(fn, "interval", seconds=interval, id=f"refresh_{component}")
    return sched
