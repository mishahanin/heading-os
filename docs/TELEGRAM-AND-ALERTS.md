<!-- version: 1.0.0 | last-updated: 2026-07-01 -->
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
| Used in HEADING OS by | `/telegram`, `/viraid`, the Sentinel monitor, the alert nudges | The optional Fireside team daemon only |

**Almost everything you care about uses your user account.** Capturing notes with
Viraid, getting urgent alerts, reading and sending messages: all of that runs through
*your* Telegram, so it can reach *your* private channels without being added to them.

You only need a bot if you run the optional Fireside team daemon. If you are setting up
a personal workspace, you can skip the bot section entirely.

The rest of this page: first wire your user account (sections 2 to 4), then create your
channels (5 to 6), then point each feature at them (7 to 9). The bot is section 10.

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
member is the cleanest choice: it acts as a private notepad and alert board that only you
can see.

You will typically create two:

- **A capture channel** (the maintainer's is named `M's VIRAID`). You drop quick notes,
  tasks, and reminders here from your phone during the day; Viraid reads them later and
  files them.
- **An alerts channel** (the maintainer's is named `Urgent Stuff for M`). This is where
  the Sentinel monitor and the nudge scripts send you urgent items and reminders.

To create one on **phone**: tap the pencil / new-message icon, choose **New Channel**,
give it a name, set it **Private**, and skip adding members. On **desktop**: hamburger
menu, **New Channel**, same steps.

Name them whatever you like. Two of the features (Viraid) currently expect a specific
name, so either reuse the maintainer's names or note section 8, which shows where to
change the expected name.

---

## 6. Find a channel's numeric ID

Some settings can point at a channel by its name, but the most reliable way is its
**numeric ID**, a number that never changes even if you rename the channel. Channel IDs
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
- **`me`**, which is your own **Saved Messages** (a good safe default while testing).

---

## 7. Where alerts and nudges are sent

Two small background scripts send you reminders: the Odin cadence nudge and the ops-radar
nudge. Each reads an optional setting in `.env` that says which channel to send to. If you
set nothing, both fall back to `me` (your Saved Messages), which is a safe default.

Add either or both of these lines to `.env` (they are optional and not in the example
file by default):

```bash
# where the weekly Odin nudge goes (also the fallback for ops-radar)
ODIN_CADENCE_TELEGRAM_TARGET=-1001234567890

# where ops-radar nudges go; if unset, falls back to ODIN_CADENCE_TELEGRAM_TARGET, then to "me"
OPS_RADAR_TELEGRAM_TARGET=@my_alerts
```

The value can be a numeric ID (from section 6), an `@username`, a channel name in the
same loose-matched form, or `me`. To send both kinds of nudge to one alerts channel, just
set `ODIN_CADENCE_TELEGRAM_TARGET` and leave the other unset.

> The Sentinel monitor has its **own** alert-channel setting, in a config file rather than
> `.env`. That is section 9.

---

## 8. How Viraid works, and how to use your own channel

**Viraid** is a capture inbox. During the day you send yourself quick lines in your
capture channel: "follow up with Alex on the ISO cert", "book the dentist", "read that
DPI paper". Later you run `/viraid` in a Claude Code session and it:

1. reads the new messages from the channel,
2. sorts each one into a type (a task, a calendar item, a CRM note, a research item, or a
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

## 9. Configure the Sentinel monitor

**Sentinel** is an always-on background watcher. Every so often (15 minutes by default) it
checks your email inbox and chosen Telegram chats, scores each new item for urgency with a
quick AI pass, and sends the urgent ones to your alerts channel. It can also auto-handle
meeting invites against your calendar rules and send you a morning and evening digest.

### 9.1 Make your own config file

Sentinel ships a template. Copy it into your private data overlay and edit the copy (never
the template):

```bash
mkdir -p ../.heading-os-data/config
cp scripts/sentinel_config.example.yaml ../.heading-os-data/config/sentinel_config.yaml
```

Your live config now lives at `.heading-os-data/config/sentinel_config.yaml`, in your
private data (not in the shared engine). Open it in any text editor.

### 9.2 The settings that matter most

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

**Where alerts land:** this is Sentinel's own alert-channel setting (separate from the
`.env` ones in section 7). Point it at your alerts channel:

```yaml
notification:
  target_chat: "Urgent Stuff for M"    # name, @username, or numeric ID
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

### 9.3 Start, stop, check

```bash
# start it in the background
uv run python scripts/sentinel.py --daemon

# is it running? when did it last check? today's counts?
uv run python scripts/sentinel.py --status

# a single safe dry-run; alerts go to your own Saved Messages, not the real channel
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

## 10. Optional: create a bot for Fireside

Skip this section unless you run the Fireside team daemon. Fireside posts to a team channel
as a **bot**, which is the right choice for something shared, since a bot has its own
identity and only sees the chats it is added to.

1. In Telegram, open a chat with **@BotFather** (the official bot-maker).
2. Send `/newbot`. Answer its two questions: a display name, then a username that must end
   in `bot` (for example `my_fireside_bot`).
3. @BotFather replies with a **token**, a line like `123456789:AAE...`. Treat it like a
   password.
4. Put the token and your team channel in `.env` (see the Fireside section of
   [Daemons](daemons.html) for the exact variable names).
5. **Add the bot to your team channel** as an administrator, or it cannot post there. Open
   the channel, Administrators, Add admin, search your bot's username, add it.

To get the team channel's numeric ID, use the same `info` command from section 6 (or add
the bot and check the daemon's log, which prints the chat ID it sees).

---

## 11. Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `setup` says credentials missing | `TELEGRAM_API_ID` / `TELEGRAM_API_HASH` / `TELEGRAM_PHONE` are not set in `.env`. Recheck section 3. |
| The login code never arrives | It comes **inside the Telegram app**, from the "Telegram" account, not by SMS. Check your other logged-in Telegram sessions. |
| It keeps asking me to log in | The `.sessions/telegram/` file was deleted or cannot be written. Re-run `setup` then `verify`. |
| Viraid reads the wrong (or no) channel | The channel name in the two skill files does not match your channel. See section 8. |
| Sentinel sends nothing | Either nothing scored above `urgency_threshold`, or the daemon is not running (`--status`), or `target_chat` does not resolve. Try `--test` and read `.sentinel/sentinel.log`. |
| Alerts go to Saved Messages instead of my channel | The target is unset or unresolved, so it fell back to `me`. Set `ODIN_CADENCE_TELEGRAM_TARGET` (section 7) or `notification.target_chat` (section 9), preferably to a numeric ID. |
| "Datacenter IP" block when reading | Some networks rate-limit. See the VPN note in [Prerequisites](prerequisites.html). |

---

## 12. Reference

| File / setting | Role |
|---|---|
| `.env` `TELEGRAM_API_ID` / `TELEGRAM_API_HASH` / `TELEGRAM_PHONE` | Your user-account login |
| `.sessions/telegram/telegram.session` | Saved login (gitignored, one per machine) |
| `.claude/skills/telegram/scripts/telegram_client.py` | The Telegram client (`setup`, `verify`, `chats`, `info`, `read`, `send`) |
| `.env` `ODIN_CADENCE_TELEGRAM_TARGET` | Channel for the weekly Odin nudge (fallback: `me`) |
| `.env` `OPS_RADAR_TELEGRAM_TARGET` | Channel for ops-radar nudges (fallback: the Odin target, then `me`) |
| `.env` `VIRAID_CHANNEL_NAME` | The channel `/viraid` reads (default `M's VIRAID`) |
| `scripts/sentinel_config.example.yaml` | Sentinel config template (copy it, do not edit it) |
| `.heading-os-data/config/sentinel_config.yaml` | Your live Sentinel config (private data) |
| `.sentinel/sentinel.log` | Sentinel's activity log |
| Fireside bot token, team chat ID | In `.env`; see [Daemons](daemons.html) |

---

*HEADING OS · Telegram and alerts · maintained by 31 Concept · see also
[INTEGRATIONS-SETUP](INTEGRATIONS-SETUP.html), [MAKE-IT-YOURS](MAKE-IT-YOURS.html), and
[Daemons](daemons.html).*
