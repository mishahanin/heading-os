#!/usr/bin/env python3
"""Apply pipeline for the update manager: snapshot -> apply -> health-gate ->
auto-rollback. The apply invariant: an auto update never leaves a component
broken -- health passes on the new version, or the old version is restored.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from scripts.utils.update_registry import Component  # noqa: E402
from scripts.utils.update_common import resolve_current, write_state  # noqa: E402


class RollbackFailed(Exception):
    """The restore command itself failed, so the prior version is NOT back.

    Distinct from "rolled back": the module docstring's invariant is that an
    auto update never leaves a component broken, and a rollback that exits
    non-zero breaks it. `_build_rollback` ran its command with `check=False` and
    discarded the exit status, so a rollback exiting 7 and restoring nothing
    produced the same "rolled-back" verdict as a rollback that worked. Measured
    2026-08-26 with apply={'cmd': 'true', 'rollback_cmd': 'exit 7'} and a
    failing health gate.
    """


def run_health(comp: Component) -> bool:
    if not comp.health:
        return True
    cmd = comp.health.get("cmd", "")
    try:
        res = subprocess.run(["bash", "-c", cmd], capture_output=True, text=True,
                             timeout=60, check=False)
    except (subprocess.SubprocessError, OSError):
        return False
    want = comp.health.get("expect_substr")
    out = res.stdout + res.stderr
    if want:
        return want in out
    return res.returncode == 0


def apply_one(comp: Component, *, applier: Callable[[], None],
              rollback: Callable[[], None]) -> str:
    if comp.tier == "observed" or comp.apply is None:
        return "skipped"
    if comp.hold or comp.pin:
        return "skipped"
    try:
        applier()
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        # The apply command failed or timed out (not the health gate). Restore the
        # prior version. For script applies the closure is a no-op -- the script
        # self-rolls-back before exiting non-zero, so the exception already means
        # "restored".
        try:
            rollback()
        except RollbackFailed as exc:
            print(exc, file=sys.stderr)
            return "rollback-failed"
        return "rolled-back"
    # A `script` apply is its own gate: it self-verified health and self-rolled-back
    # before exiting non-zero, so reaching here (exit 0) means healthy. Re-probing
    # can only produce a false negative — its rollback closure is a no-op with no
    # teeth. Only `cmd` applies need the outer health gate (their sole gate, with a
    # real rollback_cmd).
    if comp.apply and "cmd" not in comp.apply and "script" in comp.apply:
        return "applied"
    if run_health(comp):
        return "applied"
    try:
        rollback()
    except RollbackFailed as exc:
        print(exc, file=sys.stderr)
        return "rollback-failed"
    return "rolled-back"


def _default_applier(comp: Component) -> Callable[[], None]:
    """Build the applier for a component: a `cmd` one-liner, or a `script`."""
    def _run() -> None:
        if "cmd" in comp.apply:
            subprocess.run(["bash", "-c", comp.apply["cmd"]], check=True, timeout=600)
        elif "script" in comp.apply:
            root = Path(__file__).resolve().parent.parent.parent
            subprocess.run([sys.executable, str(root / comp.apply["script"])],
                          check=True, timeout=900)
        else:
            raise ValueError(f"{comp.name}: apply block has neither cmd nor script")
    return _run


def _build_rollback(comp: Component, prev: str) -> Callable[[], None]:
    """Rollback closure. For a `cmd` apply, run `rollback_cmd` with {prev}
    substituted. A `script` apply owns its rollback internally, so this is a
    no-op (the script has already restored + exited non-zero -> "rolled-back")."""
    rb = (comp.apply or {}).get("rollback_cmd")
    if not rb:
        return lambda: None
    cmd = rb.replace("{prev}", prev)

    def _run() -> None:
        try:
            res = subprocess.run(["bash", "-c", cmd], check=False, timeout=600,
                                 capture_output=True, text=True)
        except (subprocess.SubprocessError, OSError) as exc:
            raise RollbackFailed(f"{comp.name}: rollback could not run: {exc}") from exc
        if res.returncode != 0:
            tail = (res.stderr or res.stdout or "").strip().splitlines()[-1:] or [""]
            raise RollbackFailed(
                f"{comp.name}: rollback exited {res.returncode}; the prior "
                f"version was NOT restored: {tail[0][:200]}")

    return _run


MAX_AUTO_RETRIES = 3  # circuit breaker: stop re-applying a persistently-broken auto release


def _auto_due(entry: dict) -> bool:
    """Should the timer apply this auto component now? First attempt
    (pending-auto) always; a prior failure retries until MAX_AUTO_RETRIES, then
    stops (a new upstream version resets the counter in build_state)."""
    status = entry.get("status")
    if status == "pending-auto":
        return True
    if status == "failed":
        return entry.get("fail_count", 0) < MAX_AUTO_RETRIES
    return False


def cmd_apply(args, components: list[Component], state_path: Path) -> int:
    by_name = {c.name: c for c in components}
    if args.auto:
        # Delta-gate against the last check: apply only auto components that lag,
        # and stop retrying a release that keeps failing (circuit breaker).
        st: dict = {}
        if state_path.exists():
            try:
                st = json.loads(state_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                st = {}
        comps_state = st.get("components", {})
        targets = [c for c in components if c.tier == "auto"
                   and _auto_due(comps_state.get(c.name, {}))]
    elif args.name:
        if args.name not in by_name:
            print(f"unknown component: {args.name}")
            return 1
        targets = [by_name[args.name]]
    else:
        print("apply needs a component name or --auto")
        return 1

    results: dict[str, str] = {}
    for comp in targets:
        # Isolate each component: one unexpected error must not abort the batch or
        # skip _mark_state (which would hide the failure from the fail_count breaker).
        try:
            prev = resolve_current(comp)            # captured before the swap
            rollback = _build_rollback(comp, prev)
            # apply_one owns both failure paths: an apply-command failure/timeout
            # and a health-gate failure both invoke `rollback` -> "rolled-back",
            # or "rollback-failed" when the restore command itself did not work.
            result = apply_one(comp, applier=_default_applier(comp), rollback=rollback)
            print(f"{comp.name}: {result}")
        except Exception as exc:  # noqa: BLE001 - boundary; one component's failure is contained
            result = "rolled-back"
            print(f"{comp.name}: error ({type(exc).__name__}: {exc})")
        results[comp.name] = result
    _mark_state(state_path, results)
    return 1 if any(r in FAILED_RESULTS for r in results.values()) else 0


# Every outcome that must set status=failed and raise the exit code. Named once
# so a new outcome cannot be added to `apply_one` and then be invisible to BOTH
# the state file and the exit code, which is what a bare
# `result == "rolled-back"` in two places invites.
FAILED_RESULTS = frozenset({"rolled-back", "rollback-failed"})


def _mark_state(state_path: Path, results: dict[str, str]) -> None:
    """Persist failed components as status=failed so /prime surfaces them.
    Applied components will read back as `current` on the next `check`.
    """
    if not state_path.exists():
        return
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    changed = False
    for name, result in results.items():
        entry = state.get("components", {}).get(name)
        if result in FAILED_RESULTS and entry is not None:
            entry["status"] = "failed"
            entry["fail_count"] = entry.get("fail_count", 0) + 1
            changed = True
    if changed:
        write_state(state, state_path)   # shared atomic writer from update_common
