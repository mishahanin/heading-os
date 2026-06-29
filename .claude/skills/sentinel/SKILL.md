---
name: sentinel
disable-model-invocation: true
description: "Manage the Sentinel background daemon that monitors corporate email (Exchange) and Telegram for urgent messages. Start, stop, check status, view logs, and configure monitoring. EXPLICIT INVOCATION ONLY - daemons live on the always-on service host and are off-limits without CEO sign-off."
argument-hint: "[start|stop|status|logs|config]"
allowed-tools: "Bash(python3:*), Read"
model: haiku
metadata:
  author: Misha Hanin
  email: misha.hanin@odinix.com
  version: "1.2"
x-31c-orchestration:
  parallel_safe: false
  shared_state:
    - .sentinel/
  triggers:
    - sentinel
    - start sentinel
    - stop sentinel
    - comms monitor
x-31c-capability:
  what: >
    Manages the Sentinel background daemon that watches Exchange email and
    Telegram for urgent messages, scores urgency, evaluates meeting invites
    against the CEO Calendar Policy, and alerts via Telegram.
  how: >
    Explicit only: type /sentinel [start|stop|status|logs|config]; not
    auto-triggered. Drives scripts/sentinel.py; default starts the daemon if
    it is not already running and shows status.
  when: >
    Use to start, stop, or check the comms-monitoring daemon. For a one-off
    inbox triage use /email-intel; for ad-hoc Telegram use /telegram.
---
# Sentinel -- Unified Comms Monitor

Manage the Sentinel background daemon that monitors corporate email (Exchange) and Telegram for urgent messages.

## Trigger

Activate when the user says: "sentinel", "/sentinel", "start sentinel", "stop sentinel", "sentinel status", "check sentinel", "is sentinel running", "comms monitor", or asks about the background monitoring system.

## Actions

Parse the user's intent and run the appropriate command:

### Default (no arguments / just "/sentinel")
Check status first. If Sentinel is already running, show the status. If it is NOT running, start it in daemon mode automatically, then show the status.
```bash
python scripts/sentinel.py --status
# If output says "NOT running" -> start daemon, then show status again:
python scripts/sentinel.py --daemon && python scripts/sentinel.py --status
```

### Start Sentinel (foreground)
```bash
python scripts/sentinel.py
```

### Start Sentinel (background/headless)
```bash
python scripts/sentinel.py --daemon
```

### Test mode (single cycle, dry-run, notifications to Saved Messages)
```bash
python scripts/sentinel.py --test
```

### Check status
```bash
python scripts/sentinel.py --status
```

### Stop daemon
```bash
python scripts/sentinel.py --stop
```

### View recent logs
```bash
tail -50 .sentinel/sentinel.log
```

### Edit configuration
Read and present the live config for editing. The real config is private
instance data at `<data-root>/config/sentinel_config.yaml` (resolved via
`get_data_config_dir()`); the engine ships `scripts/sentinel_config.example.yaml`
as the template/fallback. On the transitional CEO workspace the data root is
in-tree, so the live file is `config/sentinel_config.yaml`.

## Configuration

Config file: `config/sentinel_config.yaml` (private data-config; engine fallback
`scripts/sentinel_config.example.yaml`)

Key settings:
- `general.check_interval_minutes` -- polling frequency (default: 15)
- `general.urgency_threshold` -- minimum score to trigger notification (default: 7, scale 1-10)
- `email.vip_senders` -- emails that always get elevated priority
- `email.ignore_patterns` -- senders/patterns to skip entirely
- `telegram.monitored_chats` -- specific groups/channels to watch
- `notification.target_chat` -- where urgent alerts go (default: "Urgent Stuff for M")
- `digest.morning_time` / `digest.evening_time` -- daily summary times (local TZ)
- `calendar.enabled` -- enable meeting invite monitoring (default: true)
- `calendar.auto_accept` -- auto-accept policy-compliant invites
- `calendar.auto_decline` -- auto-decline non-compliant invites (non-VIP)
- `calendar.escalate_vip` -- always escalate VIP/external senders
- `calendar.protected_blocks` -- protected time windows from CEO Calendar Policy
- `calendar.day_themes` -- weekly theme structure for topic alignment

## Prerequisites

- `ANTHROPIC_API_KEY` must be set in `.env` (get from console.anthropic.com)
- Telegram session must be authenticated (run `/telegram setup` if needed)
- Exchange credentials in `.env` (EXCHANGE_EMAIL, EXCHANGE_PASSWORD, EXCHANGE_SERVER)

## Runtime Files

- `.sentinel/state.json` -- persistent state (processed IDs, dedup hashes)
- `.sentinel/sentinel.log` -- rotating log (5MB x 3 files)
- `.sentinel/sentinel.pid` -- process ID for management

## Features

### Email Analysis
Sentinel analyzes all incoming emails via Claude API and provides CEO-specific recommended actions:
- Reply needed, Forward to delegate, Schedule follow-up, Approve/Decide, FYI only, Escalate

### Meeting Invite Monitoring
Sentinel monitors Exchange meeting invites and evaluates them against the CEO Calendar Policy (`reference/ceo-calendar-policy.md`):
- **Auto-accepts** policy-compliant invites
- **Auto-declines** non-compliant invites from internal senders with a proposed alternative time
- **Escalates** VIP/external sender invites and edge cases to Misha via Telegram
- All decisions (accept, decline, escalate) are notified via Telegram

## Output

When asked about Sentinel, report:
1. Running status (PID, last check time)
2. Today's stats (emails, TG messages, urgent alerts, invite decisions)
3. Any recent errors from the log
