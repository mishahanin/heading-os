#!/usr/bin/env python3
"""Scheduled task/agent install + uninstall for Sentinel.

Centralizes all Task Scheduler (Windows), launchd (macOS), and systemd-user
(Linux) logic so setup.py, provision-exec.py, offboard-exec.py, and
emergency-revoke.py all use the same installers and never drift.

The workspace-sync schedule was retired in 2026-06 (see
plans/2026-06-26-retire-workspace-sync-disk-import.md): the destructive
orphan-delete engine is gone, code-down is a plain `git pull`, data-up is
`push-all.py`, and first-run record recovery is `import-legacy-records.py`.
`uninstall_sync_schedule` is kept so offboarding can still tear down any legacy
`31c-sync-*` timer/task left on a machine; nothing installs it anymore.

Public API:
    install_sentinel_schedule(slug, workspace_dir, target_platform=None)
    uninstall_sync_schedule(slug, target_platform=None)   # teardown-only
    uninstall_sentinel_schedule(slug, target_platform=None)

All install functions return True on verified success, False otherwise.
All uninstall functions are idempotent (no-op if nothing to remove).
"""

import os
import platform as platform_mod
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from scripts.utils.colors import CYAN, GREEN, RED, RESET, YELLOW


def _ok(msg: str) -> None:
    print(f"  {GREEN}[ok]{RESET} {msg}")


def _warn(msg: str) -> None:
    print(f"  {YELLOW}[warn]{RESET} {msg}")


def _fail(msg: str) -> None:
    print(f"  {RED}[fail]{RESET} {msg}")


def _run(cmd, check: bool = False) -> subprocess.CompletedProcess:
    """Run a subprocess command. Always captures output, never raises."""
    return subprocess.run(cmd, check=check, capture_output=True, text=True)


def _resolve_python() -> str | None:
    """Return absolute path to a working Python 3.11+ interpreter.

    Prefers sys.executable (the interpreter running us) since that is already
    verified. Falls back to shutil.which on candidate names, rejecting the
    Microsoft Store stub on Windows.
    """
    if sys.executable and Path(sys.executable).exists():
        return sys.executable
    for candidate in ("python3", "python", "py"):
        path = shutil.which(candidate)
        if not path:
            continue
        if "WindowsApps" in path:
            continue
        return path
    return None


def _resolve_python3_mac() -> str:
    """Resolve an absolute path to python3 for a macOS launchd plist.

    launchd agents do NOT inherit the user's shell PATH. Homebrew on Apple
    Silicon installs to /opt/homebrew/bin/python3; Intel Macs use
    /usr/local/bin/python3; system Python 3 lives at /usr/bin/python3 only
    when Xcode Command Line Tools are installed.
    """
    path = shutil.which("python3")
    if path and Path(path).exists():
        return path
    for fallback in ("/opt/homebrew/bin/python3", "/usr/local/bin/python3", "/usr/bin/python3"):
        if Path(fallback).exists():
            return fallback
    # Last-resort: return the bare name and let launchd fail with a useful error.
    return "/usr/local/bin/python3"


def _ensure_logs_dir(workspace_dir: Path) -> Path:
    """Create workspace/.sync/logs/ so scheduled tasks can write logs there."""
    logs_dir = workspace_dir / ".sync" / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    return logs_dir


# =============================================================================
# Windows - Task Scheduler
# =============================================================================


def _install_windows_task(
    *,
    task_name: str,
    workspace_dir: Path,
    script_rel_path: str,
    script_args: list,
    cadence_min: int,
    bat_filename: str,
) -> bool:
    """Install a Windows scheduled task with log redirection and verification.

    LEGACY: CEO standing rule is "no Windows Task Scheduler" - replaced on the
    CEO machine by WSL2 systemd-user units. This path persists ONLY for exec
    workspace provisioning until the daemon-based exec scheduler ships per
    threads/business/2026-05-17-migrate-exec-scheduling-to-daemons.md. Do not
    extend this function for new job types - new exec jobs should use the
    WSL/systemd pattern (or native systemd on Linux execs).
    """
    print(
        "  NOTICE: installing a Windows Task Scheduler task. This is the "
        "legacy exec-machine path; the CEO machine no longer uses schtasks. "
        "See threads/business/2026-05-17-migrate-exec-scheduling-to-daemons.md."
    )
    _ensure_logs_dir(workspace_dir)

    py = _resolve_python()
    if not py:
        _fail(f"Cannot install task {task_name}: no Python 3 interpreter on PATH.")
        return False

    bat_path = workspace_dir / "scripts" / bat_filename
    log_filename = f"{bat_filename.rsplit('.', 1)[0]}-task.log"

    # Use relative paths (cd /d makes the workspace the working directory).
    # This keeps the third command short (~80 chars) instead of ~200 chars,
    # so editors do not visually wrap it and users do not accidentally split
    # it across lines while "tidying" the file. The Nina Falk incident
    # (2026-04-19 .. 2026-04-27) was caused by exactly that: the python
    # invocation got broken across three separate BAT lines, so the
    # scheduled task fired but ran no useful work.
    script_rel_win = script_rel_path.replace("/", "\\")
    log_rel_win = f".sync\\logs\\{log_filename}"

    args_str = ""
    if script_args:
        args_str = " " + " ".join(
            f'"{a}"' if " " in a else a for a in script_args
        )

    bat_content = (
        "@echo off\r\n"
        "REM 31C scheduled task -- generated by scripts/utils/schedule.py\r\n"
        "REM CRITICAL: the python invocation below MUST stay on a single line.\r\n"
        "REM If this file looks wrong, regenerate it by running:\r\n"
        "REM   python -c \"from scripts.utils.schedule import install_sentinel_schedule; "
        "from pathlib import Path; install_sentinel_schedule('SLUG', Path.cwd())\"\r\n"
        f'cd /d "{workspace_dir}"\r\n'
        f'"{py}" "{script_rel_win}"{args_str} >> "{log_rel_win}" 2>&1\r\n'
    )
    bat_path.write_text(bat_content, encoding="utf-8")

    if cadence_min >= 60 and cadence_min % 60 == 0:
        sc_args = ["/sc", "HOURLY", "/mo", str(cadence_min // 60)]
    else:
        sc_args = ["/sc", "MINUTE", "/mo", str(cadence_min)]

    result = _run([
        "schtasks", "/create",
        "/tn", task_name,
        "/tr", str(bat_path),
        *sc_args,
        "/rl", "LIMITED",
        "/f",
    ])

    if result.returncode != 0:
        _fail(
            f"schtasks /create failed (exit {result.returncode}): "
            f"{(result.stderr or result.stdout).strip()}"
        )
        print(f"         Retry manually from a non-elevated PowerShell:")
        print(
            f'         schtasks /create /tn {task_name} /tr "{bat_path}" '
            f'{" ".join(sc_args)} /rl LIMITED /f'
        )
        return False

    verify = _run(["schtasks", "/query", "/tn", task_name])
    if verify.returncode != 0:
        _warn(f"schtasks /create reported success but /query cannot find {task_name}.")
        return False

    _ok(f"Windows Task Scheduler: {task_name} (every {cadence_min} min) - verified")
    return True


def _uninstall_windows_task(task_name: str) -> bool:
    result = _run(["schtasks", "/delete", "/tn", task_name, "/f"])
    if result.returncode == 0:
        _ok(f"Removed Windows scheduled task: {task_name}")
        return True
    return False


# =============================================================================
# macOS - launchd
# =============================================================================


def _install_launchd_agent(
    *,
    label: str,
    workspace_dir: Path,
    script_rel_path: str,
    script_args: list,
    start_interval: int,
) -> bool:
    """Install a macOS launchd user agent with verification."""
    _ensure_logs_dir(workspace_dir)

    python3_path = _resolve_python3_mac()
    plist_path = Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"
    plist_path.parent.mkdir(parents=True, exist_ok=True)

    script_abs = workspace_dir / script_rel_path
    log_stem = label.replace(".", "-")

    args_xml = ""
    if script_args:
        args_xml = "\n        " + "\n        ".join(
            f"<string>{a}</string>" for a in script_args
        )

    plist_content = textwrap.dedent(f"""\
        <?xml version="1.0" encoding="UTF-8"?>
        <!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
        <plist version="1.0">
        <dict>
            <key>Label</key>
            <string>{label}</string>
            <key>ProgramArguments</key>
            <array>
                <string>{python3_path}</string>
                <string>{script_abs}</string>{args_xml}
            </array>
            <key>WorkingDirectory</key>
            <string>{workspace_dir}</string>
            <key>StartInterval</key>
            <integer>{start_interval}</integer>
            <key>StandardOutPath</key>
            <string>{workspace_dir}/.sync/logs/{log_stem}-stdout.log</string>
            <key>StandardErrorPath</key>
            <string>{workspace_dir}/.sync/logs/{log_stem}-stderr.log</string>
            <key>ProcessType</key>
            <string>Background</string>
            <key>LowPriorityIO</key>
            <true/>
            <key>Nice</key>
            <integer>5</integer>
        </dict>
        </plist>
    """)

    plist_path.write_text(plist_content, encoding="utf-8")

    # Idempotency: unload any existing instance before reloading.
    _run(["launchctl", "unload", str(plist_path)])

    uid = os.getuid() if hasattr(os, "getuid") else None
    loaded = False
    if uid is not None:
        result = _run(["launchctl", "bootstrap", f"gui/{uid}", str(plist_path)])
        loaded = result.returncode == 0
    if not loaded:
        result = _run(["launchctl", "load", str(plist_path)])
        loaded = result.returncode == 0

    if not loaded:
        _fail(f"launchctl could not load {label}. Plist at: {plist_path}")
        return False

    verify = _run(["launchctl", "list", label])
    if verify.returncode == 0:
        _ok(f"macOS launchd: {label} (every {start_interval}s) - loaded and verified")
        return True
    _warn(f"launchctl loaded but 'list' cannot find {label}. Agent may still run.")
    return False


def _uninstall_launchd_agent(label: str) -> bool:
    plist_path = Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"
    if not plist_path.exists():
        return False
    uid = os.getuid() if hasattr(os, "getuid") else None
    if uid is not None:
        _run(["launchctl", "bootout", f"gui/{uid}/{label}"])
    _run(["launchctl", "unload", str(plist_path)])
    plist_path.unlink(missing_ok=True)
    _ok(f"Removed launchd agent: {label}")
    return True


# =============================================================================
# Linux - systemd user timer
# =============================================================================


def _install_systemd_user_timer(
    *,
    unit_name: str,
    workspace_dir: Path,
    script_rel_path: str,
    script_args: list,
    cadence_min: int,
    description: str,
) -> bool:
    """Install a systemd user .service + .timer pair under ~/.config/systemd/user/.

    Idempotent: re-enables existing unit. Returns True on verified success.
    Writes logs to workspace/.sync/logs/<unit_name>.log.
    """
    py = _resolve_python()
    if not py:
        _fail(f"Cannot install timer {unit_name}: no Python 3 interpreter found.")
        return False

    if not shutil.which("systemctl"):
        _fail("systemctl not found. systemd user units require systemd >= 226.")
        return False

    user_units_dir = Path.home() / ".config" / "systemd" / "user"
    user_units_dir.mkdir(parents=True, exist_ok=True)

    _ensure_logs_dir(workspace_dir)
    log_path = workspace_dir / ".sync" / "logs" / f"{unit_name}.log"

    args_str = " ".join(script_args) if script_args else ""
    script_abs = workspace_dir / script_rel_path
    exec_start = f'{py} {script_abs}'
    if args_str:
        exec_start = f'{exec_start} {args_str}'

    service_unit = user_units_dir / f"{unit_name}.service"
    service_content = textwrap.dedent(f"""\
        [Unit]
        Description={description}
        After=network.target

        [Service]
        Type=oneshot
        WorkingDirectory={workspace_dir}
        ExecStart={exec_start}
        StandardOutput=append:{log_path}
        StandardError=append:{log_path}
        Environment=PYTHONUNBUFFERED=1
    """)
    service_unit.write_text(service_content, encoding="utf-8")

    timer_unit = user_units_dir / f"{unit_name}.timer"
    timer_content = textwrap.dedent(f"""\
        [Unit]
        Description=Timer for {description}

        [Timer]
        OnBootSec=2min
        OnUnitActiveSec={cadence_min}min
        Persistent=true
        Unit={unit_name}.service

        [Install]
        WantedBy=timers.target
    """)
    timer_unit.write_text(timer_content, encoding="utf-8")

    _run(["systemctl", "--user", "daemon-reload"])
    result = _run(["systemctl", "--user", "enable", "--now", f"{unit_name}.timer"])
    if result.returncode != 0:
        _fail(f"systemctl enable failed: {result.stderr.strip() or result.stdout.strip()}")
        return False

    verify = _run(["systemctl", "--user", "is-active", f"{unit_name}.timer"])
    if verify.returncode != 0:
        _warn(f"timer enabled but is-active reports: {verify.stdout.strip()}")

    _ok(f"systemd user timer: {unit_name}.timer (every {cadence_min}m) — enabled")
    _ok("Run `loginctl enable-linger $USER` once to keep timers running after logout")
    return True


def _uninstall_systemd_user_timer(unit_name: str) -> bool:
    """Stop, disable, and remove the systemd user .service + .timer pair. Idempotent."""
    user_units_dir = Path.home() / ".config" / "systemd" / "user"
    timer_unit = user_units_dir / f"{unit_name}.timer"
    service_unit = user_units_dir / f"{unit_name}.service"

    if not (timer_unit.exists() or service_unit.exists()):
        return True  # nothing to remove

    if shutil.which("systemctl"):
        _run(["systemctl", "--user", "disable", "--now", f"{unit_name}.timer"])
        _run(["systemctl", "--user", "daemon-reload"])

    for unit in (timer_unit, service_unit):
        if unit.exists():
            try:
                unit.unlink()
            except OSError as e:
                _warn(f"Could not remove {unit}: {e}")

    _ok(f"removed systemd user units: {unit_name}.{{service,timer}}")
    return True


# =============================================================================
# Public API
# =============================================================================


def install_sentinel_schedule(
    slug: str,
    workspace_dir: Path,
    target_platform: str = None,
) -> bool:
    """Install the 15-minute Sentinel check scheduled task for this exec."""
    target_platform = (target_platform or platform_mod.system()).lower()
    sentinel_script = workspace_dir / "scripts" / "sentinel.py"
    if not sentinel_script.exists():
        _warn(f"sentinel.py not found at {sentinel_script}; skipping Sentinel scheduling.")
        return False
    if target_platform == "windows":
        return _install_windows_task(
            task_name=f"31C-Sentinel-{slug}",
            workspace_dir=workspace_dir,
            script_rel_path="scripts/sentinel.py",
            script_args=["--check"],
            cadence_min=15,
            bat_filename="sentinel-check.bat",
        )
    if target_platform == "darwin":
        return _install_launchd_agent(
            label=f"io.31c.sentinel.{slug}",
            workspace_dir=workspace_dir,
            script_rel_path="scripts/sentinel.py",
            script_args=["--check"],
            start_interval=900,
        )
    if target_platform == "linux":
        return _install_systemd_user_timer(
            unit_name=f"31c-sentinel-{slug}",
            workspace_dir=workspace_dir,
            script_rel_path="scripts/sentinel.py",
            script_args=["--check"],
            cadence_min=15,
            description=f"31C Sentinel comms monitor ({slug})",
        )
    _warn(f"Unsupported platform: {target_platform}. Add manually to crontab:")
    print(f"         */15 * * * * cd {workspace_dir} && python3 scripts/sentinel.py --check")
    return False


def uninstall_sync_schedule(slug: str, target_platform: str = None) -> bool:
    target_platform = (target_platform or platform_mod.system()).lower()
    if target_platform == "windows":
        return _uninstall_windows_task(f"31C-Sync-{slug}")
    if target_platform == "darwin":
        return _uninstall_launchd_agent(f"io.31c.sync.{slug}")
    if target_platform == "linux":
        return _uninstall_systemd_user_timer(f"31c-sync-{slug}")
    return False


def uninstall_sentinel_schedule(slug: str, target_platform: str = None) -> bool:
    target_platform = (target_platform or platform_mod.system()).lower()
    if target_platform == "windows":
        return _uninstall_windows_task(f"31C-Sentinel-{slug}")
    if target_platform == "darwin":
        return _uninstall_launchd_agent(f"io.31c.sentinel.{slug}")
    if target_platform == "linux":
        return _uninstall_systemd_user_timer(f"31c-sentinel-{slug}")
    return False
