#!/usr/bin/env python3
"""The /prime page and the health-check registry must agree.

They had drifted three ways at once, measured 2026-08-20:

- `scripts/prime-health-parallel.py` carried **12** checks in `CHECKS` and ran
  them on `ThreadPoolExecutor(max_workers=8)`.
- `.claude/skills/prime/SKILL.md` said "seven health checks" and
  `max_workers=7`, and listed seven by name.
- `.claude/rules/skill-orchestrator.md` said eleven.

None of the three was checked against another, so each was written once and then
quietly outlived its subject. A reader following the skill page would have
believed five checks did not exist, including the reminders and dream-shadow
panels that only render when they have something to say — the exact checks whose
silence is indistinguishable from absence.

This test holds the page to the registry. It deliberately does NOT hold the
registry to a fixed number: adding a check is ordinary work, and the point is
that the prose moves with it.
"""
from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HELPER = ROOT / "scripts" / "prime-health-parallel.py"
SKILL = ROOT / ".claude" / "skills" / "prime" / "SKILL.md"

NUMBER_WORDS = {
    4: "four", 5: "five", 6: "six", 7: "seven", 8: "eight", 9: "nine",
    10: "ten", 11: "eleven", 12: "twelve", 13: "thirteen", 14: "fourteen",
    15: "fifteen", 16: "sixteen",
}


@pytest.fixture(scope="module")
def helper():
    spec = importlib.util.spec_from_file_location("prime_health_parallel", HELPER)
    module = importlib.util.module_from_spec(spec)
    sys.modules["prime_health_parallel"] = module
    spec.loader.exec_module(module)
    return module


def test_the_skill_page_states_the_real_check_count(helper):
    checks = getattr(helper, "CHECKS", None)
    assert checks, "prime-health-parallel.py has no CHECKS registry"
    count = len(checks)
    word = NUMBER_WORDS.get(count)
    assert word, f"add {count} to NUMBER_WORDS and update the skill page"

    text = SKILL.read_text(encoding="utf-8")
    assert word in text, (
        f"the CHECKS registry holds {count} checks but "
        f".claude/skills/prime/SKILL.md does not say '{word}'. Update the page "
        "when you add or remove a check; the registry is the source of truth."
    )

    # And the page must not ALSO claim a different number of checks somewhere.
    # Matched narrowly, as "<word> checks" or "<word> health checks": a first cut
    # flagged any count word in the paragraph and fired on "the last five render
    # nothing", which counts silent checks, not checks. A guard with a false
    # positive on its own subject teaches people to weaken it.
    stale = {
        w for n, w in NUMBER_WORDS.items()
        if n != count and re.search(rf"\b{w}\s+(?:health\s+)?checks\b", text, re.I)
    }
    assert not stale, (
        f"the page says '{sorted(stale)} checks' somewhere while the registry "
        f"holds {count}"
    )


def test_the_skill_page_states_the_real_worker_count(helper):
    source = HELPER.read_text(encoding="utf-8")
    m = re.search(r"ThreadPoolExecutor\(max_workers=(\d+)\)", source)
    assert m, "could not find the executor width in prime-health-parallel.py"
    workers = m.group(1)

    text = SKILL.read_text(encoding="utf-8")
    named = set(re.findall(r"max_workers=(\d+)", text))
    assert named, "the skill page no longer states the executor width"
    assert named == {workers}, (
        f"the helper uses max_workers={workers}; the page says {sorted(named)}"
    )


def test_every_check_in_the_registry_is_reachable(helper):
    """A registry entry that is not callable would fail only at session start."""
    checks = helper.CHECKS
    entries = checks.items() if hasattr(checks, "items") else enumerate(checks)
    for key, value in entries:
        target = value if callable(value) else getattr(value, "fn", value)
        assert callable(target) or isinstance(value, (tuple, list, dict)), (
            f"CHECKS entry {key!r} is neither callable nor a spec: {value!r}"
        )
