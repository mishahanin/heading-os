#!/usr/bin/env python3
"""Audit SKILL.md frontmatter completeness across all workspace skills.

Checks every .claude/skills/{name}/SKILL.md for the required frontmatter fields
defined in .claude/rules/development-standards.md and consumed by the parallel
orchestrator in .claude/rules/skill-orchestrator.md:

Required (top-level):
  name, description, metadata.author, metadata.version
Required (under x-31c-orchestration:):
  parallel_safe, shared_state, triggers
Recommended:
  argument-hint, allowed-tools

The orchestration fields live under a namespaced x-31c-orchestration: block in
SKILL.md. This signals "workspace extension, not part of Anthropic's standard
SKILL.md spec" so future stricter validation does not strip them.

Skills lacking the x-31c-orchestration block (or its fields) default to
parallel_safe=false per the orchestrator's safety model, which is invisible.
This audit surfaces the gap so frontmatter can be filled in deliberately.

Usage:
  python scripts/skill-metadata-check.py              # full audit with per-skill detail
  python scripts/skill-metadata-check.py --summary    # counts only, no per-skill output
  python scripts/skill-metadata-check.py --fail-on-missing  # exit 1 if any required field missing (for CI)
  python scripts/skill-metadata-check.py --json       # machine-readable JSON output
"""
import argparse
import json
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils.workspace import get_workspace_root
from scripts.utils.colors import GREEN, YELLOW, RED, CYAN, GRAY, BOLD, RESET


REQUIRED_TOP_FIELDS = ["name", "description"]
REQUIRED_ORCH_FIELDS = ["parallel_safe", "shared_state", "triggers"]
REQUIRED_METADATA = ["author", "version"]
RECOMMENDED_FIELDS = ["argument-hint", "allowed-tools"]
ORCHESTRATION_BLOCK = "x-31c-orchestration"

VALID_PARALLEL_SAFE = {"true", "false", "partial", True, False}


def parse_frontmatter(skill_md: Path) -> tuple[dict, str]:
    """Parse YAML frontmatter from a SKILL.md file.

    Returns (frontmatter_dict, error_message). error_message is empty on success.

    NOT MIGRATED to ``scripts.utils.markdown.parse_frontmatter`` (deferred from
    Phase 6.2). The shared util collapses every failure mode (no opening fence,
    no closing fence, YAML parse error, empty block, non-mapping root) into a
    single ``({}, text)`` return. This audit's whole purpose is to surface those
    distinctions to the operator so SKILL.md authoring problems can be fixed
    deliberately. Migrating here would erase the diagnostic categories that
    make the audit useful, so the custom parser is intentionally retained.
    """
    try:
        text = skill_md.read_text(encoding="utf-8")
    except OSError as e:
        return {}, f"read failed: {e}"

    if not text.startswith("---"):
        return {}, "no frontmatter (missing opening ---)"

    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, "malformed frontmatter (missing closing ---)"

    yaml_block = parts[1]
    try:
        data = yaml.safe_load(yaml_block)
    except yaml.YAMLError as e:
        return {}, f"YAML parse error: {e}"

    if data is None:
        return {}, "empty frontmatter"
    if not isinstance(data, dict):
        return {}, f"frontmatter must be a mapping, got {type(data).__name__}"

    return data, ""


def check_skill(skill_dir: Path) -> dict:
    """Check a single skill directory's SKILL.md for required frontmatter."""
    skill_md = skill_dir / "SKILL.md"
    result = {
        "name": skill_dir.name,
        "path": str(skill_md.relative_to(get_workspace_root())),
        "missing_required": [],
        "missing_recommended": [],
        "invalid_values": [],
        "error": "",
        "status": "UNKNOWN",
    }

    if not skill_md.exists():
        result["error"] = "SKILL.md not found"
        result["status"] = "ERROR"
        return result

    frontmatter, err = parse_frontmatter(skill_md)
    if err:
        result["error"] = err
        result["status"] = "ERROR"
        return result

    for field in REQUIRED_TOP_FIELDS:
        if field not in frontmatter:
            result["missing_required"].append(field)

    metadata = frontmatter.get("metadata", {})
    if not isinstance(metadata, dict):
        result["invalid_values"].append("metadata must be a mapping")
    else:
        for meta_field in REQUIRED_METADATA:
            if meta_field not in metadata:
                result["missing_required"].append(f"metadata.{meta_field}")

    for field in RECOMMENDED_FIELDS:
        if field not in frontmatter:
            result["missing_recommended"].append(field)

    # Orchestration block (x-31c-orchestration) - workspace extension, namespaced
    # to signal "not part of Anthropic's standard SKILL.md spec".
    orch = frontmatter.get(ORCHESTRATION_BLOCK)
    if orch is None:
        result["missing_required"].append(ORCHESTRATION_BLOCK)
    elif not isinstance(orch, dict):
        result["invalid_values"].append(f"{ORCHESTRATION_BLOCK} must be a mapping, got {type(orch).__name__}")
    else:
        for field in REQUIRED_ORCH_FIELDS:
            if field not in orch:
                result["missing_required"].append(f"{ORCHESTRATION_BLOCK}.{field}")

        if "parallel_safe" in orch:
            value = orch["parallel_safe"]
            if str(value).lower() not in {"true", "false", "partial"}:
                result["invalid_values"].append(f"{ORCHESTRATION_BLOCK}.parallel_safe={value!r} (must be true|false|partial)")

        if "shared_state" in orch:
            value = orch["shared_state"]
            if not isinstance(value, list):
                result["invalid_values"].append(f"{ORCHESTRATION_BLOCK}.shared_state must be a list, got {type(value).__name__}")

        if "triggers" in orch:
            value = orch["triggers"]
            if not isinstance(value, list):
                result["invalid_values"].append(f"{ORCHESTRATION_BLOCK}.triggers must be a list, got {type(value).__name__}")

    if result["missing_required"] or result["invalid_values"]:
        result["status"] = "FAIL"
    elif result["missing_recommended"]:
        result["status"] = "WARN"
    else:
        result["status"] = "PASS"

    return result


def audit_skills(skills_dir: Path) -> list[dict]:
    """Walk skills directory and audit every SKILL.md."""
    results = []
    for skill_dir in sorted(skills_dir.iterdir()):
        if not skill_dir.is_dir():
            continue
        if skill_dir.name.startswith("."):
            continue
        if skill_dir.name == "archive":
            continue
        results.append(check_skill(skill_dir))
    return results


def print_report(results: list[dict], summary_only: bool = False) -> dict:
    """Print human-readable audit report. Returns counts dict."""
    counts = {"PASS": 0, "WARN": 0, "FAIL": 0, "ERROR": 0}
    for r in results:
        counts[r["status"]] += 1

    total = len(results)
    print(f"\n{BOLD}Skill Metadata Audit{RESET}")
    print(f"{GRAY}{'=' * 60}{RESET}")
    print(f"Total skills: {total}")
    print(f"  {GREEN}PASS:{RESET}  {counts['PASS']}")
    print(f"  {YELLOW}WARN:{RESET}  {counts['WARN']}  (missing recommended fields only)")
    print(f"  {RED}FAIL:{RESET}  {counts['FAIL']}  (missing required fields or invalid values)")
    print(f"  {RED}ERROR:{RESET} {counts['ERROR']} (no SKILL.md or malformed frontmatter)")

    if summary_only:
        return counts

    for r in results:
        if r["status"] == "PASS":
            continue
        color = {"WARN": YELLOW, "FAIL": RED, "ERROR": RED}.get(r["status"], GRAY)
        print(f"\n{color}[{r['status']}]{RESET} {BOLD}{r['name']}{RESET}  ({r['path']})")
        if r["error"]:
            print(f"  {RED}error:{RESET} {r['error']}")
        if r["missing_required"]:
            print(f"  {RED}missing required:{RESET} {', '.join(r['missing_required'])}")
        if r["invalid_values"]:
            print(f"  {RED}invalid:{RESET} {'; '.join(r['invalid_values'])}")
        if r["missing_recommended"]:
            print(f"  {YELLOW}missing recommended:{RESET} {', '.join(r['missing_recommended'])}")

    return counts


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit SKILL.md frontmatter completeness.")
    parser.add_argument("--summary", action="store_true", help="Counts only, no per-skill detail")
    parser.add_argument("--fail-on-missing", action="store_true", help="Exit 1 if any skill has missing required fields")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON instead of human report")
    args = parser.parse_args()

    root = get_workspace_root()
    skills_dir = root / ".claude" / "skills"
    if not skills_dir.exists():
        print(f"{RED}skills directory not found:{RESET} {skills_dir}")
        return 2

    results = audit_skills(skills_dir)

    if args.json:
        print(json.dumps({
            "total": len(results),
            "skills": results,
        }, indent=2))
    else:
        counts = print_report(results, summary_only=args.summary)
        if args.fail_on_missing and (counts["FAIL"] > 0 or counts["ERROR"] > 0):
            return 1

    if args.fail_on_missing:
        counts = {"FAIL": sum(1 for r in results if r["status"] == "FAIL"),
                  "ERROR": sum(1 for r in results if r["status"] == "ERROR")}
        if counts["FAIL"] > 0 or counts["ERROR"] > 0:
            return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
