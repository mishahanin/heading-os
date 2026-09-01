#!/usr/bin/env python3
"""Two sessions overlapping in the registry read-modify-write lost one of them.

`session_start` and `session_end` in `.claude/hooks/bridge-hook.py` each load the
WHOLE session registry, mutate their own key, and write the whole thing back:

    reg = _load_registry()        # read
    reg[sid] = {...}              # modify
    _atomic_write(REGISTRY, ...)  # write

`_atomic_write` makes each WRITE indivisible. That is a different guarantee from
making a read and the write that follows it indivisible, and no lock was taken
across the span. Two sessions overlapping there lose one of the two mutations,
and `os.replace` reports nothing when it happens. `session_end` is the worse
half: it writes back a copy loaded before a neighbour's `session_start` landed,
so it DELETES the entry of a session that is still alive. The registry lives at
`~/.claude/state/active-sessions.json`, a per-user path shared by every project
on the machine, so the racers need not even be in one workspace.

Why the defect was invisible. `tests/test_bridge_hook_session_registry.py` has
eight tests and they all pass, but its `_run` helper drives the hook ONE PROCESS
AT A TIME. Nothing in the suite ever overlapped two invocations, so nothing
measured the span, and `codegraph` reported no covering tests for either
function. MEASURED 2026-08-31 on this machine: one whole hook run (interpreter
start, the `checkpoint_paths` import, read, modify, write) takes 0.05 s to 0.10 s
end to end, so the exposed span is a fraction of that. A test that launched N
processes and hoped for an overlap inside a window that small would pass over a
broken lock nearly every run, which is worse than no test.

So the overlap here is FORCED rather than raced. The test itself plays the
neighbour: it takes the very lock the hook takes, does its own read, holds the
lock while the real hook process is launched, and only then writes. With the fix
the hook waits and reads the neighbour's result; without it the hook slips
between the neighbour's read and the neighbour's write, which is the defect,
reproduced by construction rather than by luck. MEASURED the same day with
`with _registry_lock():` removed from both functions: both concurrency tests
below failed on every one of ten runs, and neither failed once with the lock in
place.

What these tests pin:

  * `session_end` under an overlapping `session_start` keeps the neighbour's
    live entry and still removes its own.
  * `session_start` under an overlapping `session_end` keeps its own registration
    instead of having it deleted by a stale copy.
  * the plain unlocked shape really does lose a write, so the two tests above
    are not proving something that cannot fail.
  * ordinary single-process behaviour is unchanged, and this clone really does
    take the lock, so the concurrency cases are not silently running the
    degraded path.
  * the degraded path (no `scripts/utils/checkpoint_paths.py`, which is the
    public-clone case) still registers and deregisters the session, and SAYS the
    write was unserialised.

That last one was a defect of its own, found by writing the test for it. The
comment above `_CP` promised "one line goes to stderr saying the registry write
is unserialised. Loud degradation, never a silent one", and the only branch that
printed was the one where the file WAS found and its import raised. MEASURED
2026-08-31 by copying the hook to a directory with no `scripts/utils` above it:
`session-start` exited 0, wrote the entry, and printed 0 bytes of stderr. The
promise was kept for the rare case and broken for the ordinary one, so
`_registry_lock` now prints on the `_CP is None` branch as well.
"""
from __future__ import annotations

import ast
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from scripts.utils import checkpoint_paths as CP

ROOT = Path(__file__).resolve().parent.parent
HOOK = ROOT / ".claude" / "hooks" / "bridge-hook.py"

# How long the neighbour holds the lock after its read, giving the racing hook
# process every chance to interleave. It is an upper bound, not a fixed sleep:
# `_wait_for_a_racer` returns the moment the registry changes under it, so the
# broken case costs about 0.1 s and only the correct case pays the full budget.
# Kept well under `CP.LOCK_WAIT_SECONDS` (2.0 s), because past that bound the
# hook stops waiting and writes unlocked by design, and a test that ran past it
# would be measuring the degradation rather than the lock.
HOLD_BUDGET = 0.8


@pytest.fixture
def home(tmp_path, monkeypatch):
    """An isolated HOME, so no test here touches the operator's registry."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("USERPROFILE", raising=False)
    return tmp_path


def _registry_path(home: Path) -> Path:
    return home / ".claude" / "state" / "active-sessions.json"


def _lock_path(home: Path) -> Path:
    """Derived exactly as the hook derives it, never spelled out a second time.

    A copy would drift the day the hook renames its sidecar, and the neighbour
    would then hold a lock nothing else contends for: every test below would go
    green while serialising nothing.
    """
    return _registry_path(home).with_suffix(".lock")


def _read(home: Path) -> dict:
    path = _registry_path(home)
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def _write(home: Path, reg: dict) -> None:
    """The same write shape the hook uses: whole file, replaced in one step."""
    path = _registry_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".neighbour-tmp")
    tmp.write_text(json.dumps(reg, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _seed(home: Path, session_ids: list[str]) -> None:
    _write(home, {sid: {"session_id": sid, "cwd": "/work/tree"} for sid in session_ids})


def _run(sub: str, payload: dict, home: Path) -> subprocess.CompletedProcess:
    env = dict(os.environ, HOME=str(home))
    env.pop("USERPROFILE", None)
    return subprocess.run([sys.executable, str(HOOK), sub],
                          input=json.dumps(payload), capture_output=True,
                          text=True, env=env, timeout=60)


def _wait_for_a_racer(home: Path, budget: float) -> bool:
    """Return as soon as the registry changes, or when `budget` expires.

    Bounded on `monotonic`, so no wait here can outlive the budget whatever the
    wall clock does. Returns whether a change was seen, which is what the
    premise test reads.
    """
    before = _registry_path(home).read_bytes()
    deadline = time.monotonic() + budget
    while time.monotonic() < deadline:
        if _registry_path(home).read_bytes() != before:
            return True
        time.sleep(0.01)
    return False


def _neighbour(home: Path, mutate, took_its_read: threading.Event,
               errors: list, *, locked: bool) -> None:
    """One well-behaved read-modify-write, with the span held wide open.

    This is the OTHER session: it does exactly what the hook does, under exactly
    the lock the hook takes, and holds it across the span. `locked=False` is the
    same body with no lock, used only to pin that the shape can lose a write.

    A failure is appended and reported by the caller's first assertion rather
    than raised into a thread nobody is watching, which is where a raised one
    would land.
    """
    def body() -> None:
        reg = _read(home)
        took_its_read.set()
        _wait_for_a_racer(home, HOLD_BUDGET)
        mutate(reg)
        _write(home, reg)

    try:
        if locked:
            with CP.file_lock(_lock_path(home), label="test-neighbour"):
                body()
        else:
            body()
    except Exception as exc:  # noqa: BLE001 - recorded, asserted on by the caller
        errors.append(exc)
        took_its_read.set()


def _drive(home: Path, neighbour_mutate, racer_sub: str, racer_payload: dict,
           *, locked: bool = True) -> subprocess.CompletedProcess:
    """Force the overlap: neighbour reads, then the real hook runs, then the
    neighbour writes. Every wait is bounded and every thread is joined."""
    took_its_read = threading.Event()
    errors: list = []
    racer_result: dict = {}

    neigh = threading.Thread(
        target=_neighbour,
        args=(home, neighbour_mutate, took_its_read, errors),
        kwargs={"locked": locked},
    )
    neigh.start()
    try:
        assert took_its_read.wait(timeout=15), "the neighbour never took its read"

        def race() -> None:
            try:
                racer_result["proc"] = _run(racer_sub, racer_payload, home)
            except Exception as exc:  # noqa: BLE001 - recorded, asserted below
                errors.append(exc)

        racer = threading.Thread(target=race)
        racer.start()
        racer.join(timeout=90)
        assert not racer.is_alive(), "the hook process never finished"
    finally:
        neigh.join(timeout=90)

    assert not neigh.is_alive(), "the neighbour never released the lock"
    assert not errors, errors
    proc = racer_result["proc"]
    assert proc.returncode == 0, f"the hook exited {proc.returncode}: {proc.stderr}"
    return proc


def _add_a_starting_session(sid: str):
    def mutate(reg: dict) -> None:
        reg[sid] = {"session_id": sid, "cwd": "/work/tree"}
    return mutate


def _remove_an_ending_session(sid: str):
    def mutate(reg: dict) -> None:
        reg.pop(sid, None)
    return mutate


# ============================================================
# session_end is the damaging half: it deletes a live entry
# ============================================================

@pytest.mark.slow
def test_session_end_does_not_delete_a_session_that_just_started(home):
    """sess-C starts while sess-A ends. Both outcomes must survive.

    Without the lock the hook reads {A, B}, writes {B}, and the neighbour then
    writes back the {A, B} it read plus C. sess-A's departure is lost, and in the
    mirror-image interleaving it is sess-C's arrival that is lost. Either way one
    of two mutations went nowhere and nothing said so.
    """
    _seed(home, ["sess-A", "sess-B"])
    _drive(home, _add_a_starting_session("sess-C"),
           "session-end", {"session_id": "sess-A", "cwd": "/work/tree"})
    reg = _read(home)
    assert sorted(reg) == ["sess-B", "sess-C"], (
        "the overlapping read-modify-write lost a mutation. Expected "
        f"['sess-B', 'sess-C'], got {sorted(reg)}: "
        "'sess-A' present means the session_end was thrown away, "
        "'sess-C' absent means a live session was deregistered"
    )


@pytest.mark.slow
def test_session_start_registration_is_not_deleted_by_a_session_ending(home):
    """The same span from the other side, and this is the shape that hurts.

    sess-C registers while sess-A deregisters. Without the lock the hook writes
    {A, B, C}, and the neighbour then writes back the {A, B} it read minus A,
    leaving {B}: sess-C is running, its SessionStart hook exited 0, and it has no
    entry. Anything reading the registry to find live sessions cannot see it.
    """
    _seed(home, ["sess-A", "sess-B"])
    _drive(home, _remove_an_ending_session("sess-A"),
           "session-start", {"session_id": "sess-C", "cwd": "/work/tree"})
    reg = _read(home)
    assert sorted(reg) == ["sess-B", "sess-C"], (
        "the overlapping read-modify-write lost a mutation. Expected "
        f"['sess-B', 'sess-C'], got {sorted(reg)}: "
        "'sess-C' absent means the live session's own registration was deleted "
        "by a stale copy, 'sess-A' present means the session_end was thrown away"
    )
    assert _read(home)["sess-C"]["cwd"] == "/work/tree", (
        "the surviving entry is not the one the hook wrote"
    )


@pytest.mark.slow
def test_the_unlocked_shape_really_does_lose_a_mutation(home):
    """Pins the premise of the two tests above.

    They can only prove the lock excludes if the same overlap WITHOUT a lock
    loses a write. Here the neighbour skips the lock, so the hook (which still
    takes it) has nothing to wait for and lands inside the neighbour's span. If
    this ever stops losing the mutation, the two tests above have stopped
    measuring anything and must be re-derived rather than trusted.
    """
    _seed(home, ["sess-A", "sess-B"])
    _drive(home, _remove_an_ending_session("sess-A"),
           "session-start", {"session_id": "sess-C", "cwd": "/work/tree"},
           locked=False)
    assert sorted(_read(home)) == ["sess-B"], (
        "the unlocked overlap kept both mutations, so this file's premise no "
        f"longer holds: {sorted(_read(home))}"
    )


# ============================================================
# The other direction: one process at a time is unchanged
# ============================================================

def test_a_single_session_still_registers_and_deregisters(home):
    """The lock must not have changed what one process alone does."""
    assert _run("session-start", {"session_id": "sess-A", "cwd": "/work/tree"},
                home).returncode == 0
    assert _run("session-start", {"session_id": "sess-B", "cwd": "/work/tree"},
                home).returncode == 0
    assert sorted(_read(home)) == ["sess-A", "sess-B"]
    assert _run("session-end", {"session_id": "sess-A", "cwd": "/work/tree"},
                home).returncode == 0
    assert sorted(_read(home)) == ["sess-B"]
    assert _read(home)["sess-B"]["session_id"] == "sess-B"


def test_this_clone_really_takes_the_lock(home):
    """Without this, the concurrency tests could be green over the degraded path.

    `_CP` is optional by design, and when it is absent `_registry_lock` is a
    no-op. A run in which the hook found no `checkpoint_paths` would serialise
    nothing, and the neighbour would be contending with itself. The sidecar's
    presence is what distinguishes the two, and it is asserted here rather than
    inside a concurrency test because a lock file existing is not evidence that
    the lock excludes anything.
    """
    _run("session-start", {"session_id": "sess-A", "cwd": "/work/tree"}, home)
    lock = _lock_path(home)
    assert lock.is_file(), (
        f"no {lock.name} beside the registry: this clone took no lock, so the "
        "concurrency tests in this file are measuring nothing"
    )
    assert lock != _registry_path(home), "the lock is the state file itself"
    assert sorted(_read(home)) == ["sess-A"], "the registry did not survive"


def test_the_lock_sidecar_is_never_the_registry(home):
    """Locking the file the hook is about to `os.replace` would lock an inode
    that stops being the file."""
    _run("session-start", {"session_id": "sess-A", "cwd": "/work/tree"}, home)
    assert _lock_path(home).name == "active-sessions.lock"
    assert _registry_path(home).name == "active-sessions.json"
    assert json.loads(_registry_path(home).read_text(encoding="utf-8"))["sess-A"]


# ============================================================
# The degraded path: no checkpoint_paths, which is a public clone
# ============================================================

@pytest.fixture
def hook_without_checkpoint_paths(tmp_path):
    """The hook, copied where its upward walk for `scripts/utils/` finds nothing.

    The walk is asserted to fail rather than assumed to, because an ancestor that
    happened to carry `scripts/utils/checkpoint_paths.py` would turn this into a
    second copy of the locked-path tests without saying so.
    """
    lonely = tmp_path / "public-clone" / "hooks"
    lonely.mkdir(parents=True)
    copied = lonely / "bridge-hook.py"
    shutil.copy2(HOOK, copied)
    for candidate in [copied.parent, *copied.parents]:
        assert not (candidate / "scripts" / "utils" / "checkpoint_paths.py").is_file(), (
            f"{candidate} carries checkpoint_paths, so this is not the degraded path"
        )
    return copied


def _run_copy(hook: Path, sub: str, payload: dict, home: Path):
    env = dict(os.environ, HOME=str(home))
    env.pop("USERPROFILE", None)
    return subprocess.run([sys.executable, str(hook), sub],
                          input=json.dumps(payload), capture_output=True,
                          text=True, env=env, timeout=60)


def test_the_registry_is_still_written_without_checkpoint_paths(
        hook_without_checkpoint_paths, tmp_path):
    """Failing to import a helper is not a reason to lose the entry."""
    home = tmp_path / "degraded-home"
    home.mkdir()
    proc = _run_copy(hook_without_checkpoint_paths, "session-start",
                     {"session_id": "sess-A", "cwd": "/work/tree"}, home)
    assert proc.returncode == 0, proc.stderr
    assert sorted(_read(home)) == ["sess-A"], (
        "the degraded hook lost the registration it exists to write"
    )
    assert not _lock_path(home).exists(), (
        "a lock sidecar appeared, so this run was not the degraded path"
    )
    proc = _run_copy(hook_without_checkpoint_paths, "session-end",
                     {"session_id": "sess-A", "cwd": "/work/tree"}, home)
    assert proc.returncode == 0, proc.stderr
    assert _read(home) == {}, "the degraded hook never deregistered the session"


def test_the_degraded_write_says_it_is_unserialised(
        hook_without_checkpoint_paths, tmp_path):
    """A silent degradation reads exactly like a serialised write.

    The comment above `_CP` promises this line. Until 2026-08-31 only the
    import-RAISED branch printed it, and an absent `scripts/utils/` (the public
    clone the option exists for) printed nothing at all: 0 bytes of stderr over a
    write that had no lock behind it.
    """
    home = tmp_path / "degraded-home"
    home.mkdir()
    for sub in ("session-start", "session-end"):
        proc = _run_copy(hook_without_checkpoint_paths, sub,
                         {"session_id": "sess-A", "cwd": "/work/tree"}, home)
        assert proc.returncode == 0, proc.stderr
        assert "not serialised" in proc.stderr, (
            f"{sub} degraded to an unlocked write and said nothing: "
            f"stderr was {proc.stderr!r}"
        )


# ============================================================
# Both writers must stay inside the lock
# ============================================================

def _function(name: str) -> ast.FunctionDef:
    tree = ast.parse(HOOK.read_text(encoding="utf-8"))
    matches = [n for n in tree.body
               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
               and n.name == name]
    assert len(matches) == 1, (
        f"expected exactly one top-level def {name}(...) in {HOOK.name}, "
        f"found {len(matches)}")
    return matches[0]


@pytest.mark.parametrize("fn_name", ["session_start", "session_end"])
def test_every_registry_write_sits_inside_the_lock(fn_name):
    """A source guard beside the behavioural ones, not instead of them.

    The concurrency tests above prove the lock excludes. This proves a later edit
    cannot move a write back out of it, which is the cheap half of the same
    question and the half a refactor breaks.
    """
    fn = _function(fn_name)
    spans = [(node.lineno, node.end_lineno) for node in ast.walk(fn)
             if isinstance(node, ast.With)
             and any(isinstance(item.context_expr, ast.Call)
                     and getattr(item.context_expr.func, "id", "") == "_registry_lock"
                     for item in node.items)]
    writes = [node.lineno for node in ast.walk(fn)
              if isinstance(node, ast.Call)
              and getattr(node.func, "id", "") == "_atomic_write"]
    # A floor, because a guard over an empty list of writes passes over nothing:
    # rename `_atomic_write` and the loop below would find no offenders and
    # report a clean pass while checking no write at all.
    assert len(writes) == 1, (
        f"{fn_name} makes {len(writes)} registry write(s), not 1; re-derive this "
        "guard rather than trusting it")
    outside = [line for line in writes
               if not any(lo <= line <= hi for lo, hi in spans)]
    assert not outside, (
        f"{fn_name} writes the registry outside _registry_lock() at line(s) "
        f"{outside}, which reinstates the lost-write race"
    )
