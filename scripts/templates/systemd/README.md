# systemd unit templates

systemd user-unit templates for this workspace, in two families: four
long-running **daemons** (`.service` alone) and fourteen **scheduled tasks**
(a `.service` plus its `.timer`). Installed and enabled on Linux (bare or WSL2).
Mirrors the Windows `scripts/install-*-service.ps1` family and the macOS launchd
path in `scripts/utils/schedule.py`.

Last Updated: 2026-08-24
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

| Task | Templates | Schedule | Installer |
|---|---|---|---|
| Archive transcripts | `archive-transcripts.{service,timer}` | daily 02:50 | `install-archive-transcripts-timer.sh` |
| Chronicle build | `chronicle.{service,timer}` | daily 03:00 | `install-chronicle-timer.sh` |
| Council model freshness | `council-models-check.{service,timer}` | daily 08:30 | `install-council-models-timer.sh` |
| Dream shadow | `dream-shadow.{service,timer}` | daily 03:10 | `install-dream-shadow-timer.sh` |
| Memory auto-retire | `memory-auto-retire.{service,timer}` | daily 07:20 | `install-memory-auto-retire-timer.sh` |
| Memory hygiene | `memory-hygiene.{service,timer}` | Mon 07:34 | `install-memory-hygiene-timer.sh` |
| Memory index refresh | `memory-index-refresh.{service,timer}` | daily 03:30 | `install-memory-index-timer.sh` |
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

Those three are the complete set. There is deliberately no `{{OLLAMA_HOST}}`
token: a host baked into a unit at install time becomes a second, staler source
of truth than the machine pin, for the same reason as `HEADING_OS_TZ` below.
`chronicle.service` carries that note where someone editing it will read it.

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
