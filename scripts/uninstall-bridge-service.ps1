# Remove the Startup-folder shortcut + launcher .bat installed by
# scripts/install-bridge-service.ps1.

$ErrorActionPreference = "Stop"

$workspaceRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$batPath = Join-Path $workspaceRoot "scripts\launch-bridge-daemon.bat"
$startupDir = [Environment]::GetFolderPath("Startup")
$shortcutPath = Join-Path $startupDir "31C-Bridge-Daemon.lnk"

if (Test-Path $shortcutPath) {
    Remove-Item $shortcutPath -Force
    Write-Host "Removed Startup shortcut: $shortcutPath"
} else {
    Write-Host "No Startup shortcut at: $shortcutPath"
}

if (Test-Path $batPath) {
    Remove-Item $batPath -Force
    Write-Host "Removed launcher: $batPath"
} else {
    Write-Host "No launcher at: $batPath"
}

Write-Host ""
Write-Host "Note: this does NOT stop a currently-running daemon."
Write-Host "To stop it now, kill the pythonw.exe process running bridge-daemon.py."
