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
    # Every failure inside here is "unknown", which is what "" means. Only
    # TimeoutExpired was caught, so two ordinary config-data faults escaped a
    # function documented to answer "" rather than raise. Measured 2026-08-30:
    # a component whose `current.regex` is `v(\d+` (an unbalanced paren, and the
    # registry is hand-edited YAML) raised
    # `re.error: missing ), unterminated subpattern` out of `resolve_current`,
    # and no `bash` on PATH would raise OSError the same way. `cmd_apply`'s
    # broad boundary handler happened to swallow the first; the `check` CLI path
    # this module exists for gets the raw exception.
    try:
        # `errors="replace"`, not the default strict decode. `text=True` decodes
        # the child's stdout STRICTLY, and `UnicodeDecodeError` is a `ValueError`
        # -- neither a `SubprocessError` nor an `OSError`, so the handler below
        # cannot catch it. That is the same fault as the two named above, missed
        # a third time: a tool whose version banner carries one non-UTF-8 byte (a
        # Latin-1 copyright sign, an accented word -- ordinary in a vendor CLI)
        # raised out of a function documented to answer "" rather than raise, and
        # ONE such component took the whole `check` run down. MEASURED 2026-09-01
        # with `printf 'v1.2.3 \xe9dition\n'`.
        #
        # Replacing rather than widening the `except`: the version is RECOVERABLE
        # here, and returning "" would report a healthy tool as unknown, which is
        # the misleading answer this function's "" exists to avoid. A stray byte
        # is never part of the digits a `current.regex` captures.
        # `update_sources._get_json` makes the opposite choice for the same
        # exception, correctly -- an undecodable JSON body is not recoverable.
        out = subprocess.run(["bash", "-c", comp.current.get("cmd", "")],
                            capture_output=True, text=True, errors="replace",
                            timeout=30, check=False).stdout.strip()
    except (subprocess.SubprocessError, OSError):
        return ""
    regex = comp.current.get("regex")
    if regex:
        if not out:
            return ""
        try:
            m = re.search(regex, out)
        except re.error:
            return ""
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
