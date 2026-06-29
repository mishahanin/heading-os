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
from datetime import datetime
from pathlib import Path

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
)
from scripts.utils.paths import get_data_root

# Contact slugs are kebab-case filenames under crm/contacts/. The
# allowlist rejects path traversal and any shape that could escape the
# contacts directory.
_SLUG_RE = re.compile(r"^[a-z0-9-]{1,80}$")


def log_to_crm(workspace_root: Path, conv_id: str, data_root: "Path | None" = None) -> dict:
    """Append an interaction-log entry for `conv_id` to its CRM contact.

    Returns {ok: True, slug, date} on success, or {ok: False, error}
    when the conversation is missing, has no linked contact, was already
    logged, or the CRM write fails.

    HEADING OS engine/data split: the fetch file, the crm-logged dedupe log,
    and the crm/contacts/ file are all DATA, so they resolve under
    ``data_root`` (falls back to ``workspace_root`` when not supplied).
    """
    if data_root is None:
        data_root = get_data_root()
    if not isinstance(conv_id, str) or not conv_id.strip():
        return {"ok": False, "error": "conv_id is required"}
    if len(conv_id) > 500:
        return {"ok": False, "error": "conv_id too long"}

    # Idempotency: a conversation logged once must not be logged again.
    if conv_id in read_crm_logged(data_root):
        return {"ok": False, "error": "conversation already logged to CRM"}

    fetch_path = data_root / LATEST_FETCH_FILE
    if not fetch_path.exists():
        return {"ok": False, "error": "no latest fetch on disk (run /email-intel first)"}
    try:
        data = json.loads(fetch_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
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
    log_date = raw_dt[:10] if len(raw_dt) >= 10 else datetime.now().strftime("%Y-%m-%d")

    try:
        text = contact_file.read_text(encoding="utf-8")
        text = bump_last_touch_in_text(text, log_date)
        text = append_log_entry(
            text, log_date, "Email", topic,
            "Logged from the Inbox dashboard.",
        )
        atomic_write(contact_file, text)
    except OSError as e:
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
