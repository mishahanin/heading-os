#!/usr/bin/env python3
"""Subprocess behavioral test for scripts/humanization-check.py.

Verifies that the script exits 1 on banned vocabulary (e.g. 'delve',
'leverage') and exits 0 on prose with no banned words -- as an actual
process invocation.

Actual CLI contract (verified against the script source):
  python humanization-check.py --text <str>
  Exit 0: no errors (may have warnings -- only errors trigger exit 1).
  Exit 1: one or more banned-vocab errors found.
"""
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
_SCRIPT = ROOT / "scripts" / "humanization-check.py"


def _run(text: str) -> "subprocess.CompletedProcess[str]":
    return subprocess.run(
        [sys.executable, str(_SCRIPT), "--text", text],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )


@pytest.mark.skipif(not _SCRIPT.exists(), reason="humanization-check.py not found")
def test_exits_1_on_banned_vocab_delve():
    """Script must exit 1 when banned word 'delve' is present."""
    result = _run("We must delve into this problem deeply.")
    assert result.returncode == 1, (
        "Expected exit 1 for 'delve', got "
        + str(result.returncode)
        + ".\nstdout: "
        + repr(result.stdout)
        + "\nstderr: "
        + repr(result.stderr)
    )


@pytest.mark.skipif(not _SCRIPT.exists(), reason="humanization-check.py not found")
def test_exits_1_on_banned_vocab_leverage():
    """Script must exit 1 when banned word 'leverage' is present."""
    result = _run("We can leverage this opportunity to grow.")
    assert result.returncode == 1, (
        "Expected exit 1 for 'leverage', got "
        + str(result.returncode)
        + ".\nstdout: "
        + repr(result.stdout)
        + "\nstderr: "
        + repr(result.stderr)
    )


@pytest.mark.skipif(not _SCRIPT.exists(), reason="humanization-check.py not found")
def test_exits_0_on_clean_prose():
    """Script must exit 0 on prose with no banned vocabulary."""
    result = _run(
        "We shipped the integration on Tuesday. Three customers onboarded same day."
    )
    assert result.returncode == 0, (
        "Expected exit 0 for clean prose, got "
        + str(result.returncode)
        + ".\nstdout: "
        + repr(result.stdout)
        + "\nstderr: "
        + repr(result.stderr)
    )
