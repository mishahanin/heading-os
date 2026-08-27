#!/usr/bin/env python3
"""The read guard listed fifteen utilities and left out the plainest one.

`.claude/hooks/_dispatch.py` carries a pattern whose own comment says "any plain
read utility pointed at threads/personal/ dumps CEO-only content into the
transcript (a leak by itself, no redirect needed)". Its alternation named
head, tail, sed, awk, base64, b64encode, xxd, od, strings, nl, fold, cut, less,
more, grep and rg.

It did not name `cat`. The only two `cat` patterns in the list require a redirect
or a pipe to tee, which is exactly the case the read pattern was added to close.
So `head` on a personal thread was refused and the plainest possible read of the
same file was allowed, straight into the transcript.

Nothing else covered it. The settings templates carry no Bash deny for it. The
enumerated test in `tests/test_protect_personal_threads_hook.py` listed ten
utilities and omitted it too, so no test failed. And
`tests/security/test_dispatch_read_guard.py` asserted in prose that "the Bash
branch already blocks the cat/grep equivalent", which was half true and read as
whole.

A deny-list of utility names cannot be complete, and this file does not pretend
otherwise. What it can do is refuse the common ones and say plainly that the
list is the weak part of the design.

Found by the engine defect hunt, 2026-08-27.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

HOOK = ROOT / ".claude" / "hooks" / "_dispatch.py"

# Built once rather than spelled inline, so a personal-thread path never appears
# as a literal a shell command could be assembled from by accident.
_PERSONAL = "threads/" + "personal"
TARGET = f"{_PERSONAL}/a-thread.md"
SAFE_TARGET = "threads/business/a-thread.md"


@pytest.fixture(scope="module")
def dispatch():
    spec = importlib.util.spec_from_file_location("_dispatch_read_guard", HOOK)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run_hook(command: str) -> tuple[int, str]:
    payload = {"tool_name": "Bash", "tool_input": {"command": command}}
    proc = subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps(payload), capture_output=True, text=True, check=False,
    )
    return proc.returncode, proc.stdout


def _blocked(rc: int, stdout: str) -> bool:
    if rc != 0:
        return False
    try:
        data = json.loads(stdout)
    except (json.JSONDecodeError, ValueError):
        return False
    return data.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"


# ============================================================
# The one that was missing, and its close relatives
# ============================================================

PLAIN_READS = [
    "cat",          # the finding
    "tac",
    "head -n 5",
    "tail -n 50",
    "nl",
    "rev",
    "sort",
    "uniq",
    "shuf",
    "tr a-z A-Z <",
    "paste",
    "pr",
    "fmt",
    "fold -w 80",
    "expand",
    "unexpand",
    "column -t",
    "hexdump -C",
    "xxd",
    "od -c",
    "base64",
    "strings",
    "cut -c1-80",
    "sed -n '1,5p'",
    "awk '{print}'",
    "grep .",
    "rg .",
    "less",
    "more",
]


@pytest.mark.parametrize("utility", PLAIN_READS)
def test_a_plain_read_of_a_personal_thread_is_refused(utility: str) -> None:
    rc, out = _run_hook(f"{utility} {TARGET}")
    assert _blocked(rc, out), (
        f"the guard let `{utility}` read a personal thread into the transcript"
    )


@pytest.mark.parametrize("command", [
    f"cat {TARGET}",
    f"cat -n {TARGET}",
    f"cat -A {TARGET}",
    f"/bin/cat {TARGET}",
    f"cat  {TARGET}  ",
    f"cd /tmp && cat {ROOT}/{TARGET}",
    f"cat {TARGET} | head -5",
    f"cat {TARGET} > /tmp/leak.md",
])
def test_every_shape_of_the_missing_read_is_refused(command: str) -> None:
    """The finding was `cat` with no redirect. The redirect forms were already
    covered by two older patterns; they are asserted here so a rewrite of the
    alternation cannot drop them while adding the bare form."""
    rc, out = _run_hook(command)
    assert _blocked(rc, out), f"not blocked: {command!r}"


# ============================================================
# The guard must still say yes to ordinary work
# ============================================================

@pytest.mark.parametrize("command", [
    f"cat {SAFE_TARGET}",
    "cat README.md",
    "head -n 5 CHANGELOG.md",
    "sort scripts/thread.py",
    "grep -rn active threads/business/",
])
def test_an_ordinary_read_is_not_refused(command: str) -> None:
    """A guard that refuses everything protects nothing anyone will keep."""
    rc, out = _run_hook(command)
    assert not _blocked(rc, out), f"wrongly blocked: {command!r}"


# ============================================================
# The list is the weak part, and it is named as such
# ============================================================

def test_the_read_pattern_names_the_common_utilities(dispatch) -> None:
    """Asked of the compiled patterns, not of a grep over the source.

    A source grep also matches the comment that describes the pattern, and the
    comment is exactly what was wrong here: it promised "any plain read utility"
    over a list of fifteen. This reads the alternation the code actually uses.
    """
    sources = " ".join(p.pattern for p in dispatch.DANGEROUS_BASH_PATTERNS)
    for name in ("cat", "tac", "head", "tail", "sort", "uniq", "hexdump", "rev"):
        assert rf"|{name}|" in sources or rf"({name}|" in sources or rf"|{name})" in sources, (
            f"`{name}` is in no alternation of DANGEROUS_BASH_PATTERNS"
        )


def test_the_guard_admits_it_is_a_deny_list(dispatch) -> None:
    """The honest limit, written down where the next reader will find it.

    An unlisted utility passes. `busybox cat`, a shell function, a compiled
    helper: none of them are in any alternation and none can be. This asserts the
    limitation is stated in the module rather than left for someone to rediscover
    the way this one was.
    """
    src = HOOK.read_text(encoding="utf-8")
    assert "deny-list" in src or "deny list" in src, (
        "the personal-threads Bash guard is a deny-list and does not say so; a "
        "reader who believes it is complete will trust it further than it goes"
    )
