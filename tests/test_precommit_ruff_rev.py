"""F-M9: pre-commit ruff rev must match the pinned ruff version in uv.lock.

The lookup used to end `if not m: pytest.skip("ruff not found in uv.lock")`, and
a skip is a claim that decays. MEASURED 2026-09-01 with the mutation harness:
renaming the lock's `name = "ruff"` package block left this file GREEN, because
the only assertion in it had been skipped away. Any change to uv's lock format,
or to how ruff is declared, would have removed the comparison silently and left
the pre-commit rev free to drift.

ruff is a pinned dev dependency of this repository, so its absence from the lock
is a finding in itself, never a reason to stop measuring.
"""
import re
from pathlib import Path

ENGINE = Path(__file__).resolve().parent.parent

RUFF_BLOCK = re.compile(r'\[\[package\]\]\nname = "ruff"\nversion = "([^"]+)"')


def _pinned_ruff_version() -> str:
    lock = (ENGINE / "uv.lock").read_text(encoding="utf-8")
    # Match the [[package]] block for ruff specifically (not requires-dist references)
    found = RUFF_BLOCK.findall(lock)
    assert found, (
        "no [[package]] block for ruff in uv.lock. ruff is a pinned dev "
        "dependency, so either the lock is stale, the dependency was dropped, "
        "or uv changed the lock format under this parser. Any of the three "
        "means the pre-commit rev is no longer being compared to anything."
    )
    assert len(found) == 1, (
        f"uv.lock carries {len(found)} ruff package blocks {found}; this parser "
        "would silently compare against the first"
    )
    return found[0]


def test_uv_lock_actually_pins_ruff():
    """The premise of the comparison below, asserted rather than skipped past."""
    assert re.fullmatch(r"\d+\.\d+\.\d+", _pinned_ruff_version())


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
