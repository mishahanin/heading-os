#!/usr/bin/env python3
"""Keep an ollama answering for this workspace, and say so plainly when it is not.

Every model this workspace uses runs on ONE machine - since 2026-08-23 that is
the Windows side of this laptop, reached across the WSL NAT gateway, and the
ollama that used to run inside WSL is uninstalled. That makes availability a
single point: when the Windows application is not running, embedding refuses and
the chronicle build stops. Autostart covers a reboot; nothing covered a crash.

This guard is what covers a crash. It probes the addresses
`config/ollama-hosts.yaml` names, and when none answers it starts the Windows
application through WSL interop and re-probes.

What it deliberately does NOT do:

* It never restarts a HEALTHY daemon. A watchdog that restarts on a timer would
  drop the resident model and turn the 0.87 s warm query back into a 7.00 s cold
  one, several times a day.
* It never installs, updates, or configures ollama. It starts what is there.
* It says which addresses it tried. "ollama is down" is unactionable; "nothing
  answered at http://172.30.48.1:11434 or :11436" is a next step.

Usage:
    python scripts/ollama-guard.py check          # probe only; exit 0 up, 1 down
    python scripts/ollama-guard.py heal           # probe, start if needed, re-probe
    python scripts/ollama-guard.py check --json

Exit codes: 0 an ollama is answering, 1 nothing answered, 2 nothing to probe
(the host configuration could not be read).
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils.colors import GRAY, GREEN, RED, RESET, YELLOW
from scripts.utils.ollama_host import probe as probe_host
from scripts.utils.ops_signals import ollama_hosts_in_use

# How long to wait for the application to bind after launching it. The Windows
# desktop app loads its settings and opens a tray icon before it listens;
# measured 2026-08-23, it answers within a few seconds of a cold start.
SETTLE_SECONDS = 4.0

# How long to let the interop relay run before cutting it loose.
#
# `cmd.exe /c start` returns immediately when run inside Windows. Through WSL
# interop it does NOT: measured 2026-08-23, the relay process stayed attached
# and a 20-second `subprocess.run` timeout expired while the ollama it had
# launched was already answering. So the relay is given a few seconds and then
# killed; killing it leaves the Windows application running, which is the whole
# reason `start` is used instead of executing the .exe directly.
LAUNCH_RELAY_WAIT = 6.0


@dataclass
class Outcome:
    """What the guard did, in the words the caller has to report."""

    up: bool
    launched: bool
    detail: str


def launch_command() -> list[str]:
    """The argv that starts the Windows ollama application from inside WSL.

    `%LOCALAPPDATA%` is expanded by cmd.exe rather than written out, because
    this engine is public and the real path carries a Windows account name. The
    empty string after `start` is the window TITLE argument: without it, cmd
    treats the quoted program path as the title and starts nothing.

    A list, never a shell string - `shell=True` is forbidden workspace-wide, and
    the shape is what makes that checkable.
    """
    return [
        "cmd.exe",
        "/c",
        "start",
        "",
        r"%LOCALAPPDATA%\Programs\Ollama\ollama app.exe",
    ]


def launch_windows_ollama() -> None:
    """Start the Windows application. Raises OSError when interop is unusable."""
    proc = subprocess.Popen(  # noqa: S603 - fixed argv, no user input, no shell
        launch_command(),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        proc.wait(timeout=LAUNCH_RELAY_WAIT)
    except subprocess.TimeoutExpired:
        # Expected on WSL, not an error - see LAUNCH_RELAY_WAIT. Left running it
        # would litter one orphaned relay per heal.
        proc.kill()


def ensure_up(
    hosts: list[str],
    *,
    probe=probe_host,
    launch=launch_windows_ollama,
    settle: float = SETTLE_SECONDS,
    attempts: int = 2,
) -> Outcome:
    """Probe `hosts`; start the application and re-probe when none answers.

    Args:
        hosts: addresses to try, in order. First to answer wins.
        probe: reachability test, injected for tests.
        launch: how to start the application, injected for tests.
        settle: seconds to wait after launching before re-probing.
        attempts: how many times to re-probe after a launch.

    Raises:
        ValueError: `hosts` is empty. That means the configuration could not be
            read, and reporting "up" from an empty list is a monitor that passes
            by knowing nothing.
    """
    if not hosts:
        raise ValueError("no ollama address to probe; check config/ollama-hosts.yaml")

    for host in hosts:
        if probe(host):
            return Outcome(True, False, host)

    try:
        launch()
    except (OSError, subprocess.SubprocessError) as exc:
        # Reported, never swallowed: an unusable interop path is the difference
        # between "ollama crashed" and "this guard cannot do anything about it".
        return Outcome(False, False, f"cannot start the Windows ollama: {exc}")

    for _ in range(max(1, attempts)):
        if settle:
            time.sleep(settle)
        for host in hosts:
            if probe(host):
                return Outcome(True, True, host)

    return Outcome(
        False, True, "started the application; still nothing at " + ", ".join(hosts)
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("check", "heal"), nargs="?", default="check")
    parser.add_argument("--json", action="store_true", help="machine-readable result")
    args = parser.parse_args()

    try:
        hosts = ollama_hosts_in_use()
    except (OSError, ValueError) as exc:
        sys.stderr.write(f"{RED}ollama-guard: cannot read the host config: {exc}{RESET}\n")
        return 2

    if args.command == "check":
        alive = next((h for h in hosts if probe_host(h)), None)
        outcome = Outcome(alive is not None, False, alive or ", ".join(hosts))
    else:
        try:
            outcome = ensure_up(hosts)
        except ValueError as exc:
            sys.stderr.write(f"{RED}ollama-guard: {exc}{RESET}\n")
            return 2

    if args.json:
        print(json.dumps({
            "up": outcome.up,
            "launched": outcome.launched,
            "detail": outcome.detail,
            "hosts": hosts,
        }, ensure_ascii=False))
    elif outcome.up and outcome.launched:
        print(f"{YELLOW}ollama was down; started it. Answering at {outcome.detail}{RESET}")
    elif outcome.up:
        print(f"{GREEN}ollama up{RESET} {GRAY}{outcome.detail}{RESET}")
    else:
        sys.stderr.write(f"{RED}ollama DOWN: {outcome.detail}{RESET}\n")

    return 0 if outcome.up else 1


if __name__ == "__main__":
    raise SystemExit(main())
