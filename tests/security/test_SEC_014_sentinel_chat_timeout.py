#!/usr/bin/env python3
"""SEC-014: Verify per-chat timeout in _check_monitored_chats."""

import pytest
from tests.security.conftest import read_file_content


def test_monitored_chats_has_timeout(scripts_dir):
    """_check_monitored_chats must wrap chat processing in asyncio.wait_for with timeout."""
    content = read_file_content(scripts_dir / "sentinel.py")

    # Find _check_monitored_chats method and check for wait_for + timeout
    in_method = False
    method_text = []
    for line in content.split("\n"):
        if "_check_monitored_chats" in line and "async def" in line:
            in_method = True
        elif in_method and line.strip().startswith("async def "):
            break
        if in_method:
            method_text.append(line)

    method_content = "\n".join(method_text)
    has_wait_for = "wait_for" in method_content
    has_timeout = "timeout" in method_content

    assert has_wait_for and has_timeout, (
        "_check_monitored_chats must use asyncio.wait_for(..., timeout=...) "
        "to prevent a single unresponsive chat from blocking the entire cycle"
    )
