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

import ast
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


def _executor_width() -> str:
    """The width the CODE opens its pool at, read from the call node.

    This was `re.search(r"ThreadPoolExecutor\\(max_workers=(\\d+)\\)", source)`
    over the whole file. The FIRST occurrence of that string in
    `prime-health-parallel.py` is line 11, inside the module DOCSTRING, so the
    test compared the skill page against a sentence of prose and never against
    the executor. MEASURED 2026-09-01 with the mutation harness: rewriting the
    real call to `max_workers=6` and leaving the docstring alone left this file
    green, which is the whole defect it exists to catch, one layer down.

    An AST walk cannot read a docstring, because a docstring is a Constant and
    never a Call.
    """
    tree = ast.parse(HELPER.read_text(encoding="utf-8"))
    widths = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = (func.attr if isinstance(func, ast.Attribute)
                else getattr(func, "id", None))
        if name != "ThreadPoolExecutor":
            continue
        for kw in node.keywords:
            if kw.arg == "max_workers" and isinstance(kw.value, ast.Constant):
                widths.append(str(kw.value.value))
    assert widths, (
        "no ThreadPoolExecutor(max_workers=...) CALL in prime-health-parallel.py. "
        "A mention in the docstring is not the executor."
    )
    assert len(set(widths)) == 1, (
        f"the module opens pools of differing widths {sorted(set(widths))}; the "
        "page can only state one of them"
    )
    return widths[0]


def test_the_modules_own_docstring_states_the_width_it_opens():
    """The prose the old regex was accidentally reading, now bound on purpose.

    Line 11 explains the bound to whoever changes the pool. Left unchecked it
    drifts exactly like the skill page did, and it is the more dangerous of the
    two because it sits in the file being edited.
    """
    width = _executor_width()
    doc = ast.get_docstring(ast.parse(HELPER.read_text(encoding="utf-8"))) or ""
    stated = set(re.findall(r"ThreadPoolExecutor\(max_workers=(\d+)\)", doc))
    assert stated, "the module docstring no longer states the executor width"
    assert stated == {width}, (
        f"prime-health-parallel.py opens max_workers={width} and its own "
        f"docstring says {sorted(stated)}"
    )


def test_the_skill_page_states_the_real_worker_count(helper):
    workers = _executor_width()

    text = SKILL.read_text(encoding="utf-8")
    named = set(re.findall(r"max_workers=(\d+)", text))
    assert named, "the skill page no longer states the executor width"
    assert named == {workers}, (
        f"the helper uses max_workers={workers}; the page says {sorted(named)}"
    )


def test_every_check_in_the_registry_is_reachable(helper):
    """A registry entry that is not callable would fail only at session start.

    The assertion used to end `or isinstance(value, (tuple, list, dict))`, and
    `CHECKS` is a dict of tuples, so that disjunct was True for every entry by
    construction and nothing ever looked INSIDE one. A registry holding
    `("crm_health", (None, "CRM health"))` passed it, while `run_all` does
    `fn, _label = CHECKS[key]` and then calls `fn(...)`, so `/prime` raised
    `TypeError: 'NoneType' object is not callable` at session start.

    Unpacked the way the consumer unpacks it, so the shape and the callable are
    both real assertions.
    """
    checks = helper.CHECKS
    assert isinstance(checks, dict) and checks, "CHECKS is empty or not a mapping"
    for key, value in checks.items():
        assert isinstance(value, tuple) and len(value) == 2, (
            f"CHECKS[{key!r}] is not the (callable, label) pair run_all unpacks: "
            f"{value!r}")
        fn, label = value
        assert callable(fn), (
            f"CHECKS[{key!r}] holds {fn!r} where run_all will call it; /prime "
            "raises at session start")
        assert isinstance(label, str) and label.strip(), (
            f"CHECKS[{key!r}] has no printable label")
