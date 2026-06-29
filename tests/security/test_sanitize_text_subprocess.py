#!/usr/bin/env python3
"""Subprocess behavioral test for scripts/sanitize-text.py.

Verifies that the --scan flag exits 1 on hidden characters and exits 0 on
clean text -- as an actual process invocation, not an AST check.

Actual CLI contract (verified against the script source):
  python sanitize-text.py --scan --text <str>
  Exit 0: no hidden characters found.
  Exit 1: one or more hidden characters found.
"""
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
_SCRIPT = ROOT / "scripts" / "sanitize-text.py"

# Embedded hidden characters used as test fixtures (raw Unicode code points).
# NOTE: sanitize-text.py --scan on this file itself will flag these intentionally.
_ZWSP = "​"   # zero-width space
_NBSP = " "   # non-breaking space


def _run(text: str) -> "subprocess.CompletedProcess[str]":
    return subprocess.run(
        [sys.executable, str(_SCRIPT), "--scan", "--text", text],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )


def test_scan_exits_1_on_zero_width_space():
    """--scan must exit 1 when text contains a zero-width space (U+200B)."""
    result = _run("hello" + _ZWSP + "world")
    assert result.returncode == 1, (
        "Expected exit 1 for hidden char input, got "
        + str(result.returncode)
        + ".\nstdout: "
        + repr(result.stdout)
        + "\nstderr: "
        + repr(result.stderr)
    )


def test_scan_exits_0_on_clean_text():
    """--scan must exit 0 when text is clean ASCII."""
    result = _run("Hello, clean world. No hidden characters here.")
    assert result.returncode == 0, (
        "Expected exit 0 for clean input, got "
        + str(result.returncode)
        + ".\nstdout: "
        + repr(result.stdout)
        + "\nstderr: "
        + repr(result.stderr)
    )


def test_scan_exits_1_on_non_breaking_space():
    """--scan must exit 1 when text contains a non-breaking space (U+00A0)."""
    result = _run("hello" + _NBSP + "world")
    assert result.returncode == 1, (
        "Expected exit 1 for U+00A0, got "
        + str(result.returncode)
        + ".\nstdout: "
        + repr(result.stdout)
        + "\nstderr: "
        + repr(result.stderr)
    )
