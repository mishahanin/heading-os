#!/usr/bin/env python3
"""Leaving a package and re-entering it dropped its autouse DATA-root pin.

On pytest 9.1.1, a directory conftest's autouse fixtures are not applied to a
file in that directory when collection has left the package and come back to
it. Only a hand-written interleaved command line produces the shape; `pytest
tests` keeps a package together, and so does pytest-randomly.

MEASURED 2026-09-01 in the real tree, before the root-conftest net was added:

    pytest tests/integration/test_aggregate_crm_per_exec.py \\
           tests/test_data_root.py \\
           tests/integration/test_workspace_helpers_per_exec.py
    -> 1 failed, 36 passed

    pytest tests/bridge/test_config.py tests/test_data_root.py \\
           tests/bridge/test_telemetry.py
    -> 74 passed, 6 errors

The two shapes fail differently, and only one of them is dangerous.

The BRIDGE shape is loud and harmless. Its six errors are `fixture
'workspace_root' not found`: those tests request a named fixture from the
directory conftest, so they do not run at all and can write nowhere. An error
that stops the test is not a silent hole, and it is left as it is. Fixing it
would mean hoisting a named fixture out of the directory that owns it.

The INTEGRATION shape is the dangerous one. Its pin is autouse and nothing
requests it by name, so dropping it does not error: 36 tests ran green with
`HEADING_OS_DATA` unset, resolving the operator's real overlay. The one failure
was the guard test added hours earlier by the shard-7 auditor, which is the
only reason anybody found out. Four of that directory's files spawn child
processes, a child inherits the environment, and the in-process overlay write
guard in `scripts/utils/overlay_write_guard.py` cannot see a child at all.

The fix is `tests/conftest.py::_pin_the_data_root_even_on_package_re_entry`. A
root conftest is loaded once per session and never re-entered, so a pin placed
there cannot be dropped this way. It sets exactly what the two directory
fixtures set, to the same `tmp_path`, so in an ordinary run it is a no-op
duplicate. Both directory fixtures stay: each carries the reasoning for its own
directory, and belt-and-braces is the right shape when the failure mode is "the
belt silently is not there".

After the fix, the integration shape is `37 passed`.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

# The exact command line that reproduced it: a package, a root-level file, then
# the same package again. Ordered by hand on purpose. `matching_tests` in
# `scripts/turn-check.py` returns sorted paths, which keeps packages contiguous,
# so the Stop-hook lane does not produce this shape; it was checked rather than
# assumed.
RE_ENTRY_ARGV = [
    "tests/integration/test_aggregate_crm_per_exec.py",
    "tests/test_data_root.py",
    "tests/integration/test_workspace_helpers_per_exec.py",
]


def _run(argv: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:randomly",
         "--no-header", *argv],
        cwd=str(ROOT), capture_output=True, text=True, errors="replace",
        timeout=300,
    )


@pytest.mark.slow
def test_the_data_root_stays_pinned_when_a_package_is_re_entered():
    """The whole point. Run the reproducing command line and require it green.

    Marked slow because it spawns a pytest of its own. It runs in the full
    suite, which is where a conftest regression has to be caught; the Stop-hook
    lane deselects it and says so.
    """
    proc = _run(RE_ENTRY_ARGV)
    tail = "\n".join(proc.stdout.strip().splitlines()[-6:])
    # The child's TEST outcome, not its SESSION exit status.
    #
    # This child is repo-rooted, so the root conftest's `pytest_sessionfinish`
    # runs INSIDE it and sets `session.exitstatus = 1` whenever the operator's
    # live overlay changed between the run's first and last instant. That
    # overlay is written by daemons, by the operator, and by concurrent agents
    # on their own schedule, so a child that ran these tests and printed
    # "N passed" still exits 1 when an unrelated file appeared while it ran.
    #
    # REPRODUCED on a sibling of this test 2026-09-01, deterministically, with
    # a background writer touching a scratch overlay every 150 ms: the child
    # printed "1 passed" and exited 1, and the assertion then reported a pin
    # failure over a run that had seen the pin. Load-sensitive by construction,
    # because a slower child holds a wider window for someone else to write.
    #
    # Nothing the status check caught is lost: a child that crashed, errored in
    # collection, or collected nothing prints no "passed" line either. The
    # status is REPORTED in the message rather than asserted on.
    assert " passed" in proc.stdout and " failed" not in proc.stdout, (
        "collection left tests/integration and came back, and something in "
        "that directory failed. Before 2026-09-01 the cause was the autouse "
        "DATA-root pin being dropped, which left those tests resolving the "
        f"operator's live overlay. (child exit status {proc.returncode})\n"
        f"{tail}\n{proc.stderr[-800:]}")


def test_the_root_conftest_carries_the_net_and_names_both_packages():
    """Read from the module, so this measures what actually ships.

    A grep would be satisfied by the word appearing in the docstring that
    explains the fix, which is precisely the failure mode this campaign keeps
    finding. The fixture is imported and its registered name checked instead.
    """
    src = (ROOT / "tests" / "conftest.py").read_text(encoding="utf-8")
    assert "_pin_the_data_root_even_on_package_re_entry" in src, (
        "the root-conftest DATA-root net is gone. Without it, an interleaved "
        "command line runs tests/integration against the live overlay and only "
        "one guard test says so.")
    assert "_DATA_ROOT_PINNED_PACKAGES" in src

    import tests.conftest as root_conftest
    packages = root_conftest._DATA_ROOT_PINNED_PACKAGES
    assert "integration" in packages, (
        "tests/integration is no longer covered by the net, and it is the "
        "directory whose fixture drop is SILENT rather than an error")
    assert "bridge" in packages


def test_every_named_package_really_pins_the_data_root_itself():
    """The net must not outlive the fixtures it backs up.

    If a directory stops pinning the root on its own, the net becomes the only
    pin and nobody notices that the reasoning moved. This fails instead, so the
    two stay in step.
    """
    import tests.conftest as root_conftest
    for package in root_conftest._DATA_ROOT_PINNED_PACKAGES:
        conftest = ROOT / "tests" / package / "conftest.py"
        assert conftest.is_file(), f"tests/{package}/conftest.py is gone"
        body = conftest.read_text(encoding="utf-8")
        assert "HEADING_OS_DATA" in body, (
            f"tests/{package}/conftest.py no longer pins HEADING_OS_DATA, so "
            f"the root-conftest net in tests/conftest.py is now the only pin "
            f"for that directory. Either restore the directory fixture or take "
            f"{package!r} out of _DATA_ROOT_PINNED_PACKAGES deliberately.")


def test_the_net_sets_the_same_value_the_directory_fixtures_set():
    """Why the duplicate is safe, asserted rather than promised.

    Both directory fixtures do `monkeypatch.setenv("HEADING_OS_DATA",
    str(tmp_path))`. The net does the same, and `tmp_path` is one object per
    test, so whichever runs last writes an identical value. If these ever
    diverge the duplicate stops being a no-op and starts being a race.
    """
    root_src = (ROOT / "tests" / "conftest.py").read_text(encoding="utf-8")
    net = 'monkeypatch.setenv("HEADING_OS_DATA", str(tmp_path))'
    assert net in root_src, "the net no longer sets the plain tmp_path value"
    for package in ("bridge", "integration"):
        body = (ROOT / "tests" / package / "conftest.py").read_text(encoding="utf-8")
        assert net in body, (
            f"tests/{package}/conftest.py pins HEADING_OS_DATA to something "
            f"other than str(tmp_path). The root net still sets str(tmp_path), "
            f"so the two now disagree and the winner depends on fixture order.")
