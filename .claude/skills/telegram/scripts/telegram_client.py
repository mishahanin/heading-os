#!/usr/bin/env python3
"""
telegram_client.py -- Full Telegram client for Claude Code

Usage:
  python telegram_client.py setup                          # Request OTP code
  python telegram_client.py verify <code>                   # Verify OTP code
  python telegram_client.py send <chat> "message"          # Send message
  python telegram_client.py read <chat> [--limit N]        # Read messages
  python telegram_client.py chats [--limit N] [--unread]   # List dialogs
  python telegram_client.py unread [--limit N]             # Unread messages
  python telegram_client.py search "query" [--chat X]      # Search messages
  python telegram_client.py send-file <chat> <path>        # Send file
  python telegram_client.py download <chat> <msg_id>       # Download media
  python telegram_client.py info <chat>                    # Chat/user info
  python telegram_client.py reply <chat> <msg_id> "text"   # Reply to message
  python telegram_client.py forward <from> <msg_id> <to>   # Forward message
  python telegram_client.py contacts [--limit N]           # List contacts
  python telegram_client.py mark-read <chat>               # Mark as read
  python telegram_client.py delete <chat> <msg_id>         # Delete message

Environment:
  TELEGRAM_API_ID     - from my.telegram.org
  TELEGRAM_API_HASH   - from my.telegram.org
  TELEGRAM_PHONE      - phone number for auth

Session stored in: .sessions/telegram/telegram.session
"""

import argparse
import asyncio
import io
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone

# -- Fix Windows console encoding --
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

# -- Workspace root resolution --
_script_dir = os.path.dirname(os.path.abspath(__file__))
# .claude/skills/telegram/scripts/ -> workspace root (4 levels up)
WORKSPACE_ROOT = os.path.abspath(os.path.join(_script_dir, '..', '..', '..', '..'))
SESSION_DIR = os.path.join(WORKSPACE_ROOT, '.sessions', 'telegram')
SESSION_PATH = os.path.join(SESSION_DIR, 'telegram')

# -- Load .env via workspace central loader --
sys.path.insert(0, WORKSPACE_ROOT)
try:
    from pathlib import Path
    from scripts.utils.workspace import get_outputs_dir, load_env
    load_env(Path(WORKSPACE_ROOT))
except ImportError:
    pass

# -- ANSI colors --
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
CYAN = "\033[96m"
BOLD = "\033[1m"
DIM = "\033[2m"
RESET = "\033[0m"


def get_credentials():
    """Load Telegram API credentials from environment."""
    api_id = os.environ.get("TELEGRAM_API_ID")
    api_hash = os.environ.get("TELEGRAM_API_HASH")
    phone = os.environ.get("TELEGRAM_PHONE")

    missing = []
    if not api_id:
        missing.append("TELEGRAM_API_ID")
    if not api_hash:
        missing.append("TELEGRAM_API_HASH")
    if not phone:
        missing.append("TELEGRAM_PHONE")

    if missing:
        print(f"{RED}[ERROR] Missing environment variables: {', '.join(missing)}{RESET}",
              file=sys.stderr)
        print(f"        Add them to the workspace .env file:", file=sys.stderr)
        print(f"        TELEGRAM_API_ID=12345678", file=sys.stderr)
        print(f"        TELEGRAM_API_HASH=abcdef1234567890", file=sys.stderr)
        print(f"        TELEGRAM_PHONE=+1234567890", file=sys.stderr)
        print(f"\n        Get API credentials at: https://my.telegram.org", file=sys.stderr)
        sys.exit(1)

    return int(api_id), api_hash, phone


def _configure_session_wal(client, busy_timeout_ms=30000):
    """Set WAL journal mode and busy_timeout on the session's sqlite3 connection.

    WAL allows concurrent reads while writes proceed, preventing 'database is
    locked' errors between Sentinel, telegram_client.py, and Viraid.
    Monkey-patches _cursor() so pragmas survive connection recycling.
    """
    session = client.session
    original_cursor = session._cursor

    def _patched_cursor():
        was_none = session._conn is None
        cursor = original_cursor()
        if was_none and session._conn is not None:
            session._conn.execute(f'PRAGMA busy_timeout = {int(busy_timeout_ms)}')
            session._conn.execute('PRAGMA journal_mode = WAL')
        return cursor

    session._cursor = _patched_cursor

    # Apply immediately if connection already exists
    conn = getattr(session, '_conn', None)
    if conn is not None:
        conn.execute(f'PRAGMA busy_timeout = {int(busy_timeout_ms)}')
        conn.execute('PRAGMA journal_mode = WAL')


def create_client():
    """Create a Telethon client with persistent session."""
    from telethon import TelegramClient

    api_id, api_hash, _ = get_credentials()
    os.makedirs(SESSION_DIR, exist_ok=True)
    client = TelegramClient(SESSION_PATH, api_id, api_hash)
    _configure_session_wal(client)
    return client


def format_date(dt):
    """Format a datetime for display."""
    if dt is None:
        return "unknown"
    if dt.tzinfo is not None:
        dt = dt.astimezone(tz=None)
    now = datetime.now(tz=dt.tzinfo)
    if dt.date() == now.date():
        return dt.strftime("%H:%M")
    elif dt.year == now.year:
        return dt.strftime("%b %d %H:%M")
    return dt.strftime("%Y-%m-%d %H:%M")


def format_date_iso(dt):
    """Format datetime as ISO string for JSON output."""
    if dt is None:
        return None
    return dt.isoformat()


def get_entity_name(entity):
    """Get display name from a Telethon entity."""
    from telethon import types
    if entity is None:
        return "Unknown"
    if isinstance(entity, types.User):
        parts = [entity.first_name or "", entity.last_name or ""]
        name = " ".join(p for p in parts if p)
        return name or entity.username or str(entity.id)
    if isinstance(entity, (types.Chat, types.Channel)):
        return entity.title or str(entity.id)
    return str(entity)


async def get_sender_name(msg):
    """Get sender display name from a message."""
    if msg.sender:
        return get_entity_name(msg.sender)
    try:
        sender = await msg.get_sender()
        return get_entity_name(sender)
    except Exception:
        return f"User#{msg.sender_id}"


async def resolve_chat(client, identifier):
    """Resolve a chat identifier to a Telethon entity.

    Accepts: @username, +phone, numeric ID, 'me'/'self', or fuzzy display name.
    """
    from telethon import types, errors

    if not identifier:
        raise ValueError("Chat identifier cannot be empty")

    ident = identifier.strip()

    # Special: self
    if ident.lower() in ('me', 'self', 'saved'):
        return await client.get_me()

    # Try numeric ID
    try:
        num_id = int(ident)
        try:
            return await client.get_entity(num_id)
        except (ValueError, errors.RPCError):
            pass
    except ValueError:
        pass

    # Try @username
    if ident.startswith('@'):
        try:
            return await client.get_entity(ident)
        except (ValueError, errors.RPCError) as e:
            raise ValueError(f"Username {ident} not found: {e}")

    # Try phone number
    if ident.startswith('+'):
        try:
            return await client.get_entity(ident)
        except (ValueError, errors.RPCError):
            raise ValueError(f"Phone number {ident} not found in contacts")

    # Try as username without @
    try:
        return await client.get_entity(ident)
    except (ValueError, errors.RPCError):
        pass

    # Fuzzy match against dialog names
    ident_lower = ident.lower()
    best_match = None
    best_score = 0
    async for dialog in client.iter_dialogs(limit=200):
        name = dialog.name or ""
        name_lower = name.lower()
        if ident_lower == name_lower:
            return dialog.entity
        if ident_lower in name_lower:
            score = len(ident_lower) / len(name_lower) if name_lower else 0
            if score > best_score:
                best_score = score
                best_match = dialog.entity

    if best_match and best_score > 0.3:
        return best_match

    raise ValueError(
        f"Could not resolve chat: '{identifier}'. "
        f"Try @username, phone number, chat ID, or an exact dialog name."
    )


# ========== COMMANDS ==========

async def cmd_setup(client, args):
    """Step 1: Request OTP code from Telegram."""
    _, _, phone = get_credentials()
    print(f"{CYAN}Requesting verification code from Telegram...{RESET}")
    print(f"{DIM}Phone: {phone}{RESET}")

    from telethon.errors import FloodWaitError
    try:
        sent = await client.send_code_request(phone)
        # Save phone_code_hash for verify step
        hash_path = os.path.join(SESSION_DIR, '.code_hash')
        with open(hash_path, 'w') as f:
            f.write(sent.phone_code_hash)
        print()
        print(f"{GREEN}{BOLD}Code sent!{RESET}")
        print(f"{YELLOW}Check your Telegram app for the verification code.{RESET}")
        print()
        print(f"Next step — run verify with the code:")
        print(f"  python telegram_client.py verify <code>")
    except FloodWaitError as e:
        print(f"{RED}[ERROR] Telegram rate limit — wait {e.seconds} seconds before retrying.{RESET}",
              file=sys.stderr)
        sys.exit(1)


async def cmd_verify(client, args):
    """Step 2: Verify OTP code and complete authentication."""
    _, _, phone = get_credentials()
    from telethon.errors import SessionPasswordNeededError

    hash_path = os.path.join(SESSION_DIR, '.code_hash')
    if not os.path.exists(hash_path):
        print(f"{RED}[ERROR] No pending code request. Run 'setup' first.{RESET}",
              file=sys.stderr)
        sys.exit(1)

    with open(hash_path, 'r') as f:
        phone_code_hash = f.read().strip()

    try:
        await client.sign_in(phone, args.code, phone_code_hash=phone_code_hash)
    except SessionPasswordNeededError:
        if not args.password:
            print(f"{YELLOW}This account has two-step verification enabled.{RESET}")
            print(f"Re-run with your 2FA password:")
            print(f"  python telegram_client.py verify {args.code} --password YOUR_PASSWORD")
            sys.exit(1)
        await client.sign_in(password=args.password)

    # Clean up hash file
    try:
        os.remove(hash_path)
    except OSError:
        pass

    me = await client.get_me()
    name = get_entity_name(me)
    print()
    print(f"{GREEN}{BOLD}Authenticated successfully!{RESET}")
    print(f"  Account: {name}")
    print(f"  Username: @{me.username or 'none'}")
    print(f"  Phone: {me.phone}")
    print(f"  Session saved to: .sessions/telegram/")
    print()
    print(f"{DIM}All future commands will use this session automatically.{RESET}")


async def cmd_send(client, args):
    """Send a message to a chat."""
    entity = await resolve_chat(client, args.chat)
    msg = await client.send_message(entity, args.message)
    name = get_entity_name(entity)

    if args.json:
        print(json.dumps({
            "status": "sent",
            "chat": name,
            "message_id": msg.id,
            "date": format_date_iso(msg.date),
        }))
    else:
        print(f"{GREEN}Sent to {BOLD}{name}{RESET}{GREEN} (msg_id: {msg.id}){RESET}")


async def cmd_read(client, args):
    """Read messages from a chat."""
    entity = await resolve_chat(client, args.chat)

    kwargs = {'limit': args.limit}
    if args.min_id is not None:
        kwargs['min_id'] = args.min_id
    if args.reverse:
        kwargs['reverse'] = True
    messages = await client.get_messages(entity, **kwargs)

    chat_name = get_entity_name(entity)

    if args.json:
        result = []
        msg_iter = messages if args.reverse else reversed(messages)
        for msg in msg_iter:
            sender = await get_sender_name(msg)
            result.append({
                "id": msg.id,
                "date": format_date_iso(msg.date),
                "sender": sender,
                "text": msg.text or "",
                "media": bool(msg.media),
                "reply_to": msg.reply_to.reply_to_msg_id if msg.reply_to else None,
            })
        print(json.dumps({"chat": chat_name, "messages": result}, ensure_ascii=False))
    else:
        print(f"{BOLD}Messages from {chat_name}{RESET} (last {len(messages)}):")
        print()
        msg_iter = messages if args.reverse else reversed(messages)
        for msg in msg_iter:
            sender = await get_sender_name(msg)
            date_str = format_date(msg.date)
            text = msg.text or ""
            media_tag = f" {DIM}[media]{RESET}" if msg.media else ""
            reply_tag = ""
            if msg.reply_to:
                reply_tag = f" {DIM}(reply to #{msg.reply_to.reply_to_msg_id}){RESET}"

            print(f"  {DIM}#{msg.id} {date_str}{RESET}  {CYAN}{sender}{RESET}{reply_tag}")
            if text:
                for line in text.split('\n'):
                    print(f"    {line}")
            if media_tag:
                print(f"    {media_tag}")
            print()


async def cmd_chats(client, args):
    """List dialogs/conversations."""
    dialogs = []
    async for dialog in client.iter_dialogs(limit=args.limit):
        dialogs.append(dialog)

    if args.unread:
        dialogs = [d for d in dialogs if d.unread_count > 0]

    if args.json:
        result = []
        for d in dialogs:
            result.append({
                "id": d.entity.id,
                "name": d.name,
                "unread": d.unread_count,
                "type": type(d.entity).__name__,
                "last_message_date": format_date_iso(d.date),
                "last_message": d.message.text[:100] if d.message and d.message.text else None,
            })
        print(json.dumps(result, ensure_ascii=False))
    else:
        print(f"{BOLD}Chats{RESET}" + (f" (unread only)" if args.unread else f" (last {len(dialogs)})") + ":")
        print()
        for d in dialogs:
            unread = f" {YELLOW}({d.unread_count} unread){RESET}" if d.unread_count else ""
            date_str = format_date(d.date)
            preview = ""
            if d.message and d.message.text:
                preview = d.message.text[:60].replace('\n', ' ')
                preview = f" {DIM}| {preview}{RESET}"
            print(f"  {CYAN}{d.name}{RESET}{unread}  {DIM}{date_str}{RESET}{preview}")


async def cmd_unread(client, args):
    """Show chats with unread messages."""
    args.unread = True
    await cmd_chats(client, args)


async def cmd_search(client, args):
    """Search messages globally or in a specific chat."""
    entity = None
    if args.chat:
        entity = await resolve_chat(client, args.chat)

    messages = await client.get_messages(
        entity, search=args.query, limit=args.limit
    )

    if args.json:
        result = []
        for msg in messages:
            sender = await get_sender_name(msg)
            chat = msg.chat if msg.chat else None
            result.append({
                "id": msg.id,
                "date": format_date_iso(msg.date),
                "sender": sender,
                "chat": get_entity_name(chat) if chat else "Unknown",
                "text": msg.text or "",
            })
        print(json.dumps({"query": args.query, "results": result}, ensure_ascii=False))
    else:
        scope = get_entity_name(entity) if entity else "all chats"
        print(f"{BOLD}Search results for '{args.query}' in {scope}{RESET} ({len(messages)} found):")
        print()
        for msg in messages:
            sender = await get_sender_name(msg)
            date_str = format_date(msg.date)
            chat = msg.chat if msg.chat else None
            chat_name = get_entity_name(chat) if chat else "Unknown"
            text = msg.text or "[no text]"
            text_preview = text[:120].replace('\n', ' ')

            print(f"  {DIM}#{msg.id} {date_str}{RESET}  {CYAN}{chat_name}{RESET} > {sender}")
            print(f"    {text_preview}")
            print()


async def cmd_send_file(client, args):
    """Send a file to a chat."""
    entity = await resolve_chat(client, args.chat)

    file_path = args.path
    if not os.path.isabs(file_path):
        file_path = os.path.join(WORKSPACE_ROOT, file_path)

    if not os.path.exists(file_path):
        print(f"{RED}[ERROR] File not found: {file_path}{RESET}", file=sys.stderr)
        sys.exit(1)

    print(f"{DIM}Uploading {os.path.basename(file_path)}...{RESET}", file=sys.stderr)
    msg = await client.send_file(entity, file_path, caption=args.caption or "")
    name = get_entity_name(entity)

    if args.json:
        print(json.dumps({
            "status": "sent",
            "chat": name,
            "file": os.path.basename(file_path),
            "message_id": msg.id,
        }))
    else:
        print(f"{GREEN}Sent {BOLD}{os.path.basename(file_path)}{RESET}{GREEN} to {name} (msg_id: {msg.id}){RESET}")


async def cmd_download(client, args):
    """Download media from a message."""
    entity = await resolve_chat(client, args.chat)
    messages = await client.get_messages(entity, ids=int(args.msg_id))

    if not messages:
        print(f"{RED}[ERROR] Message #{args.msg_id} not found{RESET}", file=sys.stderr)
        sys.exit(1)

    msg = messages
    if not msg.media:
        print(f"{RED}[ERROR] Message #{args.msg_id} has no media{RESET}", file=sys.stderr)
        sys.exit(1)

    output_dir = args.output or str(get_outputs_dir() / 'downloads')
    os.makedirs(output_dir, exist_ok=True)

    print(f"{DIM}Downloading media...{RESET}", file=sys.stderr)
    path = await msg.download_media(file=output_dir)

    if args.json:
        print(json.dumps({"status": "downloaded", "path": path}))
    else:
        print(f"{GREEN}Downloaded to: {BOLD}{path}{RESET}")


async def cmd_info(client, args):
    """Get information about a chat or user."""
    entity = await resolve_chat(client, args.chat)
    from telethon import types

    if args.json:
        info = {
            "id": entity.id,
            "type": type(entity).__name__,
        }
        if isinstance(entity, types.User):
            info.update({
                "first_name": entity.first_name,
                "last_name": entity.last_name,
                "username": entity.username,
                "phone": entity.phone,
                "bot": entity.bot,
                "verified": entity.verified,
                "restricted": entity.restricted,
            })
        elif isinstance(entity, (types.Chat, types.Channel)):
            info.update({
                "title": entity.title,
                "username": getattr(entity, 'username', None),
                "participants_count": getattr(entity, 'participants_count', None),
                "megagroup": getattr(entity, 'megagroup', None),
                "broadcast": getattr(entity, 'broadcast', None),
            })
        print(json.dumps(info, ensure_ascii=False))
    else:
        print(f"{BOLD}Info{RESET}")
        print(f"  ID: {entity.id}")
        print(f"  Type: {type(entity).__name__}")

        if isinstance(entity, types.User):
            print(f"  Name: {entity.first_name or ''} {entity.last_name or ''}")
            print(f"  Username: @{entity.username}" if entity.username else "  Username: none")
            print(f"  Phone: {entity.phone or 'hidden'}")
            print(f"  Bot: {entity.bot}")
        elif isinstance(entity, (types.Chat, types.Channel)):
            print(f"  Title: {entity.title}")
            if hasattr(entity, 'username') and entity.username:
                print(f"  Username: @{entity.username}")
            if hasattr(entity, 'participants_count') and entity.participants_count:
                print(f"  Members: {entity.participants_count}")
            if hasattr(entity, 'megagroup'):
                print(f"  Supergroup: {entity.megagroup}")
            if hasattr(entity, 'broadcast'):
                print(f"  Channel: {entity.broadcast}")


async def cmd_reply(client, args):
    """Reply to a specific message."""
    entity = await resolve_chat(client, args.chat)
    msg = await client.send_message(
        entity, args.message, reply_to=int(args.msg_id)
    )
    name = get_entity_name(entity)

    if args.json:
        print(json.dumps({
            "status": "sent",
            "chat": name,
            "reply_to": int(args.msg_id),
            "message_id": msg.id,
        }))
    else:
        print(f"{GREEN}Replied to #{args.msg_id} in {BOLD}{name}{RESET}{GREEN} (msg_id: {msg.id}){RESET}")


async def cmd_forward(client, args):
    """Forward a message between chats."""
    from_entity = await resolve_chat(client, args.from_chat)
    to_entity = await resolve_chat(client, args.to_chat)

    msg = await client.forward_messages(to_entity, int(args.msg_id), from_entity)
    from_name = get_entity_name(from_entity)
    to_name = get_entity_name(to_entity)

    if args.json:
        fwd_id = msg.id if not isinstance(msg, list) else msg[0].id
        print(json.dumps({
            "status": "forwarded",
            "from": from_name,
            "to": to_name,
            "original_msg_id": int(args.msg_id),
            "new_msg_id": fwd_id,
        }))
    else:
        print(f"{GREEN}Forwarded #{args.msg_id} from {from_name} to {BOLD}{to_name}{RESET}")


async def cmd_contacts(client, args):
    """List phone contacts."""
    from telethon.tl.functions.contacts import GetContactsRequest
    result = await client(GetContactsRequest(hash=0))

    contacts = result.users[:args.limit] if hasattr(result, 'users') else []

    if args.json:
        contact_list = []
        for user in contacts:
            contact_list.append({
                "id": user.id,
                "name": f"{user.first_name or ''} {user.last_name or ''}".strip(),
                "username": user.username,
                "phone": user.phone,
            })
        print(json.dumps(contact_list, ensure_ascii=False))
    else:
        print(f"{BOLD}Contacts{RESET} ({len(contacts)}):")
        print()
        for user in contacts:
            name = f"{user.first_name or ''} {user.last_name or ''}".strip()
            username = f" @{user.username}" if user.username else ""
            phone = f" {DIM}{user.phone}{RESET}" if user.phone else ""
            print(f"  {CYAN}{name}{RESET}{username}{phone}")


async def cmd_mark_read(client, args):
    """Mark all messages in a chat as read."""
    entity = await resolve_chat(client, args.chat)
    await client.send_read_acknowledge(entity)
    name = get_entity_name(entity)

    if args.json:
        print(json.dumps({"status": "marked_read", "chat": name}))
    else:
        print(f"{GREEN}Marked all messages in {BOLD}{name}{RESET}{GREEN} as read{RESET}")


async def cmd_delete(client, args):
    """Delete a message."""
    entity = await resolve_chat(client, args.chat)
    await client.delete_messages(entity, [int(args.msg_id)])
    name = get_entity_name(entity)

    if args.json:
        print(json.dumps({"status": "deleted", "chat": name, "message_id": int(args.msg_id)}))
    else:
        print(f"{GREEN}Deleted message #{args.msg_id} in {BOLD}{name}{RESET}")


# ========== MAIN ==========

def build_parser():
    parser = argparse.ArgumentParser(
        description="Telegram client for Claude Code",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument('--json', action='store_true', help='Output as JSON')
    subparsers = parser.add_subparsers(dest='command', help='Command to run')

    # setup
    subparsers.add_parser('setup', help='Request OTP code from Telegram')

    # verify
    p_verify = subparsers.add_parser('verify', help='Verify OTP code')
    p_verify.add_argument('code', help='The verification code from Telegram')
    p_verify.add_argument('--password', help='2FA password (if enabled)')

    # send
    p_send = subparsers.add_parser('send', help='Send a message')
    p_send.add_argument('chat', help='Chat identifier')
    p_send.add_argument('message', help='Message text')

    # read
    p_read = subparsers.add_parser('read', help='Read messages from a chat')
    p_read.add_argument('chat', help='Chat identifier')
    p_read.add_argument('--limit', '-n', type=int, default=20, help='Number of messages')
    p_read.add_argument('--min-id', type=int, default=None,
                        help='Only return messages with ID > this value (server-side filter)')
    p_read.add_argument('--reverse', action='store_true',
                        help='Return messages in chronological order (oldest first)')

    # chats
    p_chats = subparsers.add_parser('chats', help='List conversations')
    p_chats.add_argument('--limit', '-n', type=int, default=30, help='Number of chats')
    p_chats.add_argument('--unread', '-u', action='store_true', help='Only unread')

    # unread
    p_unread = subparsers.add_parser('unread', help='Show unread messages')
    p_unread.add_argument('--limit', '-n', type=int, default=50, help='Max chats to scan')

    # search
    p_search = subparsers.add_parser('search', help='Search messages')
    p_search.add_argument('query', help='Search query')
    p_search.add_argument('--chat', '-c', help='Search in specific chat')
    p_search.add_argument('--limit', '-n', type=int, default=20, help='Max results')

    # send-file
    p_file = subparsers.add_parser('send-file', help='Send a file')
    p_file.add_argument('chat', help='Chat identifier')
    p_file.add_argument('path', help='File path')
    p_file.add_argument('--caption', help='File caption')

    # download
    p_dl = subparsers.add_parser('download', help='Download media from a message')
    p_dl.add_argument('chat', help='Chat identifier')
    p_dl.add_argument('msg_id', help='Message ID')
    p_dl.add_argument('--output', '-o', help='Output directory')

    # info
    p_info = subparsers.add_parser('info', help='Chat/user info')
    p_info.add_argument('chat', help='Chat identifier')

    # reply
    p_reply = subparsers.add_parser('reply', help='Reply to a message')
    p_reply.add_argument('chat', help='Chat identifier')
    p_reply.add_argument('msg_id', help='Message ID to reply to')
    p_reply.add_argument('message', help='Reply text')

    # forward
    p_fwd = subparsers.add_parser('forward', help='Forward a message')
    p_fwd.add_argument('from_chat', help='Source chat')
    p_fwd.add_argument('msg_id', help='Message ID')
    p_fwd.add_argument('to_chat', help='Destination chat')

    # contacts
    p_contacts = subparsers.add_parser('contacts', help='List phone contacts')
    p_contacts.add_argument('--limit', '-n', type=int, default=100, help='Max contacts')

    # mark-read
    p_mr = subparsers.add_parser('mark-read', help='Mark chat as read')
    p_mr.add_argument('chat', help='Chat identifier')

    # delete
    p_del = subparsers.add_parser('delete', help='Delete a message')
    p_del.add_argument('chat', help='Chat identifier')
    p_del.add_argument('msg_id', help='Message ID')

    return parser


COMMAND_MAP = {
    'setup': cmd_setup,
    'verify': cmd_verify,
    'send': cmd_send,
    'read': cmd_read,
    'chats': cmd_chats,
    'unread': cmd_unread,
    'search': cmd_search,
    'send-file': cmd_send_file,
    'download': cmd_download,
    'info': cmd_info,
    'reply': cmd_reply,
    'forward': cmd_forward,
    'contacts': cmd_contacts,
    'mark-read': cmd_mark_read,
    'delete': cmd_delete,
}


MAX_RETRIES = 3


async def async_main():
    parser = build_parser()
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    handler = COMMAND_MAP.get(args.command)
    if not handler:
        print(f"{RED}[ERROR] Unknown command: {args.command}{RESET}", file=sys.stderr)
        sys.exit(1)

    # Check Telethon is installed
    try:
        import telethon
    except ImportError:
        print(f"{RED}[ERROR] Telethon not installed.{RESET}", file=sys.stderr)
        print(f"        Run: pip install telethon", file=sys.stderr)
        sys.exit(1)

    last_err = None
    for attempt in range(1, MAX_RETRIES + 1):
        client = create_client()
        try:
            if args.command in ('setup', 'verify'):
                await client.connect()
                await handler(client, args)
            else:
                await client.connect()
                if not await client.is_user_authorized():
                    print(f"{RED}[ERROR] Not authenticated. Run 'setup' first.{RESET}",
                          file=sys.stderr)
                    sys.exit(1)
                await handler(client, args)
            return  # success
        except (sqlite3.OperationalError, OSError) as e:
            last_err = e
            if 'locked' in str(e).lower() and attempt < MAX_RETRIES:
                delay = 2 * attempt
                print(f"{YELLOW}[WARN] Session DB locked (attempt {attempt}/{MAX_RETRIES}), "
                      f"retrying in {delay}s...{RESET}", file=sys.stderr)
                try:
                    await client.disconnect()
                except Exception as exc:
                    print(f"{YELLOW}[WARN] disconnect before retry failed: {exc}{RESET}", file=sys.stderr)
                await asyncio.sleep(delay)
                continue
            print(f"{RED}[ERROR] {type(e).__name__}: {e}{RESET}", file=sys.stderr)
            sys.exit(1)
        except ValueError as e:
            print(f"{RED}[ERROR] {e}{RESET}", file=sys.stderr)
            sys.exit(1)
        except Exception as e:
            print(f"{RED}[ERROR] {type(e).__name__}: {e}{RESET}", file=sys.stderr)
            sys.exit(1)
        finally:
            try:
                await client.disconnect()
            except Exception as exc:
                print(f"{YELLOW}[WARN] disconnect on cleanup failed: {exc}{RESET}", file=sys.stderr)

    print(f"{RED}[ERROR] Session DB locked after {MAX_RETRIES} attempts. "
          f"Sentinel may be holding the lock.{RESET}", file=sys.stderr)
    sys.exit(1)


def main():
    asyncio.run(async_main())


if __name__ == '__main__':
    main()
