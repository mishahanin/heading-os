#!/usr/bin/env python3
"""
gmail-send.py -- send a Gmail draft from the operator's personal mailbox.

Why this exists: the Gmail MCP connector can create, update, read and label
drafts, but it has no send operation, and `scripts/send-email.py` is bound to
the 31C Exchange identity. Anything addressed to a service that knows the
operator by their personal address needs this path instead.

The split is deliberate and keeps the send human-gated: a draft is composed
elsewhere (the MCP connector, or the Gmail web UI), reviewed by the operator,
and only then sent by an explicit invocation of this script. The script never
composes and never picks a draft on its own; an ambiguous match is an error,
not a guess.

Authentication is not re-invented here. It reuses the same authorized token
`scripts/gmail-reader.py` uses, via `scripts/utils/gmail_auth.py`. The
`gmail.modify` scope already covers `drafts.send`.

Usage:
    python scripts/gmail-send.py list
    python scripts/gmail-send.py send --draft-id r123456789
    python scripts/gmail-send.py send --match-subject "private information"
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.utils.venv import ensure_venv  # noqa: E402

ensure_venv()
from scripts.utils.workspace import load_env  # noqa: E402

load_env(PROJECT_ROOT)

from scripts.utils.colors import BOLD, GRAY, GREEN, RESET, YELLOW  # noqa: E402


class DraftSelectionError(Exception):
    """No draft matched, or more than one did."""


def select_draft(drafts, draft_id=None, match_subject=None):
    """Pick exactly one draft id from `drafts`.

    `drafts` is a list of {"id": str, "to": str, "subject": str} dicts. Exactly
    one selector must be given. A subject match is case-insensitive substring.
    Ambiguity raises rather than resolving to the first hit, because the caller
    is about to send mail on the operator's behalf.
    """
    if bool(draft_id) == bool(match_subject):
        raise DraftSelectionError("give exactly one of --draft-id or --match-subject")

    if draft_id:
        hits = [d for d in drafts if d["id"] == draft_id]
        if not hits:
            raise DraftSelectionError(f"no draft with id {draft_id}")
        return hits[0]["id"]

    needle = match_subject.lower()
    hits = [d for d in drafts if needle in (d.get("subject") or "").lower()]
    if not hits:
        raise DraftSelectionError(f"no draft whose subject contains {match_subject!r}")
    if len(hits) > 1:
        listed = ", ".join(f"{d['id']} ({d.get('subject','')[:40]})" for d in hits)
        raise DraftSelectionError(
            f"{len(hits)} drafts match {match_subject!r}: {listed}. Use --draft-id."
        )
    return hits[0]["id"]


def _headers(payload):
    return {h["name"]: h["value"] for h in payload.get("headers", [])}


def fetch_drafts(service, limit=25):
    listed = service.users().drafts().list(userId="me", maxResults=limit).execute()
    out = []
    for d in listed.get("drafts", []):
        meta = service.users().drafts().get(userId="me", id=d["id"], format="metadata").execute()
        hdrs = _headers(meta["message"].get("payload", {}))
        out.append({"id": d["id"], "to": hdrs.get("To", ""), "subject": hdrs.get("Subject", "")})
    return out


def cmd_list(args):
    from scripts.utils.gmail_auth import get_service

    service = get_service()
    account = service.users().getProfile(userId="me").execute().get("emailAddress", "?")
    drafts = fetch_drafts(service, args.limit)
    print(f"{BOLD}{account}{RESET}: {len(drafts)} draft(s)")
    for d in drafts:
        subject = d["subject"] or f"{GRAY}(no subject){RESET}"
        print(f"  {d['id']}  {GRAY}->{RESET} {d['to'] or '(no recipient)'}  |  {subject}")
    return 0


def cmd_send(args):
    from scripts.utils.gmail_auth import get_service

    service = get_service()
    account = service.users().getProfile(userId="me").execute().get("emailAddress", "?")
    drafts = fetch_drafts(service, args.limit)
    try:
        chosen = select_draft(drafts, args.draft_id, args.match_subject)
    except DraftSelectionError as exc:
        print(f"{YELLOW}{exc}{RESET}", file=sys.stderr)
        return 2

    sent = service.users().drafts().send(userId="me", body={"id": chosen}).execute()
    msg = service.users().messages().get(userId="me", id=sent["id"], format="metadata").execute()
    hdrs = _headers(msg.get("payload", {}))
    print(f"{GREEN}sent{RESET} from {account}")
    print(f"  message id : {sent['id']}")
    print(f"  thread id  : {sent.get('threadId','')}")
    print(f"  to         : {hdrs.get('To','')}")
    print(f"  subject    : {hdrs.get('Subject','')}")
    print(f"  date       : {hdrs.get('Date','')}")
    return 0


def main():
    parser = argparse.ArgumentParser(description="Send a Gmail draft from the personal mailbox")
    sub = parser.add_subparsers(dest="command")

    p_list = sub.add_parser("list", help="List drafts with their ids")
    p_list.add_argument("--limit", type=int, default=25)

    p_send = sub.add_parser("send", help="Send one existing draft")
    p_send.add_argument("--draft-id", help="Exact draft id (from `list`)")
    p_send.add_argument("--match-subject", help="Case-insensitive substring; must match exactly one draft")
    p_send.add_argument("--limit", type=int, default=25)

    args = parser.parse_args()
    if args.command == "list":
        return cmd_list(args)
    if args.command == "send":
        return cmd_send(args)
    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
