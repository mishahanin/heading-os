#!/usr/bin/env python3
"""The clock-sensitive tests must pass on a machine that is not Asia/Dubai.

Forty-one tests in this repository passed only because the host is set to the
operator's own timezone. None of them were testing a defect in the code: they
inherited "today" from the machine and asserted it as if it were fixed. Measured
2026-08-27 by running the whole suite at `Etc/GMT+12`, which moves the operator's
day back one:

    tests/bridge/test_sources_pulse.py        16
    tests/bridge/test_sources_agenda.py       13
    tests/bridge/test_adoption.py              5
    tests/bridge/test_sources_ops.py           3
    tests/test_timeparse.py                    2
    tests/test_compaction_probe.py             1
    tests/bridge/test_adoption_report.py       1

The four fixes pin the clock each test measures. This file is the guard that
keeps them pinned, and - more to the point - catches the NEXT test written in
these files against the host clock, which no fixture can do on its own.

Two zones, because one is not enough. `Etc/GMT+12` is UTC-12 and moves the
operator's day BACK; `Pacific/Kiritimati` is UTC+14 and moves the SYSTEM day
FORWARD. `_calendar_root` in the summary test read `NOW.astimezone()` with no
argument, which is the system zone rather than the operator's, and only a
forward shift separates the two.

Runs pytest in two child processes, measured at 3.2 s in total on 2026-08-27.
That is under the 5 s the `slow` marker nominally means, and it is still a tax
worth paying once per push rather than once per turn, so the marker stays and
`turn-check` deselects it.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

# The files whose assertions depend on which day it is. Every one of them
# failed at UTC-12 before 2026-08-27, or (the last) at UTC+14.
CLOCK_SENSITIVE = [
    "tests/bridge/test_sources_pulse.py",
    "tests/bridge/test_sources_agenda.py",
    "tests/bridge/test_adoption.py",
    "tests/bridge/test_adoption_report.py",
    "tests/bridge/test_sources_ops.py",
    "tests/bridge/test_a_summary_that_read_the_wrong_end_of_the_file.py",
    "tests/test_timeparse.py",
    "tests/test_compaction_probe.py",
]


def _run_at(zone: str) -> subprocess.CompletedProcess:
    """Run the clock-sensitive files in a child pytest pinned to `zone`.

    Both variables are set. `TZ` drives `datetime.astimezone()` with no
    argument, which is the SYSTEM zone; `HEADING_OS_TZ` drives
    `get_default_tz()`, which is the OPERATOR zone. The two are different
    seams and a test can depend on either.
    """
    env = dict(
        os.environ,
        TZ=zone,
        HEADING_OS_TZ=zone,
        PYTHONDONTWRITEBYTECODE="1",
    )
    return subprocess.run(
        [sys.executable, "-m", "pytest", *CLOCK_SENSITIVE,
         "-q", "-p", "no:cacheprovider", "--no-header", "-p", "no:xdist",
         "-m", "not acceptance", "--tb=line"],
        cwd=str(ROOT), capture_output=True, text=True, timeout=600, env=env,
    )


def test_every_clock_sensitive_file_still_exists():
    """A renamed file must not silently empty this guard.

    Without this the list above decays into names that match nothing, the child
    pytest collects zero tests, exits 5 or 0 depending on the day, and the guard
    reports a clean bill over an empty run.
    """
    missing = [rel for rel in CLOCK_SENSITIVE if not (ROOT / rel).is_file()]
    assert not missing, f"clock-sensitive files gone or renamed: {missing}"
    assert len(CLOCK_SENSITIVE) >= 8


@pytest.mark.slow
@pytest.mark.parametrize("zone", ["Etc/GMT+12", "Pacific/Kiritimati"])
def test_the_clock_sensitive_files_pass_away_from_the_operator_zone(zone):
    proc = _run_at(zone)
    out = proc.stdout + proc.stderr

    # Floor first. A run that collected nothing exits 5, and a run that
    # collected one test proves nothing about eight files.
    assert " passed" in out, f"no test reported as passed at {zone}:\n{out[-3000:]}"
    passed = int(out.split(" passed")[0].split()[-1])
    assert passed >= 100, (
        f"only {passed} test(s) ran at {zone}; the guard measured almost "
        f"nothing:\n{out[-2000:]}"
    )

    assert proc.returncode == 0, (
        f"the clock-sensitive files do not pass at {zone}. These tests read the "
        f"host clock instead of a pinned one:\n{out[-4000:]}"
    )
