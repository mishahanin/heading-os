# One-shot cleanup: delete all 10 disabled 31C scheduled tasks.
#
# These were created during the Task Scheduler era of the workspace
# (pre-2026-05-13 fireside-daemon migration). All have been Disabled
# for weeks. The 2026-05-17 cleanup pass deletes them entirely so
# the workspace has zero dependency on Windows Task Scheduler.
#
# Usage: right-click this file, "Run with PowerShell" as Administrator.
# (Or: open elevated PowerShell, run: powershell -File scripts/delete-legacy-schtasks.ps1)
#
# This script is idempotent. Run it again and it will report "not found"
# for already-deleted tasks - safe to re-run.

$tasks = @(
    '31C-Fireside-poll',
    '31C-Fireside-dayof-reminders',
    '31C-Fireside-email-backup',
    '31C-Fireside-health-check',
    '31C-Fireside-helmsman-brief',
    '31C-Fireside-speaker-dms',
    '31C-Fireside-sunday-preview',
    '31C-Fireside-unpin-weekly',
    '31C-Fireside-weekly-discrepancy-report',
    '31C-Sentinel-Watchdog'
)

# Elevation check
$current = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($current)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltinRole]::Administrator)) {
    Write-Host ""
    Write-Host "  ERROR: this script must run as Administrator." -ForegroundColor Red
    Write-Host "  Right-click the file -> Run with PowerShell" -ForegroundColor Yellow
    Write-Host "  OR open an elevated terminal and re-run." -ForegroundColor Yellow
    Write-Host ""
    Read-Host "  Press Enter to exit"
    exit 1
}

Write-Host ""
Write-Host "  Deleting 10 legacy 31C scheduled tasks..." -ForegroundColor Cyan

$deleted = 0
$notFound = 0
$failed = 0

foreach ($t in $tasks) {
    try {
        $existing = Get-ScheduledTask -TaskName $t -ErrorAction Stop
        Unregister-ScheduledTask -TaskName $t -Confirm:$false -ErrorAction Stop
        Write-Host "    deleted: $t" -ForegroundColor Green
        $deleted++
    } catch [Microsoft.PowerShell.Cmdletization.Cim.CimJobException] {
        Write-Host "    not found (already gone): $t" -ForegroundColor Gray
        $notFound++
    } catch {
        Write-Host "    FAILED: $t  ($($_.Exception.Message))" -ForegroundColor Red
        $failed++
    }
}

Write-Host ""
Write-Host "  Summary:" -ForegroundColor Cyan
Write-Host "    deleted:   $deleted"
Write-Host "    not found: $notFound"
Write-Host "    failed:    $failed"
Write-Host ""

if ($failed -eq 0) {
    Write-Host "  All clean. Workspace now has zero Task Scheduler dependency." -ForegroundColor Green
} else {
    Write-Host "  Some deletions failed - inspect via taskschd.msc" -ForegroundColor Yellow
}

Write-Host ""
Read-Host "  Press Enter to close"
