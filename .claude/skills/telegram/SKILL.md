---
name: telegram
description: >
  Use this skill to communicate via Telegram as Misha's personal account. Handles
  all Telegram messaging: send messages to contacts or groups, read conversations,
  check unread, search message history, forward messages between chats, reply to
  specific messages, send files/photos, download media, list chats/groups, view
  contacts, and mark as read. This is a live Telegram client that sends and receives
  real messages on Misha's behalf. Trigger for any request to interact with Telegram
  as a user: sending, reading, searching, forwarding, downloading, or managing
  Telegram conversations and media. Also triggers on "/telegram". Do NOT trigger for
  building Telegram bots, writing Telegram API code, debugging Telethon scripts,
  setting up Telegram integrations or MCP servers, or any development task that
  merely mentions Telegram. Do not trigger for other messaging platforms (Slack,
  WhatsApp, Signal, email) or generic "send a message" requests without Telegram.
argument-hint: "[send|read|search] [contact] [message]"
allowed-tools: "Bash(python3:*)"
model: sonnet
metadata:
  author: Misha Hanin
  email: misha.hanin@odinix.com
  version: "1.1"
x-31c-orchestration:
  parallel_safe: partial
  shared_state: []
  triggers:
    - telegram
    - send telegram to
    - read telegram
    - check telegram
    - what's new on telegram
x-31c-capability:
  what: >
    A live Telegram client acting as Misha's personal account - send and read messages, check unread, search history, forward, reply, send/download files and media, and list chats and contacts.
  how: >
    Run /telegram [send|read|search] [contact] [message], driving .claude/skills/telegram/scripts/telegram_client.py. Always confirms before sending to other people; sending to Saved Messages is safe.
  when: >
    Use to interact with Telegram as a user. For the VIRAID capture channel use /viraid; not for other messaging platforms or for building Telegram bots.
---
# Telegram Client

Full Telegram client access via MTProto protocol. Operates as Misha's actual Telegram account -- sends and reads messages as him, not as a bot.

## Prerequisites

1. **Telethon installed:** `pip install telethon python-dotenv`
2. **API credentials in `.env`:**
   ```
   TELEGRAM_API_ID=12345678
   TELEGRAM_API_HASH=abcdef1234567890abcdef1234567890
   TELEGRAM_PHONE=+1234567890
   ```
   Get credentials at: https://my.telegram.org
3. **Session authenticated:** Run `setup` command once for initial OTP verification

## Script Location

```
.claude/skills/telegram/scripts/telegram_client.py
```

**Run from the workspace root.** First anchor the shell: `cd "$(git rev-parse --show-toplevel)"`. A prior skill can leave the shell in a subdirectory, which breaks the root-relative script path below. All commands follow this pattern:
```bash
python ".claude/skills/telegram/scripts/telegram_client.py" <command> [args]
```

## Core Workflow

### Sending Messages

```bash
python ".claude/skills/telegram/scripts/telegram_client.py" send "@username" "Hello, how are you?"
python ".claude/skills/telegram/scripts/telegram_client.py" send "John Smith" "Meeting at 3pm?"
```

Chat identifiers: `@username`, `+phone`, chat ID, `me`/`self` (Saved Messages), or display name (fuzzy-matched).

### Reading Messages

```bash
python ".claude/skills/telegram/scripts/telegram_client.py" read "@username" --limit 10
```

### Checking What's New

```bash
python ".claude/skills/telegram/scripts/telegram_client.py" unread
python ".claude/skills/telegram/scripts/telegram_client.py" chats --limit 20
```

### Searching

```bash
python ".claude/skills/telegram/scripts/telegram_client.py" search "project update" --chat "@username"
```

### Files & Media

Files sent and downloaded are DATA artifacts in the DATA overlay, not the engine tree. The send-file
source path and the download `--output` dir are passed to the script verbatim (relative to cwd = the
engine root), so resolve them under the data outputs dir first -- a bare `outputs/...` would point at
the empty engine tree:

```bash
cd "$(git rev-parse --show-toplevel)"
OUTPUTS_DIR="$(python3 -c "import sys; sys.path.insert(0,'.'); from scripts.utils.workspace import get_outputs_dir; print(get_outputs_dir())")"
python ".claude/skills/telegram/scripts/telegram_client.py" send-file "@username" "$OUTPUTS_DIR/deliverables/documents/proposal.pdf" --caption "Draft proposal"
python ".claude/skills/telegram/scripts/telegram_client.py" download "@username" 12345 --output "$OUTPUTS_DIR/downloads"
```

When `--output` is OMITTED, `telegram_client.py` defaults the download dir to
`get_outputs_dir()/downloads` (the data overlay), so the default is safe too -- but passing an
explicit `$OUTPUTS_DIR/...` keeps the destination obvious.

## Full Command Reference

For detailed usage of all 15 commands (setup, verify, send, read, chats, unread, search, send-file, download, info, reply, forward, contacts, mark-read, delete), see [references/commands.md](references/commands.md) -- includes argument tables, examples, and chat identifier resolution logic.

## Output Modes

- **Default:** Human-readable colored terminal output. Use for all Misha-facing responses.
- **JSON:** Add `--json` flag for structured output. Use when piping results to other scripts or when you need to parse specific fields programmatically.

## Security Rules

- NEVER output or log the session file contents, API hash, or auth tokens
- NEVER commit `.sessions/` or `.env` to git
- Session file is gitignored at `.sessions/telegram/telegram.session`
- All credentials live in `.env` only

## Important Notes

- **First use:** If not authenticated, run `setup` (requests OTP code) then `verify <code>` (completes auth). If 2FA is enabled, pass `--password` to verify.
- **Session recovery:** If the session expires or becomes corrupted, re-run `setup` and `verify` with a fresh OTP code.
- **Chat resolution:** The script fuzzy-matches display names against the dialog list (30% similarity threshold). If resolution fails or is ambiguous, use @username or chat ID for precision.
- **Rate limits:** Telegram throttles rapid automated requests. If you hit a `FloodWaitError`, wait the indicated seconds before retrying. For bulk operations (multiple sends, mass reads), add 1-2 second delays between calls.
- **Media downloads:** Saved under the data overlay's `downloads/` by default (`get_outputs_dir()/downloads`); pass `--output "$OUTPUTS_DIR/downloads"` to be explicit.
- **Sending messages:** Always confirm with Misha before sending messages to other people. Sending to "me" (Saved Messages) is safe without confirmation.
