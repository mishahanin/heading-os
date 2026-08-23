#!/usr/bin/env python3
"""Check corporate build number and compare across all exec workspaces."""

import json
import sys
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils.workspace import (  # noqa: E402
    get_per_exec_repo_path,
    load_fleet,
)

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent.parent
CORPORATE_BUILD = WORKSPACE_ROOT / "heading-os-corporate" / "BUILD.json"


def load_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def format_age(timestamp_str: str) -> str:
    try:
        ts = datetime.fromisoformat(timestamp_str)
        now = datetime.now(ts.tzinfo)
        total_seconds = (now - ts).total_seconds()
        if total_seconds < 0:
            return "just now"
        minutes = int(total_seconds // 60)
        hours = int(total_seconds // 3600)
        days = int(total_seconds // 86400)
        if days > 1:
            return f"{days} days ago"
        if days == 1:
            return "1 day ago"
        if hours >= 1:
            return f"{hours}h ago"
        return f"{minutes}m ago"
    except (ValueError, TypeError):
        return ""


def main():
    # Load corporate build
    corp = load_json(CORPORATE_BUILD)
    if not corp:
        print("ERROR: Cannot read corporate BUILD.json")
        print(f"  Expected at: {CORPORATE_BUILD}")
        sys.exit(1)

    # Indexed directly until 2026-08-23, so a BUILD.json missing either key
    # raised a KeyError traceback instead of naming the malformed file.
    if "build" not in corp or "version" not in corp:
        print(f"ERROR: {CORPORATE_BUILD} is missing 'build' and/or 'version'")
        print(f"  Found keys: {sorted(corp)}")
        sys.exit(1)

    corp_build = corp["build"]
    corp_version = corp["version"]
    corp_ts = corp.get("timestamp", "")
    corp_summary = corp.get("summary", "")
    corp_age = format_age(corp_ts) if corp_ts else ""

    print(f"\n  Corporate Build")
    print(f"  Build {corp_build} (v{corp_version}) - {corp_age}")
    if corp_summary:
        print(f"  {corp_summary}")
    print()

    # Who has an install to compare. Until 2026-08-23 this filtered the ORG
    # CHART on `workspace_repo`, a field holding the retired `31c-workspace-`
    # name; those repos do not exist, so the table listed people whose build
    # could never be read. Membership now comes from the fleet roster, which is
    # what "has a HEADING OS install" actually means.
    execs = [r for r in load_fleet() if r["is_heading_os_user"] and r["data_repo"]]

    if not execs:
        print("  No exec workspaces registered.")
        sys.exit(0)

    max_name = max(len(e["name"] or e["slug"]) for e in execs)
    print(f"  {'Executive':<{max_name}}   Build   Status")
    print(f"  {'-' * max_name}   -----   ------")

    for ex in execs:
        name = ex["name"] or ex["slug"]
        exec_build_path = get_per_exec_repo_path(ex["slug"]) / "corporate" / "BUILD.json"
        exec_data = load_json(exec_build_path)

        if not exec_data:
            print(f"  {name:<{max_name}}   -       not found")
            continue

        ex_build = exec_data.get("build", 0)
        ex_version = exec_data.get("version", "?")
        behind = corp_build - ex_build

        if behind == 0:
            status = "up to date"
        elif behind == 1:
            status = f"1 build behind"
        else:
            status = f"{behind} builds behind"

        marker = " " if behind == 0 else " !"
        print(f"  {name:<{max_name}}   {ex_build:<7} {status}{marker}")

    print()


if __name__ == "__main__":
    main()
