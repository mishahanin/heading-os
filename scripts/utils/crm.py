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
- ``scan_contacts(config, today=None)`` - scan all contact files and
  return ``(contacts, tribe_warnings, dangling_refs)``.

Extracted from scripts/crm-health.py in Phase 6.1 of the 2026-05-12
workspace performance tune-up. Behaviour is preserved byte-for-byte.
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
    get_default_tz,
    get_crm_contacts_dir,
    get_corporate_root,
    is_exec_workspace,
)
from scripts.utils.markdown import parse_frontmatter_str as _parse_frontmatter  # noqa: E402


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


def is_radar_frozen(radar_freeze_until, today=None) -> bool:
    """True if a contact is inside an active radar-freeze window.

    Accepts an ISO date (``YYYY-MM-DD``) or full ISO datetime string. Empty or
    unparseable values mean not frozen. Matches the freeze semantics already
    honored by cold_sweep_core.route() and crm_next.rank_candidates().
    """
    if not radar_freeze_until or not str(radar_freeze_until).strip():
        return False
    if today is None:
        today = datetime.now(get_default_tz()).date()
    raw = str(radar_freeze_until).strip()
    try:
        freeze = datetime.fromisoformat(raw).date()
    except ValueError:
        try:
            freeze = date.fromisoformat(raw)
        except ValueError:
            return False
    return freeze > today


def _get_contact_files(contacts_dir: Path) -> list:
    """Collect all contact .md files from personal and corporate directories."""
    seen = set()
    files = []
    # Personal contacts first (take precedence)
    if contacts_dir.exists():
        for f in sorted(contacts_dir.glob("*.md")):
            seen.add(f.name)
            files.append(f)
    # Corporate contacts (Tribe members shared from CEO workspace)
    if is_exec_workspace():
        corp_crm = get_corporate_root() / "crm" / "contacts"
        if corp_crm.exists():
            for f in sorted(corp_crm.glob("*.md")):
                if f.name not in seen:
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
    # Resolve paths relative to workspace_root (test fixture support) or the
    # canonical workspace root when called in production.
    _ws_root = Path(workspace_root) if workspace_root else _WORKSPACE_ROOT
    _stages = parse_pipeline_stages(_ws_root / "context" / "pipeline.md")
    _aliases = parse_aliases(_ws_root / "crm" / "aliases.md")

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
        cadence_override = fm.get("cadence", "")
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
            if cadence_override:
                cadence = int(cadence_override)
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
            # Won/Lost -> cadence 0 -> skip time-based tracking (gray)
            if cadence == 0:
                commitments = parse_commitments(content)
                contacts.append({
                    "name": name,
                    "company": company,
                    "email": email,
                    "type": rel_type,
                    "stage": stage,
                    "last_touch": last_touch,
                    "cadence": 0,
                    "health": "gray",
                    "days_since": None,
                    "commitments": commitments,
                    "file": file_path.name,
                    "slug": file_path.stem,
                    "status": fm.get("status", "active"),
                    "radar_freeze_until": radar_freeze_until,
                })
                continue
            # Recalculate yellow/red proportionally when cadence changed
            if cadence != type_cadence:
                yellow = max(1, round(yellow * cadence / max(type_cadence, 1)))
                red = cadence
        elif cadence_override:
            cadence = int(cadence_override)
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
            if cadence == 0:
                commitments = parse_commitments(content)
                contacts.append({
                    "name": name,
                    "company": company,
                    "email": email,
                    "type": rel_type,
                    "stage": stage,
                    "last_touch": last_touch,
                    "cadence": 0,
                    "health": "gray",
                    "days_since": None,
                    "commitments": commitments,
                    "file": file_path.name,
                    "slug": file_path.stem,
                    "status": fm.get("status", "active"),
                    "radar_freeze_until": radar_freeze_until,
                })
                continue
            yellow = max(1, round(cadence * 0.7))
            red = cadence

        health, days = calculate_health(last_touch, cadence, yellow, red, today=today)
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
    return parse_frontmatter(text)


def resolve_entity_ref(relationship_record: dict, workspace_root: Path | None = None) -> dict | None:
    """Given a relationship record dict, load its linked entity. Returns None if
    entity_ref is missing or the linked entity does not exist."""
    slug = relationship_record.get("entity_ref")
    if not slug:
        return None
    return load_entity(slug, workspace_root=workspace_root)


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
        merged["email"] = entity.get("canonical_email", "")
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
    merged["tags"] = relationship.get("tags", [])
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
