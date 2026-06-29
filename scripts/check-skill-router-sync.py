#!/usr/bin/env python3
"""Enforce that every skill directory in .claude/skills/ has a matching entry in .claude/rules/skill-router.md.

Closes the documentation propagation gap identified by the 2026-05-14 workspace deep audit:
adding a new skill required updating three files manually with no enforcement; router drift
was already plausible at 100+ skills.

Usage:
    python scripts/check-skill-router-sync.py              Exit 0 if in sync, exit 1 with missing names
    python scripts/check-skill-router-sync.py --quiet      Suppress success output; still exits non-zero on drift
"""

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils.colors import RED, GREEN, YELLOW, RESET  # noqa: E402
from scripts.utils.workspace import get_workspace_root  # noqa: E402

ROOT = get_workspace_root()
SKILLS_DIR = ROOT / ".claude" / "skills"
ROUTER_FILE = ROOT / ".claude" / "rules" / "skill-router.md"

# Skill subdirs that are not actual skills (archived, internal).
SKIP_SUBDIRS = {"archive", "_archive", ".cache"}


def list_skill_names() -> list[str]:
    """Return every skill name from `.claude/skills/{name}/SKILL.md`, sorted."""
    if not SKILLS_DIR.exists():
        return []
    names = []
    for child in SKILLS_DIR.iterdir():
        if not child.is_dir() or child.name in SKIP_SUBDIRS:
            continue
        if (child / "SKILL.md").exists():
            names.append(child.name)
    return sorted(names)


def extract_router_skill_names(router_text: str) -> set[str]:
    """Extract every skill name referenced as a /slash trigger in skill-router.md.

    Matches `/skill-name` patterns inside backticks on markdown table rows only.
    Plugin-namespaced forms (`/superpowers:brainstorming`) are also captured.
    Restricting to table rows excludes prose paragraphs (which legitimately mention
    removed/historical skill names like `/brainstorm` v5.1.0 stubs) - those are
    documentation context, not router entries.
    """
    pattern = re.compile(r"`/([a-z0-9][a-z0-9\-]*)(?:[\s\]]|`|:)")
    matches: list[str] = []
    for line in router_text.splitlines():
        stripped = line.lstrip()
        if not stripped.startswith("|"):
            continue
        matches.extend(pattern.findall(line))
    return {name for name in matches}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    parser.add_argument("--quiet", action="store_true", help="Suppress success output")
    args = parser.parse_args()

    if not ROUTER_FILE.exists():
        print(f"{RED}ERROR: {ROUTER_FILE} not found{RESET}", file=sys.stderr)
        return 2

    router_text = ROUTER_FILE.read_text(encoding="utf-8")
    router_names = extract_router_skill_names(router_text)
    skill_names = set(list_skill_names())

    missing_from_router = sorted(skill_names - router_names)
    missing_from_disk = sorted(router_names - skill_names)

    # `missing_from_disk` is informational only - the router references many plugin-namespaced
    # and external skills (claude-api, frontend-design, etc.) that have no local SKILL.md.
    # We only fail on the inverse: a local skill not surfaced in the router.
    if missing_from_router:
        print(f"{RED}FAIL{RESET}: {len(missing_from_router)} skill(s) in .claude/skills/ are not referenced in skill-router.md:", file=sys.stderr)
        for name in missing_from_router:
            print(f"  - {name}  (add a row to the relevant skill-router registry table)", file=sys.stderr)
        return 1

    if not args.quiet:
        print(f"{GREEN}OK{RESET}: {len(skill_names)} local skill(s) all referenced in skill-router.md.")
        if missing_from_disk:
            unmatched = [n for n in missing_from_disk if n not in {"slash-command"}]
            if unmatched and len(unmatched) <= 20:
                print(f"{YELLOW}Note{RESET}: router references {len(unmatched)} name(s) without a local SKILL.md (plugin-namespaced or external, expected): {', '.join(unmatched[:10])}{'...' if len(unmatched) > 10 else ''}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
