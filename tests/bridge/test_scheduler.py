import time
from scripts.bridge_daemon.scheduler import build_scheduler

def test_scheduler_runs_job():
    cfg = {"refresh": {"inflight": 1}}
    hits = []
    sched = build_scheduler(cfg, {"inflight": lambda: hits.append("x")})
    sched.start()
    try:
        # The interval job first-fires at ~t=1s. Poll until it does rather than
        # sleeping a fixed window, so thread starvation under full-suite load
        # delays the tick instead of failing the test. Generous 10s ceiling.
        deadline = time.monotonic() + 10
        while not hits and time.monotonic() < deadline:
            time.sleep(0.02)
    finally:
        sched.shutdown(wait=False)
    assert len(hits) >= 1
