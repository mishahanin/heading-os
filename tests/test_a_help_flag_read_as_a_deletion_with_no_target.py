#!/usr/bin/env python3
"""A help flag was read as a deletion that could not name what it deleted.

MEASURED 2026-09-08 in HELM against the version this fix replaced. Driven
in-process against the real dispatcher, all six producing the byte-identical
YARD-deletion-guard refusal telling the operator to name the worktree by its
path, for commands that delete nothing:

    herdr worktree remove --help           REFUSED
    herdr worktree remove -h               REFUSED
    herdr worktree remove                  REFUSED  (correctly)
    git worktree remove --help             REFUSED
    git worktree remove -h                 REFUSED
    git worktree remove                    REFUSED  (correctly)

Allowed before and after, and asserted here so the exemption is not read as a
widening of the recognised forms: `herdr worktree --help`, `git worktree
--help`, `git help worktree`, `herdr help worktree remove`.

THE CAUSE is one line of parsing. `_yard_deletion_operands` drops every leading
dash word, so `--help`, `-h` and `--force` vanish identically and the branch
never learns the link was a usage request. `_yard_deletion_requests` then
appends a request with an empty target list, which `check_yard_deletion_guard`
refuses on. `scripts/herdr/README.md` records the herdr help form as the source
of a measurement, so the wall refused a step of its own runbook.

THE EXEMPTION IS TWO CONDITIONS AND THEY ARE THE SAFETY: no operand beyond the
verb AND every flag a help flag. Either one alone is a hole, which is why the
refused corpus below pairs each help flag with something else to do: a path,
`--force`, a `--workspace` id, a `--` separator. Whether a CLI honours a help
flag when it is also handed work is that CLI's business and not knowable from a
hook, so a link carrying work is judged exactly as it was.

Run: .venv/bin/python -m pytest \\
     tests/test_a_help_flag_read_as_a_deletion_with_no_target.py -q
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HOOK = ROOT / ".claude" / "hooks" / "_dispatch.py"

# The usage requests. Every one of the first four refused before 2026-09-08;
# the fifth is the `git -C <path>` spelling, which puts a value between the
# program and the subcommand and so proves the flags are read after the
# subcommand rather than anywhere in the line.
ALLOWED = [
    "herdr worktree remove --help",
    "herdr worktree remove -h",
    "git worktree remove --help",
    "git worktree remove -h",
    "git -C /some/where worktree remove --help",
]

# Untouched by the exemption, and each for its own reason:
# the two bare forms carry no flag at all, so `flags` is empty;
# the rest carry a flag that is not a help flag, or an operand, or both.
REFUSED = [
    "herdr worktree remove",
    "git worktree remove",
    "herdr worktree remove --force",
    "git worktree remove --force --help",
    "herdr worktree remove --force --help",
    "herdr worktree remove --workspace wNOTHING --help",
    "herdr worktree remove /some/path --help",
    "herdr worktree remove -- --help",
]

# Allowed before the fix and after it. Present so a future widening of the
# recognised forms has to go red here rather than passing as "still allowed".
UNCHANGED = [
    "herdr worktree --help",
    "git worktree --help",
    "git help worktree",
    "herdr help worktree remove",
]


@pytest.fixture(scope="module")
def guard():
    spec = importlib.util.spec_from_file_location("dispatch_help_flag", HOOK)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _decide(guard, command: str):
    """The real entry point, with the payload the harness would hand it."""
    return guard.check_yard_deletion_guard({
        "tool_name": "Bash",
        "tool_input": {"command": command},
        "cwd": str(guard.WORKSPACE),
    })


def _reason(decision) -> str:
    return (decision or {}).get("reason", "")


# ============================================================
# The floor under the three corpora
# ============================================================

def test_every_corpus_is_the_size_it_was_written_at():
    """Counted 2026-09-08. Asserted outside every loop below.

    A parametrised corpus that shrinks to nothing passes each of its cases
    vacuously and reports the same green as a full run, so the size is a claim
    made once, here, where an edit to any list has to come and change it.
    """
    assert len(ALLOWED) == 5, ALLOWED
    assert len(REFUSED) == 8, REFUSED
    assert len(UNCHANGED) == 4, UNCHANGED


# ============================================================
# The commands that used to be refused
# ============================================================

@pytest.mark.parametrize("command", ALLOWED)
def test_a_usage_request_with_nothing_else_in_it_is_not_a_deletion(guard,
                                                                   command):
    decision = _decide(guard, command)
    assert decision is None, _reason(decision)


@pytest.mark.parametrize("command", UNCHANGED)
def test_the_help_forms_that_always_passed_still_pass(guard, command):
    decision = _decide(guard, command)
    assert decision is None, _reason(decision)


# ============================================================
# The paired direction: what the exemption must NOT reach
# ============================================================

@pytest.mark.parametrize("command", REFUSED)
def test_a_link_carrying_anything_besides_a_help_flag_is_still_refused(guard,
                                                                       command):
    """Without this half the same file passes against `if flags: continue`,
    which waves through `--force` and the whole incident this wall exists for."""
    decision = _decide(guard, command)
    assert decision is not None, f"{command} was permitted"
    assert decision["decision"] == "block"
    assert decision["_policy_deny"] is True
    assert "names no checkout this hook could resolve" in _reason(decision), (
        _reason(decision))


def test_the_exemption_is_read_from_the_flags_after_the_subcommand(guard):
    """`git --no-pager` is git's own flag and says nothing about the subcommand.

    The git branch reads `_yard_git_flags`, which starts after the subcommand,
    so a flag before it is neither counted as a help flag nor able to hide one.
    """
    assert _decide(guard, "git --no-pager worktree remove") is not None
    assert _decide(guard, "git --no-pager worktree remove --help") is None


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-q"]))
