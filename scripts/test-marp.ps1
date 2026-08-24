# Run MARP test suite: unit tests + self-test
$ErrorActionPreference = "Stop"

# `$ErrorActionPreference = "Stop"` does NOT stop on a NATIVE command's non-zero
# exit code in Windows PowerShell 5.1 -- it only traps terminating .NET errors.
# So a failing pytest printed its failures, the script carried on, printed
# "All MARP tests complete", and exited 0. Every native call below is therefore
# checked against $LASTEXITCODE by hand.
function Invoke-Checked {
    param([string]$Label, [scriptblock]$Command)
    & $Command
    if ($LASTEXITCODE -ne 0) {
        Write-Host "FAILED: $Label (exit $LASTEXITCODE)" -ForegroundColor Red
        exit $LASTEXITCODE
    }
}

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$WorkspaceRoot = Split-Path -Parent $ScriptDir

Write-Host "=== MARP Test Suite ===" -ForegroundColor Cyan
Write-Host ""

Write-Host "--- Unit Tests (pytest) ---" -ForegroundColor Yellow
Set-Location $WorkspaceRoot
Invoke-Checked "unit tests" { python -m pytest tests/test_marp_render.py tests/test_marp_integration.py -v --tb=short }
Write-Host ""

Write-Host "--- Self-Test (render sample deck) ---" -ForegroundColor Yellow
Invoke-Checked "self-test" { python scripts/marp_render.py --self-test }
Write-Host ""

Write-Host "=== All MARP tests passed ===" -ForegroundColor Cyan
exit 0
