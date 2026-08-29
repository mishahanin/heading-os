"""crm_autolog.py -- Shared library for auto-logging Exchange interactions to CRM.

Resolves a recipient or sender email to a relationship record via strict match
against the address book (canonical_email + other_emails union). Appends a 1-line
interaction log entry on outbound and bumps `last_touch`. For inbound, only bumps
`last_touch` silently (log entry creation stays under /email-intel approval flow).

All writes are atomic: tmp + os.replace pattern (per the global no-non-atomic-state-writes rule).

Cached email index: the (email -> [slug]) map is built lazily and invalidated on
address-book directory mtime change. Lookup is O(1) after first call.

Audit log: every log_outbound / bump_inbound / multi-match conflict appends a
JSONL entry to .sync/logs/crm-autolog-{date}.jsonl for observability.
"""

import html as _html
import json as _json
import os
import re
import stat
import sys
from datetime import datetime, timezone

from scripts.utils.workspace import DataRootError, get_default_tz
from pathlib import Path
from typing import Optional

# Ensure scripts.utils.markdown is importable when crm_autolog is invoked as a
# library from elsewhere in the workspace.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from scripts.utils.markdown import parse_frontmatter, set_frontmatter_field


def _crm_root(workspace_root: Optional[Path] = None) -> Path:
    if workspace_root is not None:
        return Path(workspace_root)
    from scripts.utils.workspace import get_workspace_root
    return get_workspace_root()


def _address_book_dir(workspace_root: Optional[Path] = None) -> Path:
    """Resolve the address book, through the data-root seam when no root is given.

    `_crm_root(None)` is `get_workspace_root()` - the ENGINE clone - and the CRM
    tree lives in the private DATA overlay, so every production call resolved to
    `<engine>/crm/address-book`, a directory that does not exist. `resolve_recipient`
    returns None for a missing directory exactly as it does for an unknown address,
    so 19 290 auto-log calls between 2026-06-14 and 2026-08-25 were recorded as
    `"matched": false` - "no such contact" - while the real reason was that nothing
    had been looked at. Not one of them matched.

    `scripts/utils/crm.py` already carries the correct resolution, so this delegates
    rather than restating it: a second copy of a seam is the copy that stops being
    fixed. An explicit `workspace_root` still means "this exact tree", which is what
    the test fixtures pass.
    """
    if workspace_root is None:
        from scripts.utils.crm import _address_book_dir as _seam
        return _seam()
    ws = Path(workspace_root)
    # CEO workspace: crm/address-book/. Exec: corporate/crm/address-book/ (read-only).
    candidates = [
        ws / "crm" / "address-book",
        ws / "corporate" / "crm" / "address-book",
    ]
    for c in candidates:
        if c.exists():
            return c
    return candidates[0]


def _contacts_dir(workspace_root: Optional[Path] = None) -> Path:
    """Resolve the contacts tree; see `_address_book_dir` for the seam it shares."""
    if workspace_root is None:
        from scripts.utils.workspace import get_crm_contacts_dir
        return get_crm_contacts_dir()
    ws = Path(workspace_root)
    candidates = [
        ws / "crm" / "contacts",
        ws / "personal" / "crm" / "contacts",
    ]
    for c in candidates:
        if c.exists():
            return c
    return candidates[0]


def _logs_dir(workspace_root: Optional[Path] = None) -> Path:
    """Where the audit trail is written; the DATA overlay when no root is given.

    Every entry carries an e-mail address, so the trail is private data and
    belongs in the private repository, not in the clone of the PUBLIC engine.
    `_crm_root(None)` put 71 days of it under `<engine>/.sync/logs/`. Nothing
    leaked - `.sync/` is gitignored on both sides - but the routing rule is about
    where a record LIVES, not only about what git happens to carry, and the next
    person to un-ignore that directory would have shipped it.

    `.sync/` is gitignored in the data repository too, so the move changes the
    repository, not the backup status: the trail is still local-only.

    On a clone with no private overlay the data root falls back to the bundled
    `<engine>/examples`, which is INSIDE the public engine clone, so resolving
    through `get_data_root()` put the trail back where it started and the
    `mkdir` below created the directory as a side effect of merely asking for
    the path. `require_writable_data_root()` refuses instead: no overlay means
    no place a private audit trail may be written, and `_audit_log` reports the
    refusal on stderr rather than filing e-mail addresses into the public repo.
    An explicit `workspace_root` still means that exact tree.
    """
    if workspace_root is None:
        from scripts.utils.workspace import require_writable_data_root
        base = require_writable_data_root()
    else:
        base = Path(workspace_root)
    d = base / ".sync" / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _audit_log(event: dict, workspace_root: Optional[Path] = None) -> None:
    """Append a JSONL audit entry. Best-effort; never raises into the caller."""
    try:
        today = datetime.now(get_default_tz()).strftime("%Y-%m-%d")
        log_path = _logs_dir(workspace_root) / f"crm-autolog-{today}.jsonl"
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(_json.dumps({**event, "ts": datetime.now(timezone.utc).isoformat()}) + "\n")
    except DataRootError as e:
        # No private overlay on this clone, so there is nowhere the trail may
        # live. Say so on stderr; the alternative is writing e-mail addresses
        # into the public engine tree.
        print(f"[crm_autolog] audit entry not written, no private data root: {e}",
              file=sys.stderr)
    except OSError as e:
        # Best-effort: never raise into the caller. Surface to stderr so
        # daemon logs capture the failure for operator review.
        print(f"[crm_autolog] audit log failed: {e}", file=sys.stderr)


# Process-level email-index cache. Keyed by (ab_dir, mtime-signature).
# Rebuilt on directory mtime change; O(1) lookup after first call.
_EMAIL_INDEX_CACHE: dict = {}


def _index_signature(ab_dir: Path) -> tuple:
    """Cheap mtime-based signature of the address-book directory contents."""
    try:
        return tuple(sorted(
            (p.name, p.stat().st_mtime) for p in ab_dir.glob("*.md")
        ))
    except OSError:
        return ()


def _build_email_index(ab_dir: Path) -> dict:
    """Return {email_lowercase: [slug, ...]} for every entity in the address book.

    Cached; rebuilt only when mtime signature changes.
    """
    sig = _index_signature(ab_dir)
    cached = _EMAIL_INDEX_CACHE.get(str(ab_dir))
    if cached and cached[0] == sig:
        return cached[1]

    idx: dict = {}
    for entity_file in ab_dir.glob("*.md"):
        try:
            text = entity_file.read_text(encoding="utf-8")
            fm, _body = parse_frontmatter(text)
        except OSError:
            continue
        emails = set()
        ce = (fm.get("canonical_email") or "").strip().lower()
        if ce:
            emails.add(ce)
        other = fm.get("other_emails", [])
        if isinstance(other, list):
            for e in other:
                if e:
                    emails.add(str(e).strip().lower())
        elif isinstance(other, str) and other.strip():
            emails.add(other.strip().lower())
        for e in emails:
            idx.setdefault(e, []).append(entity_file.stem)

    _EMAIL_INDEX_CACHE[str(ab_dir)] = (sig, idx)
    return idx


def resolve_recipient(email: str, workspace_root: Optional[Path] = None) -> Optional[Path]:
    """Resolve an email to the matching relationship record file path.

    Strict match: lowercase exact comparison against the address book's
    canonical_email + other_emails union. Returns None on no match OR
    on ambiguous multi-match (two entities claim the same email).

    A multi-match is recorded as a `"kind": "conflict"` entry in the ordinary
    daily audit log, `.sync/logs/crm-autolog-{date}.jsonl`. This used to name a
    separate `crm-autolog-conflicts-{date}.jsonl`, which nothing writes and
    nothing reads: an operator following the docstring would have looked for a
    file that has never existed, found none, and concluded there were no
    conflicts. `_audit_log` has only ever had one destination.
    """
    if not email:
        return None
    target = email.strip().lower()
    if not target:
        return None

    ab_dir = _address_book_dir(workspace_root)
    if not ab_dir.exists():
        return None

    idx = _build_email_index(ab_dir)
    matched_slugs = idx.get(target, [])

    if len(matched_slugs) > 1:
        # Multi-match conflict: log + refuse to write. Surfaces in crm-health
        # radar so CEO can deduplicate the address book.
        _audit_log({
            "kind": "conflict",
            "email": target,
            "matched_slugs": matched_slugs,
        }, workspace_root=workspace_root)
        return None

    if len(matched_slugs) != 1:
        return None

    slug = matched_slugs[0]
    rel_path = _contacts_dir(workspace_root) / f"{slug}.md"
    return rel_path if rel_path.exists() else None


def atomic_write(path: Path, content: str) -> None:
    """Atomic write via tmp + os.replace, per global security rule.

    Clears the Windows read-only bit before the rename so corporate-sync-
    marked files (on exec workspaces) can be overwritten. See
    reference_windows_readonly_unlink memory.
    """
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    if path.exists():
        try:
            path.chmod(stat.S_IWRITE | stat.S_IREAD)
        except OSError:
            pass
    os.replace(tmp, path)


def plain_snippet(body: str, limit: int = 200) -> str:
    """Convert an HTML-or-plain email body to a single-line plain-text snippet.

    Strips tags, unescapes entities, and collapses whitespace so the CRM
    interaction log never shows raw markup. send-email.py passes the raw HTML
    body (one line, no newlines), so the previous `split("\\n")[0]` approach
    captured raw `<p>` tags. Safe on plain text (nothing to strip).
    """
    if not body:
        return ""
    # Block-level close tags and <br> become spaces so words don't run together.
    s = re.sub(r"(?is)<\s*br\s*/?\s*>", " ", body)
    s = re.sub(r"(?is)</\s*(p|div|li|tr|h[1-6]|ul|ol|table)\s*>", " ", s)
    s = re.sub(r"(?is)<[^>]+>", "", s)  # strip all remaining tags
    s = re.sub(r"<[^>]*$", "", s)  # drop a dangling unclosed tag (truncated body)
    s = _html.unescape(s)
    s = " ".join(s.split())  # collapse whitespace runs
    return s[:limit]


def bump_last_touch_in_text(text: str, new_date: str) -> str:
    """Set `last_touch: YYYY-MM-DD` in YAML frontmatter, or leave the text alone.

    Both halves now run inside the block, through
    `scripts.utils.markdown.set_frontmatter_field`. The INSERT half was scoped
    correctly and the docstring said so; the REPLACE half was not, and the
    docstring's claim that the write "lands inside the frontmatter block or
    nowhere" was false for it.

    MEASURED 2026-08-29. A card with no `last_touch` in frontmatter and a body
    line beginning `last_touch:` -- a quoted record in an interaction-log entry
    is enough -- took the replace branch, rewrote the BODY line and returned. The
    frontmatter field the whole auto-log exists to maintain stayed absent, the
    audit entry recorded `matched: true`, and `calculate_health` read the contact
    as never touched, permanently. A document with no frontmatter at all had the
    key written into its prose, which is the exact outcome the old docstring
    claimed to have fixed.
    """
    return set_frontmatter_field(text, "last_touch", new_date)


def append_log_entry(text: str, date: str, kind: str, subject: str, body: str) -> str:
    """Insert a new log entry at the top of the Interaction Log section."""
    entry = f"\n### {date} | {kind} | {subject}\n{body.strip()}\n"
    marker = "## Interaction Log"
    if marker not in text:
        # Append the section if missing
        return text.rstrip() + f"\n\n{marker}\n{entry}"
    head, _, tail = text.partition(marker)
    return head + marker + entry + tail


def log_outbound(
    recipient_email: str,
    subject: str,
    body_excerpt: str,
    date: Optional[str] = None,
    workspace_root: Optional[Path] = None,
) -> bool:
    """Log an outbound email to the matching relationship record.

    Returns True if a log entry was written, False if no match (or conflict).
    Writes a JSONL audit entry to .sync/logs/crm-autolog-{date}.jsonl on every
    invocation regardless of match outcome.
    """
    target = (recipient_email or "").strip().lower()
    rel_path = resolve_recipient(recipient_email, workspace_root=workspace_root)
    if rel_path is None:
        _audit_log({
            "kind": "outbound",
            "email": target,
            "matched": False,
        }, workspace_root=workspace_root)
        return False
    date = date or datetime.now(get_default_tz()).strftime("%Y-%m-%d")
    text = rel_path.read_text(encoding="utf-8")
    text = bump_last_touch_in_text(text, date)
    snippet = plain_snippet(body_excerpt)
    text = append_log_entry(text, date, "Email", subject or "(no subject)", snippet)
    atomic_write(rel_path, text)
    _audit_log({
        "kind": "outbound",
        "email": target,
        "matched": True,
        "slug": rel_path.stem,
        "subject": subject or "",
    }, workspace_root=workspace_root)
    return True


def bump_inbound(
    sender_email: str,
    date: Optional[str] = None,
    workspace_root: Optional[Path] = None,
) -> bool:
    """Silently bump last_touch on the matching relationship record. No log entry.

    Returns True if the sender resolved to a known contact (regardless of
    whether the last_touch value actually changed — duplicate inbound on the
    same day is a no-op write but still a match). Returns False on no match
    or multi-match conflict. Writes a JSONL audit entry on every invocation.
    """
    target = (sender_email or "").strip().lower()
    rel_path = resolve_recipient(sender_email, workspace_root=workspace_root)
    if rel_path is None:
        _audit_log({
            "kind": "inbound",
            "email": target,
            "matched": False,
        }, workspace_root=workspace_root)
        return False
    date = date or datetime.now(get_default_tz()).strftime("%Y-%m-%d")
    text = rel_path.read_text(encoding="utf-8")
    new_text = bump_last_touch_in_text(text, date)
    if new_text != text:
        atomic_write(rel_path, new_text)
    _audit_log({
        "kind": "inbound",
        "email": target,
        "matched": True,
        "slug": rel_path.stem,
    }, workspace_root=workspace_root)
    return True
