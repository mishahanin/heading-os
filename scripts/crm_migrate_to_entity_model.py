#!/usr/bin/env python3
"""crm_migrate_to_entity_model.py -- One-shot migration from legacy contact files
to the two-tier address book + relationship record model.

Workflow:
  1. Scan all execs' contact files (CEO + per-exec via aggregate-crm logic).
  2. Group records by likely-same-entity (exact email -> high confidence;
     name+employer fuzzy -> low confidence requiring manual review).
  3. Generate proposed migration map at outputs/operations/crm/2026-05-15_migration-map.md
     for CEO review. CEO inspects, flags any mis-groupings, approves.
  4. On approval, generate address book entries (one per group) and rewrite
     THE CEO'S contact files as thin relationship records. Exec-owned records
     are read for grouping (two execs' files are how a duplicate is found at
     all) and are NOT written: this script has no write path into another
     person's repository, and inventing one would edit files their owner has
     not reviewed. `--apply` reports the exec records it left alone, because
     they stay legacy-shaped and are re-grouped on every later `--propose` --
     the run is idempotent for the CEO's side only. This step used to say
     "each contact file", which named a behaviour the code has never had.
  5. All writes go to crm/.migration-staging/; only renamed into place after
     every file passes validation. Backup at crm/.migration-backup/<date>/.

Usage:
  python3 scripts/crm_migrate_to_entity_model.py --propose    # generate review map only
  python3 scripts/crm_migrate_to_entity_model.py --apply      # apply the proposed map (after review)
  python3 scripts/crm_migrate_to_entity_model.py --rollback   # restore from backup

Tests: tests/test_a_rollback_that_deleted_what_it_never_backed_up.py
"""

# For --apply and --rollback: os (chmod, for Windows read-only bits), shutil
# (rmtree for staging and backup cleanup), stat (S_IWRITE), json (the
# applied-manifest that makes rollback symmetric).
#
# This comment said json was "for migration map writing" and that was never
# true: the review map is markdown, written by `write_review_map`, and until
# 2026-08-24 no `json.` call existed anywhere in the file. A reader trusting it
# went looking for a JSON map path that did not exist.
import argparse
import contextlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.utils.atomic import atomic_write_text
from scripts.utils.crm import parse_frontmatter
from scripts.utils.workspace import (
    get_workspace_root,
    get_all_active_exec_slugs,
    get_per_exec_contacts_dir,
    get_outputs_dir,
    get_crm_contacts_dir,
)


# ============================================================
# Slug & Normalization Helpers
# ============================================================
def generate_slug(name: str, existing: set | None = None) -> str:
    """Convert a full name to kebab-case slug. Suffix on collision."""
    base = re.sub(r"[^a-z0-9\s-]", "", name.lower().strip())
    base = re.sub(r"\s+", "-", base)
    base = re.sub(r"-+", "-", base).strip("-")
    if not base:
        base = "unnamed"  # guard: empty or all-non-ASCII canonical name
    if existing is None or base not in existing:
        return base
    n = 2
    while f"{base}-{n}" in existing:
        n += 1
    return f"{base}-{n}"


def _normalize_email(email: str) -> str:
    return (email or "").strip().lower()


def _normalize_name(name: str) -> str:
    return re.sub(r"\s+", " ", (name or "").strip().lower())


def _normalize_company(company: str) -> str:
    # Collapse common variants: "AllianceCo" vs "AllianceCo", "Acme Inc." vs "Acme"
    # Coverage gap: doesn't strip Co., AG, S.A., Pte., K.K. - no live records
    # use these suffixes today. Extend if needed when exec workspaces grow.
    # Also normalize hyphens to spaces so "Acme-Holdings" == "Acme Holdings"
    base = (company or "").strip().lower()
    base = re.sub(r"[.,]", "", base)
    base = re.sub(r"\s+(inc|llc|gmbh|ltd|limited|corp|corporation)\.?$", "", base)
    base = re.sub(r"[-]", " ", base)  # treat hyphens as spaces for company matching
    base = re.sub(r"\s+", " ", base).strip()
    return base


# ============================================================
# Migration Logic: Entity Grouping
# ============================================================
def group_records(records: list[dict]) -> list[dict]:
    """Group records by likely-same-entity.

    Strategy:
      1. Exact email match -> high confidence group.
      2. Same normalized name AND exactly equal normalized company (after
         hyphen/whitespace/suffix normalization) -> low confidence group,
         flagged for manual review.
      3. Singleton groups for everyone else.

    Returns: list of dicts: {"records": [...], "confidence": "high|low|singleton",
                              "proposed_slug": str, "canonical_name": str}.
    """
    by_email: dict = {}
    no_email: list = []
    for rec in records:
        email = _normalize_email(rec.get("email", ""))
        if email:
            by_email.setdefault(email, []).append(rec)
        else:
            no_email.append(rec)

    groups: list = []

    # Pass 1: high-confidence by email
    for email, rec_list in by_email.items():
        if len(rec_list) >= 2:
            groups.append({
                "records": rec_list,
                "confidence": "high",
                "canonical_name": _canonical_name(rec_list),
                "proposed_slug": None,  # filled in by caller after collision check
            })
        else:
            groups.append({
                "records": rec_list,
                "confidence": "singleton",
                "canonical_name": rec_list[0].get("name", ""),
                "proposed_slug": None,
            })

    # Cross-pass limitation: records in no_email are NOT compared against
    # Pass-1 singletons. A person stored as {name: "X", email: "x@y.com"} by
    # one exec and {name: "X", email: ""} by another will emerge as two
    # separate singleton groups. The --propose output (Task 0.12) surfaces
    # this so the CEO can manually merge during review.

    # Pass 2: low-confidence by name+company for records without email
    name_groups: dict = {}
    # A record with no email AND no usable name still has to come out
    # somewhere. `continue` dropped it: it entered no group, so every count in
    # the review map -- all of which derive from `groups` -- was computed as if
    # it did not exist, and `--apply` backed it up, never migrated it, and
    # never removed it. An unmigrated legacy file the summary says is not
    # there. It becomes its own singleton, which is what the docstring above
    # has always promised for "everyone else".
    nameless: list = []
    for rec in no_email:
        key = (_normalize_name(rec.get("name", "")), _normalize_company(rec.get("company", "")))
        if not key[0]:
            nameless.append(rec)
            continue
        name_groups.setdefault(key, []).append(rec)

    for rec in nameless:
        groups.append({
            "records": [rec],
            "confidence": "singleton",
            "canonical_name": rec.get("name", ""),
            "proposed_slug": None,
        })

    for key, rec_list in name_groups.items():
        if len(rec_list) >= 2:
            groups.append({
                "records": rec_list,
                "confidence": "low",
                "canonical_name": _canonical_name(rec_list),
                "proposed_slug": None,
            })
        else:
            groups.append({
                "records": rec_list,
                "confidence": "singleton",
                "canonical_name": rec_list[0].get("name", ""),
                "proposed_slug": None,
            })

    return groups


def _canonical_name(records: list[dict]) -> str:
    """Pick the canonical name from a group. Longest non-empty name wins (more complete)."""
    names = [r.get("name", "") for r in records if r.get("name")]
    if not names:
        return ""
    return max(names, key=len)


# ============================================================
# Data Loading: Scan CRM Sources
# ============================================================
def _record_from(file_path: Path, owner: str, fm: dict) -> dict:
    """One scanned contact, in the shape render_relationship_record expects.

    Module scope, not a closure inside scan_all_contacts: a nested function
    cannot be tested, and the field-drop this shape once caused is exactly the
    kind of bug a test catches and a reading does not.
    """
    return {
        "owner": owner,
        "file_path": str(file_path),
        "name": fm.get("name", ""),
        "email": fm.get("email", ""),
        "company": fm.get("company", ""),
        "type": fm.get("type", ""),
        "linkedin": fm.get("linkedin", ""),
        "phone": fm.get("phone", ""),
        "region": fm.get("region", ""),
        "timezone": fm.get("timezone", ""),
        "last_touch": fm.get("last_touch", ""),
        "source": fm.get("source", ""),
        # render_relationship_record reads these; omitting them here meant its
        # `if cadence not in (None, "", 0)` never fired and the 2026-05-15 run
        # silently stripped cadence from about a hundred live contacts.
        # A field the renderer reads must be a field the scan carries.
        "cadence": fm.get("cadence"),
        "radar_freeze_until": fm.get("radar_freeze_until", ""),
    }


def _scan_summary(records: list[dict], unreadable: list[str]) -> str:
    """The scan line, naming the execs it could NOT read.

    Both callers printed "Scanned N records across all execs" whatever the scan
    reached. One shared line so the two cannot say different things about the
    same scan, which is how one of them would be fixed and the other left.
    """
    if not unreadable:
        return f"Scanned {len(records)} records across all execs."
    return (f"Scanned {len(records)} records, but NOT across all execs: "
            f"{len(unreadable)} contacts directory(ies) are absent on this "
            f"machine ({', '.join(sorted(unreadable))}). Records owned by them "
            f"are missing from this map.")


def scan_all_contacts() -> tuple[list[dict], list[str]]:
    """Scan the CEO's crm/contacts/ plus every active exec's contacts directory.

    Returns ``(records, unreadable_slugs)``. A record is a flat dict with:
    owner (slug), name, email, company, type, file_path.

    The second half of that tuple is the point. An exec whose data overlay is
    not cloned on this machine is skipped, which is correct - and both callers
    then printed "Scanned N records across all execs", which was not. A
    migration map built from three of five execs, described as covering all
    five, merges the wrong records and splits people who should have merged.
    The number of directories that could not be read is reported beside the
    record count so the operator can tell one run from the other.

    Note: 31c-crm-central is DEPRECATED (per scripts/setup.py:351-364). The
    canonical exec source is each exec's own data overlay at
    ``../.heading-os-data-{slug}/crm/contacts/`` - the path in the code below,
    not the retired ``../31c-crm-{slug}/`` this docstring named until 2026-08-27.
    """
    records = []
    unreadable: list[str] = []

    # CEO contacts at crm/contacts/
    ceo_dir = get_crm_contacts_dir()
    for f in sorted(ceo_dir.glob("*.md")):
        fm = parse_frontmatter(f.read_text(encoding="utf-8"))
        if not fm:
            continue
        records.append(_record_from(f, "owner-exec-a", fm))

    # Per-exec CRM contacts at ../.heading-os-data-{slug}/crm/contacts/.
    # Both halves of that path were wrong here: the comment named the retired
    # `31c-crm-{slug}` repo, and the join sat one level above the files (fixed
    # 2026-08-23, see tests/test_per_exec_contacts_dir.py).
    # get_all_active_exec_slugs() reads the fleet roster and already excludes
    # the admin/CEO role; load_admin_config() is a different structure and is
    # not suitable here.
    exec_slugs = get_all_active_exec_slugs()
    for slug in exec_slugs:
        exec_contacts_dir = get_per_exec_contacts_dir(slug)
        if not exec_contacts_dir.exists():
            unreadable.append(slug)
            continue
        for f in sorted(exec_contacts_dir.glob("*.md")):
            fm = parse_frontmatter(f.read_text(encoding="utf-8"))
            if not fm:
                continue
            records.append(_record_from(f, slug, fm))

    return records, unreadable


# ============================================================
# Output / Writing: Slug Assignment + Review Map
# ============================================================
def assign_slugs(groups: list[dict]) -> list[dict]:
    """Assign proposed_slug to each group, avoiding collisions."""
    existing: set = set()
    for g in groups:
        slug = generate_slug(g["canonical_name"], existing=existing)
        g["proposed_slug"] = slug
        existing.add(slug)
    return groups


def write_review_map(groups: list[dict]) -> Path:
    """Write the migration map for CEO review. Returns the output path."""
    out_dir = get_outputs_dir() / "operations" / "crm"
    out_dir.mkdir(parents=True, exist_ok=True)
    today = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d")
    out_file = out_dir / f"{today}_migration-map.md"

    lines = [
        "# CRM Migration Map - proposed entity groupings",
        "",
        f"Generated: {today}",
        f"Total groups: {len(groups)}",
        f"  High confidence: {sum(1 for g in groups if g['confidence'] == 'high')}",
        f"  Low confidence (review): {sum(1 for g in groups if g['confidence'] == 'low')}",
        f"  Singletons: {sum(1 for g in groups if g['confidence'] == 'singleton')}",
        "",
        "## Review instructions",
        "",
        "Inspect each LOW-confidence group below. If a grouping is wrong, edit the SOURCE contact files"
        " (e.g., to remove a mis-matched email or change a name field) and re-run `--propose` to regenerate"
        " this map. The `--apply` workflow re-scans the live workspace -- it does NOT parse this map for"
        " grouping decisions.",
        "",
        "Note: a contact recorded with an email by one executive and without an email by"
        " another will appear as two separate singletons in the list below. Scan the"
        " singleton list for near-duplicate names and merge manually if needed.",
        "",
        "## High-confidence groups (auto-merge on --apply)",
        "",
    ]
    for g in [g for g in groups if g["confidence"] == "high"]:
        lines.append(f"### {g['proposed_slug']} - {g['canonical_name']}")
        for r in g["records"]:
            lines.append(
                f"- {r['owner']} | {r['name']} | {r['company']} | {r['email']} | {r['file_path']}"
            )
        lines.append("")

    lines.append("## Low-confidence groups (review before --apply)")
    lines.append("")
    for g in [g for g in groups if g["confidence"] == "low"]:
        lines.append(f"### {g['proposed_slug']} - {g['canonical_name']} **REVIEW**")
        for r in g["records"]:
            lines.append(
                f"- {r['owner']} | {r['name']} | {r['company']} | {r['email']} | {r['file_path']}"
            )
        lines.append("")

    lines.append("## Singletons (one-to-one migration)")
    lines.append("")
    for g in [g for g in groups if g["confidence"] == "singleton"]:
        r = g["records"][0]
        lines.append(
            f"- {g['proposed_slug']} ({r['owner']}) | {r['name']} | {r['company']}"
        )

    out_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out_file


# ============================================================
# Command: --propose
# ============================================================
def cmd_propose() -> int:
    """Implement the --propose workflow."""
    records, unreadable = scan_all_contacts()
    print(_scan_summary(records, unreadable))
    groups = group_records(records)
    groups = assign_slugs(groups)
    out_file = write_review_map(groups)
    print(f"Migration map written: {out_file}")
    print(f"  Total groups: {len(groups)}")
    high = sum(1 for g in groups if g["confidence"] == "high")
    low = sum(1 for g in groups if g["confidence"] == "low")
    singletons = sum(1 for g in groups if g["confidence"] == "singleton")
    print(f"  High-confidence merges: {high}")
    print(f"  Low-confidence (review): {low}")
    print(f"  Singletons: {singletons}")
    print()
    print("Review the map. When satisfied, run:")
    print("  python3 scripts/crm_migrate_to_entity_model.py --apply")
    return 0


# ============================================================
# Configuration: Canonical Owner Policy
# ============================================================
# Canonical-owner policy. From the design spec - see "Canonical Owner Policy" section.
# ORDER MATTERS: higher-priority (more senior) types come first. When a contact
# has multiple types across exec records, the type with the LOWEST index wins.
# CEO-relationship types (investor, tribe, government) precede commercial types
# (prospect, partner) which precede ecosystem types (vendor, service-provider).
CANONICAL_OWNER_POLICY = {
    "investor-active": "owner-exec-a",
    "investor-passive": "owner-exec-a",
    "investor-declined": "owner-exec-a",
    "shareholder": "owner-exec-a",
    "tribe-leadership": "owner-exec-a",
    "tribe": "owner-exec-a",
    "government": "owner-exec-a",
    "regulator": "owner-exec-a",
    "advisor": "owner-exec-a",
    "media": "owner-exec-a",
    "press": "owner-exec-a",
    "prospect": "owner-exec-b",
    "customer": "owner-exec-b",
    "partner-active": "owner-exec-b",
    "partner": "owner-exec-b",
    "partner-channel": "owner-exec-b",
    "reseller": "owner-exec-b",
    "prospect-partner": "owner-exec-b",
    "ecosystem": "owner-exec-c",
    "service-provider": "owner-exec-c",
    "vendor": "owner-exec-c",
    "inactive": "owner-exec-a",
    "lead": "owner-exec-a",
    "external": "owner-exec-a",
}


# ============================================================
# Canonical Owner & Record Selection
# ============================================================
def pick_canonical_owner(records: list[dict]) -> str:
    """Pick canonical owner for a group based on the most senior type in the group.

    Iterates all records, finds the type with the lowest index in
    CANONICAL_OWNER_POLICY (lowest index = highest priority), and returns
    its mapped owner. Defaults to owner-exec-a for unknown/missing types.
    """
    type_priority = list(CANONICAL_OWNER_POLICY.keys())
    best_type = ""
    best_idx = len(type_priority)
    for r in records:
        t = (r.get("type") or "").strip()
        if t in CANONICAL_OWNER_POLICY:
            idx = type_priority.index(t)
            if idx < best_idx:
                best_idx = idx
                best_type = t
    return CANONICAL_OWNER_POLICY.get(best_type, "owner-exec-a")


def pick_canonical_record(records: list[dict]) -> dict:
    """Among multiple records, pick the one whose body has the most biographical content.

    Heuristic: read each record's body, count length minus the Interaction Log section.
    Longest non-log body wins.
    """
    best = records[0]
    best_score = 0
    for r in records:
        path = Path(r["file_path"])
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        # Strip frontmatter
        if text.startswith("---"):
            text = text.split("---", 2)[-1]
        # Strip Interaction Log + Active Commitments
        for section_header in ("## Interaction Log", "## Active Commitments"):
            if section_header in text:
                text = text.split(section_header)[0]
        score = len(text.strip())
        if score > best_score:
            best_score = score
            best = r
    return best


def extract_body_sections(file_path: Path, exclude: list[str]) -> str:
    """Read a contact file body and return it with given section headers excluded.

    Excludes are matched by prefix (e.g. '## Interaction Log' matches that section
    and everything after until the next ## header at the same level).
    """
    from scripts.utils.markdown import parse_frontmatter_str
    if not file_path.exists():
        return ""
    text = file_path.read_text(encoding="utf-8")
    _fm, text = parse_frontmatter_str(text)

    lines = text.split("\n")
    out: list = []
    skip = False
    for line in lines:
        if line.startswith("## "):
            skip = any(line.strip().startswith(prefix) for prefix in exclude)
        if not skip:
            out.append(line)
    return "\n".join(out).strip()


# YAML 1.1 resolves every one of these to a boolean or a null, not a string.
_PLAIN_UNSAFE = frozenset({
    "y", "n", "yes", "no", "true", "false", "on", "off",
    "null", "~", "none",
})


def _looks_numeric(s: str) -> bool:
    """True when a YAML reader would hand this back as a number, not a string."""
    try:
        float(s)
    except ValueError:
        return False
    return True


def _yaml_quote(value: str) -> str:
    """Quote a string value if it contains YAML-special characters.

    Existing 110 contacts have no special characters in name/employer/region fields,
    but future contacts might. This is a defensive guard.
    """
    if value is None:
        return ""
    s = str(value)
    if not s:
        return ""
    # Characters that require quoting in YAML scalar context
    specials = set(":#[]{}\"'&*?|>!%@`")
    # ...and whole WORDS that a YAML 1.1 reader resolves to something that is
    # not a string. The guard tested characters only, so a region of `NO`
    # (Norway) came back as False, `~` as None, and `007` as an int -- for
    # exactly the name/employer/region fields the docstring above says it
    # protects. `_PLAIN_UNSAFE` is the YAML 1.1 boolean/null set; the numeric
    # test is separate because no word list can cover every number.
    if s.lower() in _PLAIN_UNSAFE or _looks_numeric(s) or any(c in specials for c in s):
        # Use double-quoted form, escape internal double quotes
        return '"' + s.replace('\\', '\\\\').replace('"', '\\"') + '"'
    return s


# ============================================================
# Schema Definitions: Address Book & Relationship Record Rendering
# ============================================================
def render_address_book_entry(group: dict) -> str:
    """Render the address book entity markdown content."""
    canonical = pick_canonical_record(group["records"])
    canonical_owner = pick_canonical_owner(group["records"])
    today = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d")

    emails = set()
    other_emails: list = []
    for r in group["records"]:
        e = (r.get("email") or "").strip()
        if e:
            emails.add(e)
    canonical_email = canonical.get("email") or (sorted(emails)[0] if emails else "")
    for e in sorted(emails):
        if e and e != canonical_email:
            other_emails.append(e)

    aliases = sorted({r["name"] for r in group["records"] if r.get("name") and r["name"] != group["canonical_name"]})
    employer_aliases = sorted({r["company"] for r in group["records"] if r.get("company") and r["company"] != canonical.get("company", "")})

    fm_lines = [
        "---",
        f"slug: {group['proposed_slug']}",
        f"name: {_yaml_quote(group['canonical_name'])}",
    ]
    if aliases:
        fm_lines.append("aliases:")
        for a in aliases:
            fm_lines.append(f"  - {_yaml_quote(a)}")
    fm_lines.append(f"canonical_email: {_yaml_quote(canonical_email)}")
    if other_emails:
        fm_lines.append("other_emails:")
        for e in other_emails:
            fm_lines.append(f"  - {_yaml_quote(e)}")
    fm_lines.append(f"phone: \"{canonical.get('phone', '')}\"")
    fm_lines.append(f"linkedin: {canonical.get('linkedin', '')}")
    fm_lines.append(f"telegram: \"\"")
    employer_val = _yaml_quote(canonical.get('company', '') or 'Unknown')
    fm_lines.append(f"employer: {employer_val}")
    if employer_aliases:
        fm_lines.append("employer_aliases:")
        for a in employer_aliases:
            fm_lines.append(f"  - {_yaml_quote(a)}")
    fm_lines.append(f"title: \"\"")
    fm_lines.append(f"region: {_yaml_quote(canonical.get('region', ''))}")
    fm_lines.append(f"timezone: {_yaml_quote(canonical.get('timezone', ''))}")
    fm_lines.append(f"operating_timezone: \"\"")
    fm_lines.append(f"canonical_owner: {canonical_owner}")
    fm_lines.append(f"created: {today}")
    fm_lines.append(f"last_updated: {today}")
    fm_lines.append("---")
    fm_lines.append("")
    fm_lines.append(f"# {group['canonical_name']}")
    fm_lines.append("")

    # Body = lifted biographical content from canonical record
    body = extract_body_sections(
        Path(canonical["file_path"]),
        exclude=["## Interaction Log", "## Active Commitments", "## Linked Records"],
    )
    fm_lines.append(body)
    return "\n".join(fm_lines) + "\n"


def render_relationship_record(record: dict, entity_slug: str) -> str:
    """Render the slimmed relationship record for a given exec's view."""
    today_rec = record.get("last_touch") or datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d")
    created = today_rec
    fm = [
        "---",
        f"entity_ref: {entity_slug}",
        # Through _yaml_quote, like every field in render_address_book_entry.
        # These three were interpolated raw, so a company of `Holdings: Europe`
        # or a source of `A # B` produced frontmatter that is invalid YAML or
        # parses to the wrong value -- aborting the staged validation mid-run,
        # after the backup and before the rename, or landing mis-parsed in a
        # live record if the validator is lenient.
        f"relationship_type: {_yaml_quote(record.get('type', ''))}",
        f"last_touch: {today_rec}",
        f"created: {created}",
    ]
    cadence = record.get("cadence")
    if cadence not in (None, "", 0):
        fm.append(f"cadence: {cadence}")
    if record.get("source"):
        fm.append(f"source: {_yaml_quote(record['source'])}")
    fm.append("status: active")
    fm.append("tags: []")
    if record.get("company"):
        fm.append(f"pipeline_company: {_yaml_quote(record['company'])}")
    fm.append(f"radar_freeze_until: \"{record.get('radar_freeze_until') or ''}\"")
    fm.append(f"owner: {record['owner']}")
    fm.append("---")
    fm.append("")
    fm.append(f"# {record.get('name', '')} ({record['owner']})")
    fm.append("")

    # Lift Active Commitments + Interaction Log from original file
    body = extract_body_sections(
        Path(record["file_path"]),
        exclude=[],  # keep everything; the new render is body-stripped at top
    )
    # Filter to just the two sections we want
    keep: list = []
    in_keep = False
    for line in body.split("\n"):
        if line.startswith("## "):
            in_keep = line.strip() in ("## Active Commitments", "## Interaction Log")
        if in_keep:
            keep.append(line)
    fm.append("\n".join(keep))
    return "\n".join(fm) + "\n"


# ============================================================
# Command: --apply
# ============================================================
def cmd_apply() -> int:
    """Implement the --apply workflow.

    Reads the most recent migration map (today's date), backs up current state,
    generates address book + relationship records in staging, then renames into
    place transactionally.
    """
    ws = get_workspace_root()  # engine root: subprocess cwd + backup relativity (ws.parent)
    crm_root = get_crm_contacts_dir().parent  # DATA crm/ root (.heading-os-data for the CEO)
    today = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d")
    # The MOST RECENT map, not today's. The documented workflow is propose ->
    # CEO reviews -> apply, and nothing in it says the review must finish
    # before midnight; a CEO who read the map the next morning got "Migration
    # map not found. Run --propose first." about a file sitting right there.
    # The date bought nothing either: --apply re-scans and re-groups from disk
    # and never parses the map, so requiring today's copy only forced a
    # pointless re-run.
    map_dir = get_outputs_dir() / "operations" / "crm"
    maps = sorted(map_dir.glob("*_migration-map.md")) if map_dir.is_dir() else []
    if not maps:
        print(f"Migration map not found in {map_dir}")
        print("Run --propose first.")
        return 1
    map_file = maps[-1]
    if not map_file.name.startswith(today):
        print(f"Applying against {map_file.name}, which is not from today. "
              f"--apply re-scans from disk, so re-run --propose first if the "
              f"contacts have changed since that map was written.")

    # Re-derive the groups by re-running the scan + group (deterministic).
    records, unreadable = scan_all_contacts()
    print(_scan_summary(records, unreadable))
    groups = group_records(records)
    groups = assign_slugs(groups)

    # Backup current state
    backup_dir = crm_root / ".migration-backup" / today
    backup_dir.mkdir(parents=True, exist_ok=True)
    for r in records:
        src = Path(r["file_path"])
        rel = src.relative_to(ws.parent) if src.is_relative_to(ws.parent) else Path(src.name)
        dst = backup_dir / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    # The pre-existing address book, which the loop above never touched: it
    # iterates `records`, and records are contact FILES. Rollback nonetheless
    # ran `rmtree` over the whole `crm/address-book/`, so any entry that
    # existed before the migration was destroyed with no copy anywhere. The
    # manifest already records exactly what apply created; the backup now
    # records what it found.
    existing_ab = crm_root / "address-book"
    if existing_ab.is_dir():
        shutil.copytree(existing_ab, backup_dir / "address-book-pre-existing",
                        dirs_exist_ok=True)
    print(f"Backup written to {backup_dir} ({len(records)} files)")

    # Exec-owned records are read for grouping and never written; say so, since
    # they stay legacy-shaped and re-group on every later --propose.
    foreign = [r for r in records if r["owner"] != "owner-exec-a"]
    if foreign:
        print(f"{len(foreign)} exec-owned record(s) were used for grouping and "
              f"are NOT migrated; their files are unchanged.")

    # Staging dir
    staging = crm_root / ".migration-staging"
    if staging.exists():
        shutil.rmtree(staging)
    address_book_staging = staging / "address-book"
    contacts_staging = staging / "contacts"
    address_book_staging.mkdir(parents=True)
    contacts_staging.mkdir(parents=True)

    # Generate address book entries (one per group) + per-record relationship records
    collisions: list[tuple[str, list[str]]] = []
    # staged filename -> the legacy file it was rendered from. Drives both the
    # legacy-file cleanup below and the rollback manifest.
    created_from: dict[str, str] = {}
    for g in groups:
        slug = g["proposed_slug"]
        if not slug:
            continue
        ab_text = render_address_book_entry(g)
        (address_book_staging / f"{slug}.md").write_text(ab_text, encoding="utf-8")

        # A group is "the same person"; it can legitimately hold two CEO-owned
        # legacy files for them, which is precisely what this migration exists
        # to merge. Every one of them rendered to the SAME
        # `contacts_staging/<slug>.md`, so the last write won and the earlier
        # record's Interaction Log and Active Commitments were gone. Validation
        # checks file SHAPE, not record count, so it passed: silent data loss
        # with a green checkmark, over the CEO's relationship history, in a
        # one-shot migration.
        #
        # This refuses rather than merging. Concatenating two interaction logs
        # is a judgement about what actually happened with a person, and a
        # migration should not make that judgement silently. The operator gets
        # both paths and merges by hand, then re-runs.
        owned = [r for r in g["records"] if r["owner"] == "owner-exec-a"]
        if len(owned) > 1:
            collisions.append((slug, [r["file_path"] for r in owned]))
            continue
        for r in owned:
            rel_text = render_relationship_record(r, slug)
            (contacts_staging / f"{slug}.md").write_text(rel_text, encoding="utf-8")
            created_from[f"{slug}.md"] = r["file_path"]

    if collisions:
        print("Aborting: two or more of your own contact files map to one slug.")
        print("Merging their Interaction Logs is a judgement about what happened "
              "with a person, so this refuses instead of picking one.\n")
        for slug, paths in collisions:
            print(f"  {slug}.md would be written from {len(paths)} files:")
            for p in paths:
                print(f"    {p}")
        print("\nMerge each set into one file, then re-run --propose and --apply.")
        shutil.rmtree(staging, ignore_errors=True)
        return 1

    # Validate every staged file against the new schemas
    val = subprocess.run(
        [sys.executable, "scripts/validate-crm-schema.py", "--dir", str(staging)],
        capture_output=True, text=True, cwd=str(ws),
    )
    if val.returncode != 0:
        print("Validation FAILED on staged files. Aborting apply.")
        print(val.stdout)
        print(val.stderr)
        return 1

    # Hidden-char scan on staging
    for staged in staging.rglob("*.md"):
        scan = subprocess.run(
            [sys.executable, "scripts/sanitize-text.py", str(staged), "--scan"],
            capture_output=True, text=True, cwd=str(ws),
        )
        if scan.returncode != 0:
            print(f"Hidden-char scan FAILED on {staged}. Aborting apply.")
            return 1

    final_ab = crm_root / "address-book"
    final_ab.mkdir(exist_ok=True)

    # WRITE-AHEAD. The manifest is what makes `--rollback` symmetric, and it used
    # to be written LAST - after both rename loops and after the legacy unlinks.
    # So a failure or a Ctrl-C anywhere inside the destructive phase left a
    # half-migrated tree and NO manifest, and nothing afterwards could tell a
    # half-applied migration from an un-applied one. Rollback then restored the
    # legacy files and left every slug-named file apply had already moved in,
    # which is the both-generations duplication the manifest exists to prevent,
    # in a state the operator believes is restored.
    #
    # An INTENT record is safe in the direction a rollback needs: it names
    # everything apply is about to create, so rollback removes files that may
    # never have appeared, which is a no-op. A manifest naming LESS than what
    # happened is the unsafe direction, and that is exactly what writing it last
    # produced. `removed_legacy` cannot be known in advance, so it stays empty
    # here and the completion stamp below fills it in.
    manifest_path = backup_dir / "applied-manifest.json"
    intended_ab = sorted(p.name for p in address_book_staging.glob("*.md"))
    intended_contacts = sorted(created_from)
    atomic_write_text(manifest_path, json.dumps({
        "status": "in_progress",
        "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "created_contacts": intended_contacts,
        "created_address_book": intended_ab,
        "removed_legacy": [],
    }, indent=2))

    # Rename staging -> final using os.replace (atomic on POSIX + Windows).
    # NOT shutil.move + target.unlink() - Windows raises PermissionError on
    # read-only attributes (corporate files may be marked read-only).
    for staged in address_book_staging.glob("*.md"):
        target = final_ab / staged.name
        if target.exists():
            # Clear read-only bit on Windows so os.replace can overwrite
            try:
                os.chmod(target, stat.S_IWRITE | stat.S_IREAD)
            except OSError:
                pass
        os.replace(str(staged), str(target))

    final_contacts = get_crm_contacts_dir()
    for staged in contacts_staging.glob("*.md"):
        target = final_contacts / staged.name
        if target.exists():
            try:
                os.chmod(target, stat.S_IWRITE | stat.S_IREAD)
            except OSError:
                pass
        os.replace(str(staged), str(target))

    # Remove the legacy files this migration replaced. The docstring says each
    # contact file is REWRITTEN as a thin relationship record, but the rename
    # above only moves the new slug-named files IN — and legacy names are
    # name-derived, not slug-derived, so `os.replace` never overwrote them.
    # `crm/contacts/` was left holding both generations: every downstream
    # consumer saw each contact twice, health scores double-counted, the radar
    # showed duplicate rows. The backup two steps up is what makes this safe,
    # and its existence is why the deletion was clearly meant to be here.
    removed: list[str] = []
    for staged_name, legacy_path in created_from.items():
        legacy = Path(legacy_path)
        if legacy.name == staged_name or not legacy.is_file():
            continue  # already replaced by the rename, or gone
        with contextlib.suppress(OSError):
            os.chmod(legacy, stat.S_IWRITE | stat.S_IREAD)
        try:
            legacy.unlink()
        except OSError as exc:
            print(f"Could not remove legacy file {legacy}: {exc}")
            continue
        removed.append(str(legacy))

    # Clean staging
    shutil.rmtree(staging, ignore_errors=True)

    # The completion stamp. The intent record was written before the first
    # rename (see above), so `--rollback` has something to work from even when
    # this line is never reached; this one narrows it to what actually happened
    # and marks the apply finished.
    manifest = {
        "status": "complete",
        "applied_at_utc": datetime.now(timezone.utc).isoformat(),
        "created_contacts": sorted(created_from),
        "created_address_book": sorted(p.name for p in final_ab.glob("*.md")),
        "removed_legacy": sorted(removed),
    }
    atomic_write_text(manifest_path, json.dumps(manifest, indent=2) + "\n")

    print(f"Migration applied. Address book at {final_ab} (~{len(list(final_ab.glob('*.md')))} entities).")
    print(f"Removed {len(removed)} legacy contact file(s); all are in the backup.")
    print(f"Backup at {backup_dir} (run --rollback to restore).")
    return 0


# ============================================================
# Command: --rollback
# ============================================================
def _apply_state(backup_dir: Path) -> str:
    """What the manifest in `backup_dir` says about the apply that wrote it.

    A half-applied migration and a complete one used to look identical from
    here, because the manifest was written only after the last unlink: an apply
    interrupted anywhere in the destructive phase left no manifest at all. It is
    now written BEFORE the first rename, marked `in_progress`, and stamped
    `complete` at the end - so this can say which one the operator is rolling
    back, which is the thing they most need to know before answering "yes".
    """
    path = backup_dir / "applied-manifest.json"
    if not path.exists():
        return ("Apply state: UNKNOWN - no manifest in this backup. It predates "
                "the manifest, or the apply died before writing one.")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return f"Apply state: UNREADABLE manifest ({exc})."
    status = data.get("status")
    if status == "complete":
        return "Apply state: complete."
    if status == "in_progress":
        return ("Apply state: INTERRUPTED. The apply that wrote this backup "
                "never finished, so the tree is part-migrated. The manifest "
                "lists what it INTENDED to create; rollback removes whichever "
                "of those exist.")
    return (f"Apply state: unrecognised status {status!r}. Treating the "
            f"manifest's lists as the intent record.")


def cmd_rollback() -> int:
    """Restore from the most recent backup directory."""
    ws = get_workspace_root()  # engine root: restore relativity (ws.parent)
    crm_root = get_crm_contacts_dir().parent  # DATA crm/ root (.heading-os-data for the CEO)
    backup_root = crm_root / ".migration-backup"
    if not backup_root.exists():
        print("No backup directory found.")
        return 1
    dates = sorted(d for d in backup_root.iterdir() if d.is_dir())
    if not dates:
        print("No backup snapshots found.")
        return 1
    latest = dates[-1]
    print(f"Restoring from {latest}...")
    print(_apply_state(latest))

    # Confirm
    resp = input("This will overwrite current crm/contacts/ and remove crm/address-book/. Confirm? [yes/no]: ").strip().lower()
    if resp != "yes":
        print("Aborted.")
        return 1

    # Remove ONLY the entries this migration created. `shutil.rmtree(ab)` took
    # the whole directory, including entries that predated the migration and
    # that the backup loop -- which iterates contact FILES -- never copied. A
    # rollback of a migration destroyed unrelated data the tool had recorded
    # precisely enough to spare. The manifest read below already exists for the
    # created contacts; `created_address_book` was written and never read.
    ab = crm_root / "address-book"
    manifest_path = latest / "applied-manifest.json"
    if ab.is_dir():
        if manifest_path.exists():
            try:
                created_ab = json.loads(
                    manifest_path.read_text(encoding="utf-8")
                ).get("created_address_book", [])
            except ValueError:
                created_ab = None
            if created_ab is None:
                print("Applied-manifest unreadable; leaving crm/address-book/ "
                      "in place rather than removing entries it did not create.")
            else:
                for name in created_ab:
                    with contextlib.suppress(OSError):
                        (ab / name).unlink()
                with contextlib.suppress(OSError):
                    ab.rmdir()   # only if the migration left it empty
        else:
            # A backup from before the manifest existed cannot say what apply
            # created, so it cannot say what is safe to delete either.
            print("No applied-manifest in this backup; leaving crm/address-book/ "
                  "in place. Remove it by hand if it is entirely migration output.")

    # Undo what --apply CREATED, before restoring what it replaced. Rollback
    # only ever restored, so the slug-named relationship records apply had
    # written survived it, and an apply-then-rollback cycle left both
    # generations in crm/contacts/ under the words "Rollback complete". The
    # manifest names exactly what to remove; without one (a backup from before
    # this fix) say so rather than guessing which files are which.
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except ValueError as exc:
            print(f"Applied-manifest unreadable ({exc}); aborting rather than "
                  f"restoring on top of files it should have removed.")
            return 1
        contacts_dir = get_crm_contacts_dir()
        for name in manifest.get("created_contacts", []):
            created = contacts_dir / name
            if not created.is_file():
                continue
            with contextlib.suppress(OSError):
                os.chmod(created, stat.S_IWRITE | stat.S_IREAD)
            try:
                created.unlink()
            except OSError as exc:
                print(f"Could not remove {created}: {exc}")
        print(f"Removed {len(manifest.get('created_contacts', []))} record(s) "
              f"this migration created.")
    else:
        print("No applied-manifest.json in this backup: it predates the "
              "manifest, so this rollback restores the originals but cannot "
              "remove the slug-named records the apply created. Check "
              f"{get_crm_contacts_dir()} for duplicates afterwards.")

    # Restore each file. Clear read-only bit before overwrite so Windows
    # doesn't reject the copy (per reference_windows_readonly_unlink memory).
    for f in latest.rglob("*.md"):
        rel = f.relative_to(latest)
        target = ws.parent / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            try:
                os.chmod(target, stat.S_IWRITE | stat.S_IREAD)
            except OSError:
                pass
        shutil.copy2(f, target)
    print(f"Rollback complete. Restored {sum(1 for _ in latest.rglob('*.md'))} files.")
    return 0


# ============================================================
# Main / CLI
# ============================================================
def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--propose", action="store_true",
                        help="Generate migration map at outputs/operations/crm/")
    parser.add_argument("--apply", action="store_true",
                        help="Apply the proposed map (after manual review)")
    parser.add_argument("--rollback", action="store_true",
                        help="Restore from crm/.migration-backup/{date}/")
    args = parser.parse_args()

    if args.propose:
        sys.exit(cmd_propose())
    elif args.apply:
        sys.exit(cmd_apply())
    elif args.rollback:
        sys.exit(cmd_rollback())
    else:
        parser.error("Specify one of --propose / --apply / --rollback")


if __name__ == "__main__":
    main()
