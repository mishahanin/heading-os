# ===========================================================================
# RETIRED 2026-05-26
# ===========================================================================
#
# This installer used to write a Windows Startup-folder shortcut pointing at
# scripts\launch-all-daemons.bat, which started the four background daemons
# (fireside, sentinel, sync-exchange, eval-drift) on every logon.
#
# Those daemons were migrated to the always-on service host on 2026-05-23
# and are now supervised by systemd there. The .bat launcher is itself a
# retirement stub (exit 0), so installing this shortcut would create a Startup
# entry that runs a no-op and prints misleading "daemons starting" output.
#
# Kept as a stub so a future reader finds the trail rather than a missing
# file. Running it does nothing useful and exits non-zero so any automation
# that still calls it surfaces the obsolescence.
#
# See:
#   the service-host migration plan  (Phase 7 cutover)
#   the service-host operations dir  (briefs, baseline)
#   scripts/launch-all-daemons.bat                    (sibling retired stub)
#
# The bridge daemon is unaffected - it runs as a WSL2 systemd-user unit
# (scripts/install-bridge-service.sh on Linux/WSL,
#  scripts/install-bridge-service-mac.py on macOS).
# ===========================================================================

Write-Host "  RETIRED 2026-05-26: the four background daemons run on the service host" -ForegroundColor Yellow
Write-Host "  under systemd. No Windows Startup shortcut is needed." -ForegroundColor Yellow
Write-Host "  See the service-host migration plan." -ForegroundColor Gray
exit 1
