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
    """Parsed JSON, or None for any reason this file cannot be used.

    `FileNotFoundError` and `JSONDecodeError` only, until 2026-08-24 -- so a
    permission-denied or directory-shaped BUILD.json crashed with a traceback
    instead of taking the clean "cannot read" path that exists for exactly this.
    """
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None


def format_age(timestamp_str: str) -> str:
    try:
        ts = datetime.fromisoformat(timestamp_str)
        now = datetime.now(ts.tzinfo)
        total_seconds = (now - ts).total_seconds()
        if total_seconds < 0:
            # A post-dated stamp or a skewed clock. "just now" asserted the
            # opposite of what the file says; this says what it says.
            ahead = int(-total_seconds // 60)
            return f"{ahead}m in the FUTURE" if ahead else "in the future"
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


def _build_number(value) -> int | None:
    """The build number as an int, or None when the file does not hold one.

    `True` is an `int` in Python and is not a build number, so it is refused
    with the strings and the lists.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def build_status(corp_build: int, ex_build: int) -> tuple[str, str]:
    """(status text, warning marker) comparing one exec build to corporate.

    A function, not four lines inline, so the AHEAD case can be tested. It used
    to be `behind = corp - ex` with no negative branch, so an exec ahead of
    corporate printed `-2 builds behind` NEXT TO the `!` warning marker. An
    exec ahead is normally the SOURCE of the next corporate build, and this
    labelled it a drift defect.
    """
    behind = corp_build - ex_build
    if behind == 0:
        return "up to date", " "
    if behind == 1:
        return "1 build behind", " !"
    if behind > 1:
        return f"{behind} builds behind", " !"
    ahead = -behind
    return f"{ahead} build{'s' if ahead > 1 else ''} ahead of corporate", " "


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

    # The TYPE, not just the key. The 2026-08-23 hardening checked that both
    # keys exist and stopped there, so a `"build": "42"` parsed cleanly and then
    # raised TypeError inside `build_status` — after the header had printed,
    # taking the whole table down over one semantically wrong file. `load_json`
    # exists "for exactly this", and a file that decodes is past it.
    corp_build = _build_number(corp["build"])
    if corp_build is None:
        print(f"ERROR: {CORPORATE_BUILD} has a non-integer 'build': "
              f"{corp['build']!r}")
        sys.exit(1)
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

        # One malformed exec file is that ROW's problem, never the table's. The
        # execs listed after it were fine and their status is the reason anyone
        # runs this.
        ex_build = _build_number(exec_data.get("build", 0))
        if ex_build is None:
            print(f"  {name:<{max_name}}   -       malformed build "
                  f"{exec_data.get('build')!r}")
            continue
        ex_version = exec_data.get("version", "?")
        status, marker = build_status(corp_build, ex_build)
        print(f"  {name:<{max_name}}   {ex_build:<7} {status}{marker}")

    print()


if __name__ == "__main__":
    main()
