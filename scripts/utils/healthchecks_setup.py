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
import stat
import sys
from pathlib import Path

import requests

from scripts.utils.atomic import atomic_write_text
from scripts.utils.workspace import (
    get_workspace_root,
    iter_env_pairs,
    parse_env_line,
)

API_BASE = "https://healthchecks.io/api/v3"
_ENV_FILE = get_workspace_root() / ".env"


def load_env_key() -> str:
    """Return HEALTHCHECKS_API_KEY from the engine .env, or exit with an error.

    Parsing is `paths.parse_env_line`, shared with `load_env` and every other
    reader of this file. This function used to keep the quotes: MEASURED
    2026-08-28, `HEALTHCHECKS_API_KEY="abc"` written in the dotenv style the
    canonical loader documents as supported yielded the 5-character string
    '"abc"', which then went out as the `X-Api-Key` header and came back 401
    with nothing in the message to say why. A leading space in front of the key
    made it invisible here while `load_env` read it, and the exit said the key
    was "not set in .env" while it sat in the file.

    An undecodable file used to raise UnicodeDecodeError out of a provisioning
    CLI as a traceback. It now exits with the reason.
    """
    if not _ENV_FILE.exists():
        sys.exit(f"ERROR: .env not found at {_ENV_FILE}")
    try:
        text = _ENV_FILE.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        sys.exit(f"ERROR: could not read {_ENV_FILE}: {exc}")
    for key, value in iter_env_pairs(text):
        if key == "HEALTHCHECKS_API_KEY":
            return value
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
    """Atomically upsert KEY=value lines into the engine .env (tmp + os.replace).

    A writer and a reader that disagree about which line assigns a key leave a
    duplicate behind, and then the two disagree about which value is live. This
    matched with `^KEY=.*$`, which no more sees `  KEY=old` than the old reader
    did. MEASURED 2026-08-28 on `  HEALTHCHECKS_API_KEY=OLD`: the substitution
    matched nothing, `KEY=NEW` was appended, and afterwards this module's own
    reader answered NEW while `load_env` (setdefault, so the FIRST line wins)
    answered OLD. The daemons read the ping URL through `load_env`, so they
    would have gone on pinging the old check while the provisioner reported the
    new one written.

    Matching is now `parse_env_line`, the same grammar every reader uses, and
    the replacement line is written bare, so the file ends up agreeing with
    itself rather than carrying an indented ghost of the old value.
    """
    lines = _ENV_FILE.read_text(encoding="utf-8").splitlines()
    remaining = dict(updates)
    for i, line in enumerate(lines):
        pair = parse_env_line(line)
        if pair is not None and pair[0] in updates:
            lines[i] = f"{pair[0]}={updates[pair[0]]}"
            remaining.pop(pair[0], None)
    for key, val in remaining.items():
        lines.append(f"{key}={val}")
    content = "".join(f"{line}\n" for line in lines)
    # Preserve the file's own mode instead of letting the umask decide it. A
    # fresh tempfile is 0644, and `os.replace` carries the tempfile's mode onto
    # the target - so writing one ping URL back silently widened a 0600 `.env`,
    # the file holding every credential this workspace loads, to world-readable.
    # `atomic.atomic_write_text` also unlinks its tempfile on any error, which
    # the two-line version did not: a failure mid-write left `.env.tmp`, a full
    # copy of the secrets, beside the real file.
    try:
        mode = stat.S_IMODE(_ENV_FILE.stat().st_mode)
    except OSError:
        mode = 0o600
    atomic_write_text(_ENV_FILE, content, mode=mode)


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
