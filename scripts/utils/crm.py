#!/usr/bin/env python3
"""
Shared CRM utilities for 31C workspace scripts.

Library module (snake_case per workspace naming convention) - importable
by crm-health.py, generate-dashboard.py, aggregate-crm.py, and any other
script that needs to scan contact files, calculate health scores, or
parse cadence configuration.

Public surface:

- ``parse_config(config_path)`` - parse cadence defaults table from
  crm/config.md.
- ``parse_frontmatter(content)`` - parse YAML frontmatter from a contact
  file (string-valued dict for compatibility with crm-health.py).
- ``parse_commitments(content)`` - extract unchecked ``- [ ]`` items with
  optional ``(due: YYYY-MM-DD)`` annotations.
- ``calculate_health(last_touch_str, cadence_days, yellow_days, red_days, today)``
  - classify a contact as red/yellow/green/gray.
- ``scan_contacts(config, today=None)`` - scan all contact files and return
  ``(contacts, tribe_warnings, dangling_refs, stages, aliases)``. The summary
  here said a 3-tuple while the function returned five, so a caller written
  from this list unpacked wrong; the function's own docstring was correct.

Extracted from scripts/crm-health.py in Phase 6.1 of the 2026-05-12
workspace performance tune-up. Behaviour is preserved byte-for-byte.

Tests: tests/test_a_queue_that_read_corrupt_as_empty.py
"""

from __future__ import annotations

import re
import sys
from datetime import date, datetime
from pathlib import Path

# Workspace utilities (lazy-imported via the public functions; we resolve
# the import path here so callers do not need to massage sys.path.)
_HERE = Path(__file__).resolve()
_SCRIPTS_DIR = _HERE.parent.parent
_WORKSPACE_ROOT = _SCRIPTS_DIR.parent
if str(_WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(_WORKSPACE_ROOT))

from scripts.utils.workspace import (  # noqa: E402
    get_context_dir,
    get_default_tz,
    get_crm_contacts_dir,
    get_corporate_root,
    is_exec_workspace,
)
from scripts.utils.markdown import parse_frontmatter_str as _parse_frontmatter  # noqa: E402
from scripts.utils.markdown import frontmatter_list


def try_commit(commit_fn, repo: Path, files, message: str, label: str) -> bool:
    """Run `commit_fn(repo, files, message)`; return whether it landed.

    Both contact tools move a record across TWO repositories, and both caught a
    failed commit into a warning and carried on to the next one. So the source
    repo could commit the removal while the target's copy stayed untracked, and
    in a fresh clone the contact existed in neither. Returning a BOOLEAN is what
    lets the caller stop, and lets the final line say "INCOMPLETE" instead of
    "complete".
    """
    import subprocess

    try:
        commit_fn(repo, files, message)
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.decode().strip() if getattr(exc, "stderr", None) else exc
        print(f"Warning: git commit for the {label} repo failed - commit manually.")
        print(f"  {detail}")
        return False
    print(f"Committed to the {label} repo.")
    return True


def stamped_backup_path(source_path: Path, kind: str, today=None) -> Path:
    """A backup name for `source_path` that never overwrites an earlier one.

    `kind` is the marker in the name: "merged" for `scripts/merge-contacts.py`,
    "transferred" for `scripts/transfer-contact.py`.

    Both tools move a contact file aside with `Path.rename`, which on POSIX
    SILENTLY replaces an existing destination. Both used a single fixed name, so
    running either twice on one contact renamed the new file over the previous
    backup and destroyed it without a word, while still printing "Source backed
    up:". `transfer-contact.py` was fixed on its own in July and kept the
    reasoning in a comment; `merge-contacts.py` carried the same four lines and
    the same bug until 2026-08-27, because the fix was applied to one copy of
    duplicated code rather than to a shared helper. This IS that helper.

    The date comes from the configured zone, not UTC: the operator works past
    midnight local, and a backup filed under yesterday is the small version of
    the same confusion.
    """
    stamp = (today or datetime.now(get_default_tz()).date()).strftime("%Y%m%d")
    base = f"{source_path.stem}.md.{kind}-{stamp}"
    backup_path = source_path.with_name(base)
    suffix = 2
    while backup_path.exists():
        backup_path = source_path.with_name(f"{base}-{suffix}")
        suffix += 1
    return backup_path


# Types excluded from time-based cadence scoring (CEO talks daily)
NO_CADENCE_TYPES = {"tribe", "tribe-leadership", "inactive"}


def parse_config(config_path: Path) -> dict:
    """Parse cadence defaults from crm/config.md table.

    Returns a dict keyed by relationship type, with each value containing
    ``cadence``, ``yellow``, and ``red`` integer thresholds (in days).
    Returns an empty dict if the file does not exist or has no table.
    """
    defaults: dict = {}
    if not config_path.exists():
        return defaults

    content = config_path.read_text(encoding="utf-8")
    in_table = False
    separator_seen = False

    for line in content.split("\n"):
        if "| Type |" in line and "Cadence" in line:
            in_table = True
            continue
        if in_table and "---" in line:
            separator_seen = True
            continue
        if in_table and separator_seen:
            if "|" in line and line.strip():
                cells = [c.strip() for c in line.split("|")]
                cells = [c for c in cells if c]
                if len(cells) >= 4:
                    rel_type = cells[0]
                    try:
                        cadence = int(cells[1])
                        yellow = int(cells[2])
                        red = int(cells[3])
                        defaults[rel_type] = {
                            "cadence": cadence,
                            "yellow": yellow,
                            "red": red,
                        }
                    except ValueError:
                        continue
            elif not line.strip():
                break

    return defaults


def parse_frontmatter(content: str) -> dict:
    """Parse YAML frontmatter from a contact file (string-coerced values).

    Thin wrapper that delegates to
    :func:`scripts.utils.markdown.parse_frontmatter_str` and drops the body.
    Preserves the historical crm-health.py contract: all values are strings.
    """
    fm, _body = _parse_frontmatter(content)
    return fm


def parse_commitments(content: str) -> list:
    """Extract active (unchecked) commitments from a contact file."""
    commitments = []
    for line in content.split("\n"):
        line = line.strip()
        if line.startswith("- [ ]"):
            text = line[5:].strip()
            # Try to extract due date
            due_match = re.search(r"\(due:\s*(\d{4}-\d{2}-\d{2})\)", text)
            due_date = None
            if due_match:
                try:
                    due_date = date.fromisoformat(due_match.group(1))
                except ValueError:
                    pass
            commitments.append({"text": text, "due": due_date})
    return commitments


def calculate_health(last_touch_str: str, cadence_days: int, yellow_days: int,
                     red_days: int, today=None) -> tuple:
    """Calculate health state based on last touch and thresholds.

    Returns ``(health_state, days_since)`` where ``health_state`` is one of
    ``red``, ``yellow``, ``green``, ``gray``. ``days_since`` is ``None`` when
    a touch could not be parsed.
    """
    if today is None:
        today = datetime.now(get_default_tz()).date()

    if not last_touch_str or last_touch_str in ("-", "n/a", ""):
        return "red", None

    try:
        last_touch = date.fromisoformat(last_touch_str)
    except ValueError:
        return "gray", None

    days_since = (today - last_touch).days

    if days_since >= red_days:
        return "red", days_since
    elif days_since >= yellow_days:
        return "yellow", days_since
    else:
        return "green", days_since


def _cadence_override(raw, file_name: str) -> int | None:
    """A contact's explicit `cadence:`, or None when there is not a usable one.

    None means "no override" for both an absent field and an unusable one, so a
    single bad record falls back to its type default instead of taking the whole
    scan down with it. The bad value is named on stderr, because silently
    treating `cadence: 7 days` as absent is the other half of the same defect.
    """
    text = str(raw).strip()
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        print(f"crm: {file_name} has cadence {text!r}, which is not a whole "
              f"number of days; using the type default for this contact.",
              file=sys.stderr)
        return None


def is_radar_frozen(radar_freeze_until, today=None) -> bool:
    """True if a contact is inside an active radar-freeze window.

    THE single implementation. Accepts an ISO date (``YYYY-MM-DD``) or a full
    ISO datetime string, with or without a ``Z``. An empty value means not
    frozen; an UNPARSEABLE one means frozen, and says so on stderr.

    Failing closed, and the consolidation, are both from 2026-08-24. This
    docstring used to end "Matches the freeze semantics already honored by
    cold_sweep_core.route() and crm_next.rank_candidates()" — an accurate
    description of THREE separate implementations of one suppression control,
    every one of them silently fail-open. A typo in `radar_freeze_until` turned
    a do-not-contact marker into an outreach card in all three, and a fix
    applied to any one of them left the other two wrong. The other two now call
    this function.

    Fail closed because the two errors are not symmetric: a contact wrongly
    held back is a question the operator can ask, and the other direction is a
    message to someone who was explicitly frozen.
    """
    if radar_freeze_until is None or not str(radar_freeze_until).strip():
        return False
    if today is None:
        today = datetime.now(get_default_tz()).date()
    if isinstance(today, datetime):
        today = today.date()
    # No `.replace("Z", "+00:00")`: `pyproject.toml` sets
    # `requires-python = ">=3.11"` and `fromisoformat` has parsed the `Z` form
    # natively since 3.11, so it would be dead code — a mutation deleting it
    # stayed green because it changes nothing on any supported interpreter.
    raw = str(radar_freeze_until).strip()
    try:
        freeze = datetime.fromisoformat(raw).date()
    except ValueError:
        try:
            freeze = date.fromisoformat(raw)
        except ValueError:
            print(f"crm: radar_freeze_until {str(radar_freeze_until).strip()!r} "
                  f"is not an ISO date; treating the contact as frozen.",
                  file=sys.stderr)
            return True
    return freeze > today


def normalize_name(name: str) -> str:
    """Normalize a name for comparison. The single definition."""
    return " ".join(name.lower().strip().split())


def contact_identity_key(record: dict) -> str:
    """"Is this the same person?", answered one way for every tool.

    `entity_ref` when the record carries one, exact normalized NAME otherwise.
    Company is deliberately absent from the legacy key: company strings differ
    across exec repos for known dual-owned contacts, so including it breaks
    matches the name alone makes. `_legacy_fuzzy_key` in `aggregate-crm.py`
    carries the full rationale and now delegates here.

    `admin-health.py` grouped on FILENAME instead, so a person saved as
    `jordan-kim.md` by one exec and `kim-jordan.md` by another was shared to
    the aggregator and not shared to the dashboard, and the two disagreed about
    a number they both print.
    """
    ref = record.get("entity_ref")
    if ref:
        return f"entity::{ref}"
    return f"legacy::name::{normalize_name(record.get('name') or '')}"


def is_contact_file(path: Path) -> bool:
    """One rule for "is this a contact record?", because three copies disagreed.

    `aggregate-crm.py` excluded `readme.md` case-INSENSITIVELY.
    `admin-health.py` excluded exactly `README.md`, so a `readme.md` in an exec
    overlay was counted as a contact by the dashboard and not by the
    aggregator, and the two tools reported different totals for one directory.
    `_get_contact_files` here excluded nothing at all: a README only stayed out
    of the results because `scan_contacts` later drops records with no `name`
    and no `entity_ref`, which is an accident, not a rule -- a README carrying
    frontmatter would have counted.

    The suffix test is case-insensitive too. A `.MD` file on a case-insensitive
    filesystem is the same file to everything except this predicate.
    """
    return (path.is_file()
            and path.suffix.lower() == ".md"
            and path.name.lower() != "readme.md")


def _get_contact_files(contacts_dir: Path) -> list:
    """Collect all contact .md files from personal and corporate directories."""
    seen = set()
    files = []
    # Personal contacts first (take precedence)
    if contacts_dir.exists():
        for f in sorted(contacts_dir.glob("*.md")):
            if not is_contact_file(f):
                continue
            seen.add(f.name)
            files.append(f)
    # Corporate contacts (Tribe members shared from CEO workspace)
    if is_exec_workspace():
        corp_crm = get_corporate_root() / "crm" / "contacts"
        if corp_crm.exists():
            for f in sorted(corp_crm.glob("*.md")):
                if is_contact_file(f) and f.name not in seen:
                    files.append(f)
    return files


def scan_contacts(config: dict, today=None, contacts_dir: Path | None = None,
                  workspace_root: Path | None = None) -> tuple:
    """Scan all contact files and compute health.

    Args:
        config: cadence defaults from ``parse_config``.
        today: optional ``date`` override (defaults to ``datetime.now().date()``).
        contacts_dir: optional path override (defaults to ``get_crm_contacts_dir()``).
        workspace_root: optional workspace root override passed through to
            ``load_entity`` for test fixtures and CEO-only callers.

    Returns:
        ``(contacts, tribe_warnings, dangling_refs, stages, aliases)`` where
        ``contacts`` is a list of contact dicts (each with ``name``,
        ``company``, ``type``, ``last_touch``, ``cadence``, ``health``,
        ``days_since``, ``commitments``, ``file``), ``tribe_warnings`` is a
        list of @31c.io emails not typed as tribe, and ``dangling_refs`` is a
        list of dicts with ``file`` and ``entity_ref`` for relationship records
        whose address-book entity could not be resolved.  Also returns the
        parsed pipeline-stage and alias maps so callers can reuse them without
        re-parsing.
    """
    if today is None:
        today = datetime.now(get_default_tz()).date()
    if contacts_dir is None:
        contacts_dir = get_crm_contacts_dir()

    # Phase 2.4: load pipeline stages + aliases once for stage-aware cadence.
    #
    # Through the DATA-ROOT SEAM, not `_WORKSPACE_ROOT`. That constant is the
    # ENGINE clone root, computed at import from `__file__`, and the comment
    # here used to call it "the canonical workspace root when called in
    # production". On the split engine/data topology it is not: `pipeline.md`
    # and `aliases.md` are operator data and live in the overlay. Both reads
    # therefore resolved to paths that do not exist, `parse_*` returned empty
    # dicts, and stage-aware cadence has never once applied in production.
    #
    # Measured 2026-08-29 against the real tree:
    #   engine context/pipeline.md exists: False   data: True
    #   engine crm/aliases.md      exists: False   data: True
    #   stages from the ENGINE path: 0    from the DATA path: 29
    #   aliases from the ENGINE path: 0   from the DATA path: 61
    #   28 of those 29 rows carry a stage that maps to a STAGE_CADENCE entry
    #   (9 Demo/POC at 7 days, 3 Negotiation at 3, 10 Qualified, 6 Lead)
    # so every one of them fell back to the contact-type default and went
    # yellow and red days late. `STAGE_CADENCE["Won"] = 0`, the stop-tracking
    # signal, was unreachable from the pipeline side, so closed accounts kept
    # accruing red debt and feeding `/cold-sweep`'s outreach drafting.
    #
    # `census_oracles.py` reads the same two files through its corpus paths and
    # was right all along. This is the second copy.
    #
    # `workspace_root` keeps its exact meaning: a fixture override. Only the
    # FALLBACK changed, so every existing caller that passes it is unaffected.
    if workspace_root:
        _ws_root = Path(workspace_root)
        _pipeline_file = _ws_root / "context" / "pipeline.md"
        _aliases_file = _ws_root / "crm" / "aliases.md"
    else:
        _pipeline_file = get_context_dir() / "pipeline.md"
        _aliases_file = get_crm_contacts_dir().parent / "aliases.md"
    _stages = parse_pipeline_stages(_pipeline_file)
    _aliases = parse_aliases(_aliases_file)

    contacts: list = []
    tribe_warnings: list = []
    dangling_refs: list = []

    contact_files = _get_contact_files(contacts_dir)
    if not contact_files:
        return contacts, tribe_warnings, dangling_refs, _stages, _aliases

    for file_path in contact_files:
        content = file_path.read_text(encoding="utf-8")
        fm = parse_frontmatter(content)

        if not fm.get("name") and not fm.get("entity_ref"):
            continue

        # Entity-aware merge: relationship records carry entity_ref instead of
        # inline biographical facts. Load the entity and merge to flat shape.
        if fm.get("entity_ref"):
            entity = load_entity(fm["entity_ref"], workspace_root=workspace_root)
            if entity is None:
                dangling_refs.append({
                    "file": file_path.name,
                    "entity_ref": fm["entity_ref"],
                })
            fm = merge_entity_and_relationship(entity, fm)

        if not fm.get("name"):
            continue

        name = fm["name"]
        company = fm.get("company", "")
        rel_type = fm.get("type", "")
        last_touch = fm.get("last_touch", "")
        # Parsed once, here, and tolerantly. `int(cadence_override)` sat inline
        # in two branches below, so `cadence: 7 days` in ONE contact raised an
        # uncaught ValueError out of scan_contacts and aborted the whole CRM
        # scan for every caller -- crm-health, generate-dashboard, aggregate-crm
        # -- rather than degrading that one record. Every other malformed input
        # in this module degrades: `calculate_health` returns gray on an
        # unparseable date, `parse_config` skips a bad row, `parse_commitments`
        # swallows a bad due date. This is now the same.
        cadence_override = _cadence_override(fm.get("cadence", ""), file_path.name)
        email = fm.get("email", "")
        radar_freeze_until = fm.get("radar_freeze_until", "")

        # Resolve pipeline stage for this contact once (used in all append paths).
        _pc_norm = (fm.get("pipeline_company", "") or company).lower().strip()
        _pc_canonical = _aliases.get(_pc_norm, _pc_norm)
        stage = _stages.get(_pc_canonical) or _stages.get(_pc_norm) or ""

        # Detect @31c.io emails not typed as tribe/tribe-leadership.
        # Opt-out: contacts who legitimately hold a @31c.io address while not
        # being Tribe (e.g. resellers/advisors issued a company mailbox) carry
        # tribe_email_ok: true on their relationship record to suppress this.
        _tribe_email_ok = str(fm.get("tribe_email_ok", "")).strip().lower() in ("true", "yes", "1")
        if (email and "@31c.io" in email.lower()
                and rel_type not in NO_CADENCE_TYPES and not _tribe_email_ok):
            tribe_warnings.append({
                "name": name,
                "company": company,
                "type": rel_type,
                "email": email,
                "file": file_path.name,
            })

        # Skip types with no cadence tracking (tribe, tribe-leadership, inactive)
        if rel_type in NO_CADENCE_TYPES:
            health = "gray"
            days = None
            cadence = 0
            commitments = parse_commitments(content)
            contacts.append({
                "name": name,
                "company": company,
                "email": email,
                "type": rel_type,
                "stage": stage,
                "last_touch": last_touch,
                "cadence": cadence,
                "health": health,
                "days_since": days,
                "commitments": commitments,
                "file": file_path.name,
                "slug": file_path.stem,
                "status": fm.get("status", "active"),
                "radar_freeze_until": radar_freeze_until,
            })
            continue

        # Get thresholds from config or contact override.
        # Explicit per-contact cadence (cadence_override) always wins over
        # stage-aware defaults. Stage-aware cadence is applied only when the
        # contact has no explicit override.
        if rel_type in config:
            type_cadence = config[rel_type]["cadence"]
            yellow = config[rel_type]["yellow"]
            red = config[rel_type]["red"]
            if cadence_override is not None:
                cadence = cadence_override
            else:
                # Apply stage-aware cadence using pipeline_company or company
                pipeline_co = fm.get("pipeline_company", "") or company
                cadence = compute_stage_aware_cadence(
                    relationship_type=rel_type,
                    pipeline_company=pipeline_co,
                    stages=_stages,
                    aliases=_aliases,
                    type_default=type_cadence,
                )
            # Recalculate yellow/red proportionally when cadence changed
            if cadence != type_cadence:
                yellow = max(1, round(yellow * cadence / max(type_cadence, 1)))
                red = cadence
        elif cadence_override is not None:
            cadence = cadence_override
            yellow = int(cadence * 0.7)
            red = cadence
        else:
            pipeline_co = fm.get("pipeline_company", "") or company
            type_cadence = 14
            cadence = compute_stage_aware_cadence(
                relationship_type=rel_type,
                pipeline_company=pipeline_co,
                stages=_stages,
                aliases=_aliases,
                type_default=type_cadence,
            )
            yellow = max(1, round(cadence * 0.7))
            red = cadence

        # `cadence: 0` means "no time-based tracking" wherever it came from: a
        # Won/Lost pipeline stage, or an explicit per-contact override. Two of
        # the three branches above carried their own copy of this check and
        # their own copy of the gray record; the explicit-override branch, taken
        # when the contact's type is absent from the config table, had neither.
        # It fell through with cadence=0, yellow=0, red=0, and `calculate_health`
        # returns red for `days_since >= 0` -- a red for a contact touched
        # yesterday, feeding the radar and /cold-sweep's outreach drafting.
        #
        # One check after the branch tree, rather than a third copy inside it:
        # the two copies are what let the third branch be written without one.
        if cadence == 0:
            health, days = "gray", None
        else:
            health, days = calculate_health(last_touch, cadence, yellow, red,
                                            today=today)
        # Radar freeze: a contact inside an active freeze window is parked. Render
        # gray so it leaves the red/yellow radar and the dashboard; downstream
        # cadence + outreach (cold-sweep, crm_next) already honor the same field.
        # CEO directive 2026-06-04.
        if health in ("red", "yellow") and is_radar_frozen(radar_freeze_until, today):
            health = "gray"
        commitments = parse_commitments(content)

        contacts.append({
            "name": name,
            "company": company,
            "email": email,
            "type": rel_type,
            "stage": stage,
            "last_touch": last_touch,
            "cadence": cadence,
            "health": health,
            "days_since": days,
            "commitments": commitments,
            "file": file_path.name,
            "slug": file_path.stem,
            "status": fm.get("status", "active"),
            "radar_freeze_until": radar_freeze_until,
        })

    return contacts, tribe_warnings, dangling_refs, _stages, _aliases


# ===========================================================================
# Entity / Relationship helpers (Phase 0 of CRM action engine)
# ===========================================================================

def _address_book_dir(workspace_root: Path | None = None) -> Path:
    """Resolve the address-book directory.

    When `workspace_root` is None (production callers), resolves via
    `is_ceo_workspace()`: CEO -> `crm/address-book/`, exec -> `corporate/crm/address-book/`.

    When `workspace_root` is supplied (test fixtures and CEO-only callers), the
    function assumes CEO layout and returns the address-book dir under the given
    root without consulting workspace type. Test fixtures using exec layout must pass
    `workspace_root / "corporate"` instead, or extend this helper before Phase 1
    when exec-workspace integration is exercised.
    """
    if workspace_root is None:
        from scripts.utils.workspace import is_ceo_workspace
        if is_ceo_workspace():
            # CEO: crm/ resolves under the DATA root (.heading-os-data), not the
            # engine clone. get_crm_contacts_dir() is <data>/crm/contacts, so its
            # parent is the data crm/ root.
            return get_crm_contacts_dir().parent / "address-book"
        # Exec: corporate content resolves under the corporate root.
        return get_corporate_root() / "crm" / "address-book"
    return Path(workspace_root) / "crm" / "address-book"


def load_entity(slug: str, workspace_root: Path | None = None) -> dict | None:
    """Read an address book entity record by slug. Returns parsed frontmatter or None.

    The slug is a stable kebab-case identifier (e.g. 'karl-mertens'). Lookups
    are resolved against the corporate address-book directory which is
    populated by corporate sync on exec workspaces, and is the local
    crm/address-book/ on the CEO workspace.
    """
    entity_file = _address_book_dir(workspace_root) / f"{slug}.md"
    if not entity_file.exists():
        return None
    text = entity_file.read_text(encoding="utf-8")
    parsed = parse_frontmatter(text)
    # `parse_frontmatter` returns `{}` for a file with no frontmatter block, and
    # `{}` is not None, so `scan_contacts`' dangling-ref branch never fired for
    # it: the contact was merged to nothing, failed the `if not fm.get("name")`
    # check a few lines later, and vanished from CRM health, the radar and the
    # dashboard with no diagnostic anywhere. Only a MISSING file was reported.
    # A record that exists and says nothing is as dangling as one that is gone.
    if not parsed:
        return None
    return parsed


def resolve_entity_ref(relationship_record: dict, workspace_root: Path | None = None) -> dict | None:
    """Given a relationship record dict, load its linked entity. Returns None if
    entity_ref is missing or the linked entity does not exist."""
    slug = relationship_record.get("entity_ref")
    if not slug:
        return None
    return load_entity(slug, workspace_root=workspace_root)


def contact_index_by_email(contacts_dir: Path | None = None,
                           workspace_root: Path | None = None) -> dict[str, dict]:
    """Lower-cased email address to its merged contact record, for every card.

    THE ONE PLACE that answers "which contact owns this address". A card holds
    the address in either of two shapes and a reader must handle both:

      legacy   `email: someone@example.test` inline in the contact card
      entity   `entity_ref: some-slug`, with the address at
               `crm/address-book/some-slug.md::canonical_email`

    The entity shape is what `/crm` add-contact writes and what
    `crm_migrate_to_entity_model.py --apply` rewrites cards into, so it is the
    current schema, not an exotic one. Measured on the operator's tree
    2026-08-29: of 169 cards, 89 inline an address and 80 do not; 59 addresses
    that `scan_contacts` resolves are reachable ONLY through the entity.

    `scripts/inbox_pulse/rules.py` carried two copies of a walk that read the
    inline key alone, and this function exists so there is no third. Written as
    an INDEX rather than a per-address lookup because both callers ask about
    many addresses over one run, and the old shape reopened all 169 cards per
    question.

    Parity with `merge_entity_and_relationship` is deliberate: the entity's
    `other_emails` list is NOT indexed, because the canonical reader does not
    use it either and one reader disagreeing with another about who owns an
    address is the defect this replaces. It is also empty on every one of the
    165 address-book entities today, so indexing it would be speculative.
    Widening both readers together is a separate, deliberate change.
    """
    if contacts_dir is None:
        contacts_dir = get_crm_contacts_dir()
    index: dict[str, dict] = {}
    if not Path(contacts_dir).is_dir():
        return index

    for card in sorted(Path(contacts_dir).glob("*.md")):
        try:
            relationship, _body = _parse_frontmatter(card.read_text(encoding="utf-8"))
        except OSError:
            continue
        if not relationship:
            continue
        entity = resolve_entity_ref(relationship, workspace_root=workspace_root)
        merged = merge_entity_and_relationship(entity, relationship)
        # Inline wins when both are present: it is the per-relationship view,
        # and `merge_entity_and_relationship` already gives the entity's
        # `canonical_email` to `merged["email"]` when there is no inline one.
        address = str(relationship.get("email") or merged.get("email") or "").strip()
        if not address:
            continue
        index.setdefault(address.lower(), merged)
    return index


def merge_entity_and_relationship(entity: dict, relationship: dict) -> dict:
    """Merge biographical facts from entity with per-exec view from relationship.

    Returns a flat dict that mimics the legacy contact shape (name, company,
    email, type, last_touch, cadence, ...) so downstream consumers (crm-health,
    aggregate-crm) can render without caring about the two-tier structure.

    Relationship wins for: type (was relationship_type), cadence, last_touch,
    status, source, tags. Entity wins for: name, company (was employer), email
    (was canonical_email), linkedin, telegram, phone, region, timezone.

    When `entity` is None (dangling entity_ref or missing address-book file),
    entity-side fields default to empty string so the dict always has a
    consistent shape for downstream consumers.
    """
    merged: dict = {
        "name": "",
        "company": "",
        "email": "",
        "linkedin": "",
        "telegram": "",
        "phone": "",
        "region": "",
        "timezone": "",
    }
    if entity:
        merged["name"] = entity.get("name", "")
        merged["company"] = entity.get("employer", "")
        # Fall back to the card's inline address when the entity carries none.
        # The migration is supposed to move the address onto the entity, and on
        # four live contacts it did not: the address-book record exists, its
        # `canonical_email` is empty, and the real address is still sitting on
        # the relationship card. Taking the entity's value unconditionally
        # reported those four as having NO email at all, to CRM health, the
        # dashboard, `aggregate-crm` and `/cold-sweep`, which drafts outreach
        # and would have had nowhere to send it. Measured 2026-08-29:
        # four cards, every one `entity_found=True canonical_email=''`.
        #
        # The entity still WINS when it has a value, so the two-tier model is
        # unchanged; this only stops an empty entity field erasing an address
        # the workspace already knows.
        merged["email"] = entity.get("canonical_email", "") or relationship.get("email", "")
        merged["linkedin"] = entity.get("linkedin", "")
        merged["telegram"] = entity.get("telegram", "")
        merged["phone"] = entity.get("phone", "")
        merged["region"] = entity.get("region", "")
        merged["timezone"] = entity.get("timezone", "")

    # Relationship overrides / adds
    merged["type"] = relationship.get("relationship_type", "")
    merged["last_touch"] = relationship.get("last_touch", "")
    merged["cadence"] = relationship.get("cadence", "")
    merged["status"] = relationship.get("status", "active")
    merged["source"] = relationship.get("source", "")
    # `frontmatter_list`, not a `[]` default: a card written with a bare
    # `tags:` parses to None through yaml.safe_load, and the default only
    # applies when the key is ABSENT. Every reader of merged["tags"] then
    # iterates None.
    merged["tags"] = frontmatter_list(relationship.get("tags"))
    merged["entity_ref"] = relationship.get("entity_ref", "")
    merged["pipeline_company"] = relationship.get("pipeline_company", "")
    merged["radar_freeze_until"] = relationship.get("radar_freeze_until", "")
    merged["owner"] = relationship.get("owner", "")
    # Carry the tribe-warning opt-out through the merge (relationship wins, then
    # entity) so entity_ref contacts can suppress the @31c.io false positive.
    merged["tribe_email_ok"] = relationship.get("tribe_email_ok", "") or (
        entity.get("tribe_email_ok", "") if entity else "")
    return merged


# ===========================================================================
# Pipeline-stage-aware cadence (Phase 2 of CRM action engine)
# ===========================================================================

STAGE_CADENCE = {
    "Lead": 14,
    "Qualified": 14,
    "Demo/POC": 7,    # canonical stage string in context/pipeline.md
    "Demo": 7,        # accept either spelling for forward-compat
    "Proposal": 7,
    "Negotiation": 3,
    "Won": 0,         # 0 = no tracking
    "Lost": 0,
}


def parse_pipeline_stages(pipeline_path: Path) -> dict:
    """Parse context/pipeline.md and return {company_name_lowercase: stage_name}.

    Pipeline.md uses a markdown table with a "Company" column and a "Stage"
    column (verified against context/pipeline.md as of 2026-05-16). Stage values
    are canonical: Lead, Qualified, Demo/POC, Proposal, Negotiation, Won, Lost.

    Parser scans for the header row containing both "Company" and "Stage",
    then extracts subsequent table rows until a non-table line ends the table.

    Note: pipeline.md Company cells may contain contact-name parentheticals
    (e.g., "ExampleTelco (Adrian Cole)"). These are stripped at parse time
    to produce clean canonical keys that match crm/aliases.md entries.
    """
    if not pipeline_path.exists():
        return {}
    text = pipeline_path.read_text(encoding="utf-8")
    stages: dict = {}
    in_table = False
    headers: list = []
    for line in text.split("\n"):
        if line.startswith("|") and "Company" in line and "Stage" in line:
            in_table = True
            headers = [c.strip().lower() for c in line.split("|") if c.strip()]
            continue
        if in_table and line.startswith("|---"):
            continue
        if in_table and line.startswith("|"):
            cells = [c.strip() for c in line.split("|")[1:-1]]
            if len(cells) < len(headers):
                continue
            try:
                company_idx = headers.index("company")
                stage_idx = headers.index("stage")
            except ValueError:
                in_table = False
                continue
            company = cells[company_idx]
            stage = cells[stage_idx]
            # Strip parenthetical contact-name suffix: "ExampleTelco (Adrian Cole)"
            # -> "ExampleTelco". pipeline.md uses parens to disambiguate WHICH contact
            # at a given company; the company name proper precedes the paren.
            company_clean = re.sub(r"\s*\([^)]*\)\s*$", "", company).strip()
            if company_clean and stage:
                stages[company_clean.lower()] = stage
        elif in_table and not line.startswith("|"):
            in_table = False
    return stages


def parse_aliases(aliases_path: Path) -> dict:
    """Parse crm/aliases.md and return {variant_lowercase: canonical_lowercase}."""
    if not aliases_path.exists():
        return {}
    text = aliases_path.read_text(encoding="utf-8")
    aliases: dict = {}
    current_canonical = None
    in_aliases_section = False
    for line in text.split("\n"):
        if line.strip() == "## Aliases":
            in_aliases_section = True
            continue
        if not in_aliases_section:
            continue
        if line.startswith("### "):
            current_canonical = line[4:].strip().lower()
            aliases[current_canonical] = current_canonical
        elif line.startswith("- ") and current_canonical:
            variant = line[2:].strip().lower()
            aliases[variant] = current_canonical
    return aliases


def compute_stage_aware_cadence(
    relationship_type: str,
    pipeline_company: str,
    stages: dict,
    aliases: dict,
    type_default: int,
) -> int:
    """Compute the effective cadence for a contact.

    Order of precedence (highest to lowest):
      1. If pipeline_company resolves to a Won/Lost stage -> 0 (no tracking)
      2. Pipeline stage override (when company matches)
      3. Type-default cadence (caller's fallback)

    Note: relationship_type is reserved for future use (e.g., "tribe types
    always return 0 regardless of pipeline stage"). Currently the function
    does NOT consume this parameter -- caller computes type_default from the
    type-cadence table and passes it as the explicit fallback.
    """
    if not pipeline_company:
        return type_default
    company_norm = pipeline_company.lower().strip()
    # Resolve through aliases
    canonical = aliases.get(company_norm, company_norm)
    stage = stages.get(canonical)
    if stage is None:
        # Try exact match on the original
        stage = stages.get(company_norm)
    if stage is None:
        return type_default
    return STAGE_CADENCE.get(stage, type_default)


# ===========================================================================
# Dormancy detection (Phase 2)
# ===========================================================================

# Types excluded from dormancy auto-demote (tribe relationships, dormant/won/lost contacts)
DORMANCY_EXCLUDED_TYPES = {"tribe", "tribe-leadership", "shareholder", "inactive"}
DORMANCY_EXCLUDED_STATUSES = {"dormant", "won", "lost", "blocked", "off-limits"}


def find_dormancy_candidates(contacts: list, today=None, threshold_days: int = 90) -> list:
    """Identify contacts that should be auto-demoted to dormant.

    Criteria:
      - status == "active" (or unset, defaults to active)
      - type not in DORMANCY_EXCLUDED_TYPES
      - last_touch older than threshold_days days

    Returns the subset that meet all criteria. CEO approves the batch
    before any status flip happens (this function only proposes).
    """
    if today is None:
        today = datetime.now(get_default_tz()).date()
    candidates = []
    for c in contacts:
        if c.get("status", "active") in DORMANCY_EXCLUDED_STATUSES:
            continue
        if c.get("type", "") in DORMANCY_EXCLUDED_TYPES:
            continue
        lt_str = c.get("last_touch", "")
        if not lt_str:
            continue
        try:
            lt = date.fromisoformat(lt_str)
        except (ValueError, TypeError):
            continue
        delta = (today - lt).days
        if delta >= threshold_days:
            # Shallow-copy before mutating: avoids surprising the caller whose
            # `contacts` list still holds the original dict references.
            c_copy = dict(c)
            c_copy["days_silent"] = delta
            candidates.append(c_copy)
    return candidates
