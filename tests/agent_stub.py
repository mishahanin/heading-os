"""A real process named `claude`, sitting in a directory, for a test's duration.

Consumed by `tests/test_a_brief_that_herdr_said_it_delivered_somewhere_else.py`
and `tests/test_a_brief_from_another_session_that_authorised_a_release.py`.

Since 2026-09-06 `scripts/herdr-brief.py` refuses to send into a pane with no
agent, and it establishes that by asking `/proc` for a process whose working
directory IS the pane's checkout. Both of those test files drive the sender as a
SUBPROCESS, so the answer cannot be patched in-process.

It must not be patched out of process either. An environment variable naming a
fake `/proc` would be a bypass shipped inside the guard, reachable by anything
that can set an environment variable -- which is exactly the shape of thing the
guard exists to refuse. So the fixture makes the process real instead:
`/bin/sleep` copied to a file named `claude` reports `comm=claude`, which is what
the check reads.

Lives here rather than in either test file because the second copy of a fixture
is the one that stops being fixed, and this one encodes a decision (no env-var
seam) that must not be quietly re-litigated in a copy.
"""
from __future__ import annotations

import contextlib
import shutil
import subprocess
import time
from pathlib import Path


@contextlib.contextmanager
def agent_in(checkout: Path, tmp_path: Path):
    """Yield a live process named `claude` whose cwd is `checkout`."""
    bindir = tmp_path / "agentbin"
    bindir.mkdir(exist_ok=True)
    binary = bindir / "claude"
    if not binary.exists():
        shutil.copy2("/bin/sleep", binary)
    proc = subprocess.Popen([str(binary), "300"], cwd=str(checkout),
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        # Waited for rather than assumed: Popen returns before the exec lands,
        # and a race here would fail the test this fixture exists to serve.
        for _ in range(300):
            try:
                if (Path(f"/proc/{proc.pid}/comm").read_text(
                        encoding="utf-8").strip() == "claude"):
                    break
            except OSError:
                pass
            time.sleep(0.01)
        else:                                    # pragma: no cover - diagnostic
            raise AssertionError(
                "the stand-in agent never appeared in /proc as `claude`; this "
                "platform cannot host the fixture and the test should skip")
        yield proc
    finally:
        proc.terminate()
        proc.wait(timeout=10)
