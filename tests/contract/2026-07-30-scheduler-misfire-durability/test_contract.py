"""Frozen contract: an APScheduler job must not be discarded for being late.

Spec: docs/superpowers/specs/2026-07-30-scheduler-misfire-durability-design.md
Plan: docs/superpowers/plans/2026-07-30-scheduler-misfire-durability.md

The defect this contract closes, measured on this machine over the 24 hours to
2026-07-30: APScheduler's ``misfire_grace_time`` defaults to 1 second, so a job
whose due moment slips past that is DISCARDED rather than run late. A 1-minute
heartbeat lost 1059 of 1440 runs and a 2-hour Exchange sync ran twice instead of
twelve times, while systemd reported ``active running`` throughout.

Every behavioural test here drives a REAL BackgroundScheduler and asserts on the
scheduler's own events, so it measures APScheduler rather than restating our own
constant back to us. Lateness is produced by handing a job a ``next_run_time``
already in the past, never by sleeping, so the tests are deterministic and fast.

The tree test carries its own AST walk rather than calling the audit helper the
implementation will ship. That is deliberate: a contract that called the
implementation's own audit function would pass vacuously if that function were
broken into always answering "no objection".

Authoring rule observed throughout: the code under test is imported INSIDE each
test body, because ``scripts/utils/scheduler_defaults.py`` does not exist yet and
a module-scope import would stop this file collecting.
"""
import ast
import queue
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from apscheduler.events import EVENT_JOB_EXECUTED, EVENT_JOB_MISSED
from apscheduler.schedulers.background import BackgroundScheduler

ENGINE = Path(__file__).resolve().parents[3]

WAIT_S = 10          # generous; each helper returns as soon as its event arrives
LATE_BY_S = 1800     # 30 minutes overdue, far past APScheduler's 1 second default


# ============================================================
# Helpers: real scheduler, deterministic lateness
# ============================================================

def _first_event(job_defaults, *, late_by=LATE_BY_S, **job_kw):
    """Start a scheduler holding one already-due job; return its first event.

    The event's ``.code`` is EVENT_JOB_EXECUTED when the job ran, and
    EVENT_JOB_MISSED when APScheduler discarded it for lateness.
    """
    seen = queue.Queue()
    sched = (BackgroundScheduler(job_defaults=job_defaults) if job_defaults
             else BackgroundScheduler())
    sched.add_listener(seen.put, EVENT_JOB_EXECUTED | EVENT_JOB_MISSED)
    sched.add_job(
        lambda: None, "interval", hours=1, id="probe",
        next_run_time=datetime.now(timezone.utc) - timedelta(seconds=late_by),
        **job_kw,
    )
    sched.start()
    try:
        return seen.get(timeout=WAIT_S)
    finally:
        sched.shutdown(wait=False)


def _executions(job_defaults, *, late_by, interval_s, window_s=2.5, **job_kw):
    """How many times a job overdue by `late_by` runs inside `window_s`.

    Bounded by wall clock rather than by "wait until the queue falls quiet": an
    interval job fires forever, so a quiet-queue loop would never return. The
    backlog fires immediately at start(), and `interval_s` is chosen well beyond
    `window_s` so the job's NEXT scheduled run cannot contaminate the count.
    """
    seen = queue.Queue()
    sched = (BackgroundScheduler(job_defaults=job_defaults) if job_defaults
             else BackgroundScheduler())
    sched.add_listener(seen.put, EVENT_JOB_EXECUTED)
    sched.add_job(
        lambda: None, "interval", seconds=interval_s, id="probe",
        next_run_time=datetime.now(timezone.utc) - timedelta(seconds=late_by),
        **job_kw,
    )
    sched.start()
    deadline = time.monotonic() + window_s
    count = 0
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return count
            try:
                seen.get(timeout=remaining)
                count += 1
            except queue.Empty:
                return count
    finally:
        sched.shutdown(wait=False)


# ============================================================
# The defect itself, pinned so it cannot quietly return
# ============================================================

def test_the_library_default_discards_a_late_job():
    """Not a test of our code: a test of the environment our code lives in. If
    APScheduler ever changes this default, the reason for this whole slice has
    changed and someone should find out from a test rather than from a stale
    docstring."""
    assert _first_event(None).code == EVENT_JOB_MISSED


# ============================================================
# SC-1: a late job runs
# ============================================================

def test_a_job_overdue_by_half_an_hour_still_runs():
    from scripts.utils.scheduler_defaults import JOB_DEFAULTS

    assert _first_event(JOB_DEFAULTS).code == EVENT_JOB_EXECUTED


def test_the_constant_is_the_only_variable_that_changed_the_outcome():
    """Same job, same lateness, same scheduler class. Only the defaults differ."""
    from scripts.utils.scheduler_defaults import JOB_DEFAULTS

    assert _first_event(None).code == EVENT_JOB_MISSED
    assert _first_event(JOB_DEFAULTS).code == EVENT_JOB_EXECUTED


# ============================================================
# SC-2: a call site keeps the final say, in both directions
# ============================================================

def test_an_explicit_grace_overrides_the_default_in_both_directions():
    """Asserted as a set, because each half alone proves nothing. Passing
    grace=1 makes a job miss whatever the scheduler's defaults are, so the
    downward half is only a claim about overriding when paired with the run it
    contradicts. The probe measured the unpaired version as asserting nothing.
    """
    from scripts.utils.scheduler_defaults import JOB_DEFAULTS

    # the default is what ran it...
    assert _first_event(JOB_DEFAULTS).code == EVENT_JOB_EXECUTED
    # ...and a call-site override is what stops it
    assert _first_event(JOB_DEFAULTS, misfire_grace_time=1).code == EVENT_JOB_MISSED
    # upward, against a scheduler carrying no defaults at all
    assert _first_event(None, misfire_grace_time=None).code == EVENT_JOB_EXECUTED
    assert _first_event(None).code == EVENT_JOB_MISSED


# ============================================================
# SC-3: a backlog collapses into one run
# ============================================================

def test_a_backlog_runs_once_not_once_per_missed_interval():
    """A job 1000 seconds overdue on a 600 second interval has two due run times
    waiting, the most recent of them 400 seconds stale. Running once is what
    makes grace=None safe rather than explosive.

    The numbers are chosen, not arbitrary. Measured on 2026-07-30, this same
    test at 30 seconds overdue on a 10 second interval returned 1 under BOTH
    grace=None and grace=1, because coalescing keeps only the LAST due time and
    at a 10 second interval that time is recent enough to clear a 1 second
    grace. The probe correctly reported that version as asserting nothing. At
    600 seconds the surviving due time is 400 seconds stale, so grace=1 yields
    0 runs and the count becomes a claim about the constant:

        late_by=  30 interval= 10  ->  safe=1  grace1=1   (proves nothing)
        late_by=1000 interval=600  ->  safe=1  grace1=0   (proves the point)

    The coalesce=False measurement sits inside this test rather than beside it,
    so the number 1 is a contrast rather than a coincidence.

    Stated honestly: APScheduler's OWN default coalesce is already True
    (schedulers/base.py:910-915), so merely omitting the key does NOT produce
    the burst; it takes an explicit coalesce=False. The key is in JOB_DEFAULTS
    for legibility next to grace=None, not because the library default is unsafe.
    """
    from scripts.utils.scheduler_defaults import JOB_DEFAULTS

    assert _executions(JOB_DEFAULTS, late_by=1000, interval_s=600) == 1
    assert _executions({"misfire_grace_time": None, "coalesce": False},
                       late_by=1000, interval_s=600) > 1


# ============================================================
# SC-6: inheritance works through real production code
# ============================================================

def test_a_job_added_with_no_options_of_its_own_still_gets_the_safe_values():
    """The exact shape of the defect, and the test the probe forced.

    An earlier draft asserted that build_scheduler's OWN jobs come out safe.
    That version passed with no implementation, because build_scheduler already
    sets the option at its call site. It measured the one scheduler in the tree
    that was already correct and therefore proved nothing.

    The real gap is the job added LATER by someone else:
    scripts/bridge-daemon.py registers five jobs on this same scheduler object
    and passes no misfire option, so today they inherit APScheduler's 1 second
    grace while the comment two lines above them says the opposite. Fixing the
    CONSTRUCTOR is what closes that, and this test adds a bare job exactly the
    way bridge-daemon.py does, then asks what it inherited.
    """
    from scripts.bridge_daemon.scheduler import build_scheduler

    sched = build_scheduler({"refresh": {"a": 30}}, {"a": lambda: None})
    sched.add_job(lambda: None, "interval", hours=6, id="added_later")
    sched.start(paused=True)     # start() flushes pending jobs, applying defaults
    try:
        added = sched.get_job("added_later")
        assert added.misfire_grace_time is None
        assert added.coalesce is True
        assert added.max_instances == 1
    finally:
        sched.shutdown(wait=False)


# ============================================================
# SC-4 and SC-5: the invariant holds across the tree
# ============================================================

SCHEDULER_NAMES = frozenset({
    "AsyncIOScheduler", "BackgroundScheduler", "BlockingScheduler",
    "GeventScheduler", "QtScheduler", "TornadoScheduler", "TwistedScheduler",
})


def _unguarded(src: str, filename: str):
    """(lineno, why) for every scheduler construction in `src` that is unsafe."""
    for node in ast.walk(ast.parse(src, filename=filename)):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", None)
        if name not in SCHEDULER_NAMES:
            continue
        keywords = {kw.arg: kw.value for kw in node.keywords}
        if "job_defaults" not in keywords:
            yield node.lineno, "built without job_defaults"
            continue
        value = keywords["job_defaults"]
        if isinstance(value, ast.Dict):
            keys = {k.value for k in value.keys if isinstance(k, ast.Constant)}
            if "misfire_grace_time" not in keys:
                yield node.lineno, "job_defaults carries no misfire_grace_time"


def test_no_scheduler_under_scripts_is_built_without_safe_defaults():
    """Measured before implementation: four violations, at
    bridge_daemon/scheduler.py:6, eval-drift-daemon.py:811,
    fireside-bot-daemon.py:227, sync-exchange-daemon.py:184."""
    failures = []
    for path in sorted((ENGINE / "scripts").rglob("*.py")):
        src = path.read_text(encoding="utf-8")
        if not any(name in src for name in SCHEDULER_NAMES):
            continue
        rel = path.relative_to(ENGINE)
        for line, why in _unguarded(src, str(rel)):
            failures.append(f"{rel}:{line}: {why}")

    assert not failures, (
        "APScheduler scheduler(s) built without safe job defaults:\n  "
        + "\n  ".join(failures))


def test_this_walk_actually_reaches_the_daemons():
    """A tree assertion that silently matched nothing would pass forever."""
    found = 0
    for path in sorted((ENGINE / "scripts").rglob("*.py")):
        src = path.read_text(encoding="utf-8")
        if not any(name in src for name in SCHEDULER_NAMES):
            continue
        for node in ast.walk(ast.parse(src, filename=path.name)):
            if isinstance(node, ast.Call):
                fn = node.func
                name = (fn.attr if isinstance(fn, ast.Attribute)
                        else getattr(fn, "id", None))
                if name in SCHEDULER_NAMES:
                    found += 1

    assert found >= 4, f"expected at least 4 scheduler constructions, saw {found}"


def test_the_ratchet_ships_as_a_maintained_test_not_only_as_this_contract():
    """This contract is deleted at retirement. The property must survive it, so
    the guard has to exist as an ordinary suite member that walks the tree."""
    guard = ENGINE / "tests" / "test_scheduler_misfire_guard.py"
    assert guard.exists(), f"missing ratchet: {guard.relative_to(ENGINE)}"

    src = guard.read_text(encoding="utf-8")
    names = [n.name for n in ast.walk(ast.parse(src))
             if isinstance(n, ast.FunctionDef) and n.name.startswith("test_")]
    assert any("tree" in n for n in names), (
        f"the ratchet defines no tree-wide test; found {names}")


# ============================================================
# SC-8: the reason lives where the next author will look
# ============================================================

def test_the_reason_is_recorded_at_the_definition():
    """The correct value already existed in scripts/bridge_daemon/scheduler.py
    and did not travel, because the reasoning sat in a comment nobody else read.
    A constant without its reason invites the same failure again."""
    import scripts.utils.scheduler_defaults as mod

    doc = mod.__doc__ or ""
    assert "misfire_grace_time" in doc
    assert "coalesce" in doc
    assert "job_defaults" in doc
