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

Tests: tests/test_an_edit_that_deleted_the_addresses_it_promised_to_keep.py, tests/test_gmail_send.py

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

from scripts.utils.venv_guard import ensure_venv  # noqa: E402

ensure_venv()
from scripts.utils.workspace import load_env  # noqa: E402

load_env(PROJECT_ROOT)

from scripts.utils.colors import BOLD, GRAY, GREEN, RESET, YELLOW  # noqa: E402


class DraftSelectionError(Exception):
    """No draft matched, or more than one did."""


def select_draft(drafts, draft_id=None, match_subject=None,
                 complete=False, searched=None):
    """Pick exactly one draft id from `drafts`.

    `drafts` is a list of {"id": str, "to": str, "subject": str} dicts. Exactly
    one selector must be given. A subject match is case-insensitive substring.
    Ambiguity raises rather than resolving to the first hit, because the caller
    is about to send mail on the operator's behalf.

    `complete` is the caller's report of whether `drafts` is the whole mailbox
    or a bounded prefix of it. Over a prefix, neither "no draft matched" nor
    "exactly one draft matched" is a statement this function can make: the next
    unlooked-at page can hold the missing draft or a second one with the same
    subject. So a truncated walk refuses instead of answering, and names the
    horizon it did search. Sending the wrong draft is not recoverable.

    It defaults to FALSE, which reads backwards until you ask what a caller who
    omitted it actually knows. Nothing: a list arrived from somewhere and its
    completeness was never established. Defaulting to True would let that
    caller assert uniqueness over an unknown subset, which is the exact defect
    this parameter was added to close, re-openable by forgetting one keyword.
    Unknown therefore means friction, in line with every other unclassified
    input in this workspace. Only `--draft-id` is unaffected: an id either
    exists in the list or it does not, and no unread page changes that.
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
    if len(hits) > 1:
        listed = ", ".join(f"{d['id']} ({d.get('subject','')[:40]})" for d in hits)
        raise DraftSelectionError(
            f"{len(hits)} drafts match {match_subject!r}: {listed}. Use --draft-id."
        )
    if not complete:
        horizon = searched if searched is not None else len(drafts)
        found = (f"1 matched within it ({hits[0]['id']})" if hits
                 else "none matched within it")
        raise DraftSelectionError(
            f"only the first {horizon} draft(s) were searched and {found}; "
            f"more drafts remain unread, so this match is not known to be "
            f"unique. Raise --limit, or pass --draft-id."
        )
    if not hits:
        raise DraftSelectionError(f"no draft whose subject contains {match_subject!r}")
    return hits[0]["id"]


def _headers(payload):
    return {h["name"]: h["value"] for h in payload.get("headers", [])}


PAGE_SIZE = 100             # drafts.list caps maxResults at 500; 100 is its default
MAX_LIST_PAGES = 50         # 5,000 drafts, then stop and say so
SEARCH_LIMIT = 500          # how far --match-subject looks before it refuses


def fetch_drafts(service, limit=25, max_pages=MAX_LIST_PAGES):
    """Up to `limit` drafts, following nextPageToken. Returns (rows, complete).

    A single `drafts.list` call is not "the first `limit` drafts": the API is
    documented to return fewer than maxResults and still hand back a page
    token. Following it is also what makes `--match-subject` mean anything.
    Unpaged, that flag searched one page and then claimed a UNIQUE match over
    it, so a draft on page two produced "no draft whose subject contains ...",
    and two drafts sharing a subject across the page boundary looked
    unambiguous. The module docstring promises "an ambiguous match is an error,
    not a guess"; a guess is exactly what an unstated horizon produces.

    `complete` is False when either bound below stopped the walk, and the
    caller says so rather than asserting uniqueness over a subset.
    """
    # Bounded twice, and not hypothetically: on 2026-08-24 a mutation run in
    # this repo disabled the equivalent token line in gmail-reader.py, the stub
    # replied with the same page forever, and the process reached 47 GB before
    # the OOM-killer took the whole session. A loop whose termination depends
    # entirely on a remote party is a loop that can consume all memory.
    out = []
    token = None
    seen_tokens = set()
    for _ in range(max_pages):
        kwargs = {"userId": "me", "maxResults": min(PAGE_SIZE, limit - len(out))}
        if token:
            kwargs["pageToken"] = token
        listed = service.users().drafts().list(**kwargs).execute()
        for d in listed.get("drafts", []):
            meta = service.users().drafts().get(
                userId="me", id=d["id"], format="metadata").execute()
            hdrs = _headers(meta["message"].get("payload", {}))
            out.append({"id": d["id"], "to": hdrs.get("To", ""),
                        "subject": hdrs.get("Subject", "")})
        token = listed.get("nextPageToken")
        if not token:
            return out, True
        if len(out) >= limit:
            return out, False
        if token in seen_tokens:
            print(f"warning: the server repeated page token {token!r}; "
                  f"stopping after {len(out)} draft(s)", file=sys.stderr)
            return out, False
        seen_tokens.add(token)
    print(f"warning: stopped at the {max_pages}-page cap with {len(out)} "
          f"draft(s); more remain", file=sys.stderr)
    return out, False


def cmd_list(args):
    from scripts.utils.gmail_auth import get_service

    service = get_service()
    account = service.users().getProfile(userId="me").execute().get("emailAddress", "?")
    drafts, complete = fetch_drafts(service, args.limit)
    tail = "" if complete else f" {GRAY}(more remain; raise --limit){RESET}"
    print(f"{BOLD}{account}{RESET}: {len(drafts)} draft(s){tail}")
    for d in drafts:
        subject = d["subject"] or f"{GRAY}(no subject){RESET}"
        print(f"  {d['id']}  {GRAY}->{RESET} {d['to'] or '(no recipient)'}  |  {subject}")
    return 0


def _resolve_draft_id(service, draft_id):
    """Confirm one draft id exists, without listing. Raises on a real miss."""
    from googleapiclient.errors import HttpError
    try:
        service.users().drafts().get(
            userId="me", id=draft_id, format="minimal").execute()
    except HttpError as exc:
        if getattr(exc, "status_code", None) == 404 or "404" in str(exc):
            raise DraftSelectionError(f"no draft with id {draft_id}") from exc
        raise
    return draft_id


def cmd_send(args):
    from scripts.utils.gmail_auth import get_service

    service = get_service()
    account = service.users().getProfile(userId="me").execute().get("emailAddress", "?")
    try:
        if args.draft_id and args.match_subject:
            raise DraftSelectionError("give exactly one of --draft-id or --match-subject")
        if args.draft_id:
            # Straight to the id. This used to search inside the first
            # --limit (25) drafts, so an operator pasting the id that
            # gmail-draft.py had just printed got "no draft with id ..." as
            # soon as they had 26 drafts. The draft existed; the lookup had
            # not paged far enough, and the message sent them hunting for a
            # problem that was not there.
            chosen = _resolve_draft_id(service, args.draft_id)
        else:
            drafts, complete = fetch_drafts(service, args.limit)
            chosen = select_draft(drafts, args.draft_id, args.match_subject,
                                  complete=complete, searched=len(drafts))
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
    # How far --match-subject looks, not how much it shows. It defaulted to 25
    # alongside `list`, which made a uniqueness claim over one page. Every
    # draft inside the horizon costs one metadata fetch, so this trades a
    # slower search for an answer the script is entitled to give.
    p_send.add_argument("--limit", type=int, default=SEARCH_LIMIT,
                        help=f"How many drafts --match-subject searches "
                             f"(default {SEARCH_LIMIT})")

    args = parser.parse_args()
    if args.command == "list":
        return cmd_list(args)
    if args.command == "send":
        return cmd_send(args)
    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
