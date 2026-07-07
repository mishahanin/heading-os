"""Baseline data-overlay migration (F-9.7).

A no-op that establishes the migration contract for every future migration:

  - ``VERSION`` is the schema version this brings the overlay TO.
  - ``up(data_root, dry_run)`` does the forward work. It MUST be idempotent
    (safe to re-run), back up first via the existing backup tooling when it
    mutates real data, and honor ``dry_run`` (describe, change nothing).

The v1 overlay layout is the starting point, so there is nothing to migrate
here. The first breaking overlay change ships as ``0002_<slug>.py`` with real
``up`` work, and the runner + the ``require_writable_data_root`` refusal make it
impossible to write to an un-migrated overlay.
"""
from pathlib import Path

VERSION = 1


def up(data_root: Path, dry_run: bool = False) -> None:
    """No-op baseline: the v1 layout needs no migration."""
    return None
