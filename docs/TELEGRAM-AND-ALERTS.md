<!-- version: 1.3.1 | last-updated: 2026-08-22 -->
# Telegram and alerts

Connect HEADING OS to Telegram, create your own capture and alert channels, and tune
what the Sentinel monitor sends you. Written for someone who has never touched an API.

> This page is the step-by-step. For the shorter credential reference see
> [INTEGRATIONS-SETUP](INTEGRATIONS-SETUP.html); for where your settings live and what
> survives an update see [MAKE-IT-YOURS](MAKE-IT-YOURS.html).

---

## 1. The one idea to hold first: account vs bot

Telegram lets a program connect in two completely different ways, and HEADING OS uses
both, for different jobs. Getting this straight up front saves confusion later.

| | **Your user account** | **A bot** |
|---|---|---|
| What it is | Your own Telegram, the one you log into on your phone | A separate robot account you create with @BotFather |
| How it signs in | `api_id` + `api_hash` from my.telegram.org, plus your phone | A bot **token** from @BotFather |
| What it can do | Read and send as **you**, in any of your chats and channels | Only see chats it was explicitly added to; posts as the bot |
| Used in HEADING OS by | `/telegram`, `/viraid`, the Sentinel monitor's *reading* | The optional Fireside team daemon, the system alert nudges (Odin cadence, ops-radar, council model-freshness, reminders, critical daemon alerts), and the Sentinel monitor's *alerts* |

**Reading and capturing always uses your user account.** Viraid capture, Sentinel's chat
monitoring, and anything you drive with `/telegram` runs through *your* Telegram. It
reaches *your* private channels without being added to them.

**Sending system nudges now uses a bot, not your account.** A message your own account
sends to a channel it already owns does not reliably push-notify your phone. A bot
message always does, because Telegram treats it like a message from any other contact. A
second, personal notifications bot (section 7) is recommended even if you never touch
Fireside, specifically so alerts actually reach your phone.

The rest of this page runs in that order. First wire your user account (sections 2 to 4).
Then create your channels (5 to 6) and the notifications bot (7). Then point each feature
at them (8 to 10). The Fireside team bot is section 11.

---

## 2. Get your `api_id` and `api_hash` (one time, five minutes)

These two values are how a program proves it may act as your account. You get them once,
for free, from Telegram's own site.

1. Open **[my.telegram.org](https://my.telegram.org)** in a browser.
2. Enter your phone number (the one your Telegram uses). Telegram sends a login code
   **inside the Telegram app**, not by SMS. Type that code into the web page.
3. Click **API development tools**.
4. Fill the short form. **App title** and **Short name** can be anything (for example
   `heading-os` and `headingos`). Platform: pick **Desktop**. Leave the URL blank.
5. Click **Create application**. You now see two values:
   - **`App api_id`**: a number, for example `12345678`.
   - **`App api_hash`**: a long string of letters and numbers.

Keep this page open for the next step. Treat the `api_hash` like a password: it is not
something to paste into a chat or commit to a repository.

---

## 3. Put three values in `.env`

Open the `.env` file at the root of your engine clone (create it from `.env.example`
first if it is not there: `cp .env.example .env`). Find the Telegram block and fill it
in:

```bash
# --- Telegram Client ---
TELEGRAM_API_ID=12345678            # the App api_id number from step 2
TELEGRAM_API_HASH=paste_your_hash_here   # the App api_hash from step 2
TELEGRAM_PHONE=+15551234567         # your number, with country code, no spaces
```

| Variable | What goes here |
|---|---|
| `TELEGRAM_API_ID` | the numeric `api_id` |
| `TELEGRAM_API_HASH` | the `api_hash` string |
| `TELEGRAM_PHONE` | your phone in international form, for example `+15551234567` |

`.env` is gitignored, so these never leave your machine and are never part of the engine
repository. Save the file.

---

## 4. Log in once

Now let the program sign in to Telegram as you. From the engine folder, in a terminal:

```bash
# ask Telegram to send a login code
uv run python .claude/skills/telegram/scripts/telegram_client.py setup
```

Telegram sends a code **to your Telegram app** (look for a message from "Telegram").
Then enter it:

```bash
# replace 12345 with the code you received
uv run python .claude/skills/telegram/scripts/telegram_client.py verify 12345
```

If your account has two-step verification, it asks for that password too. When it
finishes, your login is saved to `.sessions/telegram/telegram.session` (also gitignored).
You do this **once per machine**; after that every feature reuses the saved login.

Check it worked:

```bash
uv run python .claude/skills/telegram/scripts/telegram_client.py chats --limit 5
```

A list of your five most recent chats means you are connected. Inside a Claude Code
session, `/telegram` and `/viraid` now work.

---

## 5. Create your channels in the Telegram app

HEADING OS uses ordinary Telegram channels as work surfaces. You create them the normal
way, in the Telegram app, in about a minute each. A **channel** where you are the only
member is the cleanest choice. It acts as a private notepad and alert board that only you
can see.

You will typically create two:

- **A capture channel** (the maintainer's is named `M's VIRAID`). You drop quick notes,
  tasks, and reminders here from your phone during the day; Viraid reads them later and
  files them.
- **An alerts channel** (the maintainer's is named `Urgent Stuff for M`), if you want
  urgent items collected in one board rather than in a direct chat. This one is optional.
  The notifications bot of section 7 delivers every alert and nudge in HEADING OS, and
  that bot can just as well message you directly. For a direct chat, point the
  `*_TELEGRAM_TARGET` settings at your own user id. For a board, point them at the
  channel's numeric id, with the bot added as an admin.

To create one on **phone**: tap the pencil / new-message icon, choose **New Channel**,
give it a name, set it **Private**, and skip adding members. On **desktop**: hamburger
menu, **New Channel**, same steps.

Name them whatever you like. Two of the features (Viraid) currently expect a specific
name. Reuse the maintainer's names, or read section 9, which shows where to change the
expected name.

---

## 6. Find a channel's numeric ID

Some settings can point at a channel by its name. The most reliable way is its **numeric
ID**, a number that never changes even if you rename the channel. Channel IDs
look like `-1001234567890` (the leading `-100` is just Telegram's marker for a channel or
group).

The easy way to read it, once you are logged in (section 4):

```bash
uv run python .claude/skills/telegram/scripts/telegram_client.py info "Urgent Stuff for M"
```

That prints the channel's details, including its numeric ID. Copy the number (with the
leading `-100`). You will paste it into a setting in sections 7 and 9.

You can also refer to a channel by:

- **name** in quotes, for example `"Urgent Stuff for M"` (matched loosely, so close is
  fine);
- **@username**, if you gave the channel a public username, for example `@my_alerts`;
- **`me`**, which is your own **Saved Messages** - resolvable **only** for your user
  account (`/telegram send me "..."`, `/viraid`, and the like). The notifications bot in
  section 7 cannot resolve `me`, `self` or `saved`. A bot has no concept of "its own"
  account the way your user session does, so none of the `*_TELEGRAM_TARGET` settings
  below may use it.

---

## 7. Create a notifications bot for reliable alerts

Do this section even if you never touch Fireside. It fixes a real problem. A message your
own account sends to a channel it already owns does not reliably trigger a phone push.
The system alert nudges in section 8 can therefore go unnoticed. A dedicated bot always
push-notifies, because Telegram treats a bot message like a message from any other
contact.

1. In Telegram, open a chat with **@BotFather** (the official bot-maker).
2. Send `/newbot`. Answer its two questions. First a display name (for example
   `HEADING OS`). Then a username that must end in `bot` (for example
   `headingos_bot`).
3. @BotFather replies with a **token**, a line like `123456789:AAE...`. Treat it like a
   password.
4. Put the token in `.env`:

   ```bash
   TELEGRAM_NOTIFY_BOT_TOKEN=123456789:AAE-your-token-here
   ```

5. **Add the bot to your alerts channel** as an administrator, or it cannot post
   there. Use the same channel from section 5/6; you need no new channel. Open the
   channel, Administrators, Add admin, search your bot's username, add it.

   *Direct-message alternative:* instead of a channel you can have the bot message you
   privately. Open the bot's chat and press **Start** once. A bot cannot DM you until you
   do. Point the target (section 8) at your own numeric user id rather than a channel id.
   A bot cannot resolve a `@username` to a private chat, so the DM target must be the
   numeric id.
6. Smoke test it:

   ```bash
   uv run python3 -c "from scripts.utils import telegram_notify; print(telegram_notify.notify('<your channel id or @username>', 'HEADING OS notify-bot smoke test'))"
   ```

   `True` and a real phone push means it worked.

This bot is completely separate from your user-account credentials (section 3) and from
any Fireside bot token (section 11) - never reuse a token across them.

---

## 8. Where alerts and nudges are sent

Several background scripts send you reminders. They are the Odin cadence nudge, the
ops-radar nudge and the council model-freshness nudge. So are due reminders, critical
daemon alerts, and the Sentinel monitor's urgency alerts and digests. Each reads an optional
setting in `.env` that says which channel to send to. All of them deliver through the
notifications bot from section 7, never to your account's own Saved Messages. If a target
is unset, or would resolve to `me`, `self` or `saved`, no notification is sent. The miss
is logged, and `/prime` backstops the same signal.

Add any of these lines to `.env` (all are optional; they ship commented out in
`.env.example`):

```bash
# where the weekly Odin nudge goes (also the fallback for ops-radar, council, reminders)
ODIN_CADENCE_TELEGRAM_TARGET=-1001234567890

# where ops-radar nudges go; if unset, falls back to ODIN_CADENCE_TELEGRAM_TARGET
OPS_RADAR_TELEGRAM_TARGET=@my_alerts

# where Sentinel's urgency alerts and digests go; same fallback
SENTINEL_TELEGRAM_TARGET=-1001234567890
```

The value can be a numeric ID (from section 6) or an `@username` - NOT `me`, since the
notifications bot cannot resolve it. To send every kind of nudge and alert to one place,
just set `ODIN_CADENCE_TELEGRAM_TARGET` and leave the rest unset.

> Sentinel used to post its alerts itself, as your own user account, into a channel named
> in its config file. Since 2026-08-07 it goes through the notifications bot like
> everything else, so there is one delivery surface and one place to point it.

---

## 9. How Viraid works, and how to use your own channel

**Viraid** is a capture inbox. During the day you send yourself quick lines in your
capture channel. Three examples: "follow up with Alex on the ISO cert", "book the
dentist", "read that DPI paper". Later you run `/viraid` in a Claude Code session and it:

1. reads the new messages from the channel,
2. sorts each one into a type (task, calendar item, CRM note, research item, or
   plain note),
3. adds workspace context (who Alex is, whether you are free at that time),
4. proposes what to do with each,
5. **stops and waits for your yes** before doing anything,
6. once you approve, files the item and deletes the message from the channel so it stays
   clean.

Nothing is sent to anyone else and nothing is executed without your approval; Viraid only
reads a channel and files things into your own workspace.

**Pointing Viraid at your channel.** The channel name is a setting, not code. Viraid reads
`VIRAID_CHANNEL_NAME` from `.env`, and falls back to `M's VIRAID` when it is unset. So you
have two easy choices:

- **Name your capture channel `M's VIRAID`** and set nothing. It just works.
- **Use any name you like** and tell Viraid about it. Add one line to `.env`:

  ```bash
  VIRAID_CHANNEL_NAME=My Capture
  ```

  The value can be the channel's name, its `@username`, or its numeric ID (section 6). No
  quotes needed. Save `.env`, and the next `/viraid` reads your channel.

Because `.env` is gitignored, this setting is yours and a future engine update never
touches it (see [MAKE-IT-YOURS](MAKE-IT-YOURS.html#7-what-happens-when-you-update-the-engine)).

---

## 10. Configure the Sentinel monitor

**Sentinel** is an always-on background watcher. Every so often (15 minutes by default) it
checks your email inbox and chosen Telegram chats. It scores each new item for urgency
with a quick AI pass, then sends the urgent ones to your alerts channel. It can also
auto-handle meeting invites against your calendar rules. It sends a morning digest and an
evening digest as well.

### 10.1 Make your own config file

Sentinel ships a template. Copy it into your private data overlay and edit the copy (never
the template):

```bash
mkdir -p ../.heading-os-data/config
cp scripts/sentinel_config.example.yaml ../.heading-os-data/config/sentinel_config.yaml
```

Your live config now lives at `.heading-os-data/config/sentinel_config.yaml`, in your
private data (not in the shared engine). Open it in any text editor.

### 10.2 The settings that matter most

The file is grouped into sections. You do not need to touch all of them; these are the
ones people actually change.

**How often, and how urgent is urgent:**

```yaml
general:
  check_interval_minutes: 15    # how often Sentinel looks
  urgency_threshold: 7          # only alert on items scoring this or higher (1 to 10)
  timezone: "UTC"               # your zone, for example "Asia/Dubai"
```

**Email watching** (needs the Exchange settings from
[INTEGRATIONS-SETUP](INTEGRATIONS-SETUP.html)):

```yaml
email:
  enabled: true
  vip_senders:                  # these always count as important
    - "key-partner@example.com"
  ignore_patterns:              # these are never even scored
    - "noreply@*"
    - "*newsletter*"
    - "*@linkedin.com"
```

**Telegram watching:** list the chats and channels Sentinel should watch (by name,
`@username`, or numeric ID):

```yaml
telegram:
  enabled: true
  check_personal_dms: true
  monitored_chats:
    - name: "Key Contact"
      priority: "high"
    - name: "@some_group"
      priority: "medium"
```

**Where alerts land:** not in this file. Sentinel delivers over the notifications
bot, to the chat id in `SENTINEL_TELEGRAM_TARGET` (falling back to
`ODIN_CADENCE_TELEGRAM_TARGET`) in `.env`, exactly like every other HEADING OS
notification. A bot cannot resolve a human-readable channel name, so a name
would silently fail; the id is the only thing that works. What this file still
controls is repetition:

```yaml
notification:
  dedup_cooldown_minutes: 60           # do not repeat the same alert within an hour
```

**Daily digests:**

```yaml
digest:
  enabled: true
  morning_time: "08:00"
  evening_time: "22:00"
```

The `calendar:` section controls automatic meeting-invite handling (auto-accept, decline,
or escalate against protected time blocks). It is powerful but optional; leave
`calendar.enabled: false` until you have your email working and want it.

### 10.3 Start, stop, check

```bash
# start it in the background
uv run python scripts/sentinel.py --daemon

# is it running? when did it last check? today's counts?
uv run python scripts/sentinel.py --status

# a single safe dry-run; nothing is sent anywhere, alerts are written to the log only
uv run python scripts/sentinel.py --test

# stop it
uv run python scripts/sentinel.py --stop

# watch what it is doing
tail -50 .sentinel/sentinel.log
```

Run `--test` first: it does one pass without touching your real alerts channel, so you can
confirm the wiring before going live. After any config edit, stop and start again for the
change to take effect.

---

## 11. Optional: create a bot for Fireside

Skip this section unless you run the Fireside team daemon. Fireside posts to a team channel
as a **bot**, which is the right choice for something shared. A bot has its own identity
and only sees the chats it is added to. This is a separate bot and token from the
personal notifications bot in section 7 - never reuse one token for both.

1. In Telegram, open a chat with **@BotFather** (the official bot-maker).
2. Send `/newbot`. Answer its two questions: first a display name, and second a username
   that must end in `bot` (for example `my_fireside_bot`).
3. @BotFather replies with a **token**, a line like `123456789:AAE...`. Treat it like a
   password.
4. Put the token and your team channel in `.env` (see the Fireside section of
   [Daemons](daemons.html) for the exact variable names).
5. **Add the bot to your team channel** as an administrator, or it cannot post there. Open
   the channel, Administrators, Add admin, search your bot's username, add it.

To get the team channel's numeric ID, use the same `info` command from section 6. You can
also add the bot and read the daemon's log, which prints the chat ID it sees.

---

## 12. Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `setup` says credentials missing | `TELEGRAM_API_ID` / `TELEGRAM_API_HASH` / `TELEGRAM_PHONE` are not set in `.env`. Recheck section 3. |
| The login code never arrives | It comes **inside the Telegram app**, from the "Telegram" account, not by SMS. Check your other logged-in Telegram sessions. |
| It keeps asking me to log in | The `.sessions/telegram/` file was deleted or cannot be written. Re-run `setup` then `verify`. |
| Viraid reads the wrong (or no) channel | The channel name in the two skill files does not match your channel. See section 9. |
| Sentinel sends nothing | Either nothing scored above `urgency_threshold`, or the daemon is not running (`--status`), or no bot target is set. The daemon logs `Notifications route to bot target <id>` at boot, or an error naming the missing env var. Start it normally (`--daemon`) and read `.sentinel/sentinel.log` for that line; `--test` does not print it, because a dry run resolves no delivery. |
| Alert nudges send nothing | Either `TELEGRAM_NOTIFY_BOT_TOKEN` is unset (section 7), or the target is unset/resolves to `me`/`self`/`saved` (not valid for the bot - section 8), or the bot was never added as admin to the alerts channel. Nothing falls back to Saved Messages; a miss is logged, not silently redirected. |
| "Datacenter IP" block when reading | Some networks rate-limit. See the VPN note in [Prerequisites](prerequisites.html). |

---

## 13. Reference

| File / setting | Role |
|---|---|
| `.env` `TELEGRAM_API_ID` / `TELEGRAM_API_HASH` / `TELEGRAM_PHONE` | Your user-account login |
| `.sessions/telegram/telegram.session` | Saved login (gitignored, one per machine) |
| `.claude/skills/telegram/scripts/telegram_client.py` | The Telegram client (`setup`, `verify`, `chats`, `info`, `read`, `send`) |
| `.env` `TELEGRAM_NOTIFY_BOT_TOKEN` | Dedicated notifications bot token (section 7) |
| `scripts/utils/telegram_notify.py` | `notify(target, message) -> bool` - what every system nudge/alert sends through |
| `.env` `ODIN_CADENCE_TELEGRAM_TARGET` | Channel for the weekly Odin nudge, and the fallback every other target falls back to (unset: no send) |
| `.env` `SENTINEL_TELEGRAM_TARGET` | Where Sentinel's urgency alerts and digests land (fallback: `ODIN_CADENCE_TELEGRAM_TARGET`) |
| `.env` `OPS_RADAR_TELEGRAM_TARGET` | Channel for ops-radar nudges (fallback: the Odin target, then unconfigured) |
| `.env` `VIRAID_CHANNEL_NAME` | The channel `/viraid` reads (default `M's VIRAID`) |
| `scripts/sentinel_config.example.yaml` | Sentinel config template (copy it, do not edit it) |
| `.heading-os-data/config/sentinel_config.yaml` | Your live Sentinel config (private data) |
| `.sentinel/sentinel.log` | Sentinel's activity log |
| Fireside bot token, team chat ID | In `.env`; see [Daemons](daemons.html) |

---

*HEADING OS · Telegram and alerts · maintained by Misha Hanin · see also
[INTEGRATIONS-SETUP](INTEGRATIONS-SETUP.html), [MAKE-IT-YOURS](MAKE-IT-YOURS.html), and
[Daemons](daemons.html).*
