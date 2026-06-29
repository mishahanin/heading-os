# ===========================================================================
# RETIRED 2026-05-24
# ===========================================================================
#
# This PowerShell installer used to create a Windows logon-startup item that
# launched the bridge daemon via C:\Python314\pythonw.exe + scripts\launch-
# bridge-daemon.bat + a Startup-folder .lnk shortcut.
#
# The bridge daemon has been migrated to WSL2 Ubuntu under a `systemd --user`
# unit (auto-starts with WSL). The Windows Startup shortcut was removed at
# cutover. Running this PS1 today would re-introduce a Windows-side daemon
# that conflicts on port 31415 with the live WSL daemon and would itself
# crash on missing dependencies (C:\Python314 has lost fastapi/anthropic/etc).
#
# Canonical installer is now scripts/install-bridge-service.sh, invoked from
# WSL:
#
#   wsl -d Ubuntu-24.04 -- bash "/path/to/heading-os/scripts/install-bridge-service.sh"
#
# Check daemon status:
#   wsl -d Ubuntu-24.04 -- systemctl --user status bridge-daemon
#
# See:
#   memory/reference_bridge_daemon_python_env.md   (interpreter + pip notes)
#   memory/reference_daemon_launcher_architecture.md (full migration history)
#
# This stub exits 1 so any caller (including automated re-provisioning) gets
# a hard fail rather than silently recreating the conflict.
# ===========================================================================

Write-Host "RETIRED 2026-05-24 - bridge daemon now runs under WSL systemd --user." -ForegroundColor Yellow
Write-Host "Use scripts/install-bridge-service.sh from WSL instead. See header for details." -ForegroundColor Yellow
exit 1
