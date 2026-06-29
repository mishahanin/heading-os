"""APScheduler boot. Registers refresh jobs based on config cadences."""
from typing import Callable
from apscheduler.schedulers.background import BackgroundScheduler

def build_scheduler(cfg: dict, jobs: dict[str, Callable]) -> BackgroundScheduler:
    sched = BackgroundScheduler()
    for component, fn in jobs.items():
        interval = cfg.get("refresh", {}).get(component, cfg.get("refresh", {}).get("default", 60))
        # misfire_grace_time=None: never skip a tick due to lateness. WSL 9P
        # latency on /mnt/c causes the scheduler's own ticks to be delayed
        # 3-7s every minute, which exceeds APScheduler's 1s default grace
        # and silently drops every refresh. coalesce=True merges the queued
        # misses into a single run, max_instances=1 prevents overlap.
        sched.add_job(fn, "interval", seconds=interval, id=f"refresh_{component}",
                      max_instances=1, coalesce=True, misfire_grace_time=None)
    return sched
