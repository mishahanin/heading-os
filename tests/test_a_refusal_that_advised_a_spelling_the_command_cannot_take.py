#!/usr/bin/env python3
"""The refusal told the operator to name a path the herdr form never reads.

MEASURED 2026-09-08 against the version this fix replaced. Both forms that can
fail to resolve a target got the same closing sentence:

    Name the worktree by its path, or name the workspace id that herdr knows,
    and run it again.

Half of it cannot work for the herdr form, and the wall is the one place where
that costs the most, because a refusal is read by someone who is already
stuck. `_yard_deletion_operands` does collect a positional path, but the herdr
branch of `_yard_deletion_requests` never reads it: the checkout is resolved
from `--workspace` alone, through `_yard_herdr_checkouts`. And herdr itself
exits 2 on an extra operand, so following the advice fails twice over, once at
this wall and once at the program.

The advice is now per form. The git and `rm` forms take a path, and are told
so; the herdr form takes a workspace id, and is told that a path will not do.

Both directions: the sentence each form gets, and the sentence it must NOT get.
The refusal itself is unchanged, and the case that must still be refused for
its own reason is driven here too, so a future edit cannot satisfy this file by
making the refusal disappear.

Run: .venv/bin/python -m pytest \\
     tests/test_a_refusal_that_advised_a_spelling_the_command_cannot_take.py -q
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HOOK = ROOT / ".claude" / "hooks" / "_dispatch.py"

PATH_ADVICE = "Name the worktree by its path"
ID_ADVICE = "Name the workspace id herdr knows"


@pytest.fixture(scope="module")
def guard():
    spec = importlib.util.spec_from_file_location("dispatch_advice", HOOK)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _reason(guard, command: str) -> str:
    decision = guard.check_yard_deletion_guard({
        "tool_name": "Bash",
        "tool_input": {"command": command},
        "cwd": str(guard.WORKSPACE),
    })
    assert decision is not None, f"{command} was permitted"
    assert decision["decision"] == "block"
    return decision["reason"]


@pytest.mark.parametrize("command", [
    "herdr worktree remove",
    "herdr worktree remove --force",
    "herdr worktree remove --workspace wNOTHING",
])
def test_the_herdr_form_is_told_to_name_a_workspace_id(guard, command):
    reason = _reason(guard, command)
    assert ID_ADVICE in reason, reason
    assert PATH_ADVICE not in reason, (
        "the herdr form was sent to name a path. It is collected as an operand "
        "and then never read, and herdr exits 2 on it")
    assert "a path does not work for this form" in reason.lower(), reason


@pytest.mark.parametrize("command", [
    "git worktree remove",
    "git worktree remove --force",
])
def test_the_git_form_is_told_to_name_a_path(guard, command):
    reason = _reason(guard, command)
    assert PATH_ADVICE in reason, reason
    assert ID_ADVICE not in reason, (
        "the git form was offered a herdr workspace id, which git does not take")


def test_the_refusal_itself_is_unchanged(guard):
    """The advice moved; the reason it refuses did not.

    Without this the file above could be satisfied by a version that stopped
    refusing altogether, which is the hole the whole guard exists to close.
    """
    for command in ("herdr worktree remove --force", "git worktree remove"):
        reason = _reason(guard, command)
        assert "names no checkout this hook could resolve" in reason, reason
        assert "intentional policy block" in reason, reason


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-q"]))
