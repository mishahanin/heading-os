#!/usr/bin/env python3
"""SEC-010: Verify sentinel state save uses atomic write-then-replace."""

import pytest
from tests.security.conftest import read_file_content


def test_state_save_uses_atomic_replace(scripts_dir):
    """State save must write to .tmp then os.replace() for atomicity."""
    content = read_file_content(scripts_dir / "sentinel.py")
    # Find the save method and verify it uses os.replace
    assert "os.replace(" in content, (
        "sentinel.py must use os.replace() for atomic state file writes"
    )


def test_state_save_writes_to_tmp_first(scripts_dir):
    """State save must write to a temp file before replacing."""
    content = read_file_content(scripts_dir / "sentinel.py")
    assert ".tmp" in content or "with_suffix" in content, (
        "sentinel.py must write to a .tmp file before atomic replace"
    )
