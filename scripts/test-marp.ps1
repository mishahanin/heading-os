# Run MARP test suite: unit tests + self-test
$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$WorkspaceRoot = Split-Path -Parent $ScriptDir

Write-Host "=== MARP Test Suite ===" -ForegroundColor Cyan
Write-Host ""

Write-Host "--- Unit Tests (pytest) ---" -ForegroundColor Yellow
Set-Location $WorkspaceRoot
python -m pytest tests/test_marp_render.py tests/test_marp_integration.py -v --tb=short
Write-Host ""

Write-Host "--- Self-Test (render sample deck) ---" -ForegroundColor Yellow
python scripts/marp_render.py --self-test
Write-Host ""

Write-Host "=== All MARP tests complete ===" -ForegroundColor Cyan
