#!/usr/bin/env python3
"""gmail-draft.py -- compose a Gmail draft, with attachments, in the personal mailbox.

Why this exists: the Gmail MCP connector can create a draft but only from
base64 content passed inline, so a letter with medical scans or invoices cannot
be assembled through it, and `scripts/send-email.py` is bound to the 31C
Exchange identity. This script builds the MIME message from files on disk and
calls drafts().create through the same authorized token `gmail-send.py` uses.

It composes and stops there. Nothing here sends: the draft waits in the mailbox
for the operator, who reviews it and then sends it from Gmail or with
`python scripts/gmail-send.py send --draft-id ID`. That split is the same one
`gmail-send.py` documents, and it is what keeps the send human-gated.

Usage:
    python scripts/gmail-draft.py --to them@example.com \\
        --subject "Claim AB-1234-567" --body-file letter.md --attach report.pdf

    # reply inside an existing thread, subject derived from the parent:
    python scripts/gmail-draft.py --to them@example.com \\
        --reply-to-message 19fb012b14992f4c --body-file reply.md \\
        --body-after-separator --attach a.pdf --attach b.jpg
"""

from __future__ import annotations

import argparse
import base64
import mimetypes
import sys
from email.message import EmailMessage
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.utils.venv_guard import ensure_venv  # noqa: E402

ensure_venv()
from scripts.utils.workspace import load_env  # noqa: E402

load_env(PROJECT_ROOT)

from scripts.utils.colors import BOLD, GRAY, GREEN, RESET, YELLOW  # noqa: E402

# Gmail refuses a message whose parts exceed 25 MB.
MAX_TOTAL_BYTES = 25 * 1024 * 1024


class DraftBuildError(Exception):
    """The draft cannot be assembled from what the caller gave."""


def body_text(path: Path, after_separator: bool = False) -> str:
    """Read the letter body from `path`.

    Letters in this workspace are drafted as markdown with a header block
    (To, Cc, Subject) above a `---` line and the letter itself below it. With
    `after_separator` the header block is dropped, so the same file serves both
    the human reading it and this script. Without it the whole file is the body,
    which is the safe default: silently truncating someone's letter at a stray
    `---` would be worse than sending a couple of header lines.
    """
    text = path.read_text(encoding="utf-8")
    if not after_separator:
        return text
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if line.strip() == "---":
            return "\n".join(lines[i + 1 :]).strip() + "\n"
    raise DraftBuildError(f"--body-after-separator given but no '---' line in {path}")


def reply_subject(parent_subject: str) -> str:
    """Prefix with Re: unless the parent subject already carries one."""
    if parent_subject.lower().startswith("re:"):
        return parent_subject
    return f"Re: {parent_subject}"


def build_message(to, cc, bcc, subject, body, attachments, in_reply_to=None) -> EmailMessage:
    """Assemble the MIME message. Raises when the attachments exceed Gmail's limit."""
    msg = EmailMessage()
    msg["To"] = ", ".join(to)
    if cc:
        msg["Cc"] = ", ".join(cc)
    if bcc:
        msg["Bcc"] = ", ".join(bcc)
    msg["Subject"] = subject
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
        msg["References"] = in_reply_to
    msg.set_content(body)

    total = 0
    for path in attachments:
        if not path.is_file():
            raise DraftBuildError(f"attachment not found: {path}")
        data = path.read_bytes()
        total += len(data)
        if total > MAX_TOTAL_BYTES:
            raise DraftBuildError(
                f"attachments exceed Gmail's 25 MB limit at {path.name} "
                f"({total / 1_048_576:.1f} MB so far)"
            )
        guessed, _ = mimetypes.guess_type(path.name)
        maintype, subtype = (guessed or "application/octet-stream").split("/", 1)
        msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=path.name)
    return msg


def parent_headers(service, message_id: str):
    """Return (rfc822 Message-ID, subject, thread id) of the message being replied to."""
    meta = service.users().messages().get(userId="me", id=message_id, format="metadata").execute()
    headers = {h["name"].lower(): h["value"] for h in meta.get("payload", {}).get("headers", [])}
    return headers.get("message-id"), headers.get("subject", ""), meta.get("threadId")


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a Gmail draft with attachments")
    parser.add_argument("--to", action="append", required=True, help="Recipient (repeatable)")
    parser.add_argument("--cc", action="append", default=[], help="Cc recipient (repeatable)")
    parser.add_argument("--bcc", action="append", default=[], help="Bcc recipient (repeatable)")
    parser.add_argument("--subject", help="Subject; derived from the parent when replying")
    parser.add_argument("--body-file", required=True, type=Path, help="File holding the letter")
    parser.add_argument(
        "--body-after-separator",
        action="store_true",
        help="Drop everything up to and including the first '---' line of the body file",
    )
    parser.add_argument("--attach", action="append", default=[], type=Path, help="File (repeatable)")
    parser.add_argument("--reply-to-message", help="Gmail message id to reply to, threading the draft")
    args = parser.parse_args()

    from scripts.utils.gmail_auth import get_service

    service = get_service()
    account = service.users().getProfile(userId="me").execute().get("emailAddress", "?")

    in_reply_to = thread_id = None
    subject = args.subject
    if args.reply_to_message:
        in_reply_to, parent_subject, thread_id = parent_headers(service, args.reply_to_message)
        if not subject:
            subject = reply_subject(parent_subject)
    if not subject:
        print(f"{YELLOW}--subject is required unless --reply-to-message is given{RESET}", file=sys.stderr)
        return 2

    try:
        body = body_text(args.body_file, args.body_after_separator)
        msg = build_message(args.to, args.cc, args.bcc, subject, body, args.attach, in_reply_to)
    except (DraftBuildError, OSError) as exc:
        print(f"{YELLOW}{exc}{RESET}", file=sys.stderr)
        return 2

    message = {"raw": base64.urlsafe_b64encode(msg.as_bytes()).decode()}
    if thread_id:
        message["threadId"] = thread_id
    draft = service.users().drafts().create(userId="me", body={"message": message}).execute()

    size = sum(p.stat().st_size for p in args.attach)
    print(f"{GREEN}draft created{RESET} in {BOLD}{account}{RESET}")
    print(f"  draft id    : {draft['id']}")
    print(f"  to          : {', '.join(args.to)}")
    print(f"  subject     : {subject}")
    if thread_id:
        print(f"  thread id   : {thread_id}  {GRAY}(in-reply-to {in_reply_to}){RESET}")
    if args.attach:
        print(f"  attachments : {len(args.attach)} file(s), {size / 1_048_576:.1f} MB")
    print(f"\n{GRAY}Nothing was sent. Review it, then:{RESET}")
    print(f"  python scripts/gmail-send.py send --draft-id {draft['id']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
