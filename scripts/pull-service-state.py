#!/usr/bin/env python3
"""Pull service-host state from the managed VM into a read-only laptop mirror.

The service-host VM is a separate entity this workspace MANAGES: once its
daemons go live the VM is the authoritative writer of their state. This
laptop-side client copies that state (via scp over SSH) into the mirror dir
on the laptop so the laptop has a current read-only view. The laptop never
writes back.

The VM host address is read from SERVICE_VM_HOST in .env (a secret, never in
the engine). The VM repo path, mirror dir, and state-dir layout come from the
private config/service-host.json (engine ships scripts/service-host.example.json).
Some state dirs may not exist on the VM yet (e.g., before a daemon's first
run); those report as "not present" - that's not a failure.

Usage:
    python scripts/pull-service-state.py
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import json

from scripts.utils.workspace import get_data_root, load_env, resolve_config_with_example
from scripts.utils.rmtree import rmtree_force
from scripts.utils.colors import GREEN, YELLOW, RED, CYAN, GRAY, BOLD, RESET

# Service-host topology comes from the private data overlay; the engine ships a
# generic example. Post engine/data split the VM carries TWO roots — the engine
# clone (vm_engine_root) and the data overlay (vm_data_root) — each overridable
# per-instance via .env (SERVICE_VM_ENGINE_ROOT / SERVICE_VM_DATA_ROOT). The host
# ADDRESS is always SERVICE_VM_HOST in .env.
_SVC = json.loads(
    resolve_config_with_example(
        "service-host.json", Path(__file__).resolve().parent / "service-host.example.json"
    ).read_text(encoding="utf-8")
)
MIRROR_REL = _SVC.get("mirror_dir", "datastore/operations/service-mirror")
# scp over a slow or half-open link hung the run indefinitely; this is a
# per-directory ceiling, not a whole-run one.
SCP_TIMEOUT_S = 600


def vm_roots() -> dict:
    """The two VM roots, .env overrides winning.

    This used to be a module-level dict. `load_env()` -- the thing that copies
    .env into os.environ -- runs inside main(), which is AFTER module import, so
    the overrides the docstring above promises were read before they existed and
    every run silently used the config-file values instead. Resolving on call is
    what makes the promise true.
    """
    return {
        "engine": os.environ.get("SERVICE_VM_ENGINE_ROOT") or _SVC.get("vm_engine_root", ""),
        "data": os.environ.get("SERVICE_VM_DATA_ROOT") or _SVC.get("vm_data_root", ""),
    }


def _vm_path(entry, roots: dict) -> tuple[str, str]:
    """Resolve one state_dirs entry to (mirror_name, absolute VM path).

    New 3-tuple form [name, root, rel] joins `rel` onto the named VM root
    (engine|data). Falls back to the retired 2-tuple [name, rel] form by joining
    onto the engine root, so an un-migrated config still resolves.

    A malformed entry used to raise ValueError from the tuple unpack, and an
    empty root produced the path "/rel" -- scp then pulled from the VM's root
    filesystem. Both are refused by name now.
    """
    if not isinstance(entry, (list, tuple)) or len(entry) not in (2, 3):
        raise ValueError(f"malformed state_dirs entry (want 2 or 3 items): {entry!r}")
    name, root, rel = entry if len(entry) == 3 else (entry[0], "engine", entry[1])
    base = roots.get(root, "")
    if not base:
        raise ValueError(
            f"state_dirs entry {name!r} needs VM root {root!r}, which is empty; "
            f"set SERVICE_VM_{root.upper()}_ROOT in .env or vm_{root}_root in "
            f"service-host.json")
    return name, f"{base.rstrip('/')}/{rel.lstrip('/')}"


def state_dirs() -> list:
    """(local mirror name, VM absolute path) pairs. Call AFTER load_env()."""
    roots = vm_roots()
    return [_vm_path(e, roots) for e in _SVC["state_dirs"]]



def main() -> int:
    # The mirror is PRIVATE data (routing-map: datastore/operations/service-mirror/
    # -> private), so it must resolve under the DATA root, never the engine clone.
    data_root = get_data_root()
    load_env()
    try:
        targets = state_dirs()
    except ValueError as exc:
        print(f"{RED}service-host.json: {exc}{RESET}")
        return 1
    host = os.environ.get("SERVICE_VM_HOST")
    if not host:
        print(f"{RED}SERVICE_VM_HOST not set in .env{RESET}")
        print(f"{GRAY}Add a line:  SERVICE_VM_HOST=<vm-ip-or-hostname>{RESET}")
        return 1

    mirror = data_root / MIRROR_REL
    mirror.mkdir(parents=True, exist_ok=True)
    print(f"{BOLD}Pulling service-host state from {host}{RESET}")

    pulled = skipped = failures = 0
    for name, vm_path in targets:
        dest_abs = mirror / name
        # Pull into a SCRATCH directory and swap on success. The old code
        # rmtree'd the live mirror first, so an unreachable VM or a half-finished
        # transfer left the operator with neither the new copy nor the last good
        # one -- and this mirror is the only local record of the VM's state.
        staging_abs = mirror / f".{name}.incoming"
        if staging_abs.exists():
            rmtree_force(staging_abs)
        # Relative dest (resolved against cwd=data_root below) avoids the
        # Windows-drive-letter colon issue in scp.
        dest_rel = f"{MIRROR_REL}/.{name}.incoming"
        cmd = ["scp", "-r", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15",
               f"root@{host}:{vm_path}", dest_rel]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True,
                                    cwd=data_root, timeout=SCP_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            rmtree_force(staging_abs)
            print(f"  {YELLOW}{name}: scp exceeded {SCP_TIMEOUT_S}s; "
                  f"previous mirror left intact{RESET}")
            failures += 1
            continue
        if result.returncode == 0:
            if dest_abs.exists():
                rmtree_force(dest_abs)
            staging_abs.rename(dest_abs)
            print(f"  {GREEN}{name}{RESET}")
            pulled += 1
        else:
            rmtree_force(staging_abs)
            err = (result.stderr.strip().splitlines() or ["unknown error"])[-1]
            stderr_lower = result.stderr.lower()
            if "no such file" in stderr_lower or "not a regular file" in stderr_lower:
                print(f"  {GRAY}{name}: not present on VM yet{RESET}")
                skipped += 1
            else:
                print(f"  {YELLOW}{name}: {err}{RESET}")
                failures += 1

    summary = f"pulled={pulled} skipped={skipped} failed={failures}"
    if failures:
        print(f"{YELLOW}{summary}{RESET}")
        return 1
    print(f"{GREEN}{summary}{RESET}")
    print(f"{GRAY}mirror: {mirror}{RESET}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
