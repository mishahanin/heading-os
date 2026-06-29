#!/usr/bin/env python3
"""SEC-012: Verify sentinel uses interruptible sleep for graceful shutdown."""

import pytest
from tests.security.conftest import read_file_content


def test_sentinel_uses_event_not_sleep(scripts_dir):
    """Sentinel main loop must use asyncio.Event for interruptible wait."""
    content = read_file_content(scripts_dir / "sentinel.py")
    assert "_stop_event" in content, (
        "sentinel.py must use an asyncio.Event (_stop_event) for interruptible sleep"
    )


def test_sentinel_no_bare_asyncio_sleep_in_loop(scripts_dir):
    """The main run loop must not use bare asyncio.sleep() for long waits."""
    content = read_file_content(scripts_dir / "sentinel.py")
    lines = content.split("\n")

    # Find the start() method's main loop and check for asyncio.sleep
    in_start_method = False
    for i, line in enumerate(lines, 1):
        if "async def start(" in line:
            in_start_method = True
        elif in_start_method and line.strip().startswith("async def "):
            in_start_method = False

        if in_start_method and "asyncio.sleep(self.config.check_interval)" in line:
            pytest.fail(
                f"Line {i}: bare asyncio.sleep() in main loop blocks graceful shutdown. "
                f"Use asyncio.wait_for(self._stop_event.wait(), timeout=interval) instead."
            )
