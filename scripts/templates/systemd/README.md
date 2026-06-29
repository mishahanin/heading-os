# systemd unit templates

systemd user-unit templates for the workspace's long-running daemons. One template per daemon. Used by `scripts/install-daemon-service.sh` to install + enable a unit on Linux (bare or WSL2). Mirrors the Windows `scripts/install-*-service.ps1` family and the macOS launchd path in `scripts/utils/schedule.py`.

Last Updated: 2026-05-23
Consumed by: `scripts/install-daemon-service.sh`, `scripts/uninstall-daemon-service.sh`, `scripts/restart-daemon-service.sh`

## Templates

| Daemon | Template | ExecStart subcommand | Type |
|---|---|---|---|
| Bridge (FastAPI dashboard) | `bridge-daemon.service` | `--start` | persistent, Restart=on-failure |
| Sentinel (comms monitor) | `sentinel.service` | (none — foreground) | persistent, Restart=on-failure |
| Fireside-bot (Telegram polling) | `fireside-bot-daemon.service` | `daemon` | persistent, Restart=on-failure |
| Sync-Exchange (Exchange + calendar) | `sync-exchange-daemon.service` | `daemon` | persistent, Restart=on-failure |
| Eval-Drift (nightly trace replay) | `eval-drift-daemon.service` | `daemon` | persistent (APScheduler @02:00 local time) |

## Placeholders

The installer substitutes:

- `{{WORKSPACE}}` — absolute path to the workspace root
- `{{PYTHON}}` — absolute path to the Python interpreter (typically `/usr/bin/python3` or a venv)

## Install (Linux)

```bash
scripts/install-daemon-service.sh bridge        # bridge daemon
scripts/install-daemon-service.sh sentinel      # comms monitor
scripts/install-daemon-service.sh fireside-bot  # Telegram bot
scripts/install-daemon-service.sh sync-exchange # Exchange + calendar sync
scripts/install-daemon-service.sh eval-drift    # nightly eval replay
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
