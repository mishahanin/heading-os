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
def service_config() -> tuple[dict, str | None]:
    """Read service-host.json. Returns (config, error); NEVER raises.

    Resolved on CALL, never at import. `resolve_config_with_example` reaches the
    data root, which is read out of `HEADING_OS_DATA` on every call, so a
    module-level `_SVC, _SVC_ERROR = ...` stored one answer for the life of the
    process: a caller that repointed the root afterwards still got whatever the
    root was during this module's import.

    The pair is returned from ONE read so the config and its error can never
    come from two different loads. It never raises: this used to run at import,
    where no handler was in scope, and an unparseable config raised
    json.JSONDecodeError while a top-level JSON list raised AttributeError from
    the `.get` in the callers, both killing the run with a raw traceback before
    `main` -- and its named, actionable message -- existed. Carrying the error as
    a value lets `state_dirs` raise the ValueError that `main` already catches
    and prints properly.
    """
    try:
        path = resolve_config_with_example(
            "service-host.json",
            Path(__file__).resolve().parent / "service-host.example.json",
        )
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        # UnicodeDecodeError is a ValueError, NOT an OSError, and this
        # function used to run at import. A `config/service-host.json` saved as
        # UTF-16 therefore killed the process with a raw traceback before
        # `main` could print the named message this docstring promises.
        return {}, f"could not be read: {exc}"
    except Exception as exc:  # noqa: BLE001 - the docstring's promise is total
        # Reading the file is only half of what this block does. Resolving WHERE
        # it lives runs first, through `resolve_config_with_example` ->
        # `get_data_config_dir()` -> `get_workspace_root()`, and a root that will
        # not resolve (marker gone, an unresolvable `~` in WORKSPACE_ROOT) raises
        # RuntimeError, which is neither an OSError nor a UnicodeDecodeError.
        # MEASURED 2026-09-01 with a resolver raising RuntimeError: it came
        # straight out of a function documented "NEVER raises", then out of
        # `state_dirs()`, past `main`'s `except ValueError`, as a traceback.
        # Nothing is swallowed: the reason is RETURNED, `state_dirs` raises it as
        # the ValueError `main` already catches, and `main` prints it and exits 1.
        return {}, f"could not be located: {type(exc).__name__}: {exc}"
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        return {}, f"is not valid JSON ({path}): {exc}"
    if not isinstance(data, dict):
        return {}, f"must hold a JSON object, not a {type(data).__name__} ({path})"
    return data, None


# scp over a slow or half-open link hung the run indefinitely; this is a
# per-directory ceiling, not a whole-run one.
SCP_TIMEOUT_S = 600


def mirror_rel() -> str:
    """The mirror directory, relative to the data root. Call time, not import.

    This was `MIRROR_REL = _SVC.get(...)` at module scope, which is frozen for
    the same reason `service_config` above is: it is derived from a value that
    resolved the data root once, during import.
    """
    svc, _ = service_config()
    return svc.get("mirror_dir", "datastore/operations/service-mirror")


def vm_roots() -> dict:
    """The two VM roots, .env overrides winning.

    This used to be a module-level dict. `load_env()` -- the thing that copies
    .env into os.environ -- runs inside main(), which is AFTER module import, so
    the overrides the docstring above promises were read before they existed and
    every run silently used the config-file values instead. Resolving on call is
    what makes the promise true.
    """
    svc, _ = service_config()
    return {
        "engine": os.environ.get("SERVICE_VM_ENGINE_ROOT") or svc.get("vm_engine_root", ""),
        "data": os.environ.get("SERVICE_VM_DATA_ROOT") or svc.get("vm_data_root", ""),
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
    # Types first, because everything below indexes and joins these. A
    # non-string element raised AttributeError out of `lstrip`/`rstrip`,
    # and `main` catches only ValueError, so the run ended in a traceback
    # rather than the named one-line reason this function promises.
    if not all(isinstance(v, str) for v in (name, root, rel)):
        raise ValueError(
            f"state_dirs entry must hold strings only (name, root, rel): "
            f"{entry!r}")
    # The mirror name is joined onto the mirror directory and the previous
    # copy is rmtree'd, so a PATH here deletes outside the mirror.
    # MEASURED 2026-08-29: `..`, `.` and a single-segment absolute name all
    # came back accepted. `..` deletes the mirror's parent wholesale, `.`
    # deletes the mirror, and `/tmp` deletes /tmp.
    if name != Path(name).name or name in ("", ".", ".."):
        raise ValueError(
            f"state_dirs mirror name {name!r} must be a plain directory "
            f"name: it is joined onto the mirror and the previous copy is "
            f"deleted, so a path here deletes outside the mirror")
    base = roots.get(root, "")
    if not isinstance(base, str):
        raise ValueError(
            f"VM root {root!r} must be a string, not a "
            f"{type(base).__name__}")
    if not base:
        raise ValueError(
            f"state_dirs entry {name!r} needs VM root {root!r}, which is empty; "
            f"set SERVICE_VM_{root.upper()}_ROOT in .env or vm_{root}_root in "
            f"service-host.json")
    return name, f"{base.rstrip('/')}/{rel.lstrip('/')}"


def state_dirs() -> list:
    """(local mirror name, VM absolute path) pairs. Call AFTER load_env().

    Every other key in this file is read with `.get` and a default; `state_dirs`
    was the one subscript, so a config missing it raised KeyError -- which is not
    a ValueError, so `main`'s handler could not catch it and the run ended in a
    traceback instead of the one-line reason.
    """
    svc, error = service_config()
    if error:
        raise ValueError(error)
    entries = svc.get("state_dirs")
    if entries is None:
        raise ValueError(
            "no 'state_dirs' key, so there is nothing to pull; copy the list "
            "from scripts/service-host.example.json")
    if not isinstance(entries, list):
        raise ValueError(
            f"'state_dirs' must be a list of entries, not a {type(entries).__name__}")
    roots = vm_roots()
    return [_vm_path(e, roots) for e in entries]



def main() -> int:
    # load_env FIRST. `get_data_root()` reads HEADING_OS_DATA out of os.environ,
    # and `load_env()` is what copies .env into os.environ, so resolving the
    # root above the load read the override before it existed. MEASURED
    # 2026-08-30 with HEADING_OS_DATA in .env: the root came back as the
    # examples fallback before the load and as the operator's overlay after it,
    # so every run wrote the mirror under the wrong root and reported success.
    # This is the same ordering bug `vm_roots` above already burned this file
    # once, in the same run, for the same reason.
    load_env()
    # The mirror is PRIVATE data (routing-map: datastore/operations/service-mirror/
    # -> private), so it must resolve under the DATA root, never the engine clone.
    data_root = get_data_root()
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

    # Bound once, not re-read per entry: `mirror` below and `dest_rel` inside
    # the loop must name the same directory, or the staging swap writes one
    # tree and renames another.
    mirror_rel_dir = mirror_rel()
    mirror = data_root / mirror_rel_dir
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
        dest_rel = f"{mirror_rel_dir}/.{name}.incoming"
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
