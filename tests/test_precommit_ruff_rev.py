"""F-M9: pre-commit ruff rev must match the pinned ruff version in uv.lock."""
import re
from pathlib import Path

import pytest

ENGINE = Path(__file__).resolve().parent.parent


def _pinned_ruff_version() -> str:
    lock = (ENGINE / "uv.lock").read_text(encoding="utf-8")
    # Match the [[package]] block for ruff specifically (not requires-dist references)
    m = re.search(r'\[\[package\]\]\nname = "ruff"\nversion = "([^"]+)"', lock)
    if not m:
        pytest.skip("ruff not found in uv.lock")
    return m.group(1)


def test_precommit_ruff_rev_matches_uv_lock():
    pinned = _pinned_ruff_version()
    config = (ENGINE / ".pre-commit-config.yaml").read_text(encoding="utf-8")
    expected_rev = f"v{pinned}"
    m = re.search(r"astral-sh/ruff-pre-commit.*?rev:\s*(\S+)", config, re.DOTALL)
    assert m is not None, "ruff-pre-commit hook not found in .pre-commit-config.yaml"
    actual_rev = m.group(1)
    assert actual_rev == expected_rev, \
        f"pre-commit ruff rev={actual_rev!r} but uv.lock pins ruff=={pinned!r}. " \
        f"Expected rev: {expected_rev!r} (F-M9)"
