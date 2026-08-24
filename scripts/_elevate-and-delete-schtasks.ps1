# Re-launch delete-legacy-schtasks.ps1 elevated.
#
# The -File path is quoted by hand. Windows PowerShell joins an -ArgumentList
# ARRAY with spaces and quotes nothing, so a $PSScriptRoot containing a space --
# `C:\Users\Some Name\...` -- reached the elevated child as several arguments and
# it failed behind the UAC prompt, where the error is not visible. Found by the
# 2026-08-23 audit. A single pre-quoted string is the shape that survives.
$target = Join-Path $PSScriptRoot 'delete-legacy-schtasks.ps1'
if (-not (Test-Path -LiteralPath $target)) {
    Write-Error "delete-legacy-schtasks.ps1 not found beside this script at $target"
    exit 1
}
$arguments = '-NoProfile -ExecutionPolicy Bypass -File "{0}"' -f $target
Start-Process -FilePath powershell -Verb RunAs -Wait -ArgumentList $arguments
