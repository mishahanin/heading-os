#!/usr/bin/env python3
"""The runaway-loop guard undercounted exactly when tool calls overlapped.

`check_rate_limit` in `.claude/hooks/_dispatch.py` counts Write and Edit calls
per day and BLOCKS past `RATE_LIMIT_HARD`. It did `_load_rate_state()`, mutated,
`_save_rate_state(state)`. The SAVE is atomic — a per-pid staging file and an
`os.replace` — but the read-modify-write SEQUENCE was not, and nothing serialised
it. Two hook processes read the same state, each appended its own entry, and
whichever replaced second won. The earlier process's write was gone: its entry in
`recent`, and its increment.

That is the wrong direction for this particular counter. The hook runs once per
tool call, so lost updates arrive in proportion to how many tool calls OVERLAP,
which is the condition a runaway loop produces. The guard undercounted hardest in
the scenario it exists to catch.

MEASURED 2026-09-04 in this worktree, against the code before the fix. Sixteen
hook subprocesses launched at once, each writing a unique marker, all pointed at
ONE `WS_RATE_LIMIT_STATE` file — the shape `pytest -n auto` produces. Five
trials, markers surviving out of sixteen:

    15, 12, 14, 13, 12        (RED 5/5)

`count` in the state file equalled the number of surviving markers exactly in
every trial, which is what rules out the other candidate: `recent` is capped at
`RATE_LIMIT_LOOP_WINDOW = 20`, sixteen is under twenty, so eviction cannot
explain it and `count` would not have moved with it. A lost update can.

Widened afterwards on an idle machine, the two versions of the file swapped in
place and run alternately: fifteen trials against the unchanged code, 240 hook
calls, 70 of them lost — 29%, per trial between 6% and 44%. Twenty of twenty
trials that reported a per-trial verdict were red. Against the fixed code, 400
calls over twenty-five trials, none lost.

`check_tool_budget` shares the same file through `tool_history` and had the same
unlocked pair, so it lost events in the same trials — trial 1 above came back
with 12 in `count` and 10 in `tool_history`, two entries short of even the
already-short count.

**Why five trials and not one.** A single trial is not evidence: the same probe
run in HELM the day before came back 13/16, 16/16, 13/16, so one trial passes by
luck about a third of the time. Five independent trials put a false green at
about (1/3)^5, near 0.4%, and every pre-fix run of this file was red 5 out of 5.

How this surfaced: a full-suite run failed once on
`tests/test_denial_log_isolation.py::test_the_suite_does_not_spend_the_operators_daily_write_allowance`
and passed on the next run. That test asserts a marker unique to its own hook
call survives in a state file every xdist worker shares. It was telling the
truth.
"""
from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

HOOK = ROOT / ".claude" / "hooks" / "_dispatch.py"

# The hook is not importable as a module (`_dispatch` under a dotted `.claude`
# directory), so production drives it as a script. These probes do the same.
RUNNER = (
    "import sys, runpy; sys.argv = [sys.argv[1]]; "
    "runpy.run_path(sys.argv[0], run_name='__main__')"
)

# Sixteen, because that is what `-n auto` produces on this machine and it is the
# width at which the defect was measured. Under the loop window of 20, so an
# eviction cannot be mistaken for a lost update.
CONCURRENCY = 16

# See the module docstring: one trial passes by luck about a third of the time
# against the unfixed code.
TRIALS = 5


def _drive_hook(state_file: Path, payload: dict) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["WS_RATE_LIMIT_STATE"] = str(state_file)
    return subprocess.run(
        [sys.executable, "-c", RUNNER, str(HOOK)],
        input=json.dumps(payload), capture_output=True, text=True,
        cwd=str(ROOT), env=env, timeout=180,
    )


def _fan_out(state_file: Path, payloads: list[dict]) -> dict:
    """Launch every payload at once against one state file; return the state."""
    with ThreadPoolExecutor(max_workers=len(payloads)) as pool:
        results = list(pool.map(lambda p: _drive_hook(state_file, p), payloads))
    failed = [(r.returncode, r.stderr[-400:]) for r in results if r.returncode != 0]
    assert not failed, f"{len(failed)} hook processes exited non-zero: {failed[:2]}"
    assert state_file.is_file(), (
        f"{state_file} was never written, so the counters ran nowhere and every "
        f"assertion below would pass against a guard that is off, not correct")
    return json.loads(state_file.read_text(encoding="utf-8"))


def _write_payloads(n: int) -> tuple[list[dict], list[str]]:
    markers = [f"rate-race-{uuid.uuid4().hex}.txt" for _ in range(n)]
    payloads = [
        {"tool_name": "Write",
         "tool_input": {"file_path": str(ROOT / "outputs" / "scratch" / m),
                        "content": "a write made to be counted"}}
        for m in markers
    ]
    return payloads, markers


# ------------------------------------------------- the guard, under concurrency

def test_every_concurrent_write_is_counted_by_the_runaway_loop_guard(tmp_path):
    """The headline. Sixteen at once, five times, and none may be lost.

    Asserted on both halves of the state the guard reads: the `recent` window
    each call appends its own marker to, and the `count` the hard cap is
    compared against. They moved together before the fix and they must move
    together after it.
    """
    shortfalls = []
    for trial in range(TRIALS):
        state_file = tmp_path / f"trial-{trial}" / "dispatch-rate.json"
        payloads, markers = _write_payloads(CONCURRENCY)

        state = _fan_out(state_file, payloads)

        blob = json.dumps(state)
        survived = sum(1 for m in markers if m in blob)
        if survived != CONCURRENCY or state.get("count") != CONCURRENCY:
            shortfalls.append(
                f"trial {trial}: {survived}/{CONCURRENCY} markers in `recent`, "
                f"count={state.get('count')}")

    assert not shortfalls, (
        f"{len(shortfalls)} of {TRIALS} trials lost events from the runaway-loop "
        f"guard's own counter: {shortfalls}. The read-modify-write in "
        f"check_rate_limit is not serialised, so overlapping hook processes "
        f"overwrite each other — and overlap is the condition a runaway loop "
        f"produces, so the cap undercounts hardest in the case it exists to "
        f"catch.")


def test_every_concurrent_call_is_counted_by_the_sibling_tool_budget(tmp_path):
    """The neighbour on the same file. Fixing one and leaving the other racing
    would move the defect rather than close it.

    Driven with `Read`, which `check_rate_limit` ignores and `check_tool_budget`
    counts, so a failure here can only be the sibling's own load-save pair.
    """
    shortfalls = []
    for trial in range(TRIALS):
        state_file = tmp_path / f"budget-{trial}" / "dispatch-rate.json"
        payloads = [
            {"tool_name": "Read",
             "tool_input": {"file_path": str(ROOT / "outputs" / "scratch" /
                                             f"rate-race-{uuid.uuid4().hex}.txt")}}
            for _ in range(CONCURRENCY)
        ]

        state = _fan_out(state_file, payloads)

        history = state.get("tool_history", [])
        if len(history) != CONCURRENCY:
            shortfalls.append(f"trial {trial}: {len(history)}/{CONCURRENCY} entries")
        # The write counter must NOT have moved: Read is not a write, and a
        # `check_rate_limit` that counted it would inflate the daily cap with
        # reads and mask the file-write loops it exists to catch.
        assert state.get("count", 0) == 0, (
            f"trial {trial}: check_rate_limit counted {state.get('count')} Read "
            f"calls against the daily WRITE cap")

    assert not shortfalls, (
        f"{len(shortfalls)} of {TRIALS} trials lost entries from "
        f"check_tool_budget's rolling history: {shortfalls}. It shares "
        f"dispatch-rate.json with check_rate_limit and had the same unlocked "
        f"load-modify-save.")


def test_one_call_on_its_own_still_counts_exactly_one(tmp_path):
    """The positive anchor.

    Without it, every assertion above is satisfied by a lock so wide that no
    hook ever counts anything, or by a guard that refuses the call outright.
    """
    state_file = tmp_path / "dispatch-rate.json"
    payloads, markers = _write_payloads(1)

    proc = _drive_hook(state_file, payloads[0])

    assert proc.returncode == 0, f"the hook exited {proc.returncode}: {proc.stderr}"
    state = json.loads(state_file.read_text(encoding="utf-8"))
    assert state["count"] == 1
    assert markers[0] in json.dumps(state["recent"])
    assert len(state.get("tool_history", [])) == 1


# --------------------------------------------------- the lock, in the source

def _dispatch_tree() -> ast.Module:
    return ast.parse(HOOK.read_text(encoding="utf-8"))


def _function(name: str) -> ast.FunctionDef:
    for node in ast.walk(_dispatch_tree()):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{name} is gone from {HOOK}")


@pytest.mark.parametrize("check", ["check_rate_limit", "check_tool_budget"])
def test_the_load_and_the_save_sit_inside_one_hold_of_the_lock(check):
    """Asked of the AST, not of the text.

    A substring scan for `_rate_state_lock` passes against a file that merely
    mentions it in a comment, and passes against a `with` block that holds the
    lock for the load and releases it before the save — which is the defect
    wearing a lock. What has to be true is that BOTH calls are lexically inside
    the same `with _rate_state_lock()` body.

    Both checks are asked, because they share one state file and a fix that
    landed in one of two callers is this repository's most repeated defect
    shape.
    """
    func = _function(check)

    holds = [
        node for node in ast.walk(func)
        if isinstance(node, ast.With)
        and any(isinstance(item.context_expr, ast.Call)
                and getattr(item.context_expr.func, "id", None) == "_rate_state_lock"
                for item in node.items)
    ]
    assert len(holds) == 1, (
        f"{check} holds {len(holds)} `_rate_state_lock()` blocks; expected "
        f"exactly one covering its whole read-modify-write")

    called_inside = {
        node.func.id for node in ast.walk(holds[0])
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert {"_load_rate_state", "_save_rate_state"} <= called_inside, (
        f"{check} calls {sorted(called_inside & {'_load_rate_state', '_save_rate_state'})} "
        f"inside the lock. A hold that covers the load but not the save leaves "
        f"the window the lock was added to close.")


def test_the_lock_is_a_sidecar_and_never_the_state_file_itself():
    """`_save_rate_state` promotes a staging file with `os.replace`.

    A lock taken on the state file locks an INODE, and after the first save that
    inode is no longer the file anyone is reading. The sidecar `<name>.lock` is
    never replaced, so its identity is stable for as long as the state file has
    a name.
    """
    dispatch = _load_dispatch()
    state_file = Path("/nowhere/dispatch-rate.json")
    dispatch.RATE_LIMIT_STATE_FILE = state_file

    assert dispatch._rate_lock_path() == state_file.with_name(
        "dispatch-rate.json.lock")


def test_the_lock_path_follows_a_redirected_state_file(tmp_path):
    """Resolved at call time, never frozen at import.

    `WS_RATE_LIMIT_STATE` points the whole suite at one file, and several tests
    move `RATE_LIMIT_STATE_FILE` with `monkeypatch.setattr` after import. A lock
    path computed once at module scope would keep guarding the file the hook was
    imported with, so two runs against different state files would serialise
    against each other and neither would be serialised against itself.
    """
    dispatch = _load_dispatch()
    dispatch.RATE_LIMIT_STATE_FILE = tmp_path / "redirected.json"

    assert dispatch._rate_lock_path() == tmp_path / "redirected.json.lock"


# ------------------------------------------------ the two degradation paths

def test_the_wait_is_bounded_and_the_counter_proceeds_unlocked(tmp_path,
                                                               monkeypatch,
                                                               capsys):
    """A hook must never block a tool call forever.

    So the wait expires and the check counts UNLOCKED rather than raising or
    hanging. That direction is deliberate: this counter is telemetry with a cap
    on it, not an authorisation boundary, and a stuck lock file that blocked
    every Write in the workspace would be a far worse failure than an
    undercount. Expiry is loud — a line on stderr — because a silent degradation
    is how the unlocked years happened.

    The competing hold is taken on a second file description in this same
    process, which `flock` treats as a different holder.
    """
    from scripts.utils.checkpoint_paths import file_lock

    dispatch = _load_dispatch()
    state_file = tmp_path / "dispatch-rate.json"
    dispatch.RATE_LIMIT_STATE_FILE = state_file
    monkeypatch.setattr(dispatch, "RATE_LOCK_WAIT_SECONDS", 0.1)

    payload = {"tool_name": "Write",
               "tool_input": {"file_path": str(tmp_path / "x.md"), "content": "x"}}

    with file_lock(dispatch._rate_lock_path(), label="test-holder") as held:
        assert held, "the test never took the lock, so nothing was contended"
        started = time.monotonic()
        dispatch.check_rate_limit(payload)
        waited = time.monotonic() - started

    assert waited < 2.0, (
        f"the check sat on the lock for {waited:.2f}s against a 0.1s bound; a "
        f"hook that waits without a bound wedges the tool call")
    assert json.loads(state_file.read_text(encoding="utf-8"))["count"] == 1, (
        "the write was not counted at all. On timeout the check must degrade to "
        "the unlocked behaviour it had before the lock existed, not skip the "
        "count")
    assert "busy" in capsys.readouterr().err, (
        "the degradation was silent; an expiry nobody can see is how an "
        "unserialised counter survives another year")


def test_the_counter_still_counts_where_fcntl_is_absent(tmp_path, monkeypatch):
    """Windows has no `fcntl`, and this file has a cross-platform history.

    The degradation is explicit rather than an ImportError: `file_lock` yields
    False and the block runs, so behaviour on a platform without `flock` is
    exactly what it was before the lock existed. Simulated by hiding the module
    from the import machinery, which is what a non-POSIX interpreter does.
    """
    dispatch = _load_dispatch()
    state_file = tmp_path / "dispatch-rate.json"
    dispatch.RATE_LIMIT_STATE_FILE = state_file
    monkeypatch.setitem(sys.modules, "fcntl", None)

    payload = {"tool_name": "Write",
               "tool_input": {"file_path": str(tmp_path / "x.md"), "content": "x"}}
    dispatch.check_rate_limit(payload)
    dispatch.check_rate_limit(payload)

    assert json.loads(state_file.read_text(encoding="utf-8"))["count"] == 2, (
        "without fcntl the check counted nothing. The lock is an improvement "
        "on a best-effort counter, never a precondition for it")


def _load_dispatch():
    """Import `.claude/hooks/_dispatch.py` under a private module name.

    By PATH rather than by package name: the file is not importable as a module
    under a dotted `.claude` directory, and binding a package name for it would
    be the startup-hook defect this workspace already records.
    """
    import importlib.util
    spec = importlib.util.spec_from_file_location("_rate_race_dispatch", HOOK)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
