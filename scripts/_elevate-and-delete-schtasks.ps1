$target = Join-Path $PSScriptRoot 'delete-legacy-schtasks.ps1'
Start-Process -FilePath powershell -Verb RunAs -Wait -ArgumentList @(
    '-NoProfile',
    '-ExecutionPolicy', 'Bypass',
    '-File', $target
)
