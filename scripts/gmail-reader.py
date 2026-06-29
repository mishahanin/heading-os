#!/usr/bin/env python3
"""
gmail-reader.py -- Gmail Reader for Claude Code

Read unread emails from Gmail via the Gmail API.

Prerequisites:
    pip install google-api-python-client google-auth-httplib2 google-auth-oauthlib

Setup:
    Uses the same Google OAuth credentials as google-contacts.py:
      .sessions/google/credentials.json
    Separate token stored at:
      .sessions/google/gmail_token.json
    First run opens browser for OAuth consent.

Usage:
    python scripts/gmail-reader.py unread [--count 5]
    python scripts/gmail-reader.py latest [--count 5]
    python scripts/gmail-reader.py read <message_id>
    python scripts/gmail-reader.py mark-read <message_id> [<message_id> ...]
    python scripts/gmail-reader.py mark-all-read    # marks ALL unread as read
"""

import argparse
import base64
import html
import os
import re
import sys
from pathlib import Path
from email.utils import parsedate_to_datetime

# Fix Windows console encoding
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.utils.venv import ensure_venv  # noqa: E402

ensure_venv()
from scripts.utils.workspace import load_env  # noqa: E402

load_env(PROJECT_ROOT)

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.modify",
]
CREDS_PATH = os.getenv(
    "GOOGLE_GMAIL_CREDENTIALS_PATH",
    os.getenv(
        "GOOGLE_CONTACTS_CREDENTIALS_PATH",
        str(PROJECT_ROOT / ".sessions" / "google" / "credentials.json"),
    ),
)
TOKEN_PATH = str(PROJECT_ROOT / ".sessions" / "google" / "gmail_token.json")


def get_service():
    creds = None
    if os.path.exists(TOKEN_PATH):
        creds = Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists(CREDS_PATH):
                print(f"ERROR: credentials.json not found at {CREDS_PATH}")
                print("Place your Google OAuth credentials there or set GOOGLE_GMAIL_CREDENTIALS_PATH in .env")
                sys.exit(1)
            flow = InstalledAppFlow.from_client_secrets_file(CREDS_PATH, SCOPES)
            creds = flow.run_local_server(port=0)
        os.makedirs(os.path.dirname(TOKEN_PATH), mode=0o700, exist_ok=True)
        with open(TOKEN_PATH, "w") as f:
            f.write(creds.to_json())
        os.chmod(TOKEN_PATH, 0o600)
    return build("gmail", "v1", credentials=creds)


def get_header(headers, name):
    for h in headers:
        if h["name"].lower() == name.lower():
            return h["value"]
    return ""


def decode_body(payload):
    """Extract plain text body from message payload."""
    if payload.get("body", {}).get("data"):
        return base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", errors="replace")

    parts = payload.get("parts", [])
    # Prefer text/plain
    for part in parts:
        mime = part.get("mimeType", "")
        if mime == "text/plain" and part.get("body", {}).get("data"):
            return base64.urlsafe_b64decode(part["body"]["data"]).decode("utf-8", errors="replace")
    # Fallback to text/html stripped
    for part in parts:
        mime = part.get("mimeType", "")
        if mime == "text/html" and part.get("body", {}).get("data"):
            raw = base64.urlsafe_b64decode(part["body"]["data"]).decode("utf-8", errors="replace")
            text = re.sub(r"<[^>]+>", " ", raw)
            text = html.unescape(text)
            text = re.sub(r"\s+", " ", text).strip()
            return text
    # Recurse into multipart
    for part in parts:
        if part.get("parts"):
            result = decode_body(part)
            if result:
                return result
    return "(no text body)"


def list_messages(service, query, count):
    results = service.users().messages().list(
        userId="me", q=query, maxResults=count
    ).execute()
    return results.get("messages", [])


def get_message_summary(service, msg_id):
    msg = service.users().messages().get(
        userId="me", id=msg_id, format="metadata",
        metadataHeaders=["From", "To", "Subject", "Date", "Cc"]
    ).execute()
    headers = msg.get("payload", {}).get("headers", [])
    labels = msg.get("labelIds", [])
    return {
        "id": msg_id,
        "from": get_header(headers, "From"),
        "to": get_header(headers, "To"),
        "cc": get_header(headers, "Cc"),
        "subject": get_header(headers, "Subject"),
        "date": get_header(headers, "Date"),
        "unread": "UNREAD" in labels,
        "snippet": msg.get("snippet", ""),
    }


def get_message_full(service, msg_id):
    msg = service.users().messages().get(userId="me", id=msg_id, format="full").execute()
    headers = msg.get("payload", {}).get("headers", [])
    labels = msg.get("labelIds", [])
    body = decode_body(msg.get("payload", {}))
    # Truncate very long bodies
    if len(body) > 3000:
        body = body[:3000] + "\n\n[...truncated]"
    return {
        "id": msg_id,
        "from": get_header(headers, "From"),
        "to": get_header(headers, "To"),
        "cc": get_header(headers, "Cc"),
        "subject": get_header(headers, "Subject"),
        "date": get_header(headers, "Date"),
        "unread": "UNREAD" in labels,
        "body": body,
    }


def cmd_unread(args):
    service = get_service()
    messages = list_messages(service, "is:unread", args.count)
    if not messages:
        print("No unread emails.")
        return
    print(f"Found {len(messages)} unread email(s):\n")
    for i, m in enumerate(messages, 1):
        s = get_message_summary(service, m["id"])
        print(f"--- {i}. {s['subject']} ---")
        print(f"  From:    {s['from']}")
        print(f"  Date:    {s['date']}")
        print(f"  To:      {s['to']}")
        if s['cc']:
            print(f"  CC:      {s['cc']}")
        print(f"  Preview: {s['snippet'][:200]}")
        print(f"  ID:      {s['id']}")
        print()


def cmd_latest(args):
    service = get_service()
    messages = list_messages(service, "", args.count)
    if not messages:
        print("No emails found.")
        return
    print(f"Latest {len(messages)} email(s):\n")
    for i, m in enumerate(messages, 1):
        s = get_message_summary(service, m["id"])
        status = "[UNREAD]" if s["unread"] else "[read]"
        print(f"--- {i}. {status} {s['subject']} ---")
        print(f"  From:    {s['from']}")
        print(f"  Date:    {s['date']}")
        print(f"  Preview: {s['snippet'][:200]}")
        print(f"  ID:      {s['id']}")
        print()


def cmd_read(args):
    service = get_service()
    msg = get_message_full(service, args.message_id)
    print(f"Subject: {msg['subject']}")
    print(f"From:    {msg['from']}")
    print(f"To:      {msg['to']}")
    if msg['cc']:
        print(f"CC:      {msg['cc']}")
    print(f"Date:    {msg['date']}")
    print(f"Status:  {'UNREAD' if msg['unread'] else 'Read'}")
    print(f"\n{'='*60}\n")
    print(msg['body'])


def cmd_mark_read(args):
    service = get_service()
    for mid in args.message_ids:
        service.users().messages().modify(
            userId="me", id=mid,
            body={"removeLabelIds": ["UNREAD"]}
        ).execute()
        print(f"Marked as read: {mid}")


def cmd_mark_all_read(args):
    service = get_service()
    messages = list_messages(service, "is:unread", 100)
    if not messages:
        print("No unread emails.")
        return
    for m in messages:
        s = get_message_summary(service, m["id"])
        service.users().messages().modify(
            userId="me", id=m["id"],
            body={"removeLabelIds": ["UNREAD"]}
        ).execute()
        print(f"  [x] {s['subject']}")
    print(f"\nMarked {len(messages)} emails as read.")


def main():
    parser = argparse.ArgumentParser(description="Gmail Reader for Claude Code")
    sub = parser.add_subparsers(dest="command")

    p_unread = sub.add_parser("unread", help="List unread emails")
    p_unread.add_argument("--count", type=int, default=5)

    p_latest = sub.add_parser("latest", help="List latest emails")
    p_latest.add_argument("--count", type=int, default=5)

    p_read = sub.add_parser("read", help="Read full email by ID")
    p_read.add_argument("message_id")

    p_mark = sub.add_parser("mark-read", help="Mark emails as read")
    p_mark.add_argument("message_ids", nargs="+")

    p_all = sub.add_parser("mark-all-read", help="Mark all unread as read")

    args = parser.parse_args()
    if args.command == "unread":
        cmd_unread(args)
    elif args.command == "latest":
        cmd_latest(args)
    elif args.command == "read":
        cmd_read(args)
    elif args.command == "mark-read":
        cmd_mark_read(args)
    elif args.command == "mark-all-read":
        cmd_mark_all_read(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
