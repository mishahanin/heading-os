---
name: bridge-health
description: Quick health check on the bridge daemon (CEO machine + any exec mirrors). Surfaces heartbeat staleness, version drift, active sessions, and the Phase 1 -> Phase 2 adoption gate metrics. Use when the dashboard feels stale, the sync-pill is red, the daemon may have crashed, or before a /push-updates that changes daemon config. NEVER auto-trigger - invoke explicitly via /bridge-health.
argument-hint: "[--stale SECONDS] [--gate] [--json]"
allowed-tools: "Bash(python3:*), Bash(python:*)"
disable-model-invocation: true
model: haiku
metadata:
  author: Misha Hanin
  email: misha.hanin@odinix.com
  version: "1.0"
x-31c-orchestration:
  parallel_safe: true
  shared_state: []
  triggers: []
x-31c-capability:
  what: >
    Read-only rollup health check on the bridge daemon fleet - heartbeat
    staleness, version drift, config drift, active sessions, and the optional
    Phase 1 to Phase 2 adoption-gate verdict.
  how: >
    Explicit invocation only - run /bridge-health [--stale SECONDS] [--gate]
    [--json]. Wraps scripts/daemon-fleet-health.py and bridge-daemon.py
    --health; prints a status grid and a one-line verdict. Never auto-triggers.
  when: >
    Use when the dashboard feels stale, the sync-pill is amber or red, or
    before scaling phases (--gate). For a single-machine liveness probe use
    python scripts/bridge-daemon.py --health directly.
---

# Bridge daemon health check

Wraps two CLI scripts into a single CEO ops command:
- `scripts/daemon-fleet-health.py` -- fleet status grid (heartbeat freshness,
  version drift, active session count per workspace)
- `scripts/bridge-daemon.py --health` -- live `/health` endpoint probe on the
  local daemon

Optionally surfaces the adoption-gate metrics (Phase 1 -> Phase 2 gate from
the bridge spec, section 4) when called with `--gate`.

## When to use

- Sync-pill on the dashboard is amber or red
- /push-updates pushed a daemon config change and the CEO wants to verify
  the fleet picked it up
- The dashboard feels stale and you don't know if the daemon is dead or
  just slow
- Before deciding whether to scale from Phase 1 (CEO only) to Phase 2 (full
  CEO coverage) or Phase 3 (exec pilot) — `--gate` shows the gate verdict

## When NOT to use

- Single-script debugging: prefer `python scripts/bridge-daemon.py --health`
  directly when the question is "is this daemon process alive on this
  machine right now". /bridge-health is for the rollup.
- One-line grep summary: prefer `python scripts/bridge-daemon.py --status`
  for a single grep-friendly line (port + pid + uptime + version +
  config_v + sessions + errors + last_hb). Designed for cron + shell
  pipelines.
- Triggering refresh: use the dashboard's sync-pill (top right) instead.
- Tweaking config: edit `corporate/daemon/config.yaml`, commit, run
  /push-updates. /bridge-health is read-only.

## Arguments

| flag | default | what it does |
|---|---|---|
| `--stale SECONDS` | 120 | seconds before a heartbeat is considered stale |
| `--gate` | off | also fetch + summarise the adoption-gate metrics |
| `--json` | off | machine-readable JSON instead of the grid |

## Steps

1. Run the local daemon liveness probe:
   ```
   python scripts/bridge-daemon.py --health
   ```
   - On success: pretty-prints the daemon's `/health` JSON (pid, uptime,
     last error, component data times).
   - On failure: report that the daemon is not reachable. The fleet-health
     grid will still run and may surface other workspaces' status.

2. Run the fleet-health grid:
   ```
   python scripts/daemon-fleet-health.py [--stale SECONDS] [--json]
   ```
   - Shows one row per discovered workspace with: status, uptime, age,
     version, active sessions.
   - Status legend: `ok` (green) / `stale` (yellow) / `version-mismatch`
     (yellow) / `config-drift` (yellow) / `error` (red) / `missing` (gray).
   - Summary line at the bottom rolls up counts per status.
   - Header line shows the stale threshold + CEO daemon version + corporate
     config version (the value used for config-drift detection).

3. If `--gate` was passed, fetch the adoption gate metrics:
   - Read the daemon's port + token from `.daemon-state/port` and
     `.daemon-state/token`.
   - Curl `http://127.0.0.1:{port}/telemetry/summary?days=14` with the
     bearer token.
   - Pretty-print the four metrics (tab time, actions per day, browser-
     first weekday mornings, return-to-browser rate) with PASS/BELOW
     labels against the spec thresholds, and the overall `ALL PASS` /
     `NOT YET` verdict.

4. End with a brief one-line verdict:
   - `Healthy.` if all fleet rows are `ok` and the local probe succeeded.
   - `Local daemon down.` if the liveness probe failed. Note that
     `--health` falls back to reading `.daemon-state/heartbeat.json`
     when the HTTP probe fails, so the report distinguishes "daemon
     reachable" (exit 0) from "daemon dead but on-disk state survives"
     (exit 1) from "neither, daemon never started" (exit 2).
   - `Fleet drift: {workspace} {status}.` if any non-CEO row is stale,
     version-mismatched, config-drift, or error.
   - `Adoption gate {ALL PASS|NOT YET}.` appended when `--gate` was used.

## Remedies by status

- `stale` -> heartbeat older than `--stale` seconds. Daemon likely crashed.
  Remedy: restart via `python scripts/bridge-daemon.py --start` (CEO) or
  the platform installer (`install-bridge-service.ps1` on Win,
  `install-bridge-service-mac.py` on macOS, `install-bridge-service.sh`
  + systemd user unit on Linux when Phase 3 of the cross-platform plan
  lands).
- `version-mismatch` -> exec daemon CODE is older than CEO's daemon code.
  Remedy: redeploy the bridge daemon code to that exec workspace
  (`git pull` the engine clone, daemon restart).
- `config-drift` -> exec daemon's `config_loaded_version` differs from
  the corporate repo's current `corporate/daemon/config.yaml` version
  field. Remedy: the in-daemon reconciliation tick (60s) auto-reloads
  the merged config on mtime change, so this usually clears within a
  minute of the exec's next `git pull`. If it doesn't,
  the exec needs a daemon restart so the new APScheduler cadences take
  effect.
- `error` -> heartbeat parse failed or `recent_error_count > 0`.
  Remedy: read `.daemon-state/bridge.log` on that workspace.
- `missing` -> no heartbeat.json. Daemon was never started on that
  workspace.

## Notes

- This skill is read-only; it never mutates state, never sends emails,
  never calls /refresh.
- The fleet-health script discovers workspaces by scanning the CEO
  workspace's parent directory + `~/exec-workspaces/` for any folder
  with `.daemon-state/heartbeat.json`. Today (Phase 1) only the CEO
  workspace shows up; the grid grows as execs are provisioned.
- The local liveness probe relies on `.daemon-state/port` being present
  and containing a valid TCP port. If the daemon was killed without
  cleaning up, the port file may be stale - in that case the probe
  fails with "not reachable" and you should manually restart the
  daemon via `python scripts/bridge-daemon.py --start`.
