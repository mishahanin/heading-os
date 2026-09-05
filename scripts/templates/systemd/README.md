# systemd unit templates

systemd user-unit templates for this workspace, in two families: four
long-running **daemons** (`.service` alone) and fifteen **scheduled tasks**
(a `.service` plus its `.timer`). Installed and enabled on Linux (bare or WSL2).
Mirrors the Windows `scripts/install-*-service.ps1` family and the macOS launchd
path in `scripts/utils/schedule.py`.

Last Updated: 2026-09-05
Consumed by: `scripts/install-daemon-service.sh`, `scripts/uninstall-daemon-service.sh`, `scripts/restart-daemon-service.sh` (daemons); one `scripts/install-<name>-timer.sh` per scheduled task

## Daemons

Installed by `scripts/install-daemon-service.sh <name>`.

| Daemon | Template | ExecStart subcommand | Type |
|---|---|---|---|
| Bridge (FastAPI dashboard) | `bridge-daemon.service` | `--start` | persistent, Restart=on-failure |
| Sentinel (comms monitor) | `sentinel.service` | (none, foreground) | persistent, Restart=on-failure |
| Fireside-bot (Telegram polling) | `fireside-bot-daemon.service` | `daemon` | persistent, Restart=on-failure |
| Sync-Exchange (Exchange + calendar) | `sync-exchange-daemon.service` | `daemon` | persistent, Restart=on-failure |

## Scheduled tasks

Each has its OWN installer, not `install-daemon-service.sh`. `Type=oneshot`
throughout: the timer is the schedule, the service is one run. The three
mechanisms every one of them needs to survive a reboot (`Persistent=true`,
`systemctl --user enable`, `loginctl enable-linger`) are baked into the
installers, so copy a sibling rather than hand-rolling a new one.

**Installing is not arming, and only one installer can currently tell you which
you got.** `install-nightly-refresh-timer.sh --check` verifies each of the three
mechanisms SEPARATELY, reports installed-but-disabled as a state distinct from
not-installed, and exits non-zero naming the one that is missing. The other
fourteen installers print their state and leave the reading to you. Copy the
`--check` block when you add the next one: an installer that was merged is not a
timer that is armed, and a directory listing cannot tell the two apart.

`nightly-refresh.service` is also the only one here that sets
`TimeoutStartSec=`, and it is a bound that unit CHOOSES rather than one systemd
imposes. `man systemd.service`, verbatim, because summarising it is what got it
backwards once already:

> Defaults to DefaultTimeoutStartSec= set in the manager, except when
> Type=oneshot is used, in which case the timeout is disabled by default
> (see systemd-system.conf(5)).

Every unit in the table above is `Type=oneshot`, so none of them inherits a start
timeout and none of them needs to be rescued from one. The nightly caps itself at
5400s because an unbounded run that wedges holds a machine's worth of pytest
workers indefinitely. MEASURED 2026-09-05 in HELM on `ca9457d`: a clean full run
took 979s at one-minute load 26 with two YARDs competing, and 875s inside the
push gate under similar load. Exceeding the cap terminates the run and reports
`failed`, which on this job is the correct outcome: the green marker does not
move.

| Task | Templates | Schedule | Installer |
|---|---|---|---|
| Archive transcripts | `archive-transcripts.{service,timer}` | daily 02:50 | `install-archive-transcripts-timer.sh` |
| Chronicle build | `chronicle.{service,timer}` | daily 03:00 | `install-chronicle-timer.sh` |
| Council model freshness | `council-models-check.{service,timer}` | daily 08:30 | `install-council-models-timer.sh` |
| DataStore map refresh | `datastore-map.{service,timer}` | daily 03:20 | `install-datastore-map-timer.sh` |
| Dream shadow | `dream-shadow.{service,timer}` | daily 03:10 | `install-dream-shadow-timer.sh` |
| Memory auto-retire | `memory-auto-retire.{service,timer}` | daily 07:20 | `install-memory-auto-retire-timer.sh` |
| Memory hygiene | `memory-hygiene.{service,timer}` | Mon 07:34 | `install-memory-hygiene-timer.sh` |
| Memory index refresh | `memory-index-refresh.{service,timer}` | daily 03:30 | `install-memory-index-timer.sh` |
| Nightly refresh | `nightly-refresh.{service,timer}` | daily 01:30 | `install-nightly-refresh-timer.sh` |
| Odin cadence nudge | `odin-cadence.{service,timer}` | Mon 09:00 | `install-odin-cadence-timer.sh` |
| Odin skill proposals | `odin-propose.{service,timer}` | Mon 05:31 | `install-odin-propose-timer.sh` |
| Ollama guard | `ollama-guard.{service,timer}` | every 5 min (2 min after boot) | `install-ollama-guard-timer.sh` |
| Ops radar | `ops-radar.{service,timer}` | daily 08:00 | `install-ops-radar-timer.sh` |
| Reminders | `reminders.{service,timer}` | daily 07:45 | `install-reminders-timer.sh` |
| Router accuracy | `router-accuracy.{service,timer}` | daily 03:00 | `install-router-accuracy-timer.sh` |
| Update manager | `update-manager.{service,timer}` | daily 07:00 | `install-update-manager-timer.sh` |

`ollama-guard.timer` is the one unit here with NO `Persistent=true`, and that is
deliberate: it is a watchdog probe of the CURRENT state, so replaying a missed
look buys nothing. Its own comment carries the reasoning.

## Placeholders

The installer substitutes:

- `{{WORKSPACE}}` — absolute path to the workspace root
- `{{PYTHON}}` — absolute path to the Python interpreter (typically `/usr/bin/python3` or a venv)
- `{{TZ}}` — the operator's timezone, for the `OnCalendar` suffix and for `Environment=TZ=`
- `{{TOOLPATH}}` — the installing shell's own `PATH`, for `Environment=PATH=`.
  Substituted by `install-nightly-refresh-timer.sh` only, and used by
  `nightly-refresh.service` only.

Those four are the complete set. There is deliberately no `{{OLLAMA_HOST}}`
token: a host baked into a unit at install time becomes a second, staler source
of truth than the machine pin, for the same reason as `HEADING_OS_TZ` below.
`chronicle.service` carries that note where someone editing it will read it.

A token is substituted by the installer that renders it and by no other, so a
token added to a template also has to be added to that installer's `sed` block.
Forgetting is caught rather than shipped: every `--check` greps the rendered
units for a surviving `{{` and fails naming it.

`{{TOOLPATH}}` is the one token whose value is not derived from the workspace,
and it exists because a systemd user service inherits the MANAGER's `PATH`, which
carries no per-user tool directory. MEASURED 2026-09-05: the nightly ran under
that PATH, found none of `gh`, `git-lfs`, `node`, `npx`, `marp`, `uv`,
`pre-commit`, `claude` or `herdr`, skipped 240 tests instead of 2, exited 0, and
marked the tree green. Unlike `HEADING_OS_TZ`, this value has no `.env` key to go
stale against; it can still go stale against the machine (an nvm version bump),
and the ceiling in `config/nightly-skip-baseline.json` is what makes that loud.

The timer installers resolve `{{TZ}}` through `python3 scripts/utils/paths.py tz`,
which loads `.env` before reading `HEADING_OS_TZ`. Reading the environment alone
is not enough: nothing exports that variable, so an environment-only read renders
`UTC` on a machine whose timezone is correctly configured. An explicit
`HEADING_OS_TZ=X scripts/install-...-timer.sh` still wins, and a value resolvable
from neither source falls back to `UTC` with an announcement on stderr.

**Never add `Environment=HEADING_OS_TZ=` to a unit.** `load_env` uses
`setdefault`, so a value pinned into the unit at install time can never be
corrected by `.env` afterwards — the unit becomes a second, staler source of
truth. `Environment=TZ=` is fine: nothing reads `TZ` from `.env`, and it is what
steers libc for a naive `date.today()`.

## Install (Linux)

```bash
scripts/install-daemon-service.sh bridge        # bridge daemon
scripts/install-daemon-service.sh sentinel      # comms monitor
scripts/install-daemon-service.sh fireside-bot  # Telegram bot
scripts/install-daemon-service.sh sync-exchange # Exchange + calendar sync

scripts/install-ops-radar-timer.sh              # scheduled task: one installer each
```

For unattended boot (so daemons start without an active login):

```bash
loginctl enable-linger "$USER"
```

## Status / logs

```bash
systemctl --user status bridge-daemon
journalctl --user -u bridge-daemon -f
```

## Uninstall

```bash
scripts/uninstall-daemon-service.sh bridge
```

## Cross-platform launcher map

| OS | Mechanism | Owner |
|---|---|---|
| Windows | `install-bridge-service.ps1` + Startup-folder shortcut | `scripts/utils/schedule.py` (sync/sentinel), explicit .ps1 (bridge) |
| macOS | launchd user agent (`~/Library/LaunchAgents/io.31c.*.plist`) | `scripts/utils/schedule.py` |
| Linux | systemd user unit (`~/.config/systemd/user/*.service`) | `scripts/install-daemon-service.sh` + this directory |
