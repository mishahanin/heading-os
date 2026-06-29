#!/usr/bin/env python3
"""Install the bridge daemon as a macOS launchd agent (per-user).

This is the macOS counterpart to scripts/install-bridge-service.ps1.
launchd is the macOS-native equivalent of the Windows Startup folder:
it starts the daemon at user login and keeps it running. NOT cron - per
the workspace standing rule against schedulers that the user cannot
easily inspect or pause.

Usage:
  python3 scripts/install-bridge-service-mac.py
  python3 scripts/install-bridge-service-mac.py --uninstall

Idempotent: re-running --install overwrites the plist with the current
workspace path + python interpreter so the agent picks up moves.

Behaviour:
- Writes ~/Library/LaunchAgents/com.31c.bridge-daemon.plist
- launchctl unload (idempotent) + launchctl load -w to activate
- StandardOut/StandardError -> <workspace>/.daemon-state/bridge.launchd.log
- RunAtLoad: true (starts at login)
- KeepAlive: true (restarts on crash)
"""
from __future__ import annotations

import argparse
import os
import plistlib
import subprocess
import sys
from pathlib import Path

LABEL = "com.31c.bridge-daemon"
WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
DAEMON_SCRIPT = WORKSPACE_ROOT / "scripts" / "bridge-daemon.py"
PLIST_DIR = Path.home() / "Library" / "LaunchAgents"
PLIST_PATH = PLIST_DIR / f"{LABEL}.plist"


def _ensure_macos() -> None:
    if sys.platform != "darwin":
        print(
            f"This installer is macOS-only (sys.platform={sys.platform!r}). "
            "On Windows, use scripts/install-bridge-service.ps1 instead.",
            file=sys.stderr,
        )
        sys.exit(2)


def _resolve_python() -> str:
    """Use the interpreter that ran this script so the user can pick which
    Python (system, brew, asdf, conda) by simply running it under that
    interpreter. Falls back to PATH lookup only if sys.executable looks
    wrong (e.g. truncated)."""
    if sys.executable and Path(sys.executable).exists():
        return sys.executable
    import shutil
    p = shutil.which("python3") or shutil.which("python")
    if not p:
        print("python3 not found on PATH", file=sys.stderr)
        sys.exit(1)
    return p


def _build_plist(python_exe: str) -> dict:
    log_path = WORKSPACE_ROOT / ".daemon-state" / "bridge.launchd.log"
    return {
        "Label": LABEL,
        "ProgramArguments": [
            python_exe,
            str(DAEMON_SCRIPT),
            "--start",
        ],
        "WorkingDirectory": str(WORKSPACE_ROOT),
        "RunAtLoad": True,
        "KeepAlive": True,
        # Log both streams to the same file so a crash-loop is visible.
        "StandardOutPath": str(log_path),
        "StandardErrorPath": str(log_path),
        # Inherit PATH so 'claude' on the deep-link launch target is resolvable.
        "EnvironmentVariables": {
            "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        },
    }


def install() -> None:
    if not DAEMON_SCRIPT.is_file():
        print(f"Daemon script not found: {DAEMON_SCRIPT}", file=sys.stderr)
        sys.exit(1)
    python_exe = _resolve_python()
    PLIST_DIR.mkdir(parents=True, exist_ok=True)
    log_dir = WORKSPACE_ROOT / ".daemon-state"
    log_dir.mkdir(parents=True, exist_ok=True)

    # Unload existing agent (idempotent: ignore failure if not previously loaded).
    subprocess.run(
        ["launchctl", "unload", "-w", str(PLIST_PATH)],
        check=False, capture_output=True,
    )

    payload = _build_plist(python_exe)
    with PLIST_PATH.open("wb") as fp:
        plistlib.dump(payload, fp)
    print(f"Wrote plist: {PLIST_PATH}")

    result = subprocess.run(
        ["launchctl", "load", "-w", str(PLIST_PATH)],
        check=False, capture_output=True, text=True,
    )
    if result.returncode != 0:
        print(f"launchctl load failed: {result.stderr.strip()}", file=sys.stderr)
        sys.exit(1)
    print(f"Loaded launchd agent: {LABEL}")
    print()
    print("The daemon will start automatically at next login and is")
    print("running now. To check health:")
    print(f"  python3 {DAEMON_SCRIPT} --health")
    print("To tail logs:")
    print(f"  tail -f {WORKSPACE_ROOT}/.daemon-state/bridge.launchd.log")


def uninstall() -> None:
    if PLIST_PATH.exists():
        subprocess.run(
            ["launchctl", "unload", "-w", str(PLIST_PATH)],
            check=False, capture_output=True,
        )
        PLIST_PATH.unlink()
        print(f"Removed plist + unloaded agent: {PLIST_PATH}")
    else:
        print(f"No plist at {PLIST_PATH} - nothing to uninstall.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--uninstall", action="store_true",
                        help="remove the launchd agent + plist")
    args = parser.parse_args()
    _ensure_macos()
    if args.uninstall:
        uninstall()
    else:
        install()


if __name__ == "__main__":
    main()
