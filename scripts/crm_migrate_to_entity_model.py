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
     each contact file as a thin relationship record.
  5. All writes go to crm/.migration-staging/; only renamed into place after
     every file passes validation. Backup at crm/.migration-backup/<date>/.

Usage:
  python3 scripts/crm_migrate_to_entity_model.py --propose    # generate review map only
  python3 scripts/crm_migrate_to_entity_model.py --apply      # apply the proposed map (after review)
  python3 scripts/crm_migrate_to_entity_model.py --rollback   # restore from backup
"""

# Pre-imported for Tasks 0.13 (--apply) and 0.14 (--rollback):
# json (for migration map writing), os (chmod for atomic writes),
# shutil (rmtree for backup cleanup), stat (S_IWRITE for Windows read-only handling).
import argparse
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
from scripts.utils.crm import parse_frontmatter
from scripts.utils.workspace import (
    get_workspace_root,
    get_all_active_exec_slugs,
    get_per_exec_repo_path,
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
    for rec in no_email:
        key = (_normalize_name(rec.get("name", "")), _normalize_company(rec.get("company", "")))
        if not key[0]:
            continue
        name_groups.setdefault(key, []).append(rec)

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
def scan_all_contacts() -> list[dict]:
    """Scan CEO's crm/contacts/ + each per-exec CRM clone at ../31c-crm-{slug}/.

    Returns flat dicts with: owner (slug), name, email, company, type, file_path.

    Note: 31c-crm-central is DEPRECATED (per scripts/setup.py:351-364). The
    canonical exec CRM source is the per-exec repos at ../31c-crm-{slug}/
    (one repo per active exec). This is the same pattern aggregate-crm.py uses.
    """
    records = []

    def _record_from(file_path: Path, owner: str, fm: dict) -> dict:
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
        }

    # CEO contacts at crm/contacts/
    ceo_dir = get_crm_contacts_dir()
    for f in sorted(ceo_dir.glob("*.md")):
        fm = parse_frontmatter(f.read_text(encoding="utf-8"))
        if not fm:
            continue
        records.append(_record_from(f, "owner-exec-a", fm))

    # Per-exec CRM contacts at ../31c-crm-{slug}/contacts/
    # Use get_all_active_exec_slugs() which reads exec-registry.json (list format)
    # and already excludes the admin/CEO role. load_admin_config() returns a
    # different structure (config/admin.json) and is not suitable here.
    exec_slugs = get_all_active_exec_slugs()
    for slug in exec_slugs:
        repo_path = get_per_exec_repo_path(slug)
        exec_contacts_dir = repo_path / "contacts"
        if not exec_contacts_dir.exists():
            continue
        for f in sorted(exec_contacts_dir.glob("*.md")):
            fm = parse_frontmatter(f.read_text(encoding="utf-8"))
            if not fm:
                continue
            records.append(_record_from(f, slug, fm))

    return records


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
    records = scan_all_contacts()
    print(f"Scanned {len(records)} records across all execs.")
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
    if any(c in specials for c in s):
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
        f"relationship_type: {record.get('type', '')}",
        f"last_touch: {today_rec}",
        f"created: {created}",
    ]
    cadence = record.get("cadence")
    if cadence not in (None, "", 0):
        fm.append(f"cadence: {cadence}")
    if record.get("source"):
        fm.append(f"source: {record['source']}")
    fm.append("status: active")
    fm.append("tags: []")
    if record.get("company"):
        fm.append(f"pipeline_company: {record['company']}")
    fm.append("radar_freeze_until: \"\"")
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
    map_file = get_outputs_dir() / "operations" / "crm" / f"{today}_migration-map.md"
    if not map_file.exists():
        print(f"Migration map not found: {map_file}")
        print("Run --propose first.")
        return 1

    # Re-derive the groups by re-running the scan + group (deterministic).
    records = scan_all_contacts()
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
    print(f"Backup written to {backup_dir} ({len(records)} files)")

    # Staging dir
    staging = crm_root / ".migration-staging"
    if staging.exists():
        shutil.rmtree(staging)
    address_book_staging = staging / "address-book"
    contacts_staging = staging / "contacts"
    address_book_staging.mkdir(parents=True)
    contacts_staging.mkdir(parents=True)

    # Generate address book entries (one per group) + per-record relationship records
    for g in groups:
        slug = g["proposed_slug"]
        if not slug:
            continue
        ab_text = render_address_book_entry(g)
        (address_book_staging / f"{slug}.md").write_text(ab_text, encoding="utf-8")

        for r in g["records"]:
            if r["owner"] != "owner-exec-a":
                # Exec records go to per-exec staging area (separate migration step per exec)
                continue
            rel_text = render_relationship_record(r, slug)
            (contacts_staging / f"{slug}.md").write_text(rel_text, encoding="utf-8")

    # Validate every staged file against the new schemas
    val = subprocess.run(
        ["python3", "scripts/validate-crm-schema.py", "--dir", str(staging)],
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
            ["python3", "scripts/sanitize-text.py", str(staged), "--scan"],
            capture_output=True, text=True, cwd=str(ws),
        )
        if scan.returncode != 0:
            print(f"Hidden-char scan FAILED on {staged}. Aborting apply.")
            return 1

    # Rename staging -> final using os.replace (atomic on POSIX + Windows).
    # NOT shutil.move + target.unlink() - Windows raises PermissionError on
    # read-only attributes (corporate files may be marked read-only).
    final_ab = crm_root / "address-book"
    final_ab.mkdir(exist_ok=True)
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

    # Clean staging
    shutil.rmtree(staging, ignore_errors=True)

    print(f"Migration applied. Address book at {final_ab} (~{len(list(final_ab.glob('*.md')))} entities).")
    print(f"Backup at {backup_dir} (run --rollback to restore).")
    return 0


# ============================================================
# Command: --rollback
# ============================================================
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

    # Confirm
    resp = input("This will overwrite current crm/contacts/ and remove crm/address-book/. Confirm? [yes/no]: ").strip().lower()
    if resp != "yes":
        print("Aborted.")
        return 1

    # Remove address book
    ab = crm_root / "address-book"
    if ab.exists():
        shutil.rmtree(ab)

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
