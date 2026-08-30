"""Real-data source for the /contacts endpoint.

Lists every CRM contact the CEO can see: their own contacts from the live
crm/contacts/ under the DATA root, plus every active executive's contacts from
that executive's DATA overlay, cloned as a sibling of the engine at
`../.heading-os-data-{slug}/crm/contacts/`. Mirrors the Tribe page format (rows
grouped by relationship_type with days-since-touch) but spans all owners, so
each row also carries who tracks the contact.

One topology, one source per owner, and the exec registry is the authority on
who counts as an executive. The 2026-08-23 migration retired both older roots:
the per-exec `31c-crm-{slug}` repos and the `31c-crm-central` aggregate. This
module kept reading them until 2026-08-30, which is why the /contacts page
showed every executive as zero contacts.

Tests: tests/bridge/test_sources_contacts.py,
tests/bridge/test_two_layers_that_disagreed_about_the_same_file.py
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
from scripts.utils.workspace import (
    get_all_active_exec_slugs,
    per_exec_overlay_dirname,
)
from scripts.utils.operator_identity import get_operator, operator_slug
from scripts.bridge_daemon._safepath import contains_symlink

CEO_OWNER = "ceo"
_OWNER_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
CONTACT_FILE_MAX_BYTES = 500_000

logger = logging.getLogger(__name__)


def _crm_central_self_dir() -> str:
    """The operator's OWN slug, the one owner this daemon never reads as an
    executive: their live contacts come from crm/contacts/, so a registry entry
    or overlay under that slug is a stale duplicate and is skipped.

    MISNAMED, deliberately kept. The name dates from the retired crm-central
    aggregate, but the question it answers ("which slug is the operator's own?")
    never had anything to do with that mirror, and two files call or monkeypatch
    it by this name. Renaming it is a separate change from the migration.

    Resolved through the operator seam: established instance -> that instance's
    configured slug, fresh clone -> generic 'operator'.
    """
    return operator_slug()


def _resolve_exec_contacts_dir(workspace_root: Path, owner: str) -> Path | None:
    """Return `owner`'s contacts directory, or None when it is not on disk.

    ONE topology, and this is it: an exec's full DATA overlay is cloned as a
    sibling of the engine clone, `../.heading-os-data-{owner}/`, with their CRM
    contacts inside it at `crm/contacts/`. The directory NAME comes from
    `per_exec_overlay_dirname`, the same helper `get_per_exec_repo_path` uses,
    so the layout is spelled once for the whole workspace.

    The name is composed against the `workspace_root` ARGUMENT rather than
    fetched from `get_per_exec_contacts_dir`, which anchors on
    `get_workspace_root()`. This function takes its root as a parameter
    precisely so a test can sandbox the sibling overlays under `tmp_path`;
    importing the rooted helper would make it ignore its own argument and read
    the operator's real siblings during the suite.

    Until 2026-08-30 this read `../31c-crm-{owner}/contacts/` and fell back to
    `../31c-crm-central/contacts/{owner}/`. Both roots were retired by the
    2026-08-23 CRM migration and neither exists on disk, so this returned None
    for every executive and the /contacts page rendered each of them as zero
    contacts while their live overlays held real files. Invisible only because
    the bridge daemon is disabled. The suite was green throughout: its fixtures
    built the dead layout, so the tests and the code agreed with each other and
    with no filesystem.
    """
    per_exec = (workspace_root.parent
                / per_exec_overlay_dirname(owner) / "crm" / "contacts")
    return per_exec if per_exec.is_dir() else None


def _owner_label(owner: str) -> str:
    """Human-readable owner name from an owner slug.

    The `ceo` slug resolves through the operator seam, the same way
    `_crm_central_self_dir` three functions up already does. It was the literal
    string of this instance's operator until 2026-08-28, so a fresh clone of the
    public engine labelled ITS owner's contacts with somebody else's name, while
    the identical question one screen above was answered from
    `operator_identity`. The engine ships generic defaults ("Operator"), and an
    established instance still reads its own configured name, so nothing changes
    here for a workspace that has one.
    """
    if owner == CEO_OWNER:
        return get_operator()["name"]
    return owner.replace("-", " ").title()


def _is_contact_file(path: Path) -> bool:
    """A contact file is a lowercase `.md` that is not a README or an underscore-file.

    The extension test is case-EXACT; only the README and underscore tests
    fold case. It used to lower the whole name first, which said `Jane.MD`
    was a contact file - an acceptance neither of the two layers around it
    can honour. `_scan`'s `glob("*.md")` is case-sensitive on posix, so the
    file never reached this function there and vanished from `/contacts` with
    no log line; on Windows, where pathlib globs case-insensitively, it DID
    reach here, was listed with `slug = "Jane"`, and then `read_one_contact`
    (which only ever opens `{slug}.md`, and whose `CONTACT_SLUG_RE` is
    lowercase-only) could never open the row. One rule, in one place: only a
    lowercase `.md` is a contact file, and `_scan` names the ones it skips.
    """
    name = path.name
    if not name.endswith(".md"):
        return False
    lowered = name.lower()
    return lowered != "readme.md" and not lowered.startswith("_")


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

    Executives come from `_resolve_exec_contacts_dir`, one source each:
    ``../.heading-os-data-{slug}/crm/contacts/``. Enumeration is the exec
    registry alone, so an offboarded executive whose overlay still sits on
    disk contributes nothing.

    Returns:
        {
            "contacts": [row, ...] sorted days-since-touch DESC
                        (longest-overlooked first, None last), matching the
                        Tribe page,
            "counts": {relationship_type: int},
            "owner_counts": {owner: int},
            "total": int - the rows RETURNED, which is every row: this scan
                     applies no cap,
            "data_time": ISO 8601 UTC of the most-recent contact file mtime,
                         or None,
        }

    HEADING OS engine/data split: the CEO's own crm/contacts/ is DATA, so it
    resolves under ``data_root`` (falls back to the ``get_data_root()`` seam when not
    supplied, NOT to ``workspace_root``). Per-exec DATA overlays are siblings of the engine clone and stay
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
        # `*.[mM][dD]` rather than `*.md`, so a case-variant extension is SEEN
        # and reported here instead of being dropped by a case-sensitive glob
        # before anything could say so. The row is still skipped: the
        # drill-down only opens `{slug}.md`, so listing it would produce a
        # contact that cannot be opened.
        for p in directory.glob("*.[mM][dD]"):
            if not _is_contact_file(p):
                lowered = p.name.lower()
                # Only a file that WOULD have been a contact but for its
                # extension case; a README.MD is excluded on its own merits
                # and needs no rename.
                if (not p.name.endswith(".md") and lowered != "readme.md"
                        and not lowered.startswith("_")):
                    logger.warning("skipping contact file with a non-lowercase "
                                   "extension (rename it to .md): %s", p)
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

    # Every active executive's contacts, from their DATA overlay sibling.
    # The registry is the ONLY enumeration: it is the authority on who is an
    # executive here. Globbing `../.heading-os-data-*` instead would be one
    # line shorter and would silently resurrect an offboarded executive whose
    # overlay is still lying around on disk.
    self_dir = _crm_central_self_dir()  # resolve once, not per loop iteration
    try:
        registry_slugs = get_all_active_exec_slugs()
    except Exception:
        # A silent [] here is not "no executives", it is "the registry could
        # not be read". Nothing else enumerates executives, so this empties
        # the exec half of the page; say so rather than render zero rows as a
        # fact. The CEO's own contacts, scanned above, still show.
        logger.warning("exec registry unreadable; no executive contacts will "
                       "be listed (the CEO's own are unaffected)",
                       exc_info=True)
        registry_slugs = []
    for owner in registry_slugs:
        if not _OWNER_RE.match(owner) or owner == self_dir:
            continue
        target = _resolve_exec_contacts_dir(workspace_root, owner)
        if target is None:
            continue
        _scan(target, owner)

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

    Same single source as `_resolve_exec_contacts_dir`: the exec's DATA
    overlay. Returns None if the owner is invalid or no source exists on disk.

    HEADING OS engine/data split: the CEO's own crm/contacts/ is DATA, so it
    resolves under ``data_root`` (falls back to the ``get_data_root()`` seam, NOT to ``workspace_root``). Per-exec
    DATA overlays are siblings of the engine clone (``workspace_root.parent``).
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
