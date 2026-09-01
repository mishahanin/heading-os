"""Logger levels the bridge daemon installs at boot.

Written 2026-08-20 after an audit of .daemon-state/bridge.log: 13,780 of the
16,277 retained lines (84.7%) were APScheduler job-lifecycle INFO ("Running
job ...", "executed successfully"). Success was logged as loudly as failure,
so a job that STOPPED firing was indistinguishable from one firing every
minute. These tests pin the two halves of the fix: apscheduler is quieted to
WARNING, and nothing else is.
"""
import importlib.util
import logging
import sys
from pathlib import Path

import pytest

_ENTRY_PATH = Path(__file__).resolve().parents[2] / "scripts" / "bridge-daemon.py"
_UNIT_TEMPLATE = (
    Path(__file__).resolve().parents[2]
    / "scripts" / "templates" / "systemd" / "bridge-daemon.service"
)


def _load_entry_module():
    # scripts/bridge-daemon.py has a hyphen, illegal in a module name; load by
    # path. Import is side-effect free - the file guards on __name__.
    spec = importlib.util.spec_from_file_location("bridge_daemon_entry", _ENTRY_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules["bridge_daemon_entry"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def entry_module():
    return _load_entry_module()


@pytest.fixture
def configured_logging(entry_module, tmp_path):
    """Run the daemon's logging setup against a tmp log file, then restore.

    _configure_logging() clears the root handlers and sets levels process-wide,
    so every mutation it makes is undone here - otherwise pytest's own capture
    handlers are gone for the rest of the session.
    """
    from scripts.bridge_daemon.error_tracker import _reset_for_tests

    root = logging.getLogger()
    saved_handlers = list(root.handlers)
    saved_root_level = root.level
    saved_aps_level = logging.getLogger("apscheduler").level
    saved_factory = logging.getLogRecordFactory()
    # install_handler() is a process-wide idempotent singleton; another test in
    # the same session may already have claimed it, which would make this a
    # no-op and hide a real regression.
    _reset_for_tests()
    try:
        yield entry_module._configure_logging(tmp_path / "bridge.log")
    finally:
        _reset_for_tests()
        for h in list(root.handlers):
            root.removeHandler(h)
        for h in saved_handlers:
            root.addHandler(h)
        root.setLevel(saved_root_level)
        logging.getLogger("apscheduler").setLevel(saved_aps_level)
        logging.setLogRecordFactory(saved_factory)


def test_apscheduler_logger_quieted_to_warning(configured_logging):
    """The job-lifecycle chatter is gated off at the apscheduler logger."""
    aps = logging.getLogger("apscheduler")
    assert aps.level == logging.WARNING
    assert not aps.isEnabledFor(logging.INFO)
    # The child loggers that actually emit the noise inherit the level.
    assert not logging.getLogger("apscheduler.executors.default").isEnabledFor(logging.INFO)


def test_apscheduler_failures_still_reach_the_log(configured_logging):
    """A genuine scheduler failure is WARNING+, so quieting INFO keeps it.

    This is the whole point of setting WARNING rather than removing the
    handler: install_error_tracker feeds heartbeat.json from WARNING+ records
    on root, and apscheduler propagates to root.
    """
    aps = logging.getLogger("apscheduler.executors.default")
    assert aps.isEnabledFor(logging.WARNING)
    assert aps.isEnabledFor(logging.ERROR)
    assert aps.propagate


def test_daemon_own_loggers_unaffected(configured_logging):
    """The daemon's own INFO lines must survive.

    pulse/mail/config call logging.info() on the root logger directly
    ("bridge.pulse: refreshed in 1439ms"); sources/search.py and
    sources/tribe.py use logging.getLogger(__name__). Neither descends from
    'apscheduler', so both must still be enabled for INFO.
    """
    assert logging.getLogger().isEnabledFor(logging.INFO)
    assert logging.getLogger("scripts.bridge_daemon.sources.search").isEnabledFor(logging.INFO)
    assert logging.getLogger("scripts.bridge_daemon.sources.tribe").isEnabledFor(logging.INFO)


def test_error_tracker_attached_to_root(configured_logging, entry_module):
    """_configure_logging still installs the WARNING+ heartbeat tracker."""
    from scripts.bridge_daemon.error_tracker import _TrackerHandler

    root = logging.getLogger()
    assert any(isinstance(h, _TrackerHandler) for h in root.handlers)


def test_unit_template_caps_the_leak():
    """The systemd template recycles the daemon every 6 hours.

    Measured 2026-08-20: RSS grew 1,198,672 kB -> 1,909,224 kB in 72 minutes
    with VmHWM == VmRSS, so the process is capped rather than left to OOM.

    Parsed, not grepped. `"RuntimeMaxSec=21600" in text` is a SUBSTRING test,
    and `RuntimeMaxSec=216000` contains it: one extra zero turns the 6-hour
    recycle into a 60-hour one, which at the measured ~600 MB/hour is no cap
    at all, and the assertion held. Measured 2026-08-31 with exactly that
    typo:

        owner tests/bridge/test_daemon_logging.py: 6 passed in 0.97s
        tests/bridge                            : 1312 passed, 1 skipped
        VERDICT: SURVIVED

    The sibling test directly below already parsed its directive into a value
    for the same class of reason; this one had not caught up. Reading the
    seconds as an integer also lets the bound be asserted as a RANGE, which
    is the honest shape: the exact number is a judgement, but "long enough to
    be useless" and "so short the daemon thrashes" are both defects.
    """
    text = _UNIT_TEMPLATE.read_text()
    values = [
        line.split("=", 1)[1].strip()
        for line in text.splitlines()
        if line.strip().startswith("RuntimeMaxSec=")
    ]
    assert len(values) == 1, f"expected exactly one RuntimeMaxSec= line, got {values}"
    seconds = int(values[0])
    assert seconds == 21600, f"the 6-hour recycle moved to {seconds}s"
    # And the range, so a future deliberate change still has to stay sane.
    assert 1800 <= seconds <= 43200, (
        f"{seconds}s is outside 30 minutes to 12 hours; below that the daemon "
        f"thrashes, above it the ~600 MB/hour leak is uncapped in practice")


def test_the_recycle_directive_is_not_commented_out():
    """A `#` in front of it leaves the literal in the file and the cap gone.

    `startswith` on the STRIPPED line is what makes the test above see this,
    so this pins the property rather than trusting the implementation of the
    other assertion. systemd ignores a commented directive silently, so there
    would be no boot-time signal at all.
    """
    text = _UNIT_TEMPLATE.read_text()
    live = [ln for ln in text.splitlines()
            if "RuntimeMaxSec" in ln and not ln.lstrip().startswith("#")]
    assert len(live) == 1, f"RuntimeMaxSec is commented out or duplicated: {live}"


def test_unit_template_restarts_after_the_recycle():
    """RuntimeMaxSec is useless unless the unit comes back up.

    systemd ends a RuntimeMaxSec kill with result 'timeout', which both
    'always' and 'on-failure' restart - measured 2026-08-20 on this machine
    with a transient probe, including against a process that traps SIGTERM and
    exits 0 (NRestarts=2 in 11 s). So this pins the property that matters, not
    one spelling of it: any Restart= policy that covers a timeout result.
    'no', 'on-success', 'on-abort' and 'on-watchdog' would silently turn the
    6-hour recycle into a 6-hour uptime limit.
    """
    text = _UNIT_TEMPLATE.read_text()
    policies = [
        line.split("=", 1)[1].strip()
        for line in text.splitlines()
        if line.startswith("Restart=")
    ]
    assert len(policies) == 1, f"expected exactly one Restart= line, got {policies}"
    assert policies[0] in {"always", "on-failure", "on-abnormal"}
