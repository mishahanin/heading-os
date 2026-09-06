#!/usr/bin/env python3
"""The wall that refused to delete a yard watched HELM go by.

MEASURED 2026-09-06 against the version this fix replaced. Driven in-process
against the real dispatcher, from this checkout:

    rm -rf <HELM>                          PERMITTED
    git worktree remove <HELM>             PERMITTED

while the same two commands aimed at any yard were refused. The cause is one
line of provenance rather than an oversight in the reasoning:
`_yard_guarded_checkouts` takes its set from `worktree_roots`, which drops the
main clone BY CONSTRUCTION, because the main clone is the clone git's worktree
registry belongs to and not a worktree of it. Correct there, a hole here.

WHY THE THREE YARD CONDITIONS ARE NOT REUSED, which is the decision this file
pins. They ask whether what is inside a checkout is safe SOMEWHERE ELSE. For
HELM there is no somewhere else: it holds the one object store every worktree of
this repository points into, so all of them die with it, and it holds the
reflog, which is the only record of what a mistake threw away. A clean working
tree establishes none of that. Condition 3 would also have been the third
instance of one shape in this guard, the other way up: the operator's own
session stands in HELM, so it would fire on every command, and a condition that
is always true teaches nothing. So the refusal is unconditional and says so.

Both directions, and each refusing half fails against the previous version:

* HELM named directly, and a directory that CONTAINS it, are refused;
* a path INSIDE HELM is not HELM, and is still permitted -- the negative anchor
  that tells this rule from "anything under the main clone";
* a main clone in the temp tree keeps the exemption every yard has, because the
  suite builds real main clones under `tmp_path`;
* when a command sweeps up both a yard and HELM, the YARD answers, so the
  refusal names the work that is actually at risk.

Run: .venv/bin/python -m pytest \\
     tests/test_a_deletion_guard_that_left_the_main_clone_unguarded.py -q
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HOOK = ROOT / ".claude" / "hooks" / "_dispatch.py"


@pytest.fixture(scope="module")
def guard():
    spec = importlib.util.spec_from_file_location("dispatch_main_clone", HOOK)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _decide(guard, command: str, cwd: Path | None = None):
    """The real entry point, with the payload the harness would hand it."""
    return guard.check_yard_deletion_guard({
        "tool_name": "Bash",
        "tool_input": {"command": command},
        "cwd": str(cwd or guard.WORKSPACE),
    })


def _reason(decision) -> str:
    return (decision or {}).get("reason", "")


@pytest.fixture
def only_helm(guard, monkeypatch, tmp_path):
    """A bench holding one main clone and no yards at all.

    Hermetic on purpose. Asserting on WHICH refusal comes back must not depend
    on how many worktrees this machine happens to have registered today, and a
    worktree nested inside HELM would otherwise answer first and make these
    cases pass for the wrong reason.
    """
    helm = tmp_path / "clones" / "engine"
    helm.mkdir(parents=True)
    monkeypatch.setattr(guard, "_yard_guarded_checkouts", list)
    monkeypatch.setattr(guard, "_yard_main_clone", lambda: helm)
    return helm


# ============================================================
# The command that used to pass
# ============================================================

@pytest.mark.parametrize("form", [
    "rm -rf {helm}",
    "rm -r {helm}",
    "rm -rf -- {helm}",
    "git worktree remove {helm}",
    "git worktree remove --force {helm}",
    "cd /tmp && rm -rf {helm}",
])
def test_deleting_the_main_clone_is_refused(guard, only_helm, form):
    decision = _decide(guard, form.format(helm=only_helm))
    assert decision is not None, f"{form} was permitted"
    assert decision["decision"] == "block"
    assert decision["_policy_deny"] is True
    assert "MAIN CLONE" in _reason(decision), _reason(decision)


def test_the_refusal_says_it_does_not_depend_on_the_tree(guard, only_helm):
    """The decision, in the refusal, so nobody reads it as a yard condition.

    A reader who takes this for the yard rule commits, sees it refuse again,
    and concludes the wall is broken.
    """
    reason = _reason(_decide(guard, f"rm -rf {only_helm}"))
    assert "whatever state it is in" in reason, reason
    assert "reflog" in reason, reason


def test_a_directory_that_contains_the_main_clone_is_refused(guard, only_helm):
    """`rm -rf <parent>` never spells HELM's name and erases it anyway."""
    decision = _decide(guard, f"rm -rf {only_helm.parent}")
    assert decision is not None, "the container was permitted"
    assert "MAIN CLONE" in _reason(decision)
    assert "contains it" in _reason(decision), _reason(decision)


# ============================================================
# The negative anchor: what this rule is NOT
# ============================================================

@pytest.mark.parametrize("command", [
    "rm -rf {helm}/scratch",
    "rm -rf {helm}/.pytest_cache",
    "rm -rf build",
    "rm {helm}",                     # no -r: cannot remove a directory
    "ls -la {helm}",
    "git worktree list",
])
def test_a_path_inside_the_main_clone_is_not_the_main_clone(guard, only_helm,
                                                            command):
    """The rule is "this target IS or CONTAINS HELM", never "is under it".

    Without this pair the same test suite passes against a guard that refuses
    every command touching anything below the main clone, which would refuse
    most of the work done in HELM.
    """
    decision = _decide(guard, command.format(helm=only_helm))
    assert decision is None, _reason(decision)


def test_a_main_clone_in_the_temp_tree_keeps_the_exemption(guard, monkeypatch):
    """Both directions of the carve-out, asked of the resolver itself.

    The suite builds real main clones under `tmp_path` (`armed_main_clone`), so
    a guard without this exemption fights its own tests, which is the reason the
    yards have the same one.
    """
    import scripts.utils.clone_guard as clone_guard

    monkeypatch.setattr(clone_guard, "main_clone_path",
                        lambda *a, **k: Path("/tmp/pytest-of-x/main-clone"))  # noqa: S108
    assert guard._yard_main_clone() is None, (
        "a throwaway main clone under the temp tree is guarded, so the suite "
        "cannot clean up after itself")

    # NOT `tmp_path`, which pytest puts under the temp tree this exemption is
    # about: the first version of this pair asserted the two halves of one
    # branch against two paths that both satisfied it, and passed while
    # measuring one direction twice.
    outside = ROOT
    monkeypatch.setattr(clone_guard, "main_clone_path", lambda *a, **k: outside)
    assert guard._yard_main_clone() == outside, (
        "a main clone outside the temp tree stopped being guarded, which is "
        "the whole clause")


# ============================================================
# Which wall answers when both match
# ============================================================

def test_a_sweep_that_takes_both_is_answered_by_the_yard(guard, monkeypatch,
                                                         tmp_path):
    """The yard is named, because the yard is the work that is at risk.

    HELM's removal is catastrophic but its content is reproducible from the
    yards and the remote; an unfinished yard exists nowhere else. A refusal that
    led with the main clone would bury the sentence the operator has to act on.
    """
    base = tmp_path / "workspaces"
    helm = base / "engine"
    yard = base / ".yard" / "task"
    yard.mkdir(parents=True)
    helm.mkdir(parents=True)
    monkeypatch.setattr(guard, "_yard_guarded_checkouts", lambda: [yard])
    monkeypatch.setattr(guard, "_yard_main_clone", lambda: helm)
    monkeypatch.setattr(guard, "_yard_unfinished",
                        lambda root, closes=None: [("tree", "2 uncommitted change(s)")])

    reason = _reason(_decide(guard, f"rm -rf {base}"))
    assert str(yard) in reason, reason
    assert "MAIN CLONE" not in reason, (
        "the sweep was answered by HELM, so the operator is told about the "
        "clone they can rebuild and not about the work they cannot")


def test_a_yard_deletion_is_untouched_by_this_clause(guard, monkeypatch,
                                                     tmp_path):
    """The other direction: the clause added nothing to the yard path."""
    yard = tmp_path / "yards" / "task"
    yard.mkdir(parents=True)
    monkeypatch.setattr(guard, "_yard_guarded_checkouts", lambda: [yard])
    monkeypatch.setattr(guard, "_yard_main_clone", lambda: tmp_path / "elsewhere")
    monkeypatch.setattr(guard, "_yard_unfinished", lambda root, closes=None: [])

    assert _decide(guard, f"rm -rf {yard}") is None, "a finished yard was refused"


# ============================================================
# Against the machine as it actually is
# ============================================================

def test_the_resolver_finds_this_checkout_s_own_main_clone(guard, helm_root):
    """Derived from the checkout, never from a constant or an environment.

    `helm_root` comes from `main_clone_path` in conftest and is used here as
    DATA. Nothing below executes against it.
    """
    assert guard._yard_main_clone() == helm_root


def test_the_live_main_clone_is_refused_end_to_end(guard, helm_root):
    """No monkeypatching at all: the guard as the harness would call it.

    Asserts only that it refuses, not which sentence comes back: whether a
    worktree happens to be registered inside HELM today is a property of the
    machine, and a test that reads which of two correct refusals arrived would
    go red on somebody else's checkout.
    """
    decision = _decide(guard, f"rm -rf {helm_root}")
    assert decision is not None and decision["decision"] == "block", (
        "the live main clone can be deleted by a tool call")


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-q"]))
