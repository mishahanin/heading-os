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

Two zones, because one is not enough. `Etc/GMT+12` is UTC-12 and
`Pacific/Kiritimati` is UTC+14, so the SYSTEM day moves back under one and
forward under the other. `_calendar_root` in the summary test read
`NOW.astimezone()` with no argument, which is the system zone rather than the
operator's, and only a forward shift separates the two.

WHAT THIS FILE DOES NOT COVER, measured 2026-09-01. It varies the SYSTEM zone
only. `tests/conftest.py` line 29 assigns `os.environ["HEADING_OS_TZ"] =
"Etc/GMT-4"` at import, before any test module is collected, and
`tests/bridge/conftest.py::_pin_the_operator_zone` re-pins it autouse. Probed by
running a child at `TZ=Pacific/Kiritimati HEADING_OS_TZ=Pacific/Kiritimati` and
asking inside a test: `HEADING_OS_TZ='Etc/GMT-4'`,
`get_default_tz=ZoneInfo('Etc/GMT-4')`, `TZ='Pacific/Kiritimati'`. So whatever
`_run_at` puts in `HEADING_OS_TZ` is overwritten, and the OPERATOR-zone seam is
pinned rather than varied here.

Until 2026-09-01 this docstring said the opposite - that both seams were
exercised and "a test can depend on either" - which is a claim of coverage the
file has never had. Both pins are deliberate and each has its own guard
(`tests/test_a_timezone_pin_a_stray_variable_could_switch_off.py` fails if line
29 goes back to `setdefault`), so the honest repair is to say so and to MEASURE
the override, which `test_the_operator_zone_reaches_the_child_pinned_not_varied`
now does. The variable is still passed: it costs nothing and it becomes live the
day someone deliberately relaxes the root pin, at which point that test fails
and this paragraph gets rewritten on purpose rather than by accident.

Runs pytest in three child processes over 223 collected tests. Measured
2026-09-01 in this repository, `.venv/bin/python -m pytest <this file>`, three
consecutive runs: 9.75 s, 9.88 s, 8.68 s wall, of which the two zone children
are 3.4-4.3 s each and the probe child 1.4 s. The docstring said "3.2 s in
total" until then, from a 2026-08-27 corpus that has since grown by a file, by
most of its tests, and by the probe; the marker was already right for the wrong
reason. Three times the 5 s the `slow` marker nominally means, so the marker
stays and `turn-check` deselects it.

The number is from this tree, warm. A copy of the repository with no `__pycache__`
runs it at 14-20 s, because every child re-compiles the whole import graph; that
is a property of the measuring harness, not of the guard, and it is recorded here
so the next person does not read one as the other.
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
    """Run the clock-sensitive files in a child pytest with `TZ` set to `zone`.

    `TZ` is the one that lands. It drives `datetime.astimezone()` with no
    argument, which is the SYSTEM zone, and that is the seam
    `test_a_summary_that_read_the_wrong_end_of_the_file.py` depended on.

    `HEADING_OS_TZ` is set beside it and does NOT land: the root conftest
    assigns it unconditionally at import. See the module docstring for the
    probe, and `test_the_operator_zone_reaches_the_child_pinned_not_varied`
    below for the assertion that keeps that statement true.
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


_TZ_PROBE = (
    "import os\n"
    "from scripts.utils.workspace import get_default_tz\n"
    "def test_probe():\n"
    "    print('PROBE', os.environ.get('HEADING_OS_TZ'), str(get_default_tz()),\n"
    "          os.environ.get('TZ'), flush=True)\n"
)


@pytest.mark.slow
def test_the_operator_zone_reaches_the_child_pinned_not_varied(tmp_path):
    """What `_run_at` actually delivers to the child, asked rather than assumed.

    The module docstring states that only the SYSTEM zone varies. That is a
    claim about two conftest files this one does not own, so it is measured
    instead of asserted in prose: a probe test is written into the tree, run
    through the same environment `_run_at` builds, and asked what it sees.

    Failing here is not a defect in the pin. It means someone deliberately
    relaxed the root conftest, `HEADING_OS_TZ` now varies too, and this file's
    coverage claim must be rewritten UPWARD rather than left describing a
    weaker guard than it has become.
    """
    # WHY `tests/.tmp/` AND NOT `tests/` (2026-09-01), AND WHY NOT `tmp_path`.
    #
    # This wrote `tests/test_zz_tzprobe_<pid>.py` and removed it ~1.4s later.
    # `scripts/run-tests.py` builds the pre-push gate as `-n auto -m "not
    # acceptance"` and does NOT deselect `slow`, so this test runs INSIDE that
    # gate while every other xdist worker is sweeping `tests/**/*.py`. Four
    # sweeps read a path list and then read the files, and a file that vanishes
    # inside that window raised FileNotFoundError from inside a guard. One
    # `git push` was enough; no second agent was ever needed. That is what
    # blocked a push on 2026-09-01.
    #
    # `.gitignore` already carries `.tmp/` with no leading slash, so it matches
    # at any depth: the probe leaves every tracked walk at once, through an
    # existing rule rather than a new bespoke pattern. A leftover from a crashed
    # run is inert rather than invisible-and-armed - pytest's default
    # `norecursedirs` includes `.*`, so a directory run never collects it, and
    # the `runtime-state-guard` pre-commit hook refuses to stage any path
    # containing `.tmp/`.
    #
    # `tmp_path` would break the test outright, which is why the fixture is
    # unused here. pytest collects conftests by walking UP from the test file's
    # own directory, so a probe outside the rootdir picks up no
    # `tests/conftest.py`, sees the `Pacific/Kiritimati` it was launched with,
    # and the assertion below measures nothing. MEASURED under `tests/.tmp/`:
    # the probe still reports `Etc/GMT-4`, so the conftest pin still reaches it.
    scratch = ROOT / "tests" / ".tmp"
    scratch.mkdir(exist_ok=True)
    probe = scratch / f"test_zz_tzprobe_{os.getpid()}.py"
    probe.write_text(_TZ_PROBE, encoding="utf-8")
    try:
        env = dict(os.environ, TZ="Pacific/Kiritimati",
                   HEADING_OS_TZ="Pacific/Kiritimati",
                   PYTHONDONTWRITEBYTECODE="1")
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", str(probe), "-q", "-s",
             "-p", "no:cacheprovider", "--no-header", "-p", "no:xdist"],
            cwd=str(ROOT), capture_output=True, text=True, timeout=300, env=env,
        )
    finally:
        probe.unlink(missing_ok=True)

    out = proc.stdout + proc.stderr
    line = next((ln for ln in out.splitlines() if ln.startswith("PROBE ")), None)
    assert line, f"the probe never reported:\n{out[-2000:]}"
    _, operator_env, default_tz, system_tz = line.split()

    assert system_tz == "Pacific/Kiritimati", (
        "TZ no longer reaches the child, so this whole file varies nothing")
    assert operator_env == "Etc/GMT-4" and default_tz == "Etc/GMT-4", (
        f"HEADING_OS_TZ now reaches the child as {operator_env!r} "
        f"(get_default_tz -> {default_tz!r}). The root conftest pin has been "
        f"relaxed, so this file DOES vary the operator zone now and its "
        f"docstring understates what it covers. Rewrite the docstring.")


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

    # The child's TEST outcome, not its SESSION exit status.
    #
    # This child is repo-rooted, so the root conftest's `pytest_sessionfinish`
    # runs INSIDE it and sets `session.exitstatus = 1` whenever the operator's
    # live overlay changed between the run's first and last instant. Daemons,
    # the operator, and concurrent agents all write into that overlay on their
    # own schedule, so a child that ran every clock-sensitive file and printed
    # "412 passed" still exits 1 when one unrelated file appeared meanwhile.
    #
    # REPRODUCED on a sibling 2026-09-01, deterministically, with a background
    # writer touching a scratch overlay every 150 ms: "1 passed" and exit 1.
    # Load-sensitive by construction, because a slower child holds a wider
    # window, which is exactly how it hides until the machine is busy.
    #
    # The floor above already refuses a run that collected nothing, and the
    # `" failed" not in out` check below refuses a real failure, so nothing the
    # status check caught is lost. The status is REPORTED, not asserted on.
    assert " failed" not in out and " error" not in out.lower(), (
        f"the clock-sensitive files do not pass at {zone}. These tests read the "
        f"host clock instead of a pinned one. (child exit status "
        f"{proc.returncode})\n{out[-4000:]}"
    )
