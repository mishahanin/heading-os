#!/usr/bin/env python3
"""Scaffold a private data folder for the HEADING OS engine.

Creates the empty data tree the engine expects and stamps the schema version.
Refuses to clobber an existing non-empty data folder.

Usage:
  python scripts/init-data.py [--path DIR]   # default: ../.heading-os-data
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils.paths import DATA_SCHEMA_VERSION, get_workspace_root
from scripts.utils.atomic import atomic_write_text

DATA_DIRS = [
    "crm/contacts",
    "knowledge",
    "outputs",
    "threads/business",
    "threads/personal",
    "context",
]


def init_data(target: Path) -> int:
    if target.exists() and any(target.iterdir()):
        print(f"Refusing to scaffold: {target} exists and is not empty.")
        return 1
    for d in DATA_DIRS:
        (target / d).mkdir(parents=True, exist_ok=True)
    # The third writer of this marker. build_data_repo.py and migrate-data.py
    # both go through atomic_write_text; a half-written marker here makes
    # paths.read_data_schema_version fall back to "assume current", which
    # silently skips every migration the overlay still needs.
    atomic_write_text(target / ".schema-version", f"{DATA_SCHEMA_VERSION}\n")
    print(f"Initialized data folder at {target} (schema v{DATA_SCHEMA_VERSION}).")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Scaffold a HEADING OS private data folder")
    ap.add_argument("--path", default=str(get_workspace_root().parent / ".heading-os-data"))
    args = ap.parse_args()
    return init_data(Path(args.path).expanduser())


if __name__ == "__main__":
    raise SystemExit(main())
