"""Real-data source for the /contacts endpoint.

Lists every CRM contact the CEO can see: the CEO's own contacts from
crm/contacts/, plus every executive's contacts from their per-exec mirror
repo ../31c-crm-{slug}/contacts/. Mirrors the Tribe page format - rows
grouped by relationship_type with days-since-touch - but spans all owners,
so each row also carries who tracks the contact.

The CEO's own contacts are read from the live crm/contacts/ directory.
Executive contacts come from the per-exec mirror repos (one repo per
executive: 31c-crm-{slug}, cloned as a sibling of the workspace root).
Each exec's /sync pushes to their own repo. The deprecated 31c-crm-central
aggregate is still read as a fallback for execs whose per-exec mirror is
not present on disk; that fallback will be removed once every active exec
has been migrated.
"""
import logging
import re
from datetime import date, datetime, timezone
from pathlib import Path

from scripts.bridge_daemon.sources.tribe import (
    CONTACT_SLUG_RE,
    _days_since,
    _display_name,
    _extract_section,
    _parse_frontmatter,
)
from scripts.utils.paths import get_data_root
from scripts.utils.workspace import get_all_active_exec_slugs
from scripts.utils.operator_identity import operator_slug
from scripts.bridge_daemon._safepath import contains_symlink

CRM_CENTRAL_DIRNAME = "31c-crm-central"
PER_EXEC_REPO_PREFIX = "31c-crm-"
CEO_OWNER = "ceo"
CEO_OWNER_LABEL = "Misha Hanin"
_OWNER_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
CONTACT_FILE_MAX_BYTES = 500_000

logger = logging.getLogger(__name__)


def _crm_central_self_dir() -> str:
    """The operator's own contacts folder inside the deprecated crm-central
    mirror; it holds a stale snapshot (the live crm/contacts/ is used instead),
    so it is skipped. Resolved through the operator seam: established instance ->
    legacy 'misha-hanin' (byte-identical), fresh clone -> generic 'operator'."""
    return operator_slug()


def _resolve_exec_contacts_dir(workspace_root: Path, owner: str) -> Path | None:
    """Return the contacts directory for `owner`, preferring per-exec mirror.

    Resolution: ../31c-crm-{owner}/contacts/ (current source of truth) if it
    exists on disk; otherwise ../31c-crm-central/contacts/{owner}/ (deprecated
    aggregate retained as fallback). Returns None if neither exists.
    """
    per_exec = workspace_root.parent / f"{PER_EXEC_REPO_PREFIX}{owner}" / "contacts"
    if per_exec.is_dir():
        return per_exec
    central = workspace_root.parent / CRM_CENTRAL_DIRNAME / "contacts" / owner
    if central.is_dir():
        return central
    return None


def _owner_label(owner: str) -> str:
    """Human-readable owner name from an owner slug."""
    if owner == CEO_OWNER:
        return CEO_OWNER_LABEL
    return owner.replace("-", " ").title()


def _is_contact_file(path: Path) -> bool:
    """A contact file is a .md that is not a README or an underscore-file."""
    name = path.name.lower()
    return name.endswith(".md") and name != "readme.md" and not name.startswith("_")


def _contact_record(path: Path, owner: str, today: date | None) -> dict | None:
    """Parse one contact .md into a row dict, or None on read failure.

    Honours `CONTACT_FILE_MAX_BYTES`, the same cap `read_one_contact` enforces.
    The list scan used to read every file whole with no size check, so one
    oversized contact -- a pasted log, an accidental dump into a `.md` -- was
    read in full on EVERY `/contacts` load while the drill-down that opens the
    same file refused it. The module declares one cap; both readers apply it.
    """
    try:
        size = path.stat().st_size
    except OSError:
        logger.warning("skipping unstattable contact file %s", path, exc_info=True)
        return None
    if size > CONTACT_FILE_MAX_BYTES:
        logger.warning("skipping oversized contact file %s (%d bytes, cap %d)",
                       path, size, CONTACT_FILE_MAX_BYTES)
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        # UnicodeDecodeError is a ValueError, NOT an OSError. One contact file
        # saved as Windows-1252 used to propagate out of the scan and 500 the
        # whole /contacts page; `read_one_contact` already catches both.
        logger.warning("skipping unreadable contact file %s", path, exc_info=True)
        return None
    fm = _parse_frontmatter(text)
    slug = path.stem
    return {
        "owner": owner,
        "owner_label": _owner_label(owner),
        "slug": slug,
        "name": _display_name(text, slug),
        "company": fm.get("company") or None,
        "relationship_type": fm.get("relationship_type") or "other",
        "last_touch": fm.get("last_touch") or None,
        "days_since_touch": _days_since(fm.get("last_touch"), today=today),
    }


def list_contacts(workspace_root: Path, today: date | None = None,
                  data_root: "Path | None" = None) -> dict:
    """Scan the CEO's crm/contacts/ + every active exec's contacts.

    Per-exec source order matches `_resolve_exec_contacts_dir`: the per-exec
    mirror ``../31c-crm-{slug}/contacts/`` wins, and the deprecated
    ``31c-crm-central`` aggregate is only a fallback. (This line used to say
    the scan read "every exec's crm-central contacts", which is the source
    that LOSES.)

    Returns {contacts, counts, owner_counts, total, data_time}. contacts
    is sorted days-since-touch DESC (longest-overlooked first, None
    last), matching the Tribe page.

    HEADING OS engine/data split: the CEO's own crm/contacts/ is DATA, so it
    resolves under ``data_root`` (falls back to the ``get_data_root()`` seam when not
    supplied, NOT to ``workspace_root``). Per-exec mirror repos are siblings of the engine clone and stay
    rooted at ``workspace_root.parent``.
    """
    if data_root is None:
        data_root = get_data_root()
    rows: list[dict] = []
    most_recent_mtime: float = 0.0

    def _scan(directory: Path, owner: str) -> None:
        nonlocal most_recent_mtime
        if not directory.is_dir():
            return
        for p in directory.glob("*.md"):
            if not _is_contact_file(p):
                continue
            rec = _contact_record(p, owner, today)
            if rec is None:
                continue
            rows.append(rec)
            try:
                mt = p.stat().st_mtime
            except OSError:
                mt = 0.0
            if mt > most_recent_mtime:
                most_recent_mtime = mt

    # The CEO's own contacts - live directory (DATA).
    _scan(data_root / "crm" / "contacts", CEO_OWNER)

    # Every active executive's contacts. Source of truth is the per-exec
    # mirror (../31c-crm-{slug}/contacts/), with crm-central retained as a
    # fallback for execs whose mirror is not yet cloned locally. The exec
    # registry drives the enumeration so a stale or partial crm-central
    # snapshot cannot mask an exec who has migrated.
    seen_owners: set[str] = set()
    self_dir = _crm_central_self_dir()  # resolve once, not per loop iteration
    try:
        registry_slugs = get_all_active_exec_slugs()
    except Exception:
        # A silent [] here is not "no executives", it is "the registry could
        # not be read", and every exec whose contacts live ONLY in a per-exec
        # mirror then vanishes from the page with no indication. Log it; the
        # crm-central backstop below still runs, so the page degrades rather
        # than empties.
        logger.warning("exec registry unreadable; per-exec mirrors will be "
                       "skipped and only the crm-central backstop is scanned",
                       exc_info=True)
        registry_slugs = []
    for owner in registry_slugs:
        if not _OWNER_RE.match(owner) or owner == self_dir:
            continue
        target = _resolve_exec_contacts_dir(workspace_root, owner)
        if target is None:
            continue
        _scan(target, owner)
        seen_owners.add(owner)

    # Crawl crm-central for any execs not in the registry (provisional
    # backstop while migration completes). Skip owners already scanned and
    # the misha-hanin snapshot.
    central = workspace_root.parent / CRM_CENTRAL_DIRNAME / "contacts"
    if central.is_dir():
        for exec_dir in sorted(central.iterdir()):
            if not exec_dir.is_dir():
                continue
            owner = exec_dir.name
            if owner == self_dir or not _OWNER_RE.match(owner):
                continue
            if owner in seen_owners:
                continue
            _scan(exec_dir, owner)

    # Sort days-since-touch DESC; None last (matches Tribe).
    def sort_key(r):
        d = r["days_since_touch"]
        return (1 if d is None else 0, -(d or 0))
    rows.sort(key=sort_key)

    counts: dict = {}
    owner_counts: dict = {}
    for r in rows:
        counts[r["relationship_type"]] = counts.get(r["relationship_type"], 0) + 1
        owner_counts[r["owner"]] = owner_counts.get(r["owner"], 0) + 1

    data_time = (
        datetime.fromtimestamp(most_recent_mtime, tz=timezone.utc).isoformat()
        if most_recent_mtime else None
    )
    return {
        "contacts": rows,
        "counts": counts,
        "owner_counts": owner_counts,
        "total": len(rows),
        "data_time": data_time,
    }


def _owner_is_valid(owner: str) -> bool:
    """True when `owner` is a well-formed owner slug this daemon may serve.

    Split out of `_contacts_base` so a caller can tell a REJECTED owner from a
    valid owner with no data on this machine. `_contacts_base` answers None to
    both, and reporting "invalid owner" for an exec whose mirror repo simply
    is not cloned here sends the reader hunting a validation bug that is not
    there.
    """
    if owner == CEO_OWNER:
        return True
    return owner != _crm_central_self_dir() and bool(_OWNER_RE.match(owner))


def _contacts_base(workspace_root: Path, owner: str,
                   data_root: "Path | None" = None) -> Path | None:
    """Resolve the directory holding `owner`'s contact files, or None.

    Same resolution order as `_resolve_exec_contacts_dir`: per-exec mirror
    wins, crm-central is the fallback. Returns None if the owner is invalid
    or no source exists on disk.

    HEADING OS engine/data split: the CEO's own crm/contacts/ is DATA, so it
    resolves under ``data_root`` (falls back to the ``get_data_root()`` seam, NOT to ``workspace_root``). Per-exec
    mirror repos are siblings of the engine clone (``workspace_root.parent``).
    """
    if data_root is None:
        data_root = get_data_root()
    if owner == CEO_OWNER:
        return data_root / "crm" / "contacts"
    if owner == _crm_central_self_dir() or not _OWNER_RE.match(owner):
        return None
    return _resolve_exec_contacts_dir(workspace_root, owner)


def read_one_contact(workspace_root: Path, owner: str, slug: str,
                     data_root: "Path | None" = None) -> dict:
    """Read a single CRM contact (CEO or exec) safely.

    Drill-down keys on (owner, slug) because the same slug can exist
    under multiple owners (a contact tracked by more than one person).

    Returns {ok, owner, owner_label, slug, name, frontmatter,
    active_commitments, interaction_log} or {ok: False, error}.

    HEADING OS engine/data split: the CEO's own crm/contacts/ is DATA
    (resolved under ``data_root``); exec mirrors stay sibling-rooted.
    """
    if data_root is None:
        data_root = get_data_root()
    if not owner or not isinstance(owner, str):
        return {"ok": False, "error": "missing owner"}
    if not slug or not isinstance(slug, str) or not CONTACT_SLUG_RE.match(slug):
        return {"ok": False, "error": "invalid slug"}
    if not _owner_is_valid(owner):
        return {"ok": False, "error": "invalid owner"}
    base = _contacts_base(workspace_root, owner, data_root=data_root)
    if base is None:
        return {"ok": False, "error": "no contacts directory on this machine"}
    base_resolved = base.resolve()
    target_raw = base / f"{slug}.md"
    target = target_raw.resolve()
    try:
        target.relative_to(base_resolved)
    except ValueError:
        return {"ok": False, "error": "path escapes contacts directory"}
    if not target.exists():
        return {"ok": False, "error": "not found"}
    try:
        if contains_symlink(base, target_raw):
            return {"ok": False, "error": "symlinks not allowed"}
        if not target.is_file():
            return {"ok": False, "error": "not a file"}
        size = target.stat().st_size
    except OSError:
        return {"ok": False, "error": "stat failed"}
    if size > CONTACT_FILE_MAX_BYTES:
        return {"ok": False, "error": f"file too large ({size} bytes)"}
    try:
        text = target.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        return {"ok": False, "error": f"read failed: {e}"}
    return {
        "ok": True,
        "owner": owner,
        "owner_label": _owner_label(owner),
        "slug": slug,
        "name": _display_name(text, slug),
        "frontmatter": _parse_frontmatter(text),
        "active_commitments": _extract_section(text, "Active Commitments"),
        "interaction_log": _extract_section(text, "Interaction Log"),
    }
