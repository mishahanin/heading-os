#!/usr/bin/env python3
"""
Send Email via Exchange with 31C Branded Signature

Sends HTML emails through EWS with the 31C email signature and
inline CID-attached logo + divider images. This is the single
entry point for all outgoing email from the workspace.

Usage:
    python scripts/send-email.py \
        --to "recipient@example.com" \
        --subject "Subject line" \
        --body "<p>HTML body content</p>"

    python scripts/send-email.py \
        --to "recipient@example.com" \
        --cc "cc1@example.com" "cc2@example.com" \
        --subject "Subject line" \
        --body "<p>HTML body</p>"

    python scripts/send-email.py \
        --to "a@example.com" "b@example.com" \
        --cc "c@example.com" \
        --bcc "d@example.com" \
        --subject "Subject" \
        --body "<p>Body</p>"

    # Plain text body (auto-wrapped in HTML)
    python scripts/send-email.py \
        --to "recipient@example.com" \
        --subject "Quick note" \
        --body "Just plain text here"

    # Batch mode: send N messages with a single exchangelib import
    # and a single Exchange Account connection.
    python scripts/send-email.py --batch messages.json

    # Threaded reply (to the sender of the matched message, preserves thread):
    python scripts/send-email.py --reply \
        --match-from "alice@example.com" \
        --match-subject "31C / Globex" \
        --body "<p>Alex, ...</p>"

    # Threaded reply-all (sender + To + CC of the matched message):
    python scripts/send-email.py --reply-all \
        --match-subject "31C / Globex Systems" \
        --body "<p>...</p>"

    # Threaded forward (quotes the original AND carries its attachments):
    python scripts/send-email.py --forward \
        --match-subject "Acme Group" \
        --to "bob@example.org" "carol@example.net" \
        --body "<p>Marlow, Alex, ...</p>"

    # Most precise: identify the original by exact Exchange item id.
    python scripts/send-email.py --reply --match-id "AAMk..." --body "<p>...</p>"

    # messages.json shape:
    # [
    #   {"to": "a@example.com",
    #    "subject": "Hello",
    #    "body": "<p>HTML body</p>",
    #    "cc": ["c@example.com"],
    #    "bcc": [],
    #    "attach": ["/abs/path/file.pdf"]},
    #   {"to": ["b@example.com", "c@example.com"],
    #    "subject": "Multi-recipient",
    #    "body": "Plain text auto-wrapped"}
    # ]

Notes:
    - Signature is always appended automatically
    - Logo and divider images are embedded as inline CID attachments
    - Body can be HTML or plain text (auto-detected)
    - Multiple --to, --cc, --bcc recipients supported
    - Batch mode amortises the ~600ms exchangelib cold-import across N sends
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path

# ============================================================
# Configuration
# ============================================================

# --- Lazy exchangelib loading (F-2.1: import must stay pure) ---
# The heavy `exchangelib` import is deferred out of module scope so that
# importing this file (e.g. pytest collection on a fresh clone without the
# `email` extra) never triggers an import-time SystemExit. Every function that
# constructs an exchangelib object calls `_ensure_exchangelib()` first, which
# imports the package (loud + attributed via optdeps.require) and binds the
# names into module globals on first use. CLI behaviour is unchanged.
Account = Configuration = Credentials = DELEGATE = None
Message = Mailbox = HTMLBody = FileAttachment = None
_EXCHANGELIB_LOADED = False


def _ensure_exchangelib():
    """Import exchangelib on first call and bind its names into module globals.

    Idempotent and cheap after the first call. Exits 1 with an actionable
    message if the `email` extra is not installed (never a bare stack trace).
    """
    global _EXCHANGELIB_LOADED
    global Account, Configuration, Credentials, DELEGATE
    global Message, Mailbox, HTMLBody, FileAttachment
    if _EXCHANGELIB_LOADED:
        return
    from scripts.utils.optdeps import require
    exchangelib = require("exchangelib", extra="email")
    Account = exchangelib.Account
    Configuration = exchangelib.Configuration
    Credentials = exchangelib.Credentials
    DELEGATE = exchangelib.DELEGATE
    Message = exchangelib.Message
    Mailbox = exchangelib.Mailbox
    HTMLBody = exchangelib.HTMLBody
    FileAttachment = exchangelib.FileAttachment
    _EXCHANGELIB_LOADED = True


def _derive_subject(mode: str, original_subject: str, override: str = None) -> str:
    """Subject for a reply/forward. Uses override if given; else prefixes the
    original subject with RE:/FW:, avoiding a double prefix when one is present."""
    if override:
        return override
    base = (original_subject or "").strip()
    low = base.lower()
    if mode == "forward":
        if low.startswith(("fw:", "fwd:")):
            return base
        return f"FW: {base}" if base else "FW:"
    # reply / reply_all
    if low.startswith("re:"):
        return base
    return f"RE: {base}" if base else "RE:"


# Resolve workspace root (scripts/ is one level down)
SCRIPT_DIR = Path(__file__).resolve().parent
WORKSPACE_ROOT = SCRIPT_DIR.parent

sys.path.insert(0, str(WORKSPACE_ROOT))

from scripts.utils.venv_guard import ensure_venv  # noqa: E402

ensure_venv()
from scripts.utils.workspace import get_data_root, load_env  # noqa: E402


def _resolve_asset(rel_path: str) -> Path:
    """Resolve a shared asset (signature HTML, brand images) that may live at
    the engine root, under the DATA root (CEO master: reference/ and datastore/
    are data-routed, so the signature and brand assets resolve there), or under
    corporate/ (exec workspace, where shared content lives in the sync-mirrored
    corporate/ subdirectory).

    Tries each candidate in turn; returns the data-root path when none exist so
    the WARN points at the canonical home for these assets on the CEO master.
    """
    candidates = [
        WORKSPACE_ROOT / rel_path,
        get_data_root() / rel_path,
        WORKSPACE_ROOT / "corporate" / rel_path,
        get_data_root() / "corporate" / rel_path,
    ]
    for cand in candidates:
        if cand.exists():
            return cand
    return get_data_root() / rel_path


SIGNATURE_PATH = _resolve_asset("reference/email-signature.html")
LOGO_PATH = _resolve_asset("datastore/brand/assets/email-signature/logo-email-signature.png")
DIVIDER_PATH = _resolve_asset("datastore/brand/assets/email-signature/divider.png")


def load_config():
    """Load Exchange credentials from .env"""
    load_env(WORKSPACE_ROOT)

    required = ["EXCHANGE_SERVER", "EXCHANGE_EMAIL", "EXCHANGE_PASSWORD"]
    config = {}
    for key in required:
        val = os.getenv(key)
        if not val:
            print(f"[ERROR] Missing {key} in .env")
            sys.exit(1)
        config[key] = val

    config["EXCHANGE_USERNAME"] = os.getenv("EXCHANGE_USERNAME", config["EXCHANGE_EMAIL"])
    return config


# ============================================================
# Resend safety
# ============================================================

# Errors that PROVE the request never reached the server, so sending again
# cannot produce a second copy. Matched by class name through the MRO, which
# avoids importing the requests and exchangelib exception hierarchies here and
# lets the tests exercise the rule with plain stand-ins.
#
# The list is deliberately short. Until 2026-08-23 both send paths retried a
# bare `except Exception` three times, so the one failure that is not a failure
# — the server accepted the message and the response was lost coming back —
# delivered the email twice to a real recipient and reported "send failed".
_SAFE_TO_RESEND = frozenset({
    "ConnectTimeout",       # requests: the connection was never established
    "NewConnectionError",   # urllib3: DNS or refused, nothing was transmitted
    "ErrorServerBusy",      # exchangelib: the server said it did not process it
    "RateLimitError",       # exchangelib: throttled, request rejected outright
})

# A ReadTimeout is the dangerous one and is deliberately absent: the request went
# out and the answer did not come back, which looks identical whether the server
# delivered the mail or dropped it.
_UNSURE_NOTE = (
    "This send was NOT retried, because the error does not prove the message "
    "never left. Check Sent Items before sending it again — it may already be out."
)


def _is_safe_to_resend(exc: BaseException) -> bool:
    """True only when resending `exc`'s request cannot duplicate an email.

    Fails toward one copy. An unrecognised error carries no proof either way,
    and the two mistakes do not cost the same: a missing email is noticed and
    re-sent by a person, a duplicate one cannot be recalled.
    """
    return any(cls.__name__ in _SAFE_TO_RESEND for cls in type(exc).__mro__)


# ============================================================
# Exchange Connection
# ============================================================

def connect(config, max_retries=3):
    """Connect to Exchange server via EWS with retry."""
    _ensure_exchangelib()
    credentials = Credentials(
        username=config["EXCHANGE_USERNAME"],
        password=config["EXCHANGE_PASSWORD"]
    )
    exchange_config = Configuration(
        server=config["EXCHANGE_SERVER"],
        credentials=credentials,
    )
    last_error = None
    for attempt in range(1, max_retries + 1):
        try:
            account = Account(
                primary_smtp_address=config["EXCHANGE_EMAIL"],
                config=exchange_config,
                autodiscover=False,
                access_type=DELEGATE,
            )
            return account
        except Exception as e:
            last_error = e
            if attempt < max_retries:
                import time
                wait = 2 ** attempt
                print(f"[WARN] Connection attempt {attempt}/{max_retries} failed: {e}. Retrying in {wait}s...")
                time.sleep(wait)
    print(f"[ERROR] Failed to connect after {max_retries} attempts: {last_error}")
    sys.exit(1)


# ============================================================
# Signature Embedding
# ============================================================

def build_signature_attachments():
    """Create inline FileAttachment objects for signature images."""
    _ensure_exchangelib()
    attachments = []

    if LOGO_PATH.exists():
        logo_data = LOGO_PATH.read_bytes()
        attachments.append(FileAttachment(
            name="logo31c.png",
            content=logo_data,
            is_inline=True,
            content_id="logo31c",
            content_type="image/png",
        ))
    else:
        print(f"[WARN] Logo not found: {LOGO_PATH}")

    if DIVIDER_PATH.exists():
        divider_data = DIVIDER_PATH.read_bytes()
        # Two divider instances side by side in the signature
        attachments.append(FileAttachment(
            name="divider31c.png",
            content=divider_data,
            is_inline=True,
            content_id="divider31c",
            content_type="image/png",
        ))
        attachments.append(FileAttachment(
            name="divider31c2.png",
            content=divider_data,
            is_inline=True,
            content_id="divider31c2",
            content_type="image/png",
        ))
    else:
        print(f"[WARN] Divider not found: {DIVIDER_PATH}")

    return attachments


def load_signature():
    """Load HTML signature from file."""
    if not SIGNATURE_PATH.exists():
        print(f"[WARN] Signature not found: {SIGNATURE_PATH}")
        return ""
    return SIGNATURE_PATH.read_text(encoding="utf-8")


def is_html(text):
    """Check if text contains HTML tags."""
    return bool(re.search(r'<[a-zA-Z/][^>]*>', text))


_SIGNOFF_KEYWORDS = r"(?:Best|Thanks|Regards|Cheers|Sincerely|Kind\s+regards|Warmly|BR|Br)"
_NAME_TOKEN = r"[A-Z][A-Za-z'\-]{1,30}"

_SIGNOFF_PATTERNS = [
    # <p>Best,<br>Misha</p> at end
    re.compile(
        rf"<p[^>]*>\s*{_SIGNOFF_KEYWORDS}[,.]?\s*<br\s*/?>\s*{_NAME_TOKEN}\s*</p>\s*$",
        re.IGNORECASE,
    ),
    # <p>Best,</p><p>Misha</p> at end
    re.compile(
        rf"<p[^>]*>\s*{_SIGNOFF_KEYWORDS}[,.]?\s*</p>\s*<p[^>]*>\s*{_NAME_TOKEN}\s*</p>\s*$",
        re.IGNORECASE,
    ),
    # Plain text: "Best,\nMisha" at end
    re.compile(
        rf"\n\s*{_SIGNOFF_KEYWORDS}[,.]?\s*\n\s*{_NAME_TOKEN}\s*\n*\Z",
        re.IGNORECASE,
    ),
]


def strip_trailing_signoff(body: str) -> str:
    """Strip a trailing "Best, <Name>" style sign-off from the body.

    The branded auto-signature already carries the sender's full name and
    title, so a manual sign-off in the body produces awkward doubling
    ("Best, / Misha / Misha Hanin / Chief Executive Officer"). Handles
    plain text plus the two common HTML shapes (<p>X,<br>N</p> and
    <p>X,</p><p>N</p>). A bare "Best," with no name is preserved.
    """
    out = body
    for pat in _SIGNOFF_PATTERNS:
        new = pat.sub("", out)
        if new != out:
            out = new
            break
    return out.rstrip()


# ============================================================
# Message Building
# ============================================================

def build_file_attachments(paths):
    """Create non-inline FileAttachment objects from filesystem paths.
    MIME type is guessed from the file extension; falls back to
    application/octet-stream when unknown."""
    import mimetypes
    file_attachments = []
    if not paths:
        return file_attachments
    _ensure_exchangelib()
    for raw in paths:
        p = Path(raw)
        if not p.exists():
            print(f"[ERROR] Attachment not found: {p}")
            sys.exit(1)
        mime, _ = mimetypes.guess_type(p.name)
        file_attachments.append(FileAttachment(
            name=p.name,
            content=p.read_bytes(),
            is_inline=False,
            content_type=mime or "application/octet-stream",
        ))
    return file_attachments


# ============================================================
# Send / Persistence
# ============================================================

def _build_full_html(body: str, signature: str) -> str:
    """Strip a trailing manual sign-off, wrap plain text in escaped HTML, apply
    the Segoe UI font stack, and append the branded signature. Shared by the
    new-message and threaded (reply/forward) paths so both render identically."""
    import html
    body = strip_trailing_signoff(body)
    if not is_html(body):
        paragraphs = body.split("\n\n")
        body_html = "".join(f"<p>{html.escape(p)}</p>" for p in paragraphs if p.strip())
    else:
        body_html = body
    wrapped_body = (
        '<div style="font-family: \'Segoe UI\', Calibri, Arial, sans-serif; '
        f'font-size: 11pt;">{body_html}</div>'
    )
    return wrapped_body + "<br>" + signature


def _autolog_to(to_list, subject, body):
    """Best-effort CRM auto-log for each resolved recipient. Never raises into
    the send path. Mirrors the new-message auto-log contract."""
    try:
        from scripts.utils.crm_autolog import log_outbound
        for to_addr in (to_list or []):
            to_addr = (to_addr or "").strip()
            if to_addr:
                log_outbound(
                    recipient_email=to_addr,
                    subject=subject or "",
                    body_excerpt=(body or "")[:300],
                )
    except Exception as e:
        # Auto-log is best-effort; never fail the send because of CRM mutation.
        print(f"WARN: crm_autolog skipped: {e}", file=sys.stderr)


def _send_email_core(account, to, subject, body, cc=None, bcc=None, attach=None,
                     signature=None):
    """Inner core: build and send one message on an established account.

    Returns ``{"to": [...], "status": "sent"|"failed", "error": str|None}``.
    Does NOT call ``sys.exit`` on failure - callers decide how to handle.

    ``signature`` can be pre-loaded by the caller (batch mode) so the signature
    HTML is not re-read per message.

    There is NO matching `sig_attachments` parameter, and the one that used to
    sit here was a lie: it was documented as letting batch mode skip rebuilding
    the inline images, but the body ignored it and called
    `build_signature_attachments()` again regardless. It has to: a
    FileAttachment binds to a Message once `.attach()` is called, so the objects
    genuinely cannot be shared across messages. Removing the parameter is the
    honest form -- keeping it invited a caller to rely on an optimisation that
    never existed.
    """
    _ensure_exchangelib()
    if signature is None:
        signature = load_signature()
    file_attachments = build_file_attachments(attach)

    # SEC-001: plain-text bodies are HTML-escaped inside _build_full_html.
    full_html = _build_full_html(body, signature)

    to_recipients = [Mailbox(email_address=addr.strip()) for addr in to]
    cc_recipients = [Mailbox(email_address=addr.strip()) for addr in cc] if cc else None
    bcc_recipients = [Mailbox(email_address=addr.strip()) for addr in bcc] if bcc else None

    msg = Message(
        account=account,
        folder=account.drafts,
        subject=subject,
        body=HTMLBody(full_html),
        to_recipients=to_recipients,
        cc_recipients=cc_recipients,
        bcc_recipients=bcc_recipients,
    )

    # Save as draft first so we can attach inline images
    try:
        msg.save()
    except Exception as e:
        return {"to": list(to), "status": "failed", "error": f"save draft failed: {e}"}

    # Attach inline signature images (rebuild per-message - FileAttachment
    # objects are bound to a Message after .attach()).
    fresh_sig_attachments = build_signature_attachments()
    # Guarded. An oversized file or an EWS error here used to raise straight
    # through send_batch, aborting every remaining message with a traceback and
    # no per-message result -- after this draft had already been saved. The
    # batch contract is "a status dict per message", so a failure returns one.
    try:
        for att in fresh_sig_attachments:
            msg.attach(att)
        for att in file_attachments:
            msg.attach(att)
    except Exception as e:
        return {"to": list(to), "status": "failed",
                "error": f"attach failed ({e}); the draft was saved but NOT sent"}

    # Send with retry. See `_is_safe_to_resend`: only a failure that proves the
    # request never reached the server is retried.
    last_error = None
    for attempt in range(1, 4):
        try:
            msg.send()
            to_str = ", ".join(to)
            cc_str = f" (CC: {', '.join(cc)})" if cc else ""
            print(f"[OK] Email sent to {to_str}{cc_str}")
            print(f"     Subject: {subject}")
            print(f"     Signature: embedded with {len(fresh_sig_attachments)} inline image(s)")
            if file_attachments:
                names = ", ".join(a.name for a in file_attachments)
                print(f"     Attachments: {len(file_attachments)} file(s) - {names}")
            # Auto-log to CRM: bumps last_touch + appends 1-line interaction log on
            # the matched relationship record. Strict email match against the address
            # book. Silent no-op on no match. CC/BCC are intentionally NOT auto-logged.
            _autolog_to(to, subject, body)
            return {"to": list(to), "status": "sent", "error": None}
        except Exception as e:
            last_error = e
            if not _is_safe_to_resend(e):
                print(f"[ERROR] Send failed: {e}")
                print(f"[ERROR] {_UNSURE_NOTE}")
                return {
                    "to": list(to),
                    "status": "failed",
                    "error": f"send failed ({e}). {_UNSURE_NOTE}",
                }
            if attempt < 3:
                import time
                wait = 2 ** attempt
                print(f"[WARN] Send attempt {attempt}/3 failed: {e}. Retrying in {wait}s...")
                time.sleep(wait)
    return {
        "to": list(to),
        "status": "failed",
        "error": f"send failed after 3 attempts: {last_error}",
    }


def _resolve_folder(account, folder_name):
    """Map a folder name to the account folder. Inbox (default) or Sent."""
    name = (folder_name or "Inbox").strip().lower()
    if name in ("sent", "sent items", "sentitems"):
        return account.sent
    return account.inbox


def find_message(account, match_id=None, match_from=None, match_subject=None,
                 folder_name="Inbox", scan_limit=50):
    """Locate the original message to reply to / forward.

    Precedence: match_id (exact Exchange item id) wins. Otherwise scan the
    folder newest-first, keep the first item whose subject contains
    match_subject (case-insensitive) AND whose sender contains match_from.
    Returns the Message or None.
    """
    folder = _resolve_folder(account, folder_name)
    if match_id:
        try:
            return folder.get(id=match_id)
        except Exception:
            # Fall back to a cross-folder lookup by id via the account root.
            try:
                return account.inbox.get(id=match_id)
            except Exception:
                return None

    qs = folder.all()
    if match_subject:
        qs = qs.filter(subject__icontains=match_subject)
    qs = qs.order_by("-datetime_received")

    want_from = (match_from or "").strip().lower()
    for item in qs[:scan_limit]:
        if want_from:
            sender = getattr(item, "sender", None)
            addr = (getattr(sender, "email_address", "") or "").lower()
            if want_from not in addr:
                continue
        return item
    return None


def _replyall_recipients(account, original):
    """All addresses a reply-all touches (sender + To + CC), minus self.
    Used only to drive the CRM auto-log; exchangelib builds the real envelope."""
    emails = set()
    snd = getattr(original, "sender", None)
    if snd and getattr(snd, "email_address", None):
        emails.add(snd.email_address)
    for grp_name in ("to_recipients", "cc_recipients"):
        for mb in (getattr(original, grp_name, None) or []):
            if getattr(mb, "email_address", None):
                emails.add(mb.email_address)
    self_email = (getattr(account, "primary_smtp_address", "") or "").lower()
    return [e for e in emails if e.lower() != self_email]


def _send_threaded_core(account, mode, original, body, to=None, cc=None, bcc=None,
                        attach=None, subject=None, signature=None):
    """Build, save, attach to, and send a threaded reply/reply_all/forward.

    Uses exchangelib's create_reply / create_reply_all / create_forward, which
    preserve conversation threading and quote the original below our body.
    Saves to Drafts first so the inline signature images (and any user files)
    can be attached before send - the same two-step pattern the new-message
    path uses. forward carries the original's attachments automatically.

    Returns {"to": [...], "status": "sent"|"failed", "error": str|None}.
    """
    _ensure_exchangelib()
    if signature is None:
        signature = load_signature()
    file_attachments = build_file_attachments(attach)
    full_html = _build_full_html(body, signature)
    derived_subject = _derive_subject(mode, getattr(original, "subject", "") or "", subject)
    to_mb = [Mailbox(email_address=a.strip()) for a in to] if to else None

    # Create the response draft object (not yet persisted).
    try:
        if mode == "reply":
            draft_ref = original.create_reply(derived_subject, HTMLBody(full_html),
                                              to_recipients=to_mb)
        elif mode == "reply_all":
            draft_ref = original.create_reply_all(derived_subject, HTMLBody(full_html))
        elif mode == "forward":
            if not to_mb:
                return {"to": [], "status": "failed",
                        "error": "forward requires --to (recipients to forward to)"}
            draft_ref = original.create_forward(derived_subject, HTMLBody(full_html),
                                                to_recipients=to_mb)
        else:
            return {"to": to or [], "status": "failed", "error": f"unknown mode: {mode}"}
        save_result = draft_ref.save(account.drafts)
    except Exception as e:
        return {"to": to or [], "status": "failed", "error": f"create/save {mode} failed: {e}"}

    # Re-fetch the persisted draft so we can attach + send.
    try:
        draft = account.drafts.get(id=save_result.id, changekey=save_result.changekey)
    except Exception as e:
        return {"to": to or [], "status": "failed", "error": f"fetch saved draft failed: {e}"}

    fresh_sig_attachments = build_signature_attachments()
    try:
        for att in fresh_sig_attachments:
            draft.attach(att)
        for att in file_attachments:
            draft.attach(att)
    except Exception as e:
        return {"to": to or [], "status": "failed",
                "error": f"attach failed ({e}); the draft was saved but NOT sent"}

    last_error = None
    for attempt in range(1, 4):
        try:
            draft.send()
            # Resolve the addresses actually touched, for logging + output.
            if mode == "reply_all":
                actual_to = _replyall_recipients(account, original)
            elif to:
                actual_to = list(to)
            else:  # reply with no explicit --to: goes to the original sender
                snd = getattr(original, "sender", None)
                actual_to = [snd.email_address] if (snd and getattr(snd, "email_address", None)) else []
            label = {"reply": "Reply", "reply_all": "Reply-all", "forward": "Forward"}[mode]
            print(f"[OK] {label} sent — {', '.join(actual_to) if actual_to else '(envelope built by Exchange)'}")
            print(f"     Subject: {derived_subject}")
            print(f"     Signature: embedded with {len(fresh_sig_attachments)} inline image(s)")
            if file_attachments:
                names = ", ".join(a.name for a in file_attachments)
                print(f"     Attachments: {len(file_attachments)} file(s) - {names}")
            _autolog_to(actual_to, derived_subject, body)
            return {"to": actual_to, "status": "sent", "error": None}
        except Exception as e:
            last_error = e
            if not _is_safe_to_resend(e):
                print(f"[ERROR] {mode} send failed: {e}")
                print(f"[ERROR] {_UNSURE_NOTE}")
                return {"to": to or [], "status": "failed",
                        "error": f"{mode} send failed ({e}). {_UNSURE_NOTE}"}
            if attempt < 3:
                import time
                wait = 2 ** attempt
                print(f"[WARN] Send attempt {attempt}/3 failed: {e}. Retrying in {wait}s...")
                time.sleep(wait)
    return {"to": to or [], "status": "failed",
            "error": f"{mode} send failed after 3 attempts: {last_error}"}


def send_email(account, to, subject, body, cc=None, bcc=None, attach=None):
    """Send a single email and exit on failure.

    Preserves the original CLI contract. Batch mode uses
    :func:`_send_email_core` directly to avoid exit-on-failure semantics.
    """
    result = _send_email_core(
        account=account, to=to, subject=subject, body=body,
        cc=cc, bcc=bcc, attach=attach,
    )
    if result["status"] != "sent":
        print(f"[ERROR] {result['error']}")
        print("         The draft was saved but NOT sent. Check Exchange drafts folder.")
        sys.exit(1)


def _normalize_addrs(value):
    """Accept str or list-of-str for to/cc/bcc; return list-of-str or None."""
    if value is None:
        return None
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(v) for v in value]
    raise ValueError(f"expected str or list, got {type(value).__name__}")


def send_batch(account, messages):
    """Send N messages on a single Account / connection.

    Args:
        account: an established exchangelib ``Account`` from :func:`connect`.
        messages: list of dicts. Each dict must have ``to`` (str or list),
            ``subject``, ``body``. Optional: ``cc``, ``bcc``, ``attach``.

    Returns:
        list of per-message result dicts: ``{"to": [...], "status": str,
        "error": str|None}``.
    """
    # Load the signature HTML once for the whole batch (the inline image
    # FileAttachment objects must be rebuilt per message because each one
    # is bound to its Message after .attach()).
    signature = load_signature()
    # Presence check only: this proves the signature images are readable BEFORE
    # the batch starts, so a missing asset fails once rather than N times.
    build_signature_attachments()

    results = []
    for idx, m in enumerate(messages, start=1):
        try:
            to = _normalize_addrs(m["to"])
            subject = m["subject"]
            body = m["body"]
        except (KeyError, ValueError) as e:
            print(f"[ERROR] Message #{idx} malformed: {e}")
            results.append({"to": [], "status": "failed", "error": f"malformed: {e}"})
            continue
        cc = _normalize_addrs(m.get("cc"))
        bcc = _normalize_addrs(m.get("bcc"))
        attach = m.get("attach")
        print(f"\n--- Batch message {idx}/{len(messages)} ---")
        result = _send_email_core(
            account=account, to=to, subject=subject, body=body,
            cc=cc, bcc=bcc, attach=attach,
            signature=signature,
        )
        results.append(result)
    return results


# ============================================================
# Main / CLI
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description="Send email via Exchange with 31C signature"
    )
    parser.add_argument("--to", nargs="+", help="Recipient email(s)")
    parser.add_argument("--cc", nargs="+", help="CC recipient(s)")
    parser.add_argument("--bcc", nargs="+", help="BCC recipient(s)")
    parser.add_argument("--subject", help="Email subject")
    parser.add_argument("--body", help="Email body (HTML or plain text)")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Validate the arguments, print what WOULD be sent, and exit 0 "
            "without connecting to Exchange. Added 2026-08-23 because the "
            "script had no way to exercise its own argument contract: checking "
            "that a flag parses meant sending a real message, and doing that "
            "once put a message on the wire that nobody wanted."
        ),
    )
    parser.add_argument(
        "--body-stdin",
        action="store_true",
        help=(
            "Read the body from stdin instead of --body. Use this for anything "
            "a person did not type at a prompt: an argv element is visible to "
            "any local account via `ps` for the life of the send, and Linux "
            "caps one at 131072 bytes, which a long HTML body exceeds."
        ),
    )
    parser.add_argument(
        "--attach",
        nargs="+",
        help="One or more file paths to attach (non-inline). MIME guessed from extension.",
    )
    parser.add_argument(
        "--batch",
        help=(
            "Path to a JSON file containing an array of message dicts. "
            "Amortises the exchangelib cold-import and Exchange Account "
            "connection across N sends. Each dict must have 'to', 'subject', "
            "'body'; optional: 'cc', 'bcc', 'attach'."
        ),
    )
    # --- Threaded reply / reply-all / forward ---
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument("--reply", action="store_true",
                            help="Threaded reply to the matched message's sender.")
    mode_group.add_argument("--reply-all", action="store_true",
                            help="Threaded reply to everyone on the matched message.")
    mode_group.add_argument("--forward", action="store_true",
                            help="Threaded forward of the matched message (requires --to).")
    parser.add_argument("--match-id",
                        help="Exact Exchange item id of the message to reply/forward (most precise).")
    parser.add_argument("--match-from",
                        help="Find the newest message whose sender email contains this (case-insensitive).")
    parser.add_argument("--match-subject",
                        help="Find the newest message whose subject contains this (case-insensitive).")
    parser.add_argument("--match-folder", default="Inbox",
                        help="Folder to search for the original: Inbox (default) or Sent.")

    args = parser.parse_args()

    # A test must never put a message on the wire. Added 2026-08-23 after it
    # happened three times in one hour: checking that a new flag parsed meant
    # running this script, and a mutation check that removed the --dry-run
    # guard sent a real message through Exchange from inside pytest.
    #
    # pytest exports PYTEST_CURRENT_TEST into os.environ, and a subprocess
    # inherits it, so this catches both an in-process import and a spawned CLI.
    # It refuses rather than silently no-opping: a test that expected a send and
    # got a quiet success would be a worse lie than a loud refusal.
    if os.environ.get("PYTEST_CURRENT_TEST") and not args.dry_run:
        print("[REFUSED] send-email.py will not send from inside a test run. "
              "Use --dry-run, or stub the transport.", file=sys.stderr)
        sys.exit(3)

    # Resolve --body-stdin into args.body once, here, so every downstream mode
    # (single, threaded) keeps reading exactly one attribute.
    if args.body_stdin:
        if args.body:
            parser.error("pass either --body or --body-stdin, not both")
        args.body = sys.stdin.read()
        if not args.body:
            parser.error("--body-stdin was given but stdin was empty")

    # --dry-run stops HERE: after every argparse check and after the body has
    # been resolved, and before load_config() reads a credential or connect()
    # opens a session. Placed at the last point where nothing has left the
    # machine, so the whole argument contract is testable and no send happens.
    if args.dry_run:
        body = args.body or ""
        print("[DRY-RUN] nothing was sent.")
        print(f"          to={args.to} cc={args.cc} bcc={args.bcc}")
        print(f"          subject={args.subject!r}")
        print(f"          body: {len(body)} char(s), "
              f"source={'stdin' if args.body_stdin else '--body'}")
        print(f"          attach={args.attach} batch={args.batch}")
        return

    # Batch mode: amortise exchangelib import + Account build over N messages.
    if args.batch:
        batch_path = Path(args.batch)
        if not batch_path.exists():
            print(f"[ERROR] Batch file not found: {batch_path}")
            sys.exit(1)
        try:
            messages = json.loads(batch_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            print(f"[ERROR] Batch file is not valid JSON: {e}")
            sys.exit(1)
        if not isinstance(messages, list):
            print("[ERROR] Batch file must contain a JSON array of message dicts.")
            sys.exit(1)
        if not messages:
            print("[INFO] Batch file is empty; nothing to send.")
            return

        config = load_config()
        account = connect(config)
        results = send_batch(account, messages)

        sent = sum(1 for r in results if r["status"] == "sent")
        failed = sum(1 for r in results if r["status"] != "sent")
        print(f"\n[BATCH] {sent} sent, {failed} failed (total {len(results)})")
        if failed:
            print("[BATCH] Failed messages:")
            for r in results:
                if r["status"] != "sent":
                    print(f"  - to={r['to']}: {r['error']}")
            sys.exit(1)
        return

    # Threaded mode: reply / reply-all / forward an existing message.
    threaded_mode = "reply" if args.reply else ("reply_all" if args.reply_all else ("forward" if args.forward else None))
    if threaded_mode:
        if not args.body:
            parser.error(f"--{threaded_mode.replace('_', '-')} requires --body")
        if not (args.match_id or args.match_from or args.match_subject):
            parser.error("threaded mode requires one of --match-id, --match-from, --match-subject")
        if args.cc or args.bcc:
            # REFUSED, not dropped. `_send_threaded_core` accepts cc/bcc and
            # never passes them to create_reply / create_reply_all /
            # create_forward, so an operator running `--forward --to X --cc Y`
            # believed Y was copied and the mail went out without them: silent
            # loss on an irreversible outbound action. Wiring them onto the
            # saved draft is the other repair, and it belongs to a change that
            # can be exercised against a live Exchange account -- which this one
            # deliberately is not.
            parser.error(
                f"--cc/--bcc are not supported with --{threaded_mode.replace('_', '-')}; "
                f"they were silently discarded before. Send a new message with "
                f"--to/--cc, or reply and copy the address into --to.")
        if threaded_mode == "forward" and not args.to:
            parser.error("--forward requires --to (the recipients to forward to)")

        config = load_config()
        account = connect(config)
        original = find_message(
            account,
            match_id=args.match_id,
            match_from=args.match_from,
            match_subject=args.match_subject,
            folder_name=args.match_folder,
        )
        if original is None:
            crit = ", ".join(filter(None, [
                f"id={args.match_id}" if args.match_id else "",
                f"from~{args.match_from}" if args.match_from else "",
                f"subject~{args.match_subject}" if args.match_subject else "",
            ]))
            print(f"[ERROR] No message found in {args.match_folder} matching: {crit}")
            sys.exit(1)
        print(f"[FOUND] {getattr(original, 'subject', '(no subject)')} "
              f"from {getattr(getattr(original, 'sender', None), 'email_address', '?')}")
        result = _send_threaded_core(
            account, threaded_mode, original, body=args.body,
            to=args.to, cc=args.cc, bcc=args.bcc, attach=args.attach,
            subject=args.subject,
        )
        if result["status"] != "sent":
            print(f"[ERROR] {result['error']}")
            print("         The draft may have been saved but NOT sent. Check Exchange drafts.")
            sys.exit(1)
        return

    # Single-message mode: original CLI contract.
    if not (args.to and args.subject and args.body):
        parser.error("either --batch or all of --to, --subject, --body are required")

    config = load_config()
    account = connect(config)
    send_email(
        account=account,
        to=args.to,
        subject=args.subject,
        body=args.body,
        cc=args.cc,
        bcc=args.bcc,
        attach=args.attach,
    )


if __name__ == "__main__":
    main()
