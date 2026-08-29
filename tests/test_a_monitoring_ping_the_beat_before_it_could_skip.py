"""Two fireside paths that a helper could silently disable.

Both defects are in `scripts/fireside-bot-daemon.py`.

1. `JobDispatcher.dispatch` piggybacks the R14 liveness beat on the `heartbeat`
   job. The call sat OUTSIDE the `try:` that wraps `fn(...)`, which is the
   whole reason the dispatcher exists: every other failure logs `job-fail` and
   the scheduler carries on. An exception out of `beat` skipped
   `cmd_heartbeat`, the job whose entire purpose is pinging FIRESIDE_HC_POLL so
   the fireside-poll healthchecks.io check stays green in webhook mode. It
   logged no `job-fail heartbeat` either, leaving only an unhandled APScheduler
   job-error. Telemetry about the daemon must never be able to stop the
   daemon's work, least of all on the one job whose skipped run has monitoring
   consequences.

2. `cmd_run` dispatched without `fb.ensure_state_dir()`. The daemon path calls
   it under a comment stating the self-heal happens "before any job runs" and
   naming the consequence when it does not: "without it every DM is rejected as
   outsider". `run <job>` is the documented smoke-test and backfill path, so on
   a host whose `fireside-state/tribe-roster.json` was gone the two paths
   behaved differently on the same tree.

Nothing here starts a daemon, binds a port, touches `.fireside/`, or reaches
Telegram: the bot, the logger and the beat are all doubles.

Run: python3 -m pytest
tests/test_a_monitoring_ping_the_beat_before_it_could_skip.py
"""
import importlib.util
import sys
import types
from argparse import Namespace
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Every job name the dispatcher maps, as cmd_* attribute names. Kept here so
# the fake bot is a faithful stand-in and a renamed job shows up as a KeyError
# in construction rather than a quietly narrower test.
JOB_ATTRS = (
    "cmd_poll", "cmd_heartbeat", "cmd_health_check", "cmd_speaker_dms",
    "cmd_helmsman_brief", "cmd_sunday_preview", "cmd_weekly_discrepancy_report",
    "cmd_email_backup", "cmd_dayof_reminders", "cmd_unpin_weekly",
    "cmd_topic_nudge", "cmd_topic_digest", "cmd_cycle_end_invite",
    "cmd_cycle_rollover",
)


@pytest.fixture(scope="module")
def fbd():
    path = Path(__file__).resolve().parent.parent / "scripts" / "fireside-bot-daemon.py"
    spec = importlib.util.spec_from_file_location("fireside_bot_daemon_beatguard", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


class _RecordingLogger:
    def __init__(self):
        self.lines = []

    def info(self, fmt, *args):
        self.lines.append(("info", fmt % args if args else fmt))

    def error(self, fmt, *args):
        self.lines.append(("error", fmt % args if args else fmt))

    def exception(self, fmt, *args):
        self.lines.append(("exception", fmt % args if args else fmt))


def _fake_bot(calls):
    bot = types.SimpleNamespace()
    for attr in JOB_ATTRS:
        setattr(bot, attr, (lambda a: (lambda ns: calls.append(a)))(attr))
    bot.ensure_state_dir = lambda: calls.append("ensure_state_dir")
    return bot


def test_the_fake_bot_covers_every_job_the_dispatcher_maps(fbd):
    """Guard the guard: an empty or partial job map would make this vacuous."""
    calls = []
    dispatcher = fbd.JobDispatcher(_fake_bot(calls), _RecordingLogger())
    assert dispatcher._fn_map, "job map is empty; every assertion below is vacuous"
    assert set(JOB_ATTRS) == {"cmd_" + n.replace("-", "_") for n in dispatcher._fn_map}


def test_a_beat_that_raises_does_not_skip_the_healthchecks_ping(fbd, monkeypatch):
    calls = []
    logger = _RecordingLogger()
    monkeypatch.setattr(fbd, "daemon_heartbeat", types.SimpleNamespace(
        beat=lambda name: (_ for _ in ()).throw(RuntimeError("beat blew up"))))

    fbd.JobDispatcher(_fake_bot(calls), logger).dispatch("heartbeat")

    # The job ran. Before the fix the exception escaped `dispatch` entirely and
    # cmd_heartbeat was never reached.
    assert "cmd_heartbeat" in calls
    assert ("info", "job-ok heartbeat") in logger.lines
    # And the beat failure is reported, not swallowed into a clean-looking run.
    assert any(level == "exception" and "beat-fail" in line
               for level, line in logger.lines), logger.lines


def test_a_beat_that_raises_does_not_escape_dispatch(fbd, monkeypatch):
    monkeypatch.setattr(fbd, "daemon_heartbeat", types.SimpleNamespace(
        beat=lambda name: (_ for _ in ()).throw(OSError("disk full"))))

    # No pytest.raises: returning normally IS the assertion the scheduler needs.
    fbd.JobDispatcher(_fake_bot([]), _RecordingLogger()).dispatch("heartbeat")


def test_a_healthy_beat_still_fires_on_the_heartbeat_job(fbd, monkeypatch):
    beaten = []
    monkeypatch.setattr(fbd, "daemon_heartbeat",
                        types.SimpleNamespace(beat=beaten.append))
    calls = []

    fbd.JobDispatcher(_fake_bot(calls), _RecordingLogger()).dispatch("heartbeat")

    assert beaten == ["fireside"]
    assert "cmd_heartbeat" in calls


def test_no_other_job_beats(fbd, monkeypatch):
    """The piggyback is scoped to `heartbeat`; the guard must not widen it."""
    beaten = []
    monkeypatch.setattr(fbd, "daemon_heartbeat",
                        types.SimpleNamespace(beat=beaten.append))
    calls = []

    fbd.JobDispatcher(_fake_bot(calls), _RecordingLogger()).dispatch("speaker-dms")

    assert beaten == []
    assert "cmd_speaker_dms" in calls


def test_run_self_heals_the_state_dir_before_dispatching(fbd, monkeypatch):
    calls = []
    bot = _fake_bot(calls)
    monkeypatch.setattr(fbd, "load_env", lambda: None)
    monkeypatch.setattr(fbd, "_load_fireside_bot", lambda: bot)
    monkeypatch.setattr(fbd, "_setup_logging", _RecordingLogger)
    monkeypatch.setattr(fbd, "daemon_heartbeat",
                        types.SimpleNamespace(beat=lambda name: None))

    fbd.cmd_run(Namespace(job="speaker-dms"))

    assert calls == ["ensure_state_dir", "cmd_speaker_dms"], calls
