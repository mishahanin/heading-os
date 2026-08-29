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

Tests: tests/test_a_page_the_server_repeated_and_a_body_never_read.py
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

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.utils.venv_guard import ensure_venv  # noqa: E402

ensure_venv()
from scripts.utils.workspace import load_env  # noqa: E402

load_env(PROJECT_ROOT)


def get_service():
    """Authorized Gmail service.

    The OAuth handling lives in `scripts/utils/gmail_auth`, shared with
    `scripts/gmail-send.py`, so one token definition serves both. The import
    stays inside the function to keep module import pure (F-2.1).
    """
    from scripts.utils.gmail_auth import get_service as _get_service

    try:
        return _get_service()
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}")
        sys.exit(1)


def get_header(headers, name):
    for h in headers:
        if h["name"].lower() == name.lower():
            return h["value"]
    return ""


NO_TEXT_BODY = "(no text body)"


def _mime_of(part):
    """The bare MIME type of a part, lowercased, without its parameters."""
    return str(part.get("mimeType") or "").split(";")[0].strip().lower()


def _raw_text(part):
    """The part's base64url body decoded to text."""
    return base64.urlsafe_b64decode(
        part["body"]["data"]).decode("utf-8", errors="replace")


def _html_to_text(raw):
    """Tags stripped, entities resolved, whitespace collapsed."""
    text = re.sub(r"<[^>]+>", " ", raw)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def decode_body(payload):
    """Plain text for a message payload, or NO_TEXT_BODY when there is none.

    The MIME type is read. It used to be ignored on the single-part path, so a
    message whose whole payload IS a `text/html` body -- an ordinary shape, and
    what any HTML-only sender produces without a multipart wrapper -- had its
    raw markup returned verbatim from a function documented to extract plain
    text. `read` then printed tag soup. Nested HTML was stripped correctly; only
    the top level was not, so the defect appeared and disappeared with the
    sender's choice of wrapper.

    A single part that is neither text/plain nor text/html (a bare PDF, say)
    has no text body at all, and NO_TEXT_BODY says so rather than decoding
    binary into replacement characters and calling it a message.

    A part carrying data but no mimeType at all is treated as text: that is
    what this function did for every message before the type was consulted,
    and inventing a refusal for it would be a new behaviour, not a fix.
    """
    if payload.get("body", {}).get("data"):
        raw = _raw_text(payload)
        mime = _mime_of(payload)
        if mime == "text/html":
            return _html_to_text(raw)
        if not mime or mime.startswith("text/"):
            return raw
        return NO_TEXT_BODY

    return _decode_parts(payload.get("parts", [])) or NO_TEXT_BODY


def _find_by_type(parts, mime):
    """The first `mime` body anywhere under `parts`, in document order, or "".

    Depth-first over the WHOLE tree for one type at a time. That is what makes
    the caller's promise below literally true.
    """
    for part in parts:
        if _mime_of(part) == mime and part.get("body", {}).get("data"):
            return _raw_text(part)
        if part.get("parts"):
            found = _find_by_type(part["parts"], mime)
            if found:
                return found
    return ""


def _decode_parts(parts):
    """Best available text in a part list, or "" when there is none.

    Plain text wins at every depth before any HTML is considered -- across
    SIBLING BRANCHES too, which is the half this did not do. The search was
    per-branch: it looked for plain text at one level, then recursed into the
    first sub-tree that produced anything and returned that. A multipart/mixed
    holding a multipart/related (text/html) followed by a multipart/alternative
    (text/plain) therefore returned the stripped HTML, because the html branch
    came first and answered. The plain part one branch to the right was never
    read, and the sentence above was false for exactly the message shape it was
    written about.

    Two whole-tree passes, plain then html, cannot have that ordering bug: the
    second pass runs only when the first found nothing anywhere.

    Returning "" for "nothing here" is the other half. The recursion used to
    test `if result:` against a function whose worst case was the non-empty
    string "(no text body)", so the first part that merely HAD sub-parts
    ended the loop and a later sibling holding the real text was never read.
    """
    plain = _find_by_type(parts, "text/plain")
    if plain:
        return plain
    raw = _find_by_type(parts, "text/html")
    return _html_to_text(raw) if raw else ""


def list_messages(service, query, count):
    results = service.users().messages().list(
        userId="me", q=query, maxResults=count
    ).execute()
    return results.get("messages", [])


PAGE_SIZE = 500
MAX_LIST_PAGES = 200        # 100,000 messages, then stop and say so


def list_all_messages(service, query, max_pages=MAX_LIST_PAGES):
    """Messages matching `query`, following nextPageToken. Returns (rows, complete).

    `mark-all-read` used to call list_messages(..., 100), which passes
    maxResults=100 and follows no page token, then printed "Marked N emails
    as read." With 101 unread the operator was told the mailbox was clean
    while 1 was still unread -- a silent partial completion on a command whose
    own usage line says "marks ALL unread as read".

    `complete` is False when either bound below stopped the walk. The caller
    reports that, because "marked N as read" over a truncated list is the very
    defect this function was written to fix.
    """
    # The paging loop is bounded twice, and the reason is not hypothetical.
    # The first version trusted the server to stop handing out tokens, with no
    # bound of its own. On 2026-08-24 a mutation-test run disabled the line
    # that sends the token, the stub replied with the same page forever, and
    # the process reached 47 GB before the kernel OOM-killer took it and every
    # other process in the WSL session with it. A loop whose termination
    # depends entirely on a remote party is a loop that can consume all
    # memory. So: a page cap, and a refusal to follow a token already followed.
    out = []
    token = None
    seen_tokens = set()
    seen_ids = set()
    for _ in range(max_pages):
        kwargs = {"userId": "me", "q": query, "maxResults": PAGE_SIZE}
        if token:
            kwargs["pageToken"] = token
        results = service.users().messages().list(**kwargs).execute()
        # De-duplicated by id, because the token guard below fires one page too
        # late to help the RESULT. A server that repeats a page also repeats its
        # token, and the repeat is only detectable after that page has already
        # been appended -- so `mark-all-read` fetched the same message twice,
        # removed UNREAD from it twice, printed its subject twice, and reported
        # two emails marked read where one existed. The guard stops the walk;
        # this stops the double count.
        for m in results.get("messages", []):
            mid = m.get("id")
            if mid is not None:
                if mid in seen_ids:
                    continue
                seen_ids.add(mid)
            out.append(m)
        token = results.get("nextPageToken")
        if not token:
            return out, True
        if token in seen_tokens:
            print(f"warning: the server repeated page token {token!r}; "
                  f"stopping after {len(out)} message(s)", file=sys.stderr)
            return out, False
        seen_tokens.add(token)
    print(f"warning: stopped at the {max_pages}-page cap with {len(out)} "
          f"message(s); more remain", file=sys.stderr)
    return out, False


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
    messages, complete = list_all_messages(service, "is:unread")
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
    if complete:
        print(f"\nMarked {len(messages)} emails as read.")
    else:
        # The whole point of this command's fix: never report completion over
        # a list that was truncated. `list_all_messages` has already said on
        # stderr why it stopped.
        print(f"\nMarked {len(messages)} emails as read. NOT all of them: the "
              f"listing was cut short (see the warning above). Run the command "
              f"again to continue.")


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
        # Non-zero, and the value is passed to the shell. `main` used to fall
        # off the end here returning None while `__main__` discarded it, so a
        # forgotten or misspelled subcommand printed usage and exited 0: a
        # wrapper or skill read that as success with an empty result. The
        # sibling `gmail-send.py` already returns 1 and calls `sys.exit(main())`
        # for the identical case, so the two disagreed about what "did nothing"
        # means.
        parser.print_help()
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
