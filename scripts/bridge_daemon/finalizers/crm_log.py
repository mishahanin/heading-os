"""CRM-log action for the bridge Inbox.

Logs an email conversation as an interaction on its linked CRM contact:
appends an entry to crm/contacts/{slug}.md's Interaction Log and bumps
last_touch, reusing the helpers in scripts/utils/crm_autolog. Invoked
from POST /inbox/crm-log.

The conversation -> contact link comes from email-intelligence.py's
crm_context.contact_slug in _latest-fetch.json. read_inbox flags rows
already logged (via _crm-logged.jsonl) so the dashboard button
disables - clicking twice must not write two entries.
"""
import json
import re
import threading
from datetime import date, datetime
from pathlib import Path

from scripts.utils.workspace import get_default_tz

from scripts.bridge_daemon.sources.inbox import (
    CRM_LOGGED_FILE,
    LATEST_FETCH_FILE,
    mark_crm_logged,
    read_crm_logged,
)
from scripts.utils.crm_autolog import (
    append_log_entry,
    atomic_write,
    bump_last_touch_in_text,
    data_is_readonly,
)
from scripts.utils.paths import get_data_root

# Contact slugs are kebab-case filenames under crm/contacts/. The
# allowlist rejects path traversal and any shape that could escape the
# contacts directory.
_SLUG_RE = re.compile(r"^[a-z0-9-]{1,80}$")


_CONTACT_WRITE_LOCK = threading.Lock()

_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _interaction_date(raw_dt: str) -> str:
    """The `YYYY-MM-DD` from an ISO timestamp, or today when it is not one.

    The comment promised a fallback when `latest_datetime` was "missing or
    malformed", but the code only measured LENGTH: any string of ten or more
    characters was sliced verbatim, so `not-a-date-xx` was written into a CRM
    contact file as a plausible-looking interaction date.
    """
    head = (raw_dt or "")[:10]
    # Shape first, then a real calendar check. `date.fromisoformat` alone is
    # too permissive on 3.11: it accepts the compact `20260824` form, and a
    # producer emitting that would write a date this file never formats.
    if not _ISO_DATE_RE.match(head):
        return datetime.now(get_default_tz()).strftime("%Y-%m-%d")
    try:
        date(int(head[0:4]), int(head[5:7]), int(head[8:10]))
    except ValueError:
        return datetime.now(get_default_tz()).strftime("%Y-%m-%d")
    return head


def log_to_crm(conv_id: str, data_root: "Path | None" = None) -> dict:
    """Append an interaction-log entry for `conv_id` to its CRM contact.

    There is no `workspace_root` parameter, deliberately. One used to sit first
    in the signature and was never read in the body, while `app.py` passed the
    DATA root into it -- so the interface advertised a need it did not have, and
    any later edit that reached for `workspace_root` (to find a script, say)
    would have silently run against the data overlay. That is exactly how the
    mark-read finalizer broke.

    Returns {ok: True, slug, date} on success, or {ok: False, error}
    when the conversation is missing, has no linked contact, was already
    logged, or the CRM write fails.

    HEADING OS engine/data split: the fetch file, the crm-logged dedupe log,
    and the crm/contacts/ file are all DATA, so they resolve under
    ``data_root`` (falls back to the ``get_data_root()`` seam when not supplied, NOT to ``workspace_root``).
    """
    if data_root is None:
        data_root = get_data_root()
    if not isinstance(conv_id, str) or not conv_id.strip():
        return {"ok": False, "error": "conv_id is required"}
    # Trimmed, as in inbox.mark_dismissed: the guard above tests the STRIPPED
    # value, so writing the raw one puts a key in the dedupe log that no read
    # can match.
    conv_id = conv_id.strip()
    if len(conv_id) > 500:
        return {"ok": False, "error": "conv_id too long"}

    # Idempotency, fast path. This read is an optimisation only; the check that
    # decides is the one under the lock further down. See the comment there.
    if conv_id in read_crm_logged(data_root):
        return {"ok": False, "error": "conversation already logged to CRM"}

    fetch_path = data_root / LATEST_FETCH_FILE
    if not fetch_path.exists():
        return {"ok": False, "error": "no latest fetch on disk (run /email-intel first)"}
    try:
        data = json.loads(fetch_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError) as e:
        # `UnicodeDecodeError` is a `ValueError` and it comes out of
        # `read_text`, ahead of `json.loads`, so the two types already named
        # here never reached it. An undecodable fetch file raised through this
        # finalizer and 500'd the endpoint rather than returning the "fetch
        # unreadable" answer the caller is written to display.
        return {"ok": False, "error": f"fetch unreadable: {e}"}
    conversations = data.get("conversations", [])
    if not isinstance(conversations, list):
        return {"ok": False, "error": "unexpected fetch schema"}
    conv = next(
        (c for c in conversations if isinstance(c, dict) and c.get("id") == conv_id),
        None,
    )
    if conv is None:
        return {"ok": False, "error": "conversation not in latest fetch"}

    crm = conv.get("crm_context") or {}
    slug = (crm.get("contact_slug") or "").strip()
    if not slug:
        return {"ok": False, "error": "no CRM contact linked to this conversation"}
    if not _SLUG_RE.match(slug):
        return {"ok": False, "error": f"invalid contact slug: {slug!r}"}

    contact_file = data_root / "crm" / "contacts" / f"{slug}.md"
    if not contact_file.exists():
        return {"ok": False, "error": f"CRM contact file not found: {slug}"}

    topic = conv.get("topic") or "(no subject)"
    # latest_datetime is ISO; take the date portion as the interaction
    # date, falling back to today if it is missing or malformed.
    raw_dt = conv.get("latest_datetime") or ""
    log_date = _interaction_date(raw_dt)

    # THE SECOND DOOR INTO THE SAME ROOM. `HEADING_OS_DATA_READONLY` was added to
    # `crm_autolog.log_outbound` and `bump_inbound` on 2026-09-02, after a daemon
    # host's send path rewrote five contact cards and wedged that host's
    # pull-only mirror for three and a half days. This finalizer reaches the same
    # contact file WITHOUT going through either of them: it calls
    # `bump_last_touch_in_text` and `append_log_entry` directly and does its own
    # `atomic_write`. So the guard there does not cover it, and a dashboard
    # crm-log click on a mirror host would dirty the tree exactly as before.
    #
    # Refused BEFORE the lock, so a host that must not write never contends for
    # it, and the answer carries the dashboard's normal error shape rather than
    # a silent no-op that looks like a successful click.
    if data_is_readonly():
        return {"ok": False,
                "error": f"HEADING_OS_DATA_READONLY is set: this host mirrors "
                         f"the data repo and must not write to it, so {slug} "
                         f"was not logged"}

    # Locked: this is a read-modify-write on a shared contact file with no
    # atomicity of its own. Two crm-log clicks for the same contact both read
    # the pre-write text and the second write dropped the first entry. Every
    # other mutation path in the daemon holds a lock.
    #
    # Check-and-mark is INSIDE the same critical section, since 2026-08-24.
    # Before that the dedupe read sat above the lock and the marker write below
    # it, so a double click on ONE conversation passed the check twice, queued
    # here, and wrote the interaction twice -- the marker only ever recorded
    # what had already happened. A guard that both racers clear guards nothing;
    # the check has to be in the same lock as the write it authorises.
    with _CONTACT_WRITE_LOCK:
        if conv_id in read_crm_logged(data_root):
            return {"ok": False, "error": "conversation already logged to CRM"}
        try:
            text = contact_file.read_text(encoding="utf-8")
            text = bump_last_touch_in_text(text, log_date)
            text = append_log_entry(
                text, log_date, "Email", topic,
                "Logged from the Inbox dashboard.",
            )
            atomic_write(contact_file, text)
        except (OSError, UnicodeDecodeError) as e:
            # A contact record is hand-edited (Zettlr, an editor with a
            # different default encoding), so a byte that is not UTF-8 is an
            # ordinary accident here. `UnicodeDecodeError` is a `ValueError`,
            # so `read_text` raised past this handler, out of the finalizer,
            # and past the `_CONTACT_WRITE_LOCK`'s caller, instead of the
            # "CRM write failed" answer the dashboard knows how to show.
            return {"ok": False, "error": f"CRM write failed: {e}"}
        ok, err = mark_crm_logged(data_root, conv_id, slug)
    if not ok:
        # The CRM entry IS written; only the dedupe record failed. Report
        # success but flag the gap so a retry could double-log.
        return {"ok": True, "slug": slug, "date": log_date,
                "warning": f"dedupe log not updated: {err}"}
    return {"ok": True, "slug": slug, "date": log_date}


# CRM_LOGGED_FILE is re-exported for callers that want the log path
# without reaching into the inbox module directly.
__all__ = ["log_to_crm", "CRM_LOGGED_FILE"]
