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
import shutil
import stat
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import json

from scripts.utils.workspace import get_data_root, load_env, resolve_config_with_example
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
VM_ROOTS = {
    "engine": os.environ.get("SERVICE_VM_ENGINE_ROOT", _SVC.get("vm_engine_root", "")),
    "data": os.environ.get("SERVICE_VM_DATA_ROOT", _SVC.get("vm_data_root", "")),
}
MIRROR_REL = _SVC.get("mirror_dir", "datastore/operations/service-mirror")


def _vm_path(entry) -> tuple[str, str]:
    """Resolve one state_dirs entry to (mirror_name, absolute VM path).

    New 3-tuple form [name, root, rel] joins `rel` onto the named VM root
    (engine|data). Falls back to the retired 2-tuple [name, rel] form by joining
    onto the engine root, so an un-migrated config still resolves.
    """
    name, root, rel = entry if len(entry) == 3 else (entry[0], "engine", entry[1])
    base = VM_ROOTS.get(root, "")
    return name, f"{base}/{rel}"


# (local mirror name, VM absolute path)
STATE_DIRS = [_vm_path(e) for e in _SVC["state_dirs"]]


def _on_rm_error(func, path, exc):
    """Windows: clear the read-only bit and retry."""
    os.chmod(path, stat.S_IWRITE)
    func(path)


def main() -> int:
    # The mirror is PRIVATE data (routing-map: datastore/operations/service-mirror/
    # -> private), so it must resolve under the DATA root, never the engine clone.
    data_root = get_data_root()
    load_env()
    host = os.environ.get("SERVICE_VM_HOST")
    if not host:
        print(f"{RED}SERVICE_VM_HOST not set in .env{RESET}")
        print(f"{GRAY}Add a line:  SERVICE_VM_HOST=<vm-ip-or-hostname>{RESET}")
        return 1

    mirror = data_root / MIRROR_REL
    mirror.mkdir(parents=True, exist_ok=True)
    print(f"{BOLD}Pulling service-host state from {host}{RESET}")

    pulled = skipped = failures = 0
    for name, vm_path in STATE_DIRS:
        dest_abs = mirror / name
        # Relative dest (resolved against cwd=data_root below) avoids the
        # Windows-drive-letter colon issue in scp.
        dest_rel = f"{MIRROR_REL}/{name}"
        if dest_abs.exists():
            shutil.rmtree(dest_abs, onexc=_on_rm_error)
        cmd = ["scp", "-r", "-o", "BatchMode=yes",
               f"root@{host}:{vm_path}", dest_rel]
        result = subprocess.run(cmd, capture_output=True, text=True, cwd=data_root)
        if result.returncode == 0:
            print(f"  {GREEN}{name}{RESET}")
            pulled += 1
        else:
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
