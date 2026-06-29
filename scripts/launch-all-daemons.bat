@echo off
REM ===========================================================================
REM RETIRED 2026-05-23
REM ===========================================================================
REM
REM The four background daemons that this launcher used to start - fireside,
REM sync-exchange, sentinel, eval-drift - have been migrated to the always-on
REM service host and are now supervised there by systemd. The Startup-folder shortcut
REM (31C-launch-all-daemons.lnk) was removed at cutover.
REM
REM This file is kept as a stub so a future reader finds the trail rather
REM than a missing file. Running it by hand does nothing.
REM
REM See:
REM   the service-host migration plan  (Phase 7 cutover)
REM   the service-host operations dir  (briefs, baseline)
REM
REM The bridge daemon is unaffected - it has its own launcher
REM (scripts/launch-bridge-daemon.bat) and its own Startup shortcut
REM (31C-Bridge-Daemon.lnk).
REM ===========================================================================

exit /b 0
