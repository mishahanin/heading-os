"""The bridge scheduler: a job fires, and a bad cadence does not stop the boot.

`_coerce_interval` exists because of a boot failure its own docstring records:
"a single typo in `.daemon-state/config.yaml` -- `pulse: \"60\"` as a string, or
`pulse: 0` -- made `add_job` raise during boot and the daemon never started."
The function is unit-tested elsewhere. Its WIRING into `build_scheduler`, which
is the only place production calls it from, was not.

MEASURED 2026-08-31 in a clone under /tmp, bypassing the coercion while leaving
the function itself intact:

    interval = _coerce_interval(refresh_cfg.get(component, default),
                               component, default)
    ->
    interval = refresh_cfg.get(component, default)

    .venv/bin/python -m pytest tests/bridge/test_scheduler.py -q
    -> 1 passed
    .venv/bin/python -m pytest tests/bridge -q
    -> 1312 passed, 1 skipped

The mutation survived.
Confirmed at the only scope that settles it. The same mutation was carried into
a whole-suite run alongside five others:

    pytest tests -> 12 failed, 19810 passed, 14 skipped (0:35:43)

and not one of those twelve failures is attributable to this one. That check
matters here because three sibling findings in this shard did NOT survive it:
each looked naked against `tests/bridge` alone and was caught by a file
elsewhere in `tests/`. A directory-scoped mutation run proves a guard is
untested in that directory and nothing more.

Against the same clone, `build_scheduler` then gave:

    {'refresh': {'inflight': '60'}}  -> TypeError: unsupported type for
                                       timedelta seconds component: str
    {'refresh': {'inflight': 0}}     -> built, trigger=interval[0:00:01]
    {'refresh': {'inflight': nan}}   -> ValueError: cannot convert float NaN
                                       to integer

versus, with the coercion live:

    {'refresh': {'inflight': '60'}}  -> interval[0:01:00]
    {'refresh': {'inflight': 0}}     -> interval[0:00:05]
    {'refresh': {'inflight': nan}}   -> interval[0:01:00]

Two of the three are a daemon that does not boot; the third is a one-second
refresh loop. A single test on the happy path could not tell any of that apart.
Full-suite branch coverage the same day left `scheduler.py` at 91% with
`Missing 51-53`, the non-finite arm, which YAML reaches with a bare `.nan`.

The misfire question is settled elsewhere and deliberately not re-tested here:
mutating `JOB_DEFAULTS["misfire_grace_time"]` from None to 1 (APScheduler's
work-dropping default) was CAUGHT, by `tests/test_scheduler_defaults.py`, five
tests of it. `tests/test_scheduler_misfire_guard.py` catches the constant going
missing from the constructor. Duplicating either here would add a second copy
of a guard that already has a home.
"""
import math
import time

import pytest

from scripts.bridge_daemon.scheduler import MIN_INTERVAL_S, build_scheduler


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


def _interval_seconds(cfg: dict) -> float:
    """The interval `build_scheduler` actually registered, in seconds.

    Read off the trigger rather than off `_coerce_interval`, so the assertion
    is about what the scheduler will DO. Calling the coercer directly is what
    left the wiring untested: the function was right and unused.
    """
    sched = build_scheduler(cfg, {"inflight": lambda: None})
    try:
        jobs = sched.get_jobs()
        assert len(jobs) == 1, jobs
        return jobs[0].trigger.interval.total_seconds()
    finally:
        # Never started, so `shutdown()` would raise SchedulerNotRunningError.
        sched.remove_all_jobs()


@pytest.mark.parametrize("value,expected,why", [
    ("60", 60.0, "a quoted number in YAML is a string"),
    ("60.5", 60.5, "and so is a quoted float"),
    ("not-a-number", 60.0, "unparseable falls back to the default"),
    (None, 60.0, "an empty YAML value arrives as None"),
    ([], 60.0, "a list is not a cadence"),
    (float("nan"), 60.0, "YAML `.nan`; every comparison against it is False, "
                         "so it slipped the floor check before the "
                         "isfinite guard existed"),
    (float("inf"), 60.0, "YAML `.inf`"),
    (0, float(MIN_INTERVAL_S), "zero is floored, not accepted"),
    (-30, float(MIN_INTERVAL_S), "negative is floored too"),
    (0.5, float(MIN_INTERVAL_S), "below the floor is floored"),
    (900, 900.0, "a good value passes through untouched"),
])
def test_a_bad_refresh_cadence_never_reaches_add_job(value, expected, why):
    """One case per shape, each ON the line the coercion draws.

    Every one of these is a plausible hand edit of `.daemon-state/config.yaml`,
    and each used to be a different failure: a string and a NaN raised out of
    `add_job` so the daemon never started, and a zero registered a one-second
    refresh loop.
    """
    assert _interval_seconds({"refresh": {"inflight": value}}) == expected, why


def test_a_bad_default_is_coerced_too():
    """`refresh.default` feeds every component that has no entry of its own, so
    one typo there is not one broken job but all of them."""
    assert _interval_seconds({"refresh": {"default": "45"}}) == 45.0
    assert _interval_seconds({"refresh": {"default": float("nan")}}) == 60.0


def test_the_floor_is_a_real_floor_not_a_rounding():
    """Anchor. `MIN_INTERVAL_S` is imported rather than written as 5, so the
    parametrised cases above cannot pass by agreeing with a stale literal."""
    assert MIN_INTERVAL_S >= 1 and math.isfinite(MIN_INTERVAL_S)
    assert _interval_seconds({"refresh": {"inflight": MIN_INTERVAL_S}}) == float(MIN_INTERVAL_S)
    assert _interval_seconds({"refresh": {"inflight": MIN_INTERVAL_S - 1}}) == float(MIN_INTERVAL_S)


def test_every_job_handed_in_is_registered():
    """The loop's own contract, and the anchor under `_interval_seconds`, which
    asserts exactly one job because it hands in exactly one."""
    sched = build_scheduler({"refresh": {"inflight": 30, "mail": 90}},
                            {"inflight": lambda: None, "mail": lambda: None,
                             "pulse": lambda: None})
    try:
        by_id = {j.id: j.trigger.interval.total_seconds() for j in sched.get_jobs()}
        assert by_id == {"refresh_inflight": 30.0, "refresh_mail": 90.0,
                         "refresh_pulse": 60.0}
    finally:
        sched.remove_all_jobs()
