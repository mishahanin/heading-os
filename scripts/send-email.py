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


# The tags a body actually uses. Kept explicit because the decision this list
# drives is "escape or do not escape", and the cost of the two mistakes is not
# symmetric: escaping real HTML shows the operator their own markup, while
# NOT escaping prose deletes words from what the recipient reads.
_HTML_TAGS = frozenset({
    "a", "b", "blockquote", "body", "br", "code", "div", "em", "font", "h1",
    "h2", "h3", "h4", "h5", "h6", "head", "hr", "html", "i", "img", "li",
    "ol", "p", "pre", "s", "small", "span", "strong", "style", "sub", "sup",
    "table", "tbody", "td", "tfoot", "th", "thead", "tr", "u", "ul",
})
_TAG_NAMES = "|".join(sorted(_HTML_TAGS))
# Two shapes, and an attribute must carry `=`.
#
#   1. a bare tag:            <p>  </p>  <br>  <br/>  <br />
#   2. a tag with attributes: <a href="...">  <div style='...'>
#
# The `=` is what separates `<a href="x">` from the prose `<b and y>`, which is
# syntactically a `<b>` start tag carrying two boolean attributes and cannot be
# told from one by shape alone. Requiring `=` calls it prose. The cost is a
# BOOLEAN attribute (`<td nowrap>`) reading as prose and being escaped;
# measured 2026-08-26 across every .html this workspace sends or templates,
# there are none.
_HTML_TAG_RE = re.compile(
    rf"</?(?:{_TAG_NAMES})\s*/?>"
    rf"|<(?:{_TAG_NAMES})\s+[^>]*=[^>]*>",
    re.IGNORECASE)


def is_html(text):
    """True when the body is HTML, so `_build_full_html` must not escape it.

    Matched against a list of real tag names. The old test was
    `<[a-zA-Z/][^>]*>` - any `<`, a letter, and a later `>` - which fires on
    ordinary prose. Measured 2026-08-26:

        "if x<b and y>z then ship it"   -> classified HTML
        "the range is 3<n and n>7"      -> classified HTML
        "use <Ctrl> to cancel"          -> classified HTML

    Such a body is inserted into the message VERBATIM, and a mail client reads
    `<b and y>` as a `<b>` start tag: the words "and y" are swallowed and never
    reach the recipient. Silent deletion from an outbound message is the worst
    shape this file can produce, and the operator sees a sent mail with no
    error anywhere.

    The wrong direction is the safe one. A body using a tag outside this list
    is escaped and arrives readable as its own source, which the sender can see
    and fix; the old behaviour lost text with no signal at all.
    """
    return bool(_HTML_TAG_RE.search(text))


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
    # Plain text: "Best,\nMisha" at end.
    # `(?:\A|\n)` and not a bare `\n`: `\n` is a literal, never an anchor, so
    # the older pattern could not match a body that BEGINS with the sign-off.
    # The two HTML patterns above have no such constraint, so until 2026-08-25
    # "<p>Best,<br>Misha</p>" was stripped and "Best,\nMisha" was not, which
    # the docstring's "Handles plain text plus the two common HTML shapes"
    # claimed was one behaviour. The loop breaks on the first pattern that
    # changes the string, so a plain-text body gets no second chance.
    re.compile(
        rf"(?:\A|\n)\s*{_SIGNOFF_KEYWORDS}[,.]?\s*\n\s*{_NAME_TOKEN}\s*\n*\Z",
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

class AttachmentError(RuntimeError):
    """One attachment path could not be turned into a FileAttachment.

    Raised instead of exiting. Until 2026-08-25 `build_file_attachments` called
    ``sys.exit(1)`` on a bad path, which contradicted `_send_email_core`'s own
    documented contract ("Does NOT call sys.exit on failure") and killed a whole
    batch: SystemExit derives from BaseException, so neither `except Exception`
    inside the core caught it, and it is raised before either of them anyway.
    A five-message batch with a typo in message three died after two sends with
    no per-message result and no `[BATCH]` summary.
    """


def build_file_attachments(paths):
    """Create non-inline FileAttachment objects from filesystem paths.
    MIME type is guessed from the file extension; falls back to
    application/octet-stream when unknown.

    ``paths`` may be a list of paths or a single path string. The string form is
    accepted because the batch JSON is hand-written and `_normalize_addrs` sets
    the same precedent for to/cc/bcc: without it, ``"attach": "/tmp/f.pdf"``
    iterated the string CHARACTER BY CHARACTER, and the first character of an
    absolute path is ``/``, which exists and is a directory, so the operator got
    an uncaught ``IsADirectoryError`` naming a path they never typed.

    Raises :class:`AttachmentError` on a missing or unreadable path. Callers
    return a per-message failure dict; they never abort the run.
    """
    import mimetypes
    file_attachments = []
    if not paths:
        return file_attachments
    if isinstance(paths, str):
        paths = [paths]
    # `attach` is the one field `send_batch` reads that nothing validated, and
    # the batch JSON is hand-written. A non-iterable (`"attach": 7`, `true`)
    # reached `for raw in paths` and a list holding a non-string
    # (`["/tmp/a.pdf", 5]`) reached `Path(raw)`; both raise TypeError, which is
    # NOT an AttachmentError, so it escaped the per-message handler and aborted
    # the whole batch - AFTER the earlier messages had already gone out. A
    # partial send with a traceback is the worst outcome this file has, because
    # the operator cannot tell from it which messages left. Raise the error the
    # caller already turns into a per-message failure.
    if not isinstance(paths, (list, tuple)):
        raise AttachmentError(
            f"`attach` must be a path or a list of paths, not a "
            f"{type(paths).__name__}")
    bad = [raw for raw in paths if not isinstance(raw, (str, Path))]
    if bad:
        raise AttachmentError(
            f"`attach` holds {len(bad)} entry/entries that are not paths: "
            f"{', '.join(f'{type(b).__name__} {b!r}' for b in bad[:3])}")
    _ensure_exchangelib()
    for raw in paths:
        p = Path(raw)
        if not p.is_file():
            raise AttachmentError(f"attachment not found: {p}")
        mime, _ = mimetypes.guess_type(p.name)
        try:
            content = p.read_bytes()
        except OSError as e:
            raise AttachmentError(f"attachment unreadable: {p} ({e})") from e
        file_attachments.append(FileAttachment(
            name=p.name,
            content=content,
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
        # Normalise line endings first so a CRLF body cannot leave a stray \r
        # sitting in front of the <br> below.
        paragraphs = body.replace("\r\n", "\n").replace("\r", "\n").split("\n\n")
        chunks = []
        for para in paragraphs:
            if not para.strip():
                continue
            # Escape FIRST, then turn the SURVIVING single newlines into <br>.
            # Splitting on blank lines alone only ever produced paragraph
            # breaks. HTML collapses a bare newline inside a <p> to one space,
            # so until 2026-08-25 a plain-text body written as separate lines
            # (an address block, a numbered list, a signature the operator
            # typed) arrived at the recipient as one run-on line. The wrapper
            # carries no `white-space` rule, so nothing else preserved them.
            chunks.append("<p>" + html.escape(para).replace("\n", "<br>") + "</p>")
        body_html = "".join(chunks)
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

    Returns ``{"to": [...], "status": "sent"|"failed", "stage": str,
    "error": str|None}``.
    Does NOT call ``sys.exit`` on failure - callers decide how to handle.

    ``stage`` names how far the message got, and exists so a caller can tell the
    operator something true about the draft and the wire. The four failing
    values are ``attachments`` (nothing was built, nothing saved),
    ``save_draft`` (nothing was SENT; whether a draft exists is unknown, because
    this stage is stamped on a read timeout as well as on a refusal, and a
    timeout answers the reply rather than the write), ``attach`` (a draft EXISTS
    and was not sent), and ``send`` (a draft exists AND the request may have
    reached the server). Until 2026-08-25 `send_email` printed "The draft was saved but NOT
    sent" over all four, which on the ``send`` stage flatly contradicted the
    `_UNSURE_NOTE` printed one line above it and told the operator to resend a
    message that may already be out.

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
    try:
        file_attachments = build_file_attachments(attach)
    except AttachmentError as e:
        return {"to": list(to), "status": "failed", "stage": "attachments",
                "error": f"{e}; nothing was saved and nothing was sent"}

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
        return {"to": list(to), "status": "failed", "stage": "save_draft",
                "error": f"save draft failed: {e}"}

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
        return {"to": list(to), "status": "failed", "stage": "attach",
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
            return {"to": list(to), "status": "sent", "stage": "sent", "error": None}
        except Exception as e:
            last_error = e
            if not _is_safe_to_resend(e):
                print(f"[ERROR] Send failed: {e}")
                print(f"[ERROR] {_UNSURE_NOTE}")
                return {
                    "to": list(to),
                    "status": "failed",
                    "stage": "send",
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
        "stage": "send",
        "error": f"send failed after 3 attempts: {last_error}",
    }


# The only two folders this script can search, and every spelling accepted for
# each. An unlisted name is refused, never mapped to a default: see
# `folder_key`.
_FOLDER_ALIASES = {
    "inbox": "inbox",
    "sent": "sent",
    "sent items": "sent",
    "sentitems": "sent",
}


def folder_key(folder_name):
    """Canonical folder key for a --match-folder value, or raise ValueError.

    Refusing is the point. Until 2026-08-25 an unrecognised name fell through
    to the Inbox, so `--match-folder Drafts` (or a typo, `Snet`) searched the
    Inbox, replied into whatever thread it found there, and then reported "No
    message found in Drafts" on a miss - naming the folder it never opened.
    Both halves had to be fixed together: fixing only the fall-through would
    have left that message correct by accident.
    """
    name = (folder_name or "Inbox").strip().lower()
    if name not in _FOLDER_ALIASES:
        valid = ", ".join(sorted(set(_FOLDER_ALIASES.values())))
        raise ValueError(f"unknown folder {folder_name!r}; valid folders are: {valid}")
    return _FOLDER_ALIASES[name]


def _resolve_folder(account, folder_name):
    """Map a folder name to the account folder. Inbox (default) or Sent.

    Raises ValueError on any other name.
    """
    return account.sent if folder_key(folder_name) == "sent" else account.inbox


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
        except Exception as first_error:
            # Second chance ONLY when a different folder was searched. The old
            # comment called this "a cross-folder lookup by id via the account
            # root", and it was neither: `account.root` is not referenced
            # anywhere in this file, and under the default --match-folder Inbox
            # `folder` IS `account.inbox`, so this repeated the identical query
            # that had just failed and called the repeat a fallback.
            if folder is account.inbox:
                print(f"[WARN] Item id lookup in Inbox failed: {first_error}")
                return None
            try:
                return account.inbox.get(id=match_id)
            except Exception as second_error:
                print(f"[WARN] Item id lookup failed in {folder_name} "
                      f"({first_error}) and in Inbox ({second_error})")
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


def _reply_target(original):
    """The mailbox a reply is actually ADDRESSED to: `author`, then `sender`.

    EWS carries two addresses and they are not the same thing. `author` is the
    From header; `sender` is `message:Sender`, the mailbox that submitted the
    item - a delegate or an assistant sending on someone's behalf. exchangelib
    addresses a reply to `self.author` (`Message.create_reply`, and
    `create_reply_all` adds `self.author` to the recipient set), while this
    file read `sender` for the CRM auto-log and for the line printed back to
    the operator.

    For an ordinary message the two agree and nothing showed. For a
    delegate-sent one they differ, and the reply went to the author while the
    CRM recorded a conversation with the delegate - a wrong fact written into
    the relationship record, which is worse than a missing one because nothing
    later contradicts it. Read 2026-08-26 out of exchangelib's own source.
    """
    for attr in ("author", "sender"):
        mailbox = getattr(original, attr, None)
        if mailbox and getattr(mailbox, "email_address", None):
            return mailbox
    return None


def _replyall_recipients(account, original):
    """All addresses a reply-all touches (sender + To + CC), minus self.
    Used only to drive the CRM auto-log; exchangelib builds the real envelope."""
    emails = set()
    target = _reply_target(original)
    if target is not None:
        emails.add(target.email_address)
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

    Returns {"to": [...], "status": "sent"|"failed", "stage": str,
    "error": str|None}. ``stage`` carries the same four failing values
    `_send_email_core` documents, and for the same reason: main's threaded
    branch has to tell the operator whether a draft exists and whether the
    request reached the server, and an error string cannot say.
    """
    _ensure_exchangelib()
    if signature is None:
        signature = load_signature()
    try:
        file_attachments = build_file_attachments(attach)
    except AttachmentError as e:
        return {"to": to or [], "status": "failed", "stage": "attachments",
                "error": f"{e}; nothing was saved and nothing was sent"}
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
                return {"to": [], "status": "failed", "stage": "attachments",
                        "error": "forward requires --to (recipients to forward to)"}
            draft_ref = original.create_forward(derived_subject, HTMLBody(full_html),
                                                to_recipients=to_mb)
        else:
            return {"to": to or [], "status": "failed", "stage": "attachments",
                    "error": f"unknown mode: {mode}"}
        save_result = draft_ref.save(account.drafts)
    except Exception as e:
        return {"to": to or [], "status": "failed", "stage": "save_draft",
                "error": f"create/save {mode} failed: {e}"}

    # Re-fetch the persisted draft so we can attach + send.
    try:
        draft = account.drafts.get(id=save_result.id, changekey=save_result.changekey)
    except Exception as e:
        return {"to": to or [], "status": "failed", "stage": "attach",
                "error": f"fetch saved draft failed: {e}"}

    fresh_sig_attachments = build_signature_attachments()
    try:
        for att in fresh_sig_attachments:
            draft.attach(att)
        for att in file_attachments:
            draft.attach(att)
    except Exception as e:
        return {"to": to or [], "status": "failed", "stage": "attach",
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
            else:
                # reply with no explicit --to: exchangelib addresses it to the
                # original's AUTHOR, not its sender. See `_reply_target`.
                target = _reply_target(original)
                actual_to = [target.email_address] if target is not None else []
            label = {"reply": "Reply", "reply_all": "Reply-all", "forward": "Forward"}[mode]
            print(f"[OK] {label} sent — {', '.join(actual_to) if actual_to else '(envelope built by Exchange)'}")
            print(f"     Subject: {derived_subject}")
            print(f"     Signature: embedded with {len(fresh_sig_attachments)} inline image(s)")
            if file_attachments:
                names = ", ".join(a.name for a in file_attachments)
                print(f"     Attachments: {len(file_attachments)} file(s) - {names}")
            _autolog_to(actual_to, derived_subject, body)
            return {"to": actual_to, "status": "sent", "stage": "sent", "error": None}
        except Exception as e:
            last_error = e
            if not _is_safe_to_resend(e):
                print(f"[ERROR] {mode} send failed: {e}")
                print(f"[ERROR] {_UNSURE_NOTE}")
                return {"to": to or [], "status": "failed", "stage": "send",
                        "error": f"{mode} send failed ({e}). {_UNSURE_NOTE}"}
            if attempt < 3:
                import time
                wait = 2 ** attempt
                print(f"[WARN] Send attempt {attempt}/3 failed: {e}. Retrying in {wait}s...")
                time.sleep(wait)
    return {"to": to or [], "status": "failed", "stage": "send",
            "error": f"{mode} send failed after 3 attempts: {last_error}"}


# What the operator should do next, per failing stage. Keyed by the ``stage``
# `_send_email_core` returns, because the caller cannot know from an error
# STRING whether a draft exists or whether the request reached the server.
#
# The ``send`` line is the one this table was written for. Until 2026-08-25 all
# four stages printed "The draft was saved but NOT sent. Check Exchange drafts
# folder.", so on a ReadTimeout the operator's LAST line of output contradicted
# the `_UNSURE_NOTE` printed immediately above it: the note says the message may
# already be out, and the next line told them it was not. Acting on the last
# line sends a second copy of an irreversible message, which is precisely the
# duplicate the `_SAFE_TO_RESEND` design exists to prevent.
_STAGE_GUIDANCE = {
    "attachments": "         Nothing was saved and nothing was sent. Fix the path and run it again.",
    # NOT "no draft was saved". That stage is stamped on every exception out of
    # `msg.save()`, and a read timeout on the CreateItem call establishes only
    # that the ANSWER did not come back - the item may well exist on the server.
    # Telling the operator nothing was created is the same over-claim
    # `.claude/rules/scope-claims.md` names: a sentence asserting more than the
    # method measured. Nothing was SENT either way, which is the part that is
    # actually established, so say that and point at Drafts.
    "save_draft": ("         Nothing was sent. Whether a draft exists on the "
                   "server is UNKNOWN (a timeout answers the reply, not the "
                   "write): check Drafts before running it again."),
    "attach": "         The draft was saved but NOT sent. Check Exchange drafts folder.",
    "send": "         Check Sent Items BEFORE running this again - see the note above.",
}
# An unstamped result predates the stage key or came from somewhere new. Say
# that the state is unknown rather than guessing one of the four.
_STAGE_GUIDANCE_UNKNOWN = (
    "         The send failed at an unrecorded stage, so whether a draft exists "
    "and whether the message left is UNKNOWN. Check Sent Items and Drafts.")


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
        print(_STAGE_GUIDANCE.get(result.get("stage"), _STAGE_GUIDANCE_UNKNOWN))
        sys.exit(1)


def _require_str(value, field):
    """Return ``value`` when it is a string; raise TypeError naming the field.

    The batch JSON is hand-written, and a bare `"body": 123` used to travel all
    the way to `re.sub` inside `_build_full_html` before raising, outside every
    try in the send path.
    """
    if not isinstance(value, str):
        raise TypeError(f"'{field}' must be a string, got {type(value).__name__}")
    return value


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
    # This call catches an asset that EXISTS and cannot be read (a permissions
    # error, or an unpulled Git-LFS pointer) before message 1, because
    # `read_bytes()` raises OSError straight out of here. That is the whole of
    # what it establishes, and the return value is deliberately discarded: the
    # FileAttachment objects cannot be reused, since each binds to its Message
    # on `.attach()`.
    #
    # It does NOT catch a MISSING asset. Those two branches only print a WARN
    # and fall through, and `_send_email_core` calls this again per message, so
    # a missing logo warns N+1 times and every message still sends without it.
    # The comment here claimed the opposite until 2026-08-25 ("a missing asset
    # fails once rather than N times"), which is the one case it does not cover.
    build_signature_attachments()

    results = []
    for idx, m in enumerate(messages, start=1):
        # Everything a malformed message dict can raise belongs INSIDE this
        # try. Until 2026-08-25 it held only to/subject/body and caught only
        # (KeyError, ValueError), so four shapes aborted the whole batch with a
        # traceback and no per-message result: a non-dict entry (`m["to"]`
        # raises TypeError), a bad cc/bcc (normalised two lines BELOW the try),
        # a null `to` (normalises to None, then TypeError deep inside the core),
        # and a non-string subject or body (passes this try untouched, then
        # TypeError out of re.sub inside `_build_full_html`). The batch contract
        # is one status dict per message; a bad message must cost that message.
        try:
            to = _normalize_addrs(m["to"])
            if not to:
                raise ValueError("'to' is empty")
            subject = _require_str(m["subject"], "subject")
            body = _require_str(m["body"], "body")
            cc = _normalize_addrs(m.get("cc"))
            bcc = _normalize_addrs(m.get("bcc"))
            attach = m.get("attach")
        except (KeyError, TypeError, ValueError) as e:
            print(f"[ERROR] Message #{idx} malformed: {e}")
            results.append({"to": [], "status": "failed", "stage": "malformed",
                            "error": f"malformed: {e}"})
            continue
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

    # Which mode this invocation selects. Resolved BEFORE the --dry-run guard
    # because the checks below have to run under it.
    threaded_mode = "reply" if args.reply else ("reply_all" if args.reply_all else ("forward" if args.forward else None))

    # Every argument-contract check lives ABOVE the --dry-run guard, and this is
    # the reason the flag exists. Until 2026-08-25 they all sat below it, so
    # `--dry-run --reply --body x` with no --match-* argument printed the
    # DRY-RUN block and exited 0, while the same command without --dry-run
    # exited 2. The flag's own help text says it was added because "checking
    # that a flag parses meant sending a real message" - and in the three modes
    # that matter it still could not check that. The --cc refusal below is the
    # sharpest case: it exists because cc/bcc were once silently DISCARDED on an
    # irreversible send, and it was the one refusal a dry run could not see.
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
        try:
            folder_key(args.match_folder)
        except ValueError as e:
            parser.error(str(e))
    elif not args.batch and not (args.to and args.subject and args.body):
        parser.error("either --batch or all of --to, --subject, --body are required")

    # --dry-run stops HERE: after every argparse check above, after the mode
    # checks, and after the body has been resolved; before load_config() reads a
    # credential or connect() opens a session. It is the last point at which
    # nothing has left the machine.
    #
    # The batch file's own checks (exists, parses as JSON, is an array) stay
    # BELOW: they read the filesystem rather than the argument vector, and this
    # guard's promise is about the argument contract only.
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
    # Every check for this mode ran above the --dry-run guard; nothing is
    # re-checked here, or the two copies would drift.
    if threaded_mode:
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
            # `folder_key` and not `args.match_folder`: this line reports what
            # was SEARCHED. An unknown name is now refused above, so the two
            # can no longer diverge, and this says so from the same source.
            print(f"[ERROR] No message found in {folder_key(args.match_folder)} "
                  f"matching: {crit}")
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
            print(_STAGE_GUIDANCE.get(result.get("stage"), _STAGE_GUIDANCE_UNKNOWN))
            sys.exit(1)
        return

    # Single-message mode: original CLI contract. Its required-argument check
    # also ran above the --dry-run guard.
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
