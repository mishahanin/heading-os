"""A commit guard that prints BLOCKED must exit non-zero.

Two of them did not. `vault-guard` and `runtime-state-guard` both ended in:

    (print('BLOCKED - ...') or [print(f'  {f}') for f in leaked] or sys.exit(1))
        if leaked else sys.exit(0)

`print()` returns None, so the chain moves to the list comprehension. A
comprehension over a non-empty list returns a non-empty list of Nones, which is
TRUTHY, so `or` short-circuits there and `sys.exit(1)` is never evaluated. The
script then runs off the end and the interpreter exits 0. Measured 2026-08-23:

    $ python3 -c "<vault-guard entry>" _secure/probe.txt
    BLOCKED - vault files staged for commit
      _secure/probe.txt
    EXIT=0

So both guards announced a block and allowed the commit, for as long as they
have existed. This is the failure the repo's own CI comments call the worst
kind: a step that can never be observed failing.

These tests run each local hook's REAL `entry` string out of
`.pre-commit-config.yaml`, rather than a copy. A copy is how the two got out of
sync with reality in the first place.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / ".pre-commit-config.yaml"


def _local_hooks() -> dict[str, str]:
    data = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    hooks = {}
    for repo in data.get("repos", []):
        if repo.get("repo") != "local":
            continue
        for hook in repo.get("hooks", []):
            hooks[hook["id"]] = hook.get("entry", "")
    return hooks


HOOKS = _local_hooks()

# hook id -> (a path it must refuse, a path it must allow)
BLOCKING_GUARDS = {
    "vault-guard": ("_secure/probe.txt", "README.md"),
    "runtime-state-guard": (".claude/scheduled_tasks.json", "README.md"),
}


def _run(entry: str, *args: str) -> subprocess.CompletedProcess:
    """Run a hook's entry string the way pre-commit does: shell-split, then
    the staged filenames appended."""
    import shlex
    return subprocess.run(shlex.split(entry) + list(args),
                          capture_output=True, text=True, cwd=ROOT)


@pytest.mark.parametrize("hook_id", sorted(BLOCKING_GUARDS))
def test_the_guard_is_still_configured(hook_id):
    assert hook_id in HOOKS, (
        f"{hook_id} is gone from .pre-commit-config.yaml. If that was deliberate, "
        "remove its row from BLOCKING_GUARDS here too; if not, the gate is off."
    )


@pytest.mark.parametrize("hook_id", sorted(BLOCKING_GUARDS))
def test_the_guard_exits_non_zero_on_the_thing_it_names(hook_id):
    bad, _ = BLOCKING_GUARDS[hook_id]
    result = _run(HOOKS[hook_id], bad)
    assert "BLOCKED" in result.stdout, (
        f"{hook_id} did not even announce a block for {bad!r}"
    )
    assert result.returncode != 0, (
        f"{hook_id} printed BLOCKED for {bad!r} and then exited 0, so the commit "
        f"proceeds. stdout was: {result.stdout!r}"
    )


@pytest.mark.parametrize("hook_id", sorted(BLOCKING_GUARDS))
def test_the_guard_names_the_offending_file(hook_id):
    bad, _ = BLOCKING_GUARDS[hook_id]
    result = _run(HOOKS[hook_id], bad)
    assert bad in result.stdout, (
        f"{hook_id} blocked without saying which file, so the operator cannot act"
    )


@pytest.mark.parametrize("hook_id", sorted(BLOCKING_GUARDS))
def test_the_guard_passes_an_ordinary_file(hook_id):
    _, good = BLOCKING_GUARDS[hook_id]
    result = _run(HOOKS[hook_id], good)
    assert result.returncode == 0, (
        f"{hook_id} rejected the innocent file {good!r}: {result.stdout!r} "
        f"{result.stderr!r}"
    )


@pytest.mark.parametrize("hook_id", sorted(BLOCKING_GUARDS))
def test_the_guard_passes_when_nothing_is_staged(hook_id):
    assert _run(HOOKS[hook_id]).returncode == 0


# --- the shape that caused it must not reappear anywhere in the config --------

def test_no_local_hook_puts_sys_exit_1_behind_an_or_chain():
    """The generic form of the bug: `... or [<comprehension>] or sys.exit(1)`.
    Any truthy value earlier in the chain silently swallows the exit."""
    offenders = []
    for hook_id, entry in HOOKS.items():
        if "or sys.exit(1)" in entry:
            offenders.append(hook_id)
    assert not offenders, (
        f"these hooks reach sys.exit(1) through an `or` chain: {offenders}. "
        "Whether the exit runs then depends on the truthiness of everything "
        "before it. Use a standalone `sys.exit(1 if leaked else 0)`."
    )


def test_every_hook_that_can_print_blocked_is_covered_here():
    """Stops the next guard of this shape shipping untested."""
    printers = {hook_id for hook_id, entry in HOOKS.items() if "BLOCKED" in entry}
    uncovered = printers - set(BLOCKING_GUARDS)
    assert not uncovered, (
        f"these hooks print BLOCKED but nothing here checks they exit non-zero: "
        f"{sorted(uncovered)}. Add them to BLOCKING_GUARDS."
    )
