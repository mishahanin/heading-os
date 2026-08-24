#!/usr/bin/env python3
"""Workspace update manager: inventory external components and apply updates
under a tiered autonomy model (auto / notify / observed).

Usage:
    python scripts/update-manager.py check          # poll latest, write state
    python scripts/update-manager.py status         # print the table
    python scripts/update-manager.py apply <name>   # apply one notify/auto
    python scripts/update-manager.py apply --auto    # apply all auto-tier

Registry: config/update-registry.yaml. State: outputs/operations/updates/state.json.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from datetime import datetime, timezone  # noqa: E402
from scripts.utils.update_registry import Component, load_registry  # noqa: E402
from scripts.utils.update_common import resolve_current, versions_differ, write_state  # noqa: E402
from scripts.utils import update_sources  # noqa: E402
from scripts.utils.workspace import get_outputs_dir, get_workspace_root  # noqa: E402


def registry_path() -> Path:
    return get_workspace_root() / "config" / "update-registry.yaml"


def state_path() -> Path:
    return get_outputs_dir() / "operations" / "updates" / "state.json"


def resolve_latest(comp: Component) -> str:
    try:
        return update_sources.latest_version(comp.latest)
    except update_sources.SourceError:
        return ""


def build_state(components: list[Component], prior: dict | None = None) -> dict:
    prior_comp = (prior or {}).get("components", {})
    entries: dict[str, dict] = {}
    for comp in components:
        current = resolve_current(comp)
        latest = resolve_latest(comp)
        delta = versions_differ(current, latest)
        prior_e = prior_comp.get(comp.name, {})
        fail_count = prior_e.get("fail_count", 0)
        # A NEW upstream version resets the circuit breaker. Guard on `latest`
        # being truthy so a transient resolve failure (empty latest) does NOT
        # reset it. Breaker memory lives in fail_count, so it survives a cycle
        # where this component transiently resolves to `unknown`.
        if latest and prior_e.get("latest") and prior_e.get("latest") != latest:
            fail_count = 0
        was_failed = fail_count > 0
        if comp.hold or comp.pin:
            status = "held"
        elif not latest or not current:
            status = "unknown"            # a broken probe is unknown, never "current"
        elif not delta:
            status, fail_count = "current", 0
        elif comp.tier == "observed":
            status = "observed-stale"     # informational only; observed self-updates
        elif comp.tier == "notify":
            status = "waiting"
        elif was_failed:
            status = "failed"             # breaker remembers a prior auto failure while it still lags
        else:
            status = "pending-auto"
        entries[comp.name] = {
            "display": comp.display, "tier": comp.tier,
            "current": current, "latest": latest,
            "delta": delta, "status": status, "fail_count": fail_count,
        }
    return {"generated": None, "components": entries}


def _stamp_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_state() -> dict:
    p = state_path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def cmd_check(_args) -> int:
    comps = load_registry(registry_path())
    state = build_state(comps, _read_state())
    state["generated"] = _stamp_now()
    write_state(state, state_path())
    waiting = [n for n, e in state["components"].items() if e["status"] == "waiting"]
    print(f"checked {len(comps)} components; {len(waiting)} waiting")
    return 0


def cmd_status(_args) -> int:
    # `_read_state()`, not a second raw `json.loads`. The copy this replaces
    # crashed `status` with a JSONDecodeError traceback on a truncated state
    # file, while `check` -- reading the same file through `_read_state` --
    # degraded politely. The operator-facing command was the fragile one.
    state = _read_state()
    if not state:
        print("no state yet (or unreadable) -- run: python scripts/update-manager.py check")
        return 1
    components = state.get("components") or {}
    if not components:
        print("state file has no components -- run: python scripts/update-manager.py check")
        return 1
    print(f"{'component':22} {'current':14} {'latest':14} {'tier':9} status")
    for name, e in components.items():
        print(f"{name:22} {e['current']:14} {e['latest']:14} {e['tier']:9} {e['status']}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Workspace update manager")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check")
    sub.add_parser("status")
    ap = sub.add_parser("apply")
    ap.add_argument("name", nargs="?")
    ap.add_argument("--auto", action="store_true")
    args = parser.parse_args(argv)
    if args.cmd == "check":
        return cmd_check(args)
    if args.cmd == "status":
        return cmd_status(args)
    if args.cmd == "apply":
        # `fcntl` is UNIX-ONLY, and this repo ships ps1 installers and a
        # cross-platform schedule helper -- a hard import made the whole apply
        # tier a ModuleNotFoundError traceback on the Windows host. The lock is
        # advisory anyway: without it two concurrent applies can interleave, so
        # Windows degrades with a named warning rather than losing the command.
        fcntl = None
        if os.name == "posix":
            import fcntl  # noqa: PLC0415
        from scripts.utils.update_apply import cmd_apply  # noqa: PLC0415
        lock_path = state_path().parent / ".apply.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with open(lock_path, "w") as lock_fh:
            try:
                fcntl.flock(lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                # Manual apply racing the 07:00 timer (or vice versa). Skip rather
                # than interleave snapshot/swap on the same component.
                print("another apply is in progress; skipping")
                return 0
            return cmd_apply(args, load_registry(registry_path()), state_path())
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
