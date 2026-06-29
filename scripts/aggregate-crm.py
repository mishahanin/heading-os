#!/usr/bin/env python3
"""
CRM Aggregation Script for 31C Multi-User HEADING OS Ecosystem (per-exec model).

Reads CEO own CRM from <data-root>/crm/contacts/ plus each active exec's data
overlay at ../.heading-os-data-{slug}/crm/contacts/, parallelizes git pulls, and
generates company-wide views in <data-root>/crm/aggregated/ (CEO local, gitignored).

Fleet source of truth is admin/executives.json under the DATA root (new HEADING OS
two-part topology); the legacy config/exec-registry.json + 31c-crm-{slug} model is
retired.

Output (in <workspace>/crm/aggregated/):
  - company-radar.md     All contacts from all execs with health status
  - by-company.md        Contacts grouped by company name
  - ownership-map.md     Who owns which relationships, counts by type
  - shared-contacts.md   Same person tracked by multiple execs
  - audit/aggregation-log.jsonl  Audit trail (5000-entry cap)

Usage:
    python aggregate-crm.py                            # default: aggregate all
    python aggregate-crm.py --ceo-only                 # CEO own only (skip exec pulls)
    python aggregate-crm.py --skip-clone               # don't auto-clone missing repos
    python aggregate-crm.py --workspace-root /path     # override workspace (for tests)
    python aggregate-crm.py --json                     # output stats as JSON
"""

import argparse
import json
import re
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.utils.workspace import (
    get_workspace_root, load_admin_config, get_data_root,
    get_crm_contacts_dir, get_crm_config_path, get_personal_root,
)
from scripts.utils.colors import GREEN, YELLOW, RED, CYAN, GRAY, BOLD, RESET
from scripts.utils.markdown import parse_frontmatter_str as _parse_frontmatter

TODAY = datetime.now().date()

# ============================================================
# Configuration
# ============================================================

# ---------------------------------------------------------------------------
# Cadence config
# ---------------------------------------------------------------------------

DEFAULT_CADENCE = {
    "partner":          {"cadence": 14, "yellow": 10, "red": 14},
    "partner-active":   {"cadence": 14, "yellow": 10, "red": 14},
    "investor-active":  {"cadence": 14, "yellow": 10, "red": 14},
    "investor-passive": {"cadence": 30, "yellow": 21, "red": 30},
    "shareholder":      {"cadence": 30, "yellow": 21, "red": 30},
    "tribe":            {"cadence": 7,  "yellow": 5,  "red": 7},
    "tribe-leadership": {"cadence": 14, "yellow": 10, "red": 14},
    "reseller":         {"cadence": 21, "yellow": 14, "red": 21},
    "prospect":         {"cadence": 14, "yellow": 10, "red": 14},
    "government":       {"cadence": 30, "yellow": 21, "red": 30},
    "media":            {"cadence": 60, "yellow": 45, "red": 60},
    "inactive":         {"cadence": 0,  "yellow": 0,  "red": 0},
}

NO_CADENCE_TYPES = {"tribe", "tribe-leadership", "inactive"}


def parse_config(config_path: Path) -> dict:
    """Parse cadence defaults from crm/config.md table."""
    if not config_path.exists():
        return DEFAULT_CADENCE.copy()

    defaults = {}
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
                    try:
                        defaults[cells[0]] = {
                            "cadence": int(cells[1]),
                            "yellow": int(cells[2]),
                            "red": int(cells[3]),
                        }
                    except ValueError:
                        continue
            elif not line.strip():
                break

    return defaults if defaults else DEFAULT_CADENCE.copy()


# ============================================================
# Data Loading
# ============================================================

# ---------------------------------------------------------------------------
# Frontmatter parsing (delegates to scripts.utils.markdown.parse_frontmatter_str)
# ---------------------------------------------------------------------------

def parse_frontmatter(content: str) -> dict:
    """Parse YAML frontmatter from a contact file (string-coerced values).

    Thin wrapper that calls the shared parser and drops the body. Kept as a
    function for legacy call sites in this script.
    """
    fm, _body = _parse_frontmatter(content)
    return fm


# ============================================================
# Processing / Core Logic
# ============================================================

# ---------------------------------------------------------------------------
# Health calculation
# ---------------------------------------------------------------------------

def calculate_health(last_touch_str: str, config_entry: dict) -> tuple:
    """Return (health_state, days_since) for a contact."""
    if not last_touch_str or last_touch_str in ("-", "n/a", ""):
        return "red", None

    try:
        last_touch = datetime.strptime(last_touch_str, "%Y-%m-%d").date()
    except ValueError:
        return "gray", None

    days_since = (TODAY - last_touch).days

    if days_since >= config_entry["red"]:
        return "red", days_since
    elif days_since >= config_entry["yellow"]:
        return "yellow", days_since
    else:
        return "green", days_since


def get_thresholds(fm: dict, config: dict) -> dict:
    """Resolve cadence thresholds for a contact."""
    rel_type = fm.get("type", "")
    cadence_override = fm.get("cadence", "")

    if rel_type in config:
        entry = config[rel_type].copy()
        if cadence_override:
            try:
                entry["cadence"] = int(cadence_override)
            except ValueError:
                pass
        return entry
    elif cadence_override:
        try:
            c = int(cadence_override)
            return {"cadence": c, "yellow": int(c * 0.7), "red": c}
        except ValueError:
            pass

    return {"cadence": 14, "yellow": 10, "red": 14}


# ============================================================
# Data Sources / Contact Scanning
# ============================================================

# ---------------------------------------------------------------------------
# Contact scanning
# ---------------------------------------------------------------------------

def slug_to_display_name(slug: str) -> str:
    """Convert 'misha-hanin' to 'M. Hanin'."""
    parts = slug.split("-")
    if len(parts) >= 2:
        first_initial = parts[0][0].upper() + "."
        last = "-".join(parts[1:]).title()
        return f"{first_initial} {last}"
    return slug.title()


def scan_all_contacts(workspace_root: Path, exec_slugs: list, config: dict,
                      ceo_only: bool, skip_clone: bool) -> tuple:
    """Read CEO own + per-exec contacts. Returns (contacts, errors).

    Reads CEO own from <workspace_root>/crm/contacts/*.md, treats them as
    owner_slug = first admin slug from admin.json (defaults to 'misha-hanin').

    For each active exec slug, ensures the local clone exists (auto-clone via
    `gh repo clone` if not skip_clone), pulls latest, and reads contacts/*.md
    treating owner_slug = exec slug.

    Pulls are parallelized via ThreadPoolExecutor(max_workers=8).
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    contacts = []
    errors = []

    # CEO own
    admin_config = load_admin_config()
    admin_slug = (admin_config.get("admin_slugs") or ["misha-hanin"])[0]
    ceo_dir = get_crm_contacts_dir()
    if ceo_dir.exists():
        for file_path in sorted(ceo_dir.glob("*.md")):
            if file_path.name.lower() == "readme.md":
                continue
            ctx = _read_contact_file(file_path, admin_slug, config, errors)
            if ctx:
                contacts.append(ctx)
    else:
        errors.append(f"CEO own contacts dir not found: {ceo_dir}")

    if ceo_only:
        return contacts, errors

    # Per-exec parallel pulls
    def _pull_and_scan(slug: str) -> tuple:
        repo_path = get_per_exec_repo_path_for_workspace(workspace_root, slug)
        slug_errors = []
        slug_contacts = []

        if not repo_path.exists():
            if skip_clone:
                slug_errors.append(f"Skip-clone mode and {repo_path} missing for {slug}")
                return slug_contacts, slug_errors
            try:
                org = admin_config.get("github_org") or "mishahanin"
                result = subprocess.run(
                    ["gh", "repo", "clone", f"{org}/heading-os-data-{slug}", str(repo_path)],
                    capture_output=True, text=True, timeout=120,
                )
                if result.returncode != 0:
                    slug_errors.append(f"Clone failed for {slug}: {result.stderr.strip()}")
                    return slug_contacts, slug_errors
            except (FileNotFoundError, subprocess.TimeoutExpired) as e:
                slug_errors.append(f"Clone error for {slug}: {e}")
                return slug_contacts, slug_errors
        elif not skip_clone:
            try:
                subprocess.run(["git", "pull"], cwd=str(repo_path),
                               capture_output=True, text=True, timeout=60, check=False)
            except (subprocess.TimeoutExpired, OSError) as e:
                slug_errors.append(f"Pull warning for {slug}: {e}")

        contacts_dir = repo_path / "crm" / "contacts"
        if not contacts_dir.exists():
            slug_errors.append(f"contacts dir not found: {contacts_dir}")
            return slug_contacts, slug_errors

        for file_path in sorted(contacts_dir.glob("*.md")):
            if file_path.name.lower() == "readme.md":
                continue
            ctx = _read_contact_file(file_path, slug, config, slug_errors)
            if ctx:
                slug_contacts.append(ctx)

        return slug_contacts, slug_errors

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(_pull_and_scan, slug): slug for slug in exec_slugs}
        for fut in as_completed(futures):
            c, e = fut.result()
            contacts.extend(c)
            errors.extend(e)

    return contacts, errors


def _read_contact_file(file_path: Path, owner_slug: str, config: dict, errors: list):
    """Read one contact file, return contact dict or None."""
    try:
        content = file_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        errors.append(f"Could not read {file_path}: {e}")
        return None

    fm = parse_frontmatter(content)
    if not fm.get("name"):
        errors.append(f"No 'name' in frontmatter: {file_path}")
        return None

    thresholds = get_thresholds(fm, config)
    last_touch = fm.get("last_touch", "")
    rel_type = fm.get("type", "")

    if rel_type in NO_CADENCE_TYPES:
        health, days_since = "gray", None
    else:
        health, days_since = calculate_health(last_touch, thresholds)

    return {
        "name": fm["name"],
        "company": fm.get("company", ""),
        "title": fm.get("title", ""),
        "type": fm.get("type", ""),
        "region": fm.get("region", ""),
        "last_touch": last_touch,
        "cadence": thresholds["cadence"],
        "health": health,
        "days_since": days_since,
        "owner_slug": owner_slug,
        "owner_display": slug_to_display_name(owner_slug),
        "file_rel": str(file_path),
    }


def get_per_exec_repo_path_for_workspace(workspace_root: Path, slug: str) -> Path:
    """Per-exec data-overlay clone path, sibling of the workspace (for testability).

    New HEADING OS model: each exec's full data overlay is cloned as
    ../.heading-os-data-{slug}/ (CEO-owned, exec is collaborator). CRM contacts
    live inside it at crm/contacts/. The dotted name matches provision_exec.py
    and the data-root seam (scripts/utils/workspace.py) so provisioning and
    aggregation share ONE clone per exec rather than each creating its own.
    """
    return workspace_root.parent / f".heading-os-data-{slug}"


def load_fleet_registry(data_root: Path) -> dict:
    """Load the fleet registry from <data_root>/admin/executives.json (new model).

    Single source of truth for who the executives are. Returns an empty registry
    when the file is absent (a data-less or pre-provisioning workspace).
    """
    path = data_root / "admin" / "executives.json"
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {"version": 1, "executives": []}


# ============================================================
# Shared Contact Detection
# ============================================================

# ---------------------------------------------------------------------------
# Shared contact detection
# ---------------------------------------------------------------------------

def normalize_name(name: str) -> str:
    """Normalize a name for comparison."""
    return " ".join(name.lower().strip().split())


def _legacy_fuzzy_key(rec: dict) -> str:
    """Synthesise a grouping key for unmigrated records (no entity_ref).

    Option A: mirrors Pass 1 of the original detect_shared_contacts logic --
    exact normalized name.  This is the dominant signal that matches all 7
    known dual-owners (Jordan Kim, Dana Cole, Erik Grant, Joel Dawson,
    Marco Vella, Karl Mertens, Jordan Blake).  Company names differ across
    exec repos for several of these contacts (e.g. "CraneCo" vs
    "Crane-Co", "FlowCo Ltd" vs "FlowCo"), so including company in the
    key would break those matches.

    Pass 2 of the original logic (first+last+company) was additive for edge
    cases where name spelling differed slightly but the company was the same.
    None of the current 7 require it, so it is omitted here; the key stays
    name-only for full backward compatibility.
    """
    name = normalize_name(rec.get("name") or "")
    return f"legacy::name::{name}"


def group_by_entity(all_records: list) -> dict:
    """Group contact records (from all execs) by entity_ref.

    Returns {entity_ref: [list of relationship records across execs]}.
    Records without entity_ref fall back to exact normalized-name grouping via
    _legacy_fuzzy_key (see that function for the rationale).
    """
    grouped: dict = {}
    legacy_pool: list = []
    for rec in all_records:
        ref = rec.get("entity_ref")
        if ref:
            grouped.setdefault(ref, []).append(rec)
        else:
            legacy_pool.append(rec)
    # Phase 2: legacy fuzzy fallback for unmigrated records
    for rec in legacy_pool:
        key = _legacy_fuzzy_key(rec)
        grouped.setdefault(key, []).append(rec)
    # Phase 3: bridge migration-window mismatches.
    # When CEO records are migrated first (entity_ref populated) but exec records
    # remain legacy, the same person ends up in two buckets - one keyed by
    # entity_ref (CEO) and one by legacy::name (exec). Detect this and merge
    # so dual-owner detection still fires during the migration window.
    legacy_keys_to_remove = []
    for entity_key, entity_records in grouped.items():
        if entity_key.startswith("legacy::"):
            continue
        # Compute the legacy key that would match if this entity's owner records
        # had been unmigrated. They share normalized name.
        if not entity_records:
            continue
        sample = entity_records[0]
        candidate_legacy_key = _legacy_fuzzy_key(sample)
        if candidate_legacy_key in grouped and candidate_legacy_key != entity_key:
            # Merge legacy records into the entity bucket
            grouped[entity_key].extend(grouped[candidate_legacy_key])
            legacy_keys_to_remove.append(candidate_legacy_key)
    for k in legacy_keys_to_remove:
        del grouped[k]
    return grouped


def detect_shared_contacts(contacts: list) -> list:
    """Detect contacts tracked by multiple executives.

    When entity_ref is present on a record it is used as the grouping key
    directly (canonical identity). For records that have not yet been
    migrated, the legacy two-pass fuzzy fallback applies (exact normalized
    name, then first+last+company) via group_by_entity / _legacy_fuzzy_key.

    Returns list of groups: [{"name": ..., "company": ..., "owners": [...]}]
    """
    grouped = group_by_entity(contacts)

    shared = []
    for _key, group in grouped.items():
        owners = set(c["owner_slug"] for c in group)
        if len(owners) > 1:
            # Sort owners by slug for stable output
            sorted_group = sorted(group, key=lambda c: c["owner_slug"])
            shared.append({
                "name": group[0]["name"],
                "company": group[0]["company"],
                "owners": [
                    {"slug": c["owner_slug"], "display": c["owner_display"],
                     "last_touch": c["last_touch"], "health": c["health"]}
                    for c in sorted_group
                ],
            })

    return shared


# ============================================================
# Output / Rendering
# ============================================================

# ---------------------------------------------------------------------------
# Output generation
# ---------------------------------------------------------------------------

HEALTH_ORDER = {"red": 0, "yellow": 1, "green": 2, "gray": 3}
HEALTH_EMOJI = {"red": "RED", "yellow": "YELLOW", "green": "GREEN", "gray": "GRAY"}


def generate_company_radar(contacts: list, exec_count: int, shared_count: int) -> str:
    """Generate company-radar.md content."""
    sorted_contacts = sorted(
        contacts,
        key=lambda c: (HEALTH_ORDER.get(c["health"], 4), c["name"].lower())
    )

    lines = [
        "# Company-Wide CRM Radar",
        f"> Auto-generated by aggregate-crm.py on {TODAY.strftime('%Y-%m-%d')}",
        "",
        "| Health | Name | Company | Type | Owner | Last Touch | Cadence |",
        "|--------|------|---------|------|-------|-----------|---------|",
    ]

    for c in sorted_contacts:
        health = HEALTH_EMOJI.get(c["health"], "?")
        last = c["last_touch"] if c["last_touch"] else "-"
        cadence = f"{c['cadence']}d"
        lines.append(
            f"| {health} | {c['name']} | {c['company']} | {c['type']} "
            f"| {c['owner_display']} | {last} | {cadence} |"
        )

    red_count = sum(1 for c in contacts if c["health"] == "red")
    yellow_count = sum(1 for c in contacts if c["health"] == "yellow")
    green_count = sum(1 for c in contacts if c["health"] == "green")
    gray_count = sum(1 for c in contacts if c["health"] == "gray")

    lines.extend([
        "",
        "## Summary",
        f"- Total contacts: {len(contacts)} across {exec_count} executives",
        f"- RED: {red_count} contacts need attention",
        f"- YELLOW: {yellow_count} contacts approaching staleness",
        f"- GREEN: {green_count} contacts on track",
        f"- GRAY: {gray_count} contacts without cadence",
        f"- Shared contacts: {shared_count} people tracked by multiple execs",
    ])

    return "\n".join(lines) + "\n"


def generate_by_company(contacts: list) -> str:
    """Generate by-company.md content."""
    by_company = defaultdict(list)
    for c in contacts:
        key = c["company"].strip() if c["company"].strip() else "(No Company)"
        by_company[key].append(c)

    lines = [
        "# Contacts by Company",
        f"> Auto-generated by aggregate-crm.py on {TODAY.strftime('%Y-%m-%d')}",
        "",
    ]

    for company in sorted(by_company.keys(), key=str.lower):
        group = sorted(by_company[company], key=lambda c: c["name"].lower())
        lines.append(f"## {company}")
        lines.append("")
        lines.append("| Name | Type | Owner | Health | Last Touch |")
        lines.append("|------|------|-------|--------|-----------|")
        for c in group:
            health = HEALTH_EMOJI.get(c["health"], "?")
            last = c["last_touch"] if c["last_touch"] else "-"
            lines.append(f"| {c['name']} | {c['type']} | {c['owner_display']} | {health} | {last} |")
        lines.append("")

    return "\n".join(lines) + "\n"


def generate_ownership_map(contacts: list) -> str:
    """Generate ownership-map.md content."""
    by_owner = defaultdict(list)
    for c in contacts:
        by_owner[c["owner_slug"]].append(c)

    lines = [
        "# Relationship Ownership Map",
        f"> Auto-generated by aggregate-crm.py on {TODAY.strftime('%Y-%m-%d')}",
        "",
    ]

    for owner_slug in sorted(by_owner.keys()):
        group = by_owner[owner_slug]
        display = group[0]["owner_display"]

        # Count by type
        type_counts = defaultdict(int)
        health_counts = defaultdict(int)
        for c in group:
            t = c["type"] if c["type"] else "(untyped)"
            type_counts[t] += 1
            health_counts[c["health"]] += 1

        lines.append(f"## {display} (`{owner_slug}`)")
        lines.append(f"- **Total contacts:** {len(group)}")
        lines.append(f"- **Health:** {health_counts.get('red', 0)} red, "
                      f"{health_counts.get('yellow', 0)} yellow, "
                      f"{health_counts.get('green', 0)} green, "
                      f"{health_counts.get('gray', 0)} gray")
        lines.append("")
        lines.append("| Type | Count |")
        lines.append("|------|-------|")
        for t in sorted(type_counts.keys()):
            lines.append(f"| {t} | {type_counts[t]} |")
        lines.append("")

        # List contacts
        lines.append("| Name | Company | Type | Health |")
        lines.append("|------|---------|------|--------|")
        for c in sorted(group, key=lambda x: (HEALTH_ORDER.get(x["health"], 4), x["name"].lower())):
            health = HEALTH_EMOJI.get(c["health"], "?")
            lines.append(f"| {c['name']} | {c['company']} | {c['type']} | {health} |")
        lines.append("")

    return "\n".join(lines) + "\n"


def generate_shared_contacts(shared: list) -> str:
    """Generate shared-contacts.md content."""
    lines = [
        "# Shared Contacts",
        f"> Auto-generated by aggregate-crm.py on {TODAY.strftime('%Y-%m-%d')}",
        ">",
        "> Contacts tracked by multiple executives. Informational only -- no auto-merge.",
        "",
    ]

    if not shared:
        lines.append("No shared contacts detected.")
        lines.append("")
        return "\n".join(lines) + "\n"

    for entry in sorted(shared, key=lambda x: x["name"].lower()):
        lines.append(f"## {entry['name']} ({entry['company']})")
        lines.append("")
        lines.append("| Owner | Last Touch | Health |")
        lines.append("|-------|-----------|--------|")
        for o in entry["owners"]:
            last = o["last_touch"] if o["last_touch"] else "-"
            health = HEALTH_EMOJI.get(o["health"], "?")
            lines.append(f"| {o['display']} | {last} | {health} |")
        lines.append("")

    return "\n".join(lines) + "\n"


# ============================================================
# State Management / Audit Log
# ============================================================

# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------

AUDIT_MAX_ENTRIES = 5000


def write_audit_log(aggregated_dir: Path, contacts_count: int, exec_count: int,
                    shared_count: int, errors: list) -> None:
    """Append an entry to <aggregated_dir>/audit/aggregation-log.jsonl, capping at 5000 entries."""
    audit_dir = aggregated_dir / "audit"
    audit_dir.mkdir(parents=True, exist_ok=True)
    log_path = audit_dir / "aggregation-log.jsonl"

    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "contacts_processed": contacts_count,
        "execs": exec_count,
        "shared_contacts": shared_count,
        "errors": errors[:50],
    }

    with open(log_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")

    try:
        all_lines = log_path.read_text(encoding="utf-8").strip().split("\n")
        if len(all_lines) > AUDIT_MAX_ENTRIES:
            trimmed = all_lines[-AUDIT_MAX_ENTRIES:]
            log_path.write_text("\n".join(trimmed) + "\n", encoding="utf-8")
    except OSError:
        pass


# ============================================================
# Terminal Output
# ============================================================

# ---------------------------------------------------------------------------
# Terminal output
# ---------------------------------------------------------------------------

def print_terminal_summary(contacts: list, exec_count: int, shared: list, errors: list) -> None:
    """Print a colored summary to the terminal."""
    red_count = sum(1 for c in contacts if c["health"] == "red")
    yellow_count = sum(1 for c in contacts if c["health"] == "yellow")
    green_count = sum(1 for c in contacts if c["health"] == "green")
    gray_count = sum(1 for c in contacts if c["health"] == "gray")

    print(f"\n{BOLD}31C Company-Wide CRM Aggregation{RESET}")
    print(f"  Contacts: {len(contacts)} across {exec_count} executives")
    print(f"  Health:   {RED}{red_count} red{RESET}  {YELLOW}{yellow_count} yellow{RESET}  {GREEN}{green_count} green{RESET}  {gray_count} gray")
    print(f"  Shared:   {len(shared)} contacts tracked by multiple execs")

    if errors:
        print(f"\n  {YELLOW}Warnings ({len(errors)}):{RESET}")
        for err in errors[:10]:
            print(f"    - {err}")
        if len(errors) > 10:
            print(f"    ... and {len(errors) - 10} more")

    print()


def print_json_stats(contacts: list, exec_count: int, shared: list, errors: list) -> None:
    """Print aggregation stats as JSON."""
    by_health = defaultdict(int)
    for c in contacts:
        by_health[c["health"]] += 1

    by_owner = defaultdict(int)
    for c in contacts:
        by_owner[c["owner_slug"]] += 1

    output = {
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "total_contacts": len(contacts),
        "exec_count": exec_count,
        "shared_contacts": len(shared),
        "health": dict(by_health),
        "by_owner": dict(by_owner),
        "errors": errors,
    }
    print(json.dumps(output, indent=2))


# ============================================================
# Main / CLI
# ============================================================

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="31C CRM Aggregation -- per-exec repo model"
    )
    parser.add_argument(
        "--workspace-root",
        type=str,
        default=None,
        help="Override workspace root path (for testing). Default: auto-detect.",
    )
    parser.add_argument(
        "--ceo-only",
        action="store_true",
        help="Aggregate only CEO own CRM, skip exec repo pulls (for debugging)",
    )
    parser.add_argument(
        "--skip-clone",
        action="store_true",
        help="Skip auto-clone of missing per-exec repos; only read existing local clones",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output aggregation stats as JSON",
    )
    args = parser.parse_args()

    if args.workspace_root:
        workspace_root = Path(args.workspace_root).resolve()
    else:
        workspace_root = get_workspace_root()

    config_path = get_crm_config_path()
    config = parse_config(config_path)

    if args.ceo_only:
        exec_slugs = []
    else:
        registry = load_fleet_registry(get_data_root())
        exec_slugs = sorted([
            e["slug"] for e in registry.get("executives", [])
            if e.get("status") == "active" and e.get("role") != "admin" and e.get("slug")
        ])

    contacts, errors = scan_all_contacts(workspace_root, exec_slugs, config,
                                         ceo_only=args.ceo_only,
                                         skip_clone=args.skip_clone)

    if not contacts:
        print(f"{YELLOW}No contacts found.{RESET}", file=sys.stderr)
        sys.exit(0)

    exec_count = len(set(c["owner_slug"] for c in contacts))
    shared = detect_shared_contacts(contacts)

    aggregated_dir = get_personal_root() / "crm" / "aggregated"
    aggregated_dir.mkdir(parents=True, exist_ok=True)

    (aggregated_dir / "company-radar.md").write_text(
        generate_company_radar(contacts, exec_count, len(shared)), encoding="utf-8")
    (aggregated_dir / "by-company.md").write_text(
        generate_by_company(contacts), encoding="utf-8")
    (aggregated_dir / "ownership-map.md").write_text(
        generate_ownership_map(contacts), encoding="utf-8")
    (aggregated_dir / "shared-contacts.md").write_text(
        generate_shared_contacts(shared), encoding="utf-8")

    write_audit_log(aggregated_dir, len(contacts), exec_count, len(shared), errors)

    if args.json:
        print_json_stats(contacts, exec_count, shared, errors)
    else:
        print_terminal_summary(contacts, exec_count, shared, errors)
        print(f"  {GREEN}Output: {aggregated_dir}{RESET}\n")


if __name__ == "__main__":
    main()
