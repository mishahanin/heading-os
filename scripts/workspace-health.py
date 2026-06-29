#!/usr/bin/env python3
"""
Workspace Health Check for 31C CEO Command Center

Validates file references, context freshness, agent counts,
pipeline health, people completeness, and outputs inventory.

Usage:
    python scripts/workspace-health.py                    # full health check
    python scripts/workspace-health.py --section context  # run one section
    python scripts/workspace-health.py --section refs     # check references only
    python scripts/workspace-health.py --section counts   # check agent counts
    python scripts/workspace-health.py --section pipeline # check pipeline health
    python scripts/workspace-health.py --section people   # check people completeness
    python scripts/workspace-health.py --section outputs  # check outputs inventory
    python scripts/workspace-health.py --section datastore # check datastore status
"""

import argparse
import os
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.utils.colors import GREEN, YELLOW, RED, CYAN, BOLD, RESET
from scripts.utils.workspace import (
    get_workspace_root, get_context_dir, get_outputs_dir, get_datastore_dir,
    get_templates_dir, get_data_root,
)

WORKSPACE = get_workspace_root()

CONTEXT_DIR = get_context_dir()
REFERENCE_DIR = WORKSPACE / "reference"
OUTPUTS_DIR = get_outputs_dir()
COMMANDS_DIR = WORKSPACE / ".claude" / "commands"
SKILLS_DIR = WORKSPACE / ".claude" / "skills"
DATASTORE_DIR = get_datastore_dir()
CLAUDE_MD = WORKSPACE / "CLAUDE.md"


def ok(msg):
    print(f"  {GREEN}OK{RESET}  {msg}")


def warn(msg):
    print(f"  {YELLOW}WARN{RESET}  {msg}")


def action(msg):
    print(f"  {RED}ACTION{RESET}  {msg}")


def header(title):
    print(f"\n{BOLD}{CYAN}=== {title} ==={RESET}")


def check_reference_validation():
    """Check that all paths in CLAUDE.md Reference Resources table exist."""
    header("Reference Validation")
    issues = 0

    if not CLAUDE_MD.exists():
        action("CLAUDE.md not found!")
        return 1

    content = CLAUDE_MD.read_text(encoding="utf-8")

    # Extract paths from markdown table rows with backtick-wrapped paths
    path_pattern = re.compile(r"`([^`]+\.[a-z]+)`")
    # Look for the Reference Resources section
    in_ref_section = False
    for line in content.split("\n"):
        if "Reference Resources" in line:
            in_ref_section = True
            continue
        if in_ref_section and line.startswith("## "):
            break
        if in_ref_section and "|" in line:
            matches = path_pattern.findall(line)
            for path_str in matches:
                full_path = WORKSPACE / path_str
                if full_path.exists():
                    ok(f"{path_str}")
                else:
                    action(f"Missing: {path_str}")
                    issues += 1

    if issues == 0:
        ok("All reference paths resolve to existing files")
    return issues


def check_context_freshness(max_days=30):
    """Check freshness markers on context files."""
    header("Context Freshness")
    issues = 0
    today = datetime.now()

    context_files = list(CONTEXT_DIR.glob("*.md"))
    if not context_files:
        action("No context files found!")
        return 1

    for f in sorted(context_files):
        content = f.read_text(encoding="utf-8")
        lines = content.split("\n")[:10] if content else []

        # Check for freshness marker: > Last verified|updated: YYYY-MM-DD (first 10 lines).
        # Both verbs count as a freshness signal — both carry a date stamp; the
        # workspace convention uses "Verified" (re-confirmed) and "Updated" (content changed).
        match = None
        for line in lines:
            match = re.match(r">\s*Last (?:verified|updated):\s*(\d{4}-\d{2}-\d{2})", line, re.IGNORECASE)
            if match:
                break
        if match:
            verified_date = datetime.strptime(match.group(1), "%Y-%m-%d")
            age_days = (today - verified_date).days
            if age_days > max_days:
                warn(f"{f.name}: Last verified {age_days} days ago ({match.group(1)})")
                issues += 1
            else:
                ok(f"{f.name}: Verified {age_days} days ago ({match.group(1)})")
        else:
            # Fall back to file modification time
            mtime = datetime.fromtimestamp(f.stat().st_mtime)
            age_days = (today - mtime).days
            if age_days > max_days:
                warn(f"{f.name}: No freshness marker, modified {age_days} days ago")
                issues += 1
            else:
                warn(f"{f.name}: No freshness marker (modified {age_days}d ago - add one)")

    return issues


def check_agent_counts():
    """Count actual commands and skills, compare to CLAUDE.md."""
    header("Agent Count Verification")
    issues = 0

    # Count actual commands
    commands = list(COMMANDS_DIR.glob("*.md")) if COMMANDS_DIR.exists() else []
    command_count = len(commands)

    # Count actual skills
    skills = [d for d in SKILLS_DIR.iterdir() if d.is_dir() and (d / "SKILL.md").exists() or (d / "skill.md").exists()] if SKILLS_DIR.exists() else []
    skill_count = len(skills)

    ok(f"Commands found: {command_count} in .claude/commands/")
    ok(f"Skills found: {skill_count} in .claude/skills/")

    # List them
    print(f"\n  Commands: {', '.join(sorted(f.stem for f in commands))}")
    print(f"  Skills: {', '.join(sorted(d.name for d in skills))}")

    # Check for lowercase skill.md (should be SKILL.md)
    for d in skills:
        if (d / "skill.md").exists() and not (d / "SKILL.md").exists():
            warn(f"{d.name}/skill.md should be SKILL.md (inconsistent naming)")
            issues += 1

    return issues


def check_pipeline_health():
    """Parse pipeline.md for stale deals and missing data."""
    header("Pipeline Health")
    issues = 0

    pipeline_file = CONTEXT_DIR / "pipeline.md"
    if not pipeline_file.exists():
        action("pipeline.md not found!")
        return 1

    content = pipeline_file.read_text(encoding="utf-8")

    # Check for TBD, placeholder, and empty fields
    tbd_count = content.lower().count("tbd")
    placeholder_count = content.count("[")
    next_action_missing = content.lower().count("[next action]")

    if tbd_count > 0:
        warn(f"{tbd_count} TBD fields found in pipeline.md")
        issues += 1
    if next_action_missing > 0:
        warn(f"{next_action_missing} missing next actions in pipeline.md")
        issues += 1

    # Check file size (thin pipeline is a signal)
    size = pipeline_file.stat().st_size
    if size < 3000:
        warn(f"pipeline.md is thin ({size} bytes) - may need enrichment")
    else:
        ok(f"pipeline.md size: {size} bytes")

    # Count table rows (rough deal count)
    table_rows = [line for line in content.split("\n") if line.startswith("|") and "---" not in line and "Company" not in line and "Investor" not in line]
    deal_count = len([r for r in table_rows if r.strip() != "|"])
    ok(f"Approximately {deal_count} entries in pipeline tables")

    return issues


def check_people_completeness():
    """Parse people.md for incomplete entries."""
    header("People Completeness")
    issues = 0

    people_file = CONTEXT_DIR / "people.md"
    if not people_file.exists():
        action("people.md not found!")
        return 1

    content = people_file.read_text(encoding="utf-8")

    # Check for placeholder patterns
    add_patterns = re.findall(r"\[Add[^\]]*\]", content)
    if add_patterns:
        warn(f"{len(add_patterns)} placeholder fields ([Add ...]) in people.md")
        issues += 1

    # Check for missing emails
    tbd_emails = content.lower().count("tbd") + content.lower().count("[email]")
    if tbd_emails > 0:
        warn(f"{tbd_emails} missing email/contact entries")
        issues += 1

    size = people_file.stat().st_size
    ok(f"people.md size: {size} bytes")

    return issues


def check_outputs_inventory():
    """Count and categorize files in outputs/."""
    header("Outputs Inventory")
    issues = 0

    if not OUTPUTS_DIR.exists():
        action("outputs/ directory not found!")  # leak-guard: ok (string in a message/log, not a path)
        return 1

    files = list(OUTPUTS_DIR.glob("*"))
    files = [f for f in files if f.is_file()]

    # Categorize by extension
    by_ext = {}
    total_size = 0
    for f in files:
        ext = f.suffix.lower() or "(no ext)"
        by_ext.setdefault(ext, []).append(f)
        total_size += f.stat().st_size

    ok(f"Total files: {len(files)}")
    ok(f"Total size: {total_size / (1024*1024):.1f} MB")

    for ext, ext_files in sorted(by_ext.items()):
        ext_size = sum(f.stat().st_size for f in ext_files)
        print(f"       {ext}: {len(ext_files)} files ({ext_size / 1024:.0f} KB)")

    if len(files) > 30:
        warn(f"outputs/ has {len(files)} files - consider organizing into subdirectories")  # leak-guard: ok (string in a message/log, not a path)
        issues += 1

    return issues


def check_datastore():
    """Check DataStore status."""
    header("DataStore Status")
    issues = 0

    if not DATASTORE_DIR.exists():
        action("datastore/ directory not found!")
        return 1

    index_file = DATASTORE_DIR / "INDEX.md"
    if not index_file.exists():
        action("datastore/INDEX.md not found!")
        issues += 1
    else:
        ok("INDEX.md exists")

    # Check subdirectories
    expected_dirs = ["brand", "content", "corporate", "events", "intelligence", "investment", "operations", "products"]
    for d in expected_dirs:
        dir_path = DATASTORE_DIR / d
        if dir_path.exists():
            file_count = len(list(dir_path.glob("*")))
            if file_count > 0:
                ok(f"{d}/: {file_count} file(s)")
            else:
                warn(f"{d}/: empty - awaiting documents")
        else:
            action(f"{d}/ directory missing")
            issues += 1

    # Count total documents
    total_docs = sum(1 for _ in DATASTORE_DIR.rglob("*") if _.is_file() and _.name != "INDEX.md")
    if total_docs == 0:
        warn("DataStore has no documents yet - add source-of-truth files")
        issues += 1
    else:
        ok(f"Total documents in DataStore: {total_docs}")

    return issues


def _docs_path(name: str) -> Path:
    """Resolve a synced doc's distribution copy.

    docs/ is split by routing: most files default to ENGINE (docs/ under the
    workspace root), but CEO-ADMIN-GUIDE.* is `private` and lives under the data
    overlay (.heading-os-data/docs). Prefer whichever root actually holds the file;
    fall back to engine when neither exists, so a genuinely missing doc still flags.
    """
    engine = WORKSPACE / "docs" / name
    data = get_data_root() / "docs" / name
    if engine.exists():
        return engine
    if data.exists():
        return data
    return engine


def check_docs_sync() -> int:
    """Verify templates/ and docs/ are in sync for the 6 shared documentation files.

    The sync-docs.py PostToolUse hook auto-copies templates/ to docs/. Drift means
    either the hook failed or someone edited docs/ directly. Either way, investigate.

    templates/ is `private` (data overlay) and docs/ is split (engine default,
    CEO-ADMIN-GUIDE on data) — both are resolved through the data-seam helpers,
    not the engine root, since the two-part topology moved them off the engine tree.
    """
    header("Docs/Templates Consistency")
    issues = 0
    synced_files = [
        "GETTING-STARTED.md", "GETTING-STARTED.html",
        "CEO-ADMIN-GUIDE.md", "CEO-ADMIN-GUIDE.html",
        "EMERGENCY-PROCEDURES.md", "EMERGENCY-PROCEDURES.html",
    ]
    templates_dir = get_templates_dir()
    for name in synced_files:
        tpl = templates_dir / name
        doc = _docs_path(name)
        if not tpl.exists():
            action(f"{name}: missing from templates/")
            issues += 1
            continue
        if not doc.exists():
            action(f"{name}: missing from docs/ (sync-docs.py failed or never fired)")
            issues += 1
            continue
        try:
            if tpl.read_bytes() != doc.read_bytes():
                action(f"{name}: templates/ and docs/ out of sync (re-save templates/ to trigger sync)")
                issues += 1
            else:
                ok(f"{name}: synced")
        except OSError as e:
            warn(f"{name}: read failed ({e})")
    return issues


def check_skill_router_coverage() -> int:
    """Cross-reference .claude/rules/skill-router.md against .claude/skills/ directory listing.

    Every skill directory should either:
    - Appear in the router registry (table rows matching `/skill-name`), or
    - Be explicitly listed as NEVER auto-trigger (e.g., /prime, /osint-advanced)
    - Be a plugin-namespaced skill (documented in the plugin doctrine section)

    A skill in .claude/skills/ without any mention in the router is silently orphaned.
    """
    header("Skill Router Coverage")
    issues = 0
    router_file = WORKSPACE / ".claude" / "rules" / "skill-router.md"
    skills_dir = WORKSPACE / ".claude" / "skills"
    if not router_file.exists():
        action("skill-router.md missing")
        return 1
    if not skills_dir.exists():
        action(".claude/skills/ missing")
        return 1
    router_text = router_file.read_text(encoding="utf-8")
    skill_dirs = [d.name for d in sorted(skills_dir.iterdir()) if d.is_dir() and not d.name.startswith(".")]
    for name in skill_dirs:
        needle = f"/{name}"
        if needle in router_text or f"`{needle}`" in router_text:
            ok(f"{name}: in router")
        else:
            action(f"{name}: not mentioned in skill-router.md")
            issues += 1
    return issues


def check_doc_versions(max_age_days: int = 90) -> int:
    """Verify shared docs carry `version:` + `last-updated:` markers and are fresh.

    Every file in `templates/` and `docs/` from the sync set must have a version
    marker on line 1 (for .md/.template files) or near the top for .html files.
    Dates older than max_age_days on a widely-distributed doc are a signal to
    refresh content before next push.
    """
    header(f"Shared Doc Version Markers (freshness threshold: {max_age_days} days)")
    issues = 0
    version_pattern = re.compile(r"<!--\s*version:\s*(\S+?)\s*\|\s*last-updated:\s*(\d{4}-\d{2}-\d{2})\s*-->")
    today = datetime.now().date()
    templates_dir = get_templates_dir()
    tracked = [
        templates_dir / "GETTING-STARTED.md",
        templates_dir / "CEO-ADMIN-GUIDE.md",
        templates_dir / "EMERGENCY-PROCEDURES.md",
        templates_dir / "CLAUDE.md.template",
    ]
    for f in tracked:
        label = f"templates/{f.name}"  # f is under the data root now; relative_to(WORKSPACE) would raise
        if not f.exists():
            warn(f"{label}: missing")
            continue
        first_lines = f.read_text(encoding="utf-8").splitlines()[:3]
        first_block = "\n".join(first_lines)
        match = version_pattern.search(first_block)
        if not match:
            action(f"{label}: missing version marker on line 1")
            issues += 1
            continue
        version, date_str = match.groups()
        try:
            doc_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            warn(f"{label}: malformed date {date_str}")
            issues += 1
            continue
        age = (today - doc_date).days
        if age > max_age_days:
            warn(f"{label}: v{version}, last-updated {date_str} ({age} days old - consider refresh)")
        else:
            ok(f"{label}: v{version}, last-updated {date_str} ({age} days)")
    return issues


def check_build_sync() -> int:
    """Compare local corporate repo BUILD.json against the last publish state.

    Passes if the corporate repo is reachable and its BUILD.json parses. The
    per-exec sync-status check is already in `scripts/admin-health.py` - this
    check focuses on: does our local corporate repo look sane.
    """
    header("Corporate BUILD.json")
    issues = 0
    build_json = WORKSPACE.parent / "heading-os-corporate" / "BUILD.json"
    if not build_json.exists():
        warn(f"{build_json.relative_to(WORKSPACE.parent)}: not found (corporate repo may not be cloned locally)")
        return 0  # Not a workspace-health failure - just info
    import json
    try:
        data = json.loads(build_json.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        action(f"BUILD.json parse failed: {e}")
        return 1
    build_no = data.get("build", "?")
    last_updated = data.get("last_updated", "?")
    ok(f"Corporate BUILD #{build_no}, last_updated: {last_updated}")
    return issues


def main():
    parser = argparse.ArgumentParser(description="31C Workspace Health Check")
    parser.add_argument(
        "--section",
        choices=[
            "refs", "context", "counts", "pipeline", "people", "outputs",
            "datastore", "docs-sync", "skill-router", "doc-versions", "build",
        ],
        help="Run only a specific check section",
    )
    parser.add_argument("--max-days", type=int, default=30,
                        help="Maximum age in days for context freshness (default: 30)")
    args = parser.parse_args()

    print(f"\n{BOLD}31C Workspace Health Check{RESET}")
    print(f"Workspace: {WORKSPACE}")
    print(f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}")

    total_issues = 0
    checks = {
        "refs": check_reference_validation,
        "context": lambda: check_context_freshness(args.max_days),
        "counts": check_agent_counts,
        "pipeline": check_pipeline_health,
        "people": check_people_completeness,
        "outputs": check_outputs_inventory,
        "datastore": check_datastore,
        "docs-sync": check_docs_sync,
        "skill-router": check_skill_router_coverage,
        "doc-versions": check_doc_versions,
        "build": check_build_sync,
    }

    if args.section:
        total_issues = checks[args.section]()
    else:
        for name, check_fn in checks.items():
            total_issues += check_fn()

    # Summary
    header("Summary")
    if total_issues == 0:
        print(f"  {GREEN}{BOLD}All checks passed.{RESET}")
    else:
        print(f"  {YELLOW}{BOLD}{total_issues} issue(s) found.{RESET}")

    sys.exit(0 if total_issues == 0 else 1)


if __name__ == "__main__":
    main()
