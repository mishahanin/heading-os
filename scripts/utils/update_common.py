#!/usr/bin/env python3
"""Shared update-manager helpers: current-version resolution and version
comparison. Lives in one place because scripts/update-manager.py is a hyphenated
CLI that cannot be imported, yet scripts/utils/update_apply.py needs the same
current-version logic. Prevents the two copies from drifting.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

from scripts.utils.update_registry import Component


def write_state(state: dict, path: Path) -> None:
    """Atomic state write: .tmp then os.replace. Shared by the CLI (`check`) and
    the apply module (`_mark_state`)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def resolve_current(comp: Component) -> str:
    """Run the component's `current.cmd`; if a `regex` is given, return its first
    capture group, else the first output line. A regex that does not match yields
    "" (unknown) rather than a misleading line of noise."""
    try:
        out = subprocess.run(["bash", "-c", comp.current.get("cmd", "")],
                            capture_output=True, text=True, timeout=30,
                            check=False).stdout.strip()
    except subprocess.TimeoutExpired:
        return ""
    regex = comp.current.get("regex")
    if regex:
        if not out:
            return ""
        m = re.search(regex, out)
        return m.group(1) if (m and m.groups()) else ""
    return out.splitlines()[0] if out else ""


def _norm_version(v: str):
    """Normalize for comparison so 2026.07.20 == 2026.7.20."""
    try:
        from packaging.version import Version  # noqa: PLC0415
        return ("v", str(Version(v)))
    except Exception:  # noqa: BLE001 - unparseable/absent -> compare raw stripped
        return ("s", v.strip())


def versions_differ(current: str, latest: str) -> bool:
    if not current or not latest:
        return False
    return _norm_version(current) != _norm_version(latest)
