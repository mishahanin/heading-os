#!/usr/bin/env python3
"""SEC-016: Verify PID file creation uses file locking."""

import pytest
from tests.security.conftest import read_file_content


def test_pid_file_uses_locking(scripts_dir):
    """PID file creation must use file locking to prevent duplicate instances."""
    content = read_file_content(scripts_dir / "sentinel.py")
    has_fcntl = "fcntl" in content
    has_msvcrt = "msvcrt" in content
    has_lock = "LOCK_EX" in content or "LK_NBLCK" in content or "flock" in content

    assert has_lock or (has_fcntl or has_msvcrt), (
        "sentinel.py must use file locking (fcntl.flock on Unix, msvcrt.locking on Windows) "
        "when creating the PID file to prevent duplicate instances"
    )
