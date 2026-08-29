"""`setup.py --no-sentinel-schedule` recorded an install it had not performed.

Step 11 installs the 15-minute Sentinel comms-monitor schedule.
`--no-sentinel-schedule` skips the install, and the step then wrote
`install_sync` into `.sync/setup-state.json` anyway. Every later plain run reads
that key, prints `[skip] Scheduled tasks already installed (use
--reinstall-schedule to force)`, and installs nothing.

So one run with the flag turned it off FOREVER, behind a green line, and
recovery needed an operator who already knew to pass `--reinstall-schedule`.
The monitor that exists to notice a silent daemon was itself silently absent.

OPERATOR DECISION 2026-08-29: the flag is PER-RUN. It affects the run it is
typed on and writes no persistent opt-out. So a skipped install records nothing
and the next plain run installs the schedule.

Run: python3 -m pytest tests/test_a_setup_flag_that_recorded_a_step_it_had_skipped.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SETUP = pytest.importorskip("scripts.setup", reason="scripts/setup.py not importable")

IDENTITY = {"slug": "kestrel-holdings"}


@pytest.fixture()
def installs(monkeypatch):
    """Record every Sentinel schedule install without touching the OS."""
    calls: list[tuple] = []
    import scripts.utils.schedule as sched
    monkeypatch.setattr(sched, "install_sentinel_schedule",
                        lambda slug, root: calls.append((slug, root)))
    return calls


def test_a_skipped_install_records_nothing(installs):
    """THE case. The flag must not leave a mark that outlives its run."""
    state: dict = {"completed_steps": [], "started_at": None}

    ok = SETUP.step_install_sync(state, IDENTITY, install_sentinel=False)

    assert ok is True, "declining the schedule is not a failed step"
    assert installs == []
    assert not SETUP.is_done(state, "install_sync"), \
        "the step recorded an install it never performed"


def test_the_next_plain_run_installs_what_the_flag_skipped(installs):
    """The consequence, driven end to end: run with the flag, then without it."""
    state: dict = {"completed_steps": [], "started_at": None}

    SETUP.step_install_sync(state, IDENTITY, install_sentinel=False)
    SETUP.step_install_sync(state, IDENTITY)

    assert len(installs) == 1, "the second run skipped over the missing schedule"
    assert installs[0][0] == "kestrel-holdings"
    assert SETUP.is_done(state, "install_sync")


def test_a_real_install_is_still_recorded_once(installs):
    """The negative control. A step that recorded nothing at all would pass the
    two tests above and reinstall the schedule on every run."""
    state: dict = {"completed_steps": [], "started_at": None}

    SETUP.step_install_sync(state, IDENTITY)
    SETUP.step_install_sync(state, IDENTITY)

    assert len(installs) == 1
    assert SETUP.is_done(state, "install_sync")


def test_reinstall_still_forces_a_second_install(installs):
    """`--reinstall-schedule` is the escape hatch and must keep working."""
    state: dict = {"completed_steps": [], "started_at": None}

    SETUP.step_install_sync(state, IDENTITY)
    SETUP.step_install_sync(state, IDENTITY, reinstall=True)

    assert len(installs) == 2


def test_reinstall_with_the_flag_installs_nothing_and_keeps_the_record(installs):
    """The two flags together. `--reinstall-schedule --no-sentinel-schedule` must
    not clear a record of a schedule that is still installed on the machine."""
    state: dict = {"completed_steps": [], "started_at": None}
    SETUP.step_install_sync(state, IDENTITY)

    SETUP.step_install_sync(state, IDENTITY, reinstall=True, install_sentinel=False)

    assert len(installs) == 1
    assert SETUP.is_done(state, "install_sync"), \
        "a declined re-install erased the record of the schedule already installed"
