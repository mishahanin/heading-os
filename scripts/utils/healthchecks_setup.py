#!/usr/bin/env python3
"""Shared Healthchecks.io check-provisioning helpers.

Consumed by setup-fireside-healthchecks.py and setup-daemon-healthchecks.py.
Upserts checks (idempotent via HC.io `unique: ["name"]`) and writes the
resulting ping URLs back into the engine .env. This is provisioning logic only
-- the runtime ping helper used by the daemons is scripts/utils/healthchecks.ping().

A check spec is a dict:
    {
        "env_key": "STEWARD_HC_SENTINEL",   # .env key to receive the ping URL
        "name": "steward-sentinel",          # HC.io check name (unique key)
        "tags": "steward steward-critical",
        "desc": "...",
        "grace": 1200,                       # seconds
        # exactly one of:
        "timeout": 900,                      # simple-period check (seconds), OR
        "schedule": "0 2 * * *", "tz": "Asia/Dubai",  # cron check
    }
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import requests

from scripts.utils.workspace import get_workspace_root

API_BASE = "https://healthchecks.io/api/v3"
_ENV_FILE = get_workspace_root() / ".env"


def load_env_key() -> str:
    """Return HEALTHCHECKS_API_KEY from the engine .env, or exit with an error."""
    if not _ENV_FILE.exists():
        sys.exit(f"ERROR: .env not found at {_ENV_FILE}")
    for line in _ENV_FILE.read_text(encoding="utf-8").splitlines():
        if line.startswith("HEALTHCHECKS_API_KEY="):
            return line.split("=", 1)[1].strip()
    sys.exit("ERROR: HEALTHCHECKS_API_KEY not set in .env")


def upsert_check(api_key: str, spec: dict, dry_run: bool) -> dict:
    """Create or update one check by name (idempotent). Returns the HC.io JSON."""
    payload = {
        "name": spec["name"],
        "tags": spec["tags"],
        "desc": spec["desc"],
        "grace": spec["grace"],
        "channels": "*",
        "unique": ["name"],
    }
    if "schedule" in spec:
        payload["schedule"] = spec["schedule"]
        payload["tz"] = spec["tz"]
    else:
        payload["timeout"] = spec["timeout"]

    if dry_run:
        print(f"  DRY: would upsert {spec['name']}")
        return {"ping_url": "<dry-run>", "name": spec["name"]}

    r = requests.post(
        f"{API_BASE}/checks/",
        headers={"X-Api-Key": api_key, "Content-Type": "application/json"},
        json=payload,
        timeout=30,
    )
    r.raise_for_status()
    return r.json()


def write_env(updates: dict) -> None:
    """Atomically upsert KEY=value lines into the engine .env (tmp + os.replace)."""
    content = _ENV_FILE.read_text(encoding="utf-8")
    for key, val in updates.items():
        pattern = re.compile(rf"^{re.escape(key)}=.*$", re.MULTILINE)
        if pattern.search(content):
            content = pattern.sub(f"{key}={val}", content)
        else:
            if not content.endswith("\n"):
                content += "\n"
            content += f"{key}={val}\n"
    tmp = _ENV_FILE.with_suffix(_ENV_FILE.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, _ENV_FILE)


def run_setup(checks: list, dry_run: bool) -> None:
    """Upsert every check spec and write its ping URL back to .env."""
    api_key = load_env_key()
    print(f"Healthchecks.io API base: {API_BASE}")
    print(f"Upserting {len(checks)} checks...")

    env_updates = {}
    for spec in checks:
        result = upsert_check(api_key, spec, dry_run)
        ping_url = result.get("ping_url", "<no-ping_url>")
        env_updates[spec["env_key"]] = ping_url
        marker = "DRY" if dry_run else "OK"
        print(f"  {marker}  {spec['name']:30s} -> {ping_url}")

    if not dry_run:
        write_env(env_updates)
        print(f"\nWrote {len(env_updates)} ping URLs to {_ENV_FILE}")
    else:
        print("\n(dry-run; .env not touched)")
