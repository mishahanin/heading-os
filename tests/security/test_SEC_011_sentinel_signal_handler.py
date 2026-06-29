#!/usr/bin/env python3
"""SEC-011: Verify signal handlers registered after sentinel object creation."""

import re
import pytest
from tests.security.conftest import read_file_content


def test_signal_handler_after_sentinel_creation(scripts_dir):
    """signal.signal() calls must appear AFTER sentinel = Sentinel(...)."""
    content = read_file_content(scripts_dir / "sentinel.py")
    lines = content.split("\n")

    sentinel_creation_line = None
    first_signal_line = None

    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if re.match(r'sentinel\s*=\s*Sentinel\(', stripped):
            sentinel_creation_line = i
        if 'signal.signal(' in stripped and first_signal_line is None:
            # Only check in the main() function area (after line 2200)
            if i > 2200:
                first_signal_line = i

    assert sentinel_creation_line is not None, (
        "Could not find 'sentinel = Sentinel(...)' in sentinel.py"
    )
    assert first_signal_line is not None, (
        "Could not find signal.signal() calls in sentinel.py main area"
    )
    assert first_signal_line > sentinel_creation_line, (
        f"signal.signal() at line {first_signal_line} is registered BEFORE "
        f"sentinel object creation at line {sentinel_creation_line}. "
        f"Signal handlers must be registered AFTER the object they reference."
    )
