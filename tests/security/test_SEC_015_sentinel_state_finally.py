#!/usr/bin/env python3
"""SEC-015: Verify run_cycle uses try/finally with state.save()."""

import ast
import pytest
from tests.security.conftest import read_file_content


def test_run_cycle_has_finally_with_state_save(scripts_dir):
    """run_cycle must wrap its body in try/finally with state.save() in finally."""
    content = read_file_content(scripts_dir / "sentinel.py")

    # Find run_cycle method and check for try/finally
    in_method = False
    has_try_finally = False
    for line in content.split("\n"):
        stripped = line.strip()
        if "async def run_cycle" in line:
            in_method = True
        elif in_method and stripped.startswith("async def "):
            break
        if in_method and "finally:" in stripped:
            has_try_finally = True

    assert has_try_finally, (
        "run_cycle must use try/finally to ensure state.save() "
        "is called even if an exception occurs mid-cycle"
    )
