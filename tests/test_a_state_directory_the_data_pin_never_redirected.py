#!/usr/bin/env python3
"""Tests wrote checkpoints into the operator's live `.claude/state/`.

`.claude/state/` sits under the ENGINE root. The data overlay is a SIBLING
directory. So `HEADING_OS_DATA` - the pin every isolated test in this suite
relies on, and the one whose absence caused the 1107-stray-handoff incident -
does not redirect `.claude/state/` and never did.

The evidence was on disk on 2026-09-01, in the operator's live tree, written by
this suite:

  checkpoint-True.json          BAD_FIELD_VALUES `True` for session_id
  checkpoint-3.json             BAD_FIELD_VALUES `3` for session_id
  checkpoint-sweep-session.json the sweep's own base payload session id
  checkpoint-session.json       the `[]` and `{}` cases collapsing to one bucket

all four from `tests/test_every_hook_survives_a_malformed_payload.py`, plus a
`checkpoint-s45probe.json` from an ad-hoc audit probe with no fixture behind it.

## Why `CLAUDE_PROJECT_DIR` looked like the seam and was not

It exists, it is read, and it loses. `project_root()` builds a candidate list in
this order: `payload["workspace"]["project_dir"]`, `payload["workspace"]
["current_dir"]`, `payload["cwd"]`, THEN `CLAUDE_PROJECT_DIR`, then
`WORKSPACE_ROOT`, then `os.getcwd()`. The malformed-payload sweep sends
`"cwd": <the engine root>` in every payload it builds, so it wins at candidate 3
and the environment pin is never reached. Every test in that file looked
properly isolated and none of them was.

The tests that DO isolate correctly - `tests/test_checkpoint_operator_surface.py`,
`tests/test_checkpoint_save.py`, `tests/test_session_compaction_threshold.py` -
work only because they send no `cwd`. Their isolation is load-bearing and
silently conditional on a payload field, which is not isolation, it is luck.

## The fix, and why it is a separate question rather than a sixth candidate

`HEADING_OS_STATE_DIR`, read by `checkpoint_paths.state_root()` BEFORE the
payload is consulted at all. Adding it to `project_root()`'s candidate list
would have inherited the same defeat.

`project_root()` answers "which tree is this session working in", and the
payload is genuinely the authority on that - a plugin installed in someone
else's repository must write there and not into the plugin cache. `state_root()`
answers "where does state go", which the payload has no business deciding. The
same split fixed `overlay_write_guard._structural_overlay_root()` on 2026-08-31:
it had asked the environment where the operator's data was, and the environment
is the one thing a test session can change.

## What this file measures

Run against the pre-fix tree on 2026-09-01, every test here that names a
mechanism failed; the two vacuity jaws and the gitignore assertion passed, which
is what a jaw is for. The headline case - `test_a_hook_driven_with_a_payload_cwd
_writes_nothing_into_the_live_tree` - is the one that reproduces the actual
incident, in a subprocess, through the real hook, with the real payload shape.
"""
from __future__ import annotations

import ast
import importlib.util
import json
import os
import subprocess  # nosec B404 - fixed argv, never shell=True
import sys
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.utils.checkpoint_paths import (  # noqa: E402
    STATE_DIR_ENV,
    state_path,
    state_root,
)

HOOK = ROOT / ".claude" / "hooks" / "checkpoint-save.py"
DISPATCH = ROOT / ".claude" / "hooks" / "_dispatch.py"
CONFTEST = ROOT / "tests" / "conftest.py"
LIVE_STATE = ROOT / ".claude" / "state"


# ============================================================
# The resolver
# ============================================================

def test_an_absolute_pin_wins_over_the_project_root(tmp_path, monkeypatch):
    """The property the whole seam rests on."""
    pinned = tmp_path / "pinned-state"
    monkeypatch.setenv(STATE_DIR_ENV, str(pinned))

    assert state_root(ROOT) == pinned, (
        "state_root ignored an absolute pin and fell back to the project root, "
        "so nothing in this suite is isolated from the operator's live state")
    assert state_path(ROOT, "abc") == pinned / "checkpoint-abc.json"


def test_an_unset_pin_leaves_the_production_path_exactly_as_it_was(monkeypatch):
    """The seam must be invisible in production, or it is a behaviour change.

    This is the jaw for the test above: a `state_root` that returned the pin
    unconditionally, or that returned some third path, would satisfy it while
    breaking every hook on the operator's machine.
    """
    monkeypatch.delenv(STATE_DIR_ENV, raising=False)

    assert state_root(ROOT) == ROOT / ".claude" / "state"
    assert state_path(ROOT, "abc") == ROOT / ".claude" / "state" / "checkpoint-abc.json"


def test_the_pin_does_not_re_aim_a_project_root_that_is_already_elsewhere(
    tmp_path, monkeypatch
):
    """The narrowing, and it was learned the expensive way.

    The first version of this seam redirected unconditionally. It broke 14
    tests, and every one of them had already isolated itself properly. The
    sharpest was `test_state_lands_in_the_consumers_repository`, which builds a
    fake "someone else's repository", drives the hook at it, and asserts the
    state landed THERE - the whole plugin-bundle contract. A global pin
    overrode the destination that test exists to verify.

    Same shape as the mistake this suite already made with `HEADING_OS_DATA`: a
    global scratch pin does not create a sandbox, it moves the guarded thing
    onto the sandbox.

    The harm this seam exists to prevent is a write into the OPERATOR'S OWN
    `.claude/state/`. A project root already pointing somewhere else is already
    safe, so re-aiming it can only break a caller that chose deliberately.
    """
    someone_elses_repo = tmp_path / "someones-repo"
    someone_elses_repo.mkdir()
    monkeypatch.setenv(STATE_DIR_ENV, str(tmp_path / "pinned-state"))

    assert state_root(someone_elses_repo) == someone_elses_repo / ".claude" / "state", (
        "the pin re-aimed a project root that was already outside this clone. "
        "A plugin installed in a consumer's repository must write into THAT "
        "repository, and this is how that contract gets silently broken.")

    # The jaw: the same pin, on THIS clone, must still redirect. Without this
    # line a `state_root` that ignored the pin entirely would satisfy the
    # assertion above, and the seam would be gone.
    assert state_root(ROOT) == tmp_path / "pinned-state"


def test_a_relative_pin_is_refused_and_announced(tmp_path, monkeypatch, capsys):
    """Relative resolves against the harness's CWD, which is nobody's choice.

    Two harms, and the second is the one that bites later. It can land anywhere
    on disk; and if it lands inside the engine clone anywhere except
    `.claude/state/`, those writes are untracked-and-not-ignored, so the
    pre-commit wall and the push wall both begin refusing.

    Refused rather than raised, deliberately. A checkpoint hook runs after the
    session context is discarded, so a refusal that propagates costs a handoff
    nobody can regenerate. Both halves are asserted: the fallback AND the
    announcement, because a silent fallback is how a typo survives for a month.
    """
    monkeypatch.setenv(STATE_DIR_ENV, "some/relative/dir")

    assert state_root(ROOT) == ROOT / ".claude" / "state"

    err = capsys.readouterr().err
    assert "relative" in err.lower(), (
        f"the relative pin was ignored in silence; stderr was {err!r}")
    assert "some/relative/dir" in err, "the announcement did not name the value"


# ============================================================
# The second copy
# ============================================================

def _dispatch_module(env_value: str | None):
    """Import `_dispatch.py` fresh under a chosen environment.

    Fresh every time because its state constants are frozen at import, which is
    correct for a hook the harness starts as a subprocess and is exactly why a
    cached module would measure the wrong thing here.
    """
    if env_value is None:
        os.environ.pop(STATE_DIR_ENV, None)
    else:
        os.environ[STATE_DIR_ENV] = env_value
    spec = importlib.util.spec_from_file_location("_dispatch_state_probe", DISPATCH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("relative", [False, True])
def test_the_dispatch_copy_of_the_resolver_agrees_with_the_canonical_one(
    tmp_path, monkeypatch, relative
):
    """`.claude/hooks/_dispatch.py` reads the pin itself. It must not drift.

    The duplication is deliberate: that hook runs on every Write, Edit,
    MultiEdit, NotebookEdit, Bash and Read, and importing `checkpoint_paths` at
    its module scope to reach one five-line resolver would charge the import to
    all of them.

    A second copy is the one that stops being fixed - this repository has
    already shipped "a fix that landed in one of two copies" once - so both
    branches are compared here, not just the happy one. The relative case is
    included because refusing a relative pin in one copy and joining it in the
    other is precisely the drift that would go unnoticed.
    """
    saved = os.environ.get(STATE_DIR_ENV)
    try:
        value = "some/relative/dir" if relative else str(tmp_path / "pinned")
        monkeypatch.setenv(STATE_DIR_ENV, value)
        module = _dispatch_module(value)

        expected = state_root(module.WORKSPACE)
        assert module._state_dir() == expected, (
            f"_dispatch._state_dir() answered {module._state_dir()} while "
            f"checkpoint_paths.state_root() answered {expected}. The two copies "
            f"of this resolver have drifted.")
    finally:
        if saved is None:
            os.environ.pop(STATE_DIR_ENV, None)
        else:
            os.environ[STATE_DIR_ENV] = saved


def test_every_dispatch_state_directory_goes_through_the_resolver():
    """Four constants, and a fifth added later would be the leak again.

    Asked of the SOURCE by AST rather than of the imported values, because the
    values are already resolved by the time a test can read them: an import-time
    `WORKSPACE / ".claude" / "state" / "new-thing"` would produce a perfectly
    plausible Path and no test would notice.
    """
    tree = ast.parse(DISPATCH.read_text(encoding="utf-8"), filename=str(DISPATCH))
    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or node.value != "state":
            continue
        offenders.append(node.lineno)

    # `_state_dir` itself contains the only legitimate spelling of the literal.
    resolver_lines = {
        n.lineno
        for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name == "_state_dir"
        for n in ast.walk(n)
        if isinstance(n, ast.Constant) and n.value == "state"
    }
    stray = sorted(set(offenders) - resolver_lines)
    assert not stray, (
        f"line(s) {stray} of .claude/hooks/_dispatch.py build a `.claude/state/` "
        f"path without going through `_state_dir()`, so that directory is not "
        f"redirected by HEADING_OS_STATE_DIR and a test will write into the "
        f"operator's live tree.")


# ============================================================
# The pin, as the suite actually arms it
# ============================================================

def test_the_conftest_pin_is_an_assignment_and_not_a_setdefault():
    """Isolation a stray shell variable can switch off is not isolation.

    The same property `tests/test_a_timezone_pin_a_stray_variable_could_switch_
    off.py` holds for HEADING_OS_TZ, for the same reason, and it is asked of the
    AST so a comment claiming the assignment cannot satisfy it.
    """
    tree = ast.parse(CONFTEST.read_text(encoding="utf-8"), filename=str(CONFTEST))
    assignments = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Subscript)
                and isinstance(t.value, ast.Attribute)
                and t.value.attr == "environ"
                and isinstance(t.slice, ast.Constant)
                and t.slice.value == STATE_DIR_ENV
                for t in node.targets)
    ]
    assert assignments, (
        f"tests/conftest.py never ASSIGNS os.environ[{STATE_DIR_ENV!r}]. If it "
        f"was changed to setdefault, a shell that exports the name silently "
        f"disarms the whole suite's state isolation.")
    # Module scope plus the autouse re-arm. One alone is not enough: module
    # scope covers import time only, and a test that pops the name in a
    # `finally` leaves every later test writing to the live tree.
    assert len(assignments) >= 2, (
        f"only {len(assignments)} assignment(s) of {STATE_DIR_ENV}; expected the "
        f"module-scope pin AND the re-arm inside `_isolate_runtime_logs`")


def test_the_pinned_directory_is_ignored_by_git():
    """It lives inside the clone, so an untracked write here stops the push.

    `engine_guard._files_git_would_carry` enumerates tracked plus
    untracked-not-ignored. `.claude/state/` is invisible to it only because
    `.gitignore` covers that directory whole. The test store must be covered
    too, or a routine test run starts failing the push wall.
    """
    pinned = os.environ.get(STATE_DIR_ENV)
    assert pinned, (
        f"{STATE_DIR_ENV} is unset inside the suite, so the conftest pin is not "
        f"armed and every other test in this file is measuring the wrong thing")

    probe = Path(pinned) / "gitignore-probe.json"
    probe.parent.mkdir(parents=True, exist_ok=True)
    probe.write_text("{}", encoding="utf-8")
    try:
        result = subprocess.run(  # nosec B603 - fixed argv
            ["git", "check-ignore", "-q", str(probe)],
            cwd=ROOT, capture_output=True, check=False)
        assert result.returncode == 0, (
            f"{probe} is NOT gitignored. Every test run writes there, so the "
            f"pre-commit wall and the push wall will both start refusing on "
            f"untracked files nobody authored deliberately.")
    finally:
        probe.unlink(missing_ok=True)


# ============================================================
# The incident itself
# ============================================================

def test_a_hook_driven_with_a_payload_cwd_writes_nothing_into_the_live_tree(tmp_path):
    """The headline, and the only test here that reproduces the real defect.

    A subprocess, the real hook, and the exact payload shape that beat
    `CLAUDE_PROJECT_DIR`: `cwd` set to the engine root. Before the fix this
    wrote `checkpoint-<slug>.json` into the operator's live `.claude/state/`.

    Both halves are asserted. "Nothing appeared in the live tree" is satisfied
    by a hook that crashed, that wrote nowhere, or that was never invoked, so
    the pinned directory must show the file instead. A one-sided version of this
    test is the exact defect class the 2026-08-31 audit found 33 times.

    ## Two properties this test needs that are easy to get wrong

    The slug is UNIQUE PER RUN. A fixed one cannot distinguish this run's write
    from a leftover, so the first version opened by refusing to run at all if the
    file already existed - which turns one bad run into a permanently red test.

    And it CLEANS UP in a `finally`. Measured while writing this file: mutating
    the hook back to spelling the path itself made it write into the live
    `.claude/state/` exactly as intended, the assertion caught it, and the file
    STAYED THERE, because a failing assertion ends the test before any cleanup
    that follows it. A guard whose failure path litters the directory it guards
    is a guard that degrades the thing it protects every time it does its job.
    Deleting is safe here and only here: the unique slug proves the file is this
    test's own, so there is no real session state that could be caught by it.
    """
    slug = f"a-state-seam-probe-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    live_file = LIVE_STATE / f"checkpoint-{slug}.json"

    pinned = tmp_path / "pinned-state"
    overlay = tmp_path / "data"
    overlay.mkdir()
    env = dict(
        os.environ,
        HEADING_OS_STATE_DIR=str(pinned),
        HEADING_OS_DATA=str(overlay),
    )
    payload = {
        "session_id": slug,
        "cwd": str(ROOT),                      # the field that beat the old pin
        "hook_event_name": "PostCompact",
        "transcript_path": str(tmp_path / "absent.jsonl"),
    }

    try:
        result = subprocess.run(  # nosec B603 - fixed argv
            [sys.executable, str(HOOK)],
            input=json.dumps(payload), text=True,
            capture_output=True, cwd=str(ROOT), env=env, timeout=120, check=False)

        written = (sorted(p.name for p in pinned.rglob("checkpoint-*.json"))
                   if pinned.exists() else [])
        assert written, (
            f"the hook wrote no checkpoint under the pinned directory, so the "
            f"absence check below proves nothing. exit={result.returncode} "
            f"stdout={result.stdout[-800:]!r} stderr={result.stderr[-800:]!r}")

        assert not live_file.exists(), (
            f"the hook wrote {live_file} into the operator's LIVE state "
            f"directory despite HEADING_OS_STATE_DIR being pinned. The "
            f"payload's `cwd` has beaten the pin again, which is the original "
            f"defect.")
    finally:
        # The unique slug is what makes this safe: nothing but this test can
        # have written these two names. See the docstring for the run that
        # proved the cleanup necessary.
        live_file.unlink(missing_ok=True)
        live_file.with_suffix(".json.lock").unlink(missing_ok=True)
