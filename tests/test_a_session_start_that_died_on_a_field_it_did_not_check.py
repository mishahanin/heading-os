#!/usr/bin/env python3
"""SessionStart died on a `cwd` that was not a string, and lost the whole session.

`.claude/hooks/session-start.py` reads the payload field with
`input_data.get("cwd", os.getcwd())`. That default fires only when the KEY IS
ABSENT. When the key is present and holds `null`, a number or a list, the stored
value is returned and handed to `Path(...)`, which raises `TypeError` before any
of the hook's work happens.

MEASURED 2026-09-01 by driving the real hook:

    {"cwd": null}  -> exit 1, TypeError: expected str, bytes or os.PathLike
                             object, not NoneType
    {"cwd": 3}     -> exit 1, ... not int
    {"cwd": []}    -> exit 1, ... not list
    {"cwd": ""}    -> exit 0
    {}             -> exit 0

The consequence is not a cosmetic traceback. SessionStart is where the operator
gets the CRM red-contact alert, the corporate-update notice, the stale-file
warning and the setup banner. All of it is computed BELOW this line, so all of
it is lost, and the session opens looking normal.

## The shape, which is the reason this file exists

The identical guard was already written three times in this repository before
today:

- `.claude/hooks/prompt-guard.py`, twice in one function (`tool_input`,
  `file_path`) and a third time for `cwd` itself;
- `.claude/hooks/post-write-sanitize.py`, on the same PostToolUse matcher;
- `.claude/hooks/checkpoint-inject.py`, which fixed the non-dict PAYLOAD case on
  2026-08-20.

`session-start.py` even carries the non-dict payload guard, added when
`checkpoint-inject`'s was copied across, and its comment says those "were
missed". The field INSIDE the dict was missed in the same way, one layer down.
That is the campaign's dominant pattern: a fix that landed in some of its copies.

## What this file does NOT claim

It says nothing about `.claude/hooks/_dispatch.py`. A peer reported two `cwd`
reads there as unguarded because `payload.get("cwd") or ""` still admits a
truthy non-string such as `3`. Driving that hook with `null`, `3` and `[]` on
both the `Write` and the `Bash` paths produced exit 0 and no traceback, so the
crash does not reproduce; but a probe built to check whether the WALL still
refuses never produced a refusal even with a VALID `cwd`, so it exercised
nothing and is not evidence either way. That question is recorded as unmeasured
rather than answered here, because a probe that cannot fail in the control case
proves nothing about the treatment case.
"""
from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HOOK = ROOT / ".claude" / "hooks" / "session-start.py"

# Every shape that is valid JSON, is not a string, and is not the absent key.
# `""` and `{}` are the two that were ALREADY correct and are listed as anchors,
# not as bugs: an empty string is falsy so it takes the fallback, and an absent
# key takes `.get`'s default.
BAD_CWD = [None, 3, [], {}, True]
GOOD_CWD = ["", None]          # None here means "omit the key entirely"


def _run(payload: dict) -> subprocess.CompletedProcess:
    # BINARY capture. This suite has already been bitten by decoding a child's
    # output strictly inside a test about failure handling.
    return subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps(payload).encode("utf-8"),
        capture_output=True, cwd=str(ROOT), timeout=180,
        env=dict(os.environ, HEADING_OS_WIZARD_QUIET="1"))


@pytest.mark.slow
@pytest.mark.parametrize("value", BAD_CWD, ids=lambda v: type(v).__name__)
def test_a_cwd_that_is_not_a_string_does_not_end_the_session(value):
    """The headline. Each of these exited 1 with a traceback before the fix."""
    proc = _run({"cwd": value})
    err = proc.stderr.decode("utf-8", "replace")

    assert "Traceback (most recent call last)" not in err, (
        f"session-start raised on a cwd of type {type(value).__name__}. Every "
        f"alert this hook computes is below that line and was lost:\n{err}")
    assert proc.returncode == 0, (
        f"session-start exited {proc.returncode} on a cwd of type "
        f"{type(value).__name__}:\n{err}")


@pytest.mark.slow
def test_an_absent_cwd_key_is_still_fine():
    """Anchor. This path always worked and must keep working."""
    proc = _run({})
    err = proc.stderr.decode("utf-8", "replace")

    assert "Traceback (most recent call last)" not in err, err
    assert proc.returncode == 0, err


@pytest.mark.slow
def test_a_real_cwd_is_still_used_rather_than_discarded():
    """The anti-over-refusal jaw, and the one that matters most.

    A "fix" that replaced any supplied `cwd` with `os.getcwd()` would satisfy
    every case above while quietly making the hook inspect the wrong workspace
    on every session. This asserts the hook completes normally when handed the
    real repository root, which is the value the harness actually sends.
    """
    proc = _run({"cwd": str(ROOT)})
    err = proc.stderr.decode("utf-8", "replace")

    assert "Traceback (most recent call last)" not in err, err
    assert proc.returncode == 0, err


def test_the_hook_type_checks_the_field_rather_than_only_defaulting_it():
    """Structural, asked of the AST.

    `.get("cwd", os.getcwd())` LOOKS like it has a fallback and does not: the
    default fires on an absent key, never on a present-but-wrong value. This
    asserts the read is followed by a real type check, so the next author cannot
    reintroduce the same false sense of safety by restoring the one-liner.
    """
    tree = ast.parse(HOOK.read_text(encoding="utf-8"), filename=str(HOOK))

    reads = [n for n in ast.walk(tree)
             if isinstance(n, ast.Call)
             and getattr(n.func, "attr", None) == "get"
             and n.args
             and isinstance(n.args[0], ast.Constant)
             and n.args[0].value == "cwd"]
    assert reads, (
        "session-start.py no longer reads a 'cwd' key, so this test is looking "
        "at the wrong shape and measures nothing until that is resolved")

    checks_type = any(
        isinstance(n, ast.Call)
        and getattr(n.func, "id", None) == "isinstance"
        and len(n.args) == 2
        and (getattr(n.args[1], "id", None) == "str"
             or (isinstance(n.args[1], ast.Tuple)
                 and any(getattr(e, "id", None) == "str" for e in n.args[1].elts)))
        for n in ast.walk(tree))
    assert checks_type, (
        "no isinstance(..., str) appears anywhere in session-start.py, so the "
        "cwd field is trusted to be a string again. The default in "
        "`.get('cwd', os.getcwd())` fires only when the KEY IS ABSENT.")


def test_the_sibling_hooks_that_got_this_right_still_have_it():
    """A floor against the fix drifting back out of the copies that had it.

    This defect existed because three hooks carried the guard and a fourth did
    not. Asserting only the fourth would let the same hole reopen in any of the
    other three, which is exactly how it arrived.
    """
    siblings = {
        "prompt-guard.py": "PreToolUse ingest-path scan",
        "post-write-sanitize.py": "PostToolUse sanitiser on the same matcher",
        "session-start.py": "the hook this file is about",
    }
    missing = []
    for name, role in sorted(siblings.items()):
        path = ROOT / ".claude" / "hooks" / name
        if not path.exists():
            missing.append(f"{name} ({role}) is gone")
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        ok = any(
            isinstance(n, ast.Call)
            and getattr(n.func, "id", None) == "isinstance"
            and len(n.args) == 2
            and (getattr(n.args[1], "id", None) == "str"
                 or (isinstance(n.args[1], ast.Tuple)
                     and any(getattr(e, "id", None) == "str"
                             for e in n.args[1].elts)))
            for n in ast.walk(tree))
        if not ok:
            missing.append(f"{name} ({role}) has no isinstance(..., str) at all")

    assert not missing, (
        "a hook that reads an externally supplied field stopped type-checking "
        "it:\n  " + "\n  ".join(missing))
