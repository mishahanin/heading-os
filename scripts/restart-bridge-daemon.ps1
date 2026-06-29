# Restart the bridge daemon. Stops any running pythonw process serving
# bridge-daemon.py, then relaunches via the installed launcher .bat.
#
# Usage (from an elevated PowerShell, anywhere):
#   & "C:\ai\claude-workspaces\ceo-main\scripts\restart-bridge-daemon.ps1"

$ErrorActionPreference = "Continue"

$workspaceRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$daemonScript = Join-Path $workspaceRoot "scripts\bridge-daemon.py"
$launcherBat = Join-Path $workspaceRoot "scripts\launch-bridge-daemon.bat"

# Step 1: find any pythonw process running bridge-daemon.py and kill it.
$killed = $false
Get-CimInstance Win32_Process -Filter "Name = 'pythonw.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -and $_.CommandLine -like "*bridge-daemon.py*" } |
    ForEach-Object {
        Write-Host "Stopping bridge daemon: pid $($_.ProcessId)"
        Stop-Process -Id $_.ProcessId -Force
        $killed = $true
    }
if (-not $killed) {
    Write-Host "No running bridge daemon found."
}

Start-Sleep -Seconds 2

# Step 2: remove the stale port file so the next boot picks fresh.
$stateDir = Join-Path $workspaceRoot ".daemon-state"
Get-ChildItem -Path $stateDir -Filter "port" -ErrorAction SilentlyContinue | Remove-Item -Force -ErrorAction SilentlyContinue

# Step 3: relaunch via the installed .bat (which uses pythonw windowless).
if (Test-Path $launcherBat) {
    Write-Host "Relaunching via: $launcherBat"
    & $launcherBat
} else {
    Write-Host "Launcher .bat missing - run scripts/install-bridge-service.ps1 first." -ForegroundColor Red
    exit 1
}

# Step 4: poll for the port file (pythonw bootstrap can take 5-10s on a cold start).
$portFile = Join-Path $stateDir "port"
$maxWaitSeconds = 20
$portReady = $false
for ($i = 1; $i -le $maxWaitSeconds; $i++) {
    Start-Sleep -Seconds 1
    if (Test-Path $portFile) {
        $portReady = $true
        break
    }
}

if ($portReady) {
    $port = (Get-Content $portFile -Raw).Trim()
    # Step 5: probe /health to confirm uvicorn is actually serving.
    $healthOk = $false
    for ($i = 1; $i -le 5; $i++) {
        try {
            $r = Invoke-WebRequest -Uri "http://127.0.0.1:$port/health" -TimeoutSec 2 -UseBasicParsing
            if ($r.StatusCode -eq 200) {
                $healthOk = $true
                break
            }
        } catch {
            Start-Sleep -Seconds 1
        }
    }
    if ($healthOk) {
        Write-Host "New daemon up on port $port (health OK)" -ForegroundColor Green
    } else {
        Write-Host "Port file written but /health did not respond within 5s on port $port" -ForegroundColor Yellow
    }
    Write-Host "Health: python `"$daemonScript`" --health"
    Write-Host "Browser: http://127.0.0.1:$port/"
} else {
    Write-Host "Daemon did not write port file within ${maxWaitSeconds}s - check $stateDir\bridge.log" -ForegroundColor Yellow
}
