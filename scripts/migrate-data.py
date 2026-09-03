#!/usr/bin/env python3
"""Data-overlay migration runner (F-9.7).

Applies ordered migrations from ``scripts/migrations/`` to the private data
overlay and records the reached schema version in ``<data_root>/.schema-version``.

Usage:
    python scripts/migrate-data.py --status     # show current vs pending
    python scripts/migrate-data.py --apply      # apply pending migrations
    python scripts/migrate-data.py --stamp      # write baseline marker if absent
    python scripts/migrate-data.py --dry-run    # show what --apply/--stamp would do

Why ``--stamp`` exists (F-9.7 / H1): ``read_data_schema_version()`` falls back to
the current ``DATA_SCHEMA_VERSION`` when ``.schema-version`` is absent, so an
established overlay that never carried the marker would read as "current" even
after a future version bump, and the pending-migrations refusal would never fire.
Stamping writes a concrete baseline marker once, so a later bump is detected.
Demo overlays (read-only ``examples/``) are exempt from both apply and stamp.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.utils.atomic import atomic_write_text
from scripts.utils.clone_guard import require_main_clone
from scripts.utils.colors import GREEN, YELLOW, RED, GRAY, BOLD, RESET
from scripts.utils.paths import (
    DATA_SCHEMA_VERSION,
    data_root_is_demo,
    get_data_root,
    read_data_schema_version,
)
from scripts.migrations import max_version, registered_migrations

SCHEMA_FILE = ".schema-version"


def _write_version(data_root: Path, version: int) -> None:
    atomic_write_text(data_root / SCHEMA_FILE, f"{version}\n")


def cmd_status() -> int:
    current = read_data_schema_version()
    target = max_version()
    marker = get_data_root() / SCHEMA_FILE
    print(f"{BOLD}Data overlay schema{RESET}")
    print(f"  data root:   {get_data_root()}")
    print(f"  marker file: {'present' if marker.exists() else GRAY + 'absent (reads as current via fallback)' + RESET}")
    print(f"  current:     v{current}")
    print(f"  engine max:  v{target}")
    pending = [v for v, _ in registered_migrations() if v > current]
    if pending:
        print(f"  {YELLOW}pending:     {pending}{RESET}")
        print(f"\nRun {BOLD}python scripts/migrate-data.py --apply{RESET} to migrate.")
    else:
        print(f"  {GREEN}up to date.{RESET}")
        if not marker.exists():
            print(f"  {GRAY}tip: run --stamp to write a concrete baseline marker "
                  f"(so a future version bump is detected).{RESET}")
    return 0


def cmd_apply(dry_run: bool) -> int:
    if data_root_is_demo():
        print(f"{RED}Refusing to migrate a demo (read-only examples) overlay.{RESET}")
        return 1
    data_root = get_data_root()
    current = read_data_schema_version()
    pending = [(v, m) for v, m in registered_migrations() if v > current]
    if not pending:
        print(f"{GREEN}Nothing to apply - overlay at v{current}, engine max v{max_version()}.{RESET}")
        return 0
    for version, mod in pending:
        label = getattr(mod, "__name__", "?").rsplit(".", 1)[-1]
        if dry_run:
            print(f"{YELLOW}[dry-run]{RESET} would apply {label} -> v{version}")
            # ASK the migration what it would do. `up(data_root, dry_run)` is
            # the contract `scripts/migrations/0001_baseline.py` states every
            # migration MUST honor ("describe, change nothing"), and this loop
            # `continue`d instead - so the only call site passed `dry_run=False`
            # unconditionally and NOTHING ever reached a dry-run branch.
            # Reproduced 2026-08-27 with a fake pending migration: `up` was not
            # called at all. The first real migration would therefore ship a
            # dry-run branch no code path executes, and the operator's
            # `--dry-run` would print this one-line guess instead of the
            # migration's own account of the change.
            #
            # A migration that raises here is reported and the run stops: a
            # dry-run that cannot describe itself is not a dry-run that passed.
            try:
                mod.up(data_root, dry_run=True)
            except Exception as exc:  # noqa: BLE001 - reported, then re-raised
                print(f"{RED}{label}: dry-run failed: {exc}{RESET}",
                      file=sys.stderr)
                return 1
            continue
        print(f"Applying {label} -> v{version} ...")
        # Reported, not a traceback. The dry-run branch four lines up already
        # sets this standard, and the REAL apply is the moment the operator most
        # needs a readable account: a migration that dies half way leaves the
        # overlay at whatever the last completed step wrote, and a stack trace
        # does not say which version that is. The version marker itself is
        # correct either way (written per step, after the step), so the only
        # thing missing was saying so.
        try:
            mod.up(data_root, dry_run=False)
        except Exception as exc:  # noqa: BLE001 - reported with the version reached
            print(f"{RED}{label}: apply FAILED: {exc}{RESET}", file=sys.stderr)
            print(f"{RED}The overlay is at v{current if version == pending[0][0] else version - 1} "
                  f"and step v{version} is incomplete. Inspect it before re-running.{RESET}",
                  file=sys.stderr)
            return 1
        _write_version(data_root, version)
        print(f"  {GREEN}done, overlay now at v{version}.{RESET}")
    return 0


def cmd_stamp(dry_run: bool) -> int:
    if data_root_is_demo():
        print(f"{RED}Refusing to stamp a demo (read-only examples) overlay.{RESET}")
        return 1
    data_root = get_data_root()
    marker = data_root / SCHEMA_FILE
    if marker.exists():
        print(f"{GREEN}Marker already present: {marker} (v{read_data_schema_version()}).{RESET}")
        return 0
    current = read_data_schema_version()
    pending = [v for v, _ in registered_migrations() if v > current]
    if pending:
        print(f"{RED}Cannot stamp: migrations pending {pending}. Run --apply first.{RESET}")
        return 1
    if dry_run:
        print(f"{YELLOW}[dry-run]{RESET} would write {marker} = v{DATA_SCHEMA_VERSION}")
        return 0
    _write_version(data_root, DATA_SCHEMA_VERSION)
    print(f"{GREEN}Stamped {marker} = v{DATA_SCHEMA_VERSION}.{RESET}")
    return 0


def main() -> int:
    require_main_clone(__file__)
    parser = argparse.ArgumentParser(description="Data-overlay migration runner (F-9.7)")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--status", action="store_true", help="show current vs pending")
    group.add_argument("--apply", action="store_true", help="apply pending migrations")
    group.add_argument("--stamp", action="store_true",
                       help="write the baseline .schema-version marker if absent")
    parser.add_argument("--dry-run", action="store_true",
                        help="with --apply/--stamp: describe, change nothing")
    args = parser.parse_args()
    if args.status:
        return cmd_status()
    if args.apply:
        return cmd_apply(args.dry_run)
    if args.stamp:
        return cmd_stamp(args.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
