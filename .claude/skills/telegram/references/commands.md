# Telegram Client - Command Reference

Consumed by: `.claude/skills/telegram/SKILL.md` (command syntax reference).

Last Updated: 2026-06-10

Base command: `python ".claude/skills/telegram/scripts/telegram_client.py"`

Global flag: `--json` — output structured JSON instead of colored text.

---

## setup

First-time authentication. Sends OTP to Misha's Telegram, prompts for code.

```bash
python ".claude/skills/telegram/scripts/telegram_client.py" setup
```

Only needed once. Session persists in `.telegram-session/`.

---

## send

Send a text message.

```bash
python ".claude/skills/telegram/scripts/telegram_client.py" send <chat> "message text"
```

| Arg | Required | Description |
|-----|----------|-------------|
| `chat` | Yes | @username, +phone, chat ID, display name, or `me` |
| `message` | Yes | Message text (quote if spaces) |

Examples:
```bash
send me "Note to self"
send "@johndoe" "Are we still on for tomorrow?"
send "John Smith" "Meeting confirmed"
send "+1234567890" "Hey, it's Misha"
```

---

## read

Read recent messages from a chat.

```bash
python ".claude/skills/telegram/scripts/telegram_client.py" read <chat> [--limit N]
```

| Arg | Required | Default | Description |
|-----|----------|---------|-------------|
| `chat` | Yes | - | Chat identifier |
| `--limit` / `-n` | No | 20 | Number of messages |

Output includes: message ID, timestamp, sender name, text, media indicator, reply reference.

---

## chats

List conversations/dialogs.

```bash
python ".claude/skills/telegram/scripts/telegram_client.py" chats [--limit N] [--unread]
```

| Arg | Required | Default | Description |
|-----|----------|---------|-------------|
| `--limit` / `-n` | No | 30 | Number of chats |
| `--unread` / `-u` | No | false | Only show chats with unread messages |

---

## unread

Shortcut for `chats --unread`. Shows only chats with unread messages.

```bash
python ".claude/skills/telegram/scripts/telegram_client.py" unread [--limit N]
```

---

## search

Search messages globally or within a specific chat.

```bash
python ".claude/skills/telegram/scripts/telegram_client.py" search "query" [--chat X] [--limit N]
```

| Arg | Required | Default | Description |
|-----|----------|---------|-------------|
| `query` | Yes | - | Search text |
| `--chat` / `-c` | No | all chats | Limit search to specific chat |
| `--limit` / `-n` | No | 20 | Max results |

---

## send-file

Send a file, photo, or document.

```bash
python ".claude/skills/telegram/scripts/telegram_client.py" send-file <chat> <path> [--caption text]
```

| Arg | Required | Description |
|-----|----------|-------------|
| `chat` | Yes | Chat identifier |
| `path` | Yes | File path (relative to workspace root or absolute) |
| `--caption` | No | Caption text for the file |

---

## download

Download media from a message.

```bash
python ".claude/skills/telegram/scripts/telegram_client.py" download <chat> <msg_id> [--output path]
```

| Arg | Required | Default | Description |
|-----|----------|---------|-------------|
| `chat` | Yes | - | Chat identifier |
| `msg_id` | Yes | - | Message ID containing media |
| `--output` / `-o` | No | `outputs/downloads/` | Output directory |

---

## info

Get details about a user, group, or channel.

```bash
python ".claude/skills/telegram/scripts/telegram_client.py" info <chat>
```

Shows: ID, type, name, username, phone (if visible), member count, group/channel flags.

---

## reply

Reply to a specific message.

```bash
python ".claude/skills/telegram/scripts/telegram_client.py" reply <chat> <msg_id> "reply text"
```

| Arg | Required | Description |
|-----|----------|-------------|
| `chat` | Yes | Chat identifier |
| `msg_id` | Yes | Message ID to reply to |
| `message` | Yes | Reply text |

---

## forward

Forward a message from one chat to another.

```bash
python ".claude/skills/telegram/scripts/telegram_client.py" forward <from_chat> <msg_id> <to_chat>
```

| Arg | Required | Description |
|-----|----------|-------------|
| `from_chat` | Yes | Source chat |
| `msg_id` | Yes | Message ID to forward |
| `to_chat` | Yes | Destination chat |

---

## contacts

List phone contacts synced with Telegram.

```bash
python ".claude/skills/telegram/scripts/telegram_client.py" contacts [--limit N]
```

| Arg | Required | Default | Description |
|-----|----------|---------|-------------|
| `--limit` / `-n` | No | 100 | Max contacts to show |

---

## mark-read

Mark all messages in a chat as read.

```bash
python ".claude/skills/telegram/scripts/telegram_client.py" mark-read <chat>
```

---

## delete

Delete a message.

```bash
python ".claude/skills/telegram/scripts/telegram_client.py" delete <chat> <msg_id>
```

| Arg | Required | Description |
|-----|----------|-------------|
| `chat` | Yes | Chat identifier |
| `msg_id` | Yes | Message ID to delete |

---

## Chat Identifier Resolution

The script resolves chat identifiers in this order:

1. **`me` / `self` / `saved`** — Your Saved Messages
2. **Numeric ID** — Direct chat/group/channel ID (e.g., `-100123456`)
3. **@username** — Telegram username
4. **+phone** — Phone number (must be in contacts)
5. **Username without @** — Tries as username
6. **Fuzzy name match** — Searches dialog list for closest match (>30% similarity threshold)

When fuzzy matching is ambiguous, use @username or chat ID for precision.
