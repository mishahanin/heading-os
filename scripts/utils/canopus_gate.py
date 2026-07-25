#!/usr/bin/env python3
"""The Canopus freeze check, as run by the single test gate.

Separate from scripts/canopus.py (an operator CLI) and from run-tests.py (which
re-execs the interpreter at import time via ensure_venv, so it is not safely
importable from a test).

This is where the freeze guarantee actually fires. Everything else about the
freeze is inert without it, because a verification that is never invoked fails
100% of the time regardless of how well its expected value is protected.
"""
from __future__ import annotations

from pathlib import Path

from scripts.utils.canopus_freeze import (
    LOCK_HELD,
    LOSS_OF_LOCK,
    FreezeCorrupt,
    lock_state,
    read_anchor,
    read_freeze,
    verify_manifest,
)
from scripts.utils.colors import GREEN, RED, RESET, YELLOW


def freeze_gate(root: Path) -> int:
    """Canopus wire 1: a build cannot reach green while its contract is moved.

    Silent when no freeze is active, which is the ordinary day.
    """
    try:
        manifest = read_freeze(root)
    except FreezeCorrupt as exc:
        print(f"{RED}canopus: {exc}{RESET}")
        print(f"{RED}canopus: clear it with `python scripts/canopus.py release "
              f"--force --reason \"<why>\"`{RESET}")
        return 1
    if manifest is None:
        return 0

    # A freeze is active, so the contract cannot be checked and NOT be checked:
    # an unreadable member (permissions, a vanished mount) must fail the gate,
    # not crash run-tests.py with a traceback that reads like a tooling bug.
    try:
        report = verify_manifest(manifest, root)
        anchor = manifest.get("anchor") or ""
        if anchor:
            status, value = read_anchor(Path(anchor))
        else:
            status, value = "none", None
    except OSError as exc:
        print(f"{RED}canopus: the frozen contract could not be read, so it cannot "
              f"be verified: {exc}{RESET}")
        return 1
    state = lock_state(report, status, value)

    if state == LOSS_OF_LOCK:
        print(f"{RED}canopus: {LOSS_OF_LOCK}. The frozen contract moved; run "
              f"`python scripts/canopus.py verify` for the per-file report.{RESET}")
        return 1
    colour = GREEN if state == LOCK_HELD else YELLOW
    print(f"{colour}canopus: {state}{RESET} (label: {manifest['label']})")
    return 0
