#!/usr/bin/env python3
"""Rename the legacy `x-31c-*` frontmatter namespace to `x-heading-*`.

One-shot, idempotent dev tool for F-4.2 of the engine remediation playbook. It
rewrites ONLY the two workspace-extension keys inside a SKILL.md's YAML
frontmatter block:

    x-31c-orchestration  ->  x-heading-orchestration
    x-31c-capability     ->  x-heading-capability

It is frontmatter-scoped by construction: the substitution is applied only to
the text between the opening `---` and the first following `---` line, so prose
in the skill body that merely mentions the namespace is never touched (those
mentions are handled deliberately in the plan's Step 3). Re-running is a no-op.

Usage:
    python scripts/dev/rename-x31c-namespace.py            # dry-run (default)
    python scripts/dev/rename-x31c-namespace.py --apply    # write changes
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from scripts.utils.workspace import get_workspace_root
from scripts.utils.colors import GREEN, YELLOW, GRAY, BOLD, RESET

# Opening `---` on line 1, up to the first following line that starts with `---`
# (which may carry trailing content, e.g. `---<!-- AUTO-GENERATED -->`). Mirrors
# the frontmatter matcher in bridge_daemon/sources/capabilities.py.
_FRONTMATTER_RE = re.compile(r"\A(---\s*\n)(.*?)(\n---[^\n]*\n)", re.DOTALL)

RENAMES = {
    "x-31c-orchestration": "x-heading-orchestration",
    "x-31c-capability": "x-heading-capability",
}


def rewrite_frontmatter(text: str) -> tuple[str, int]:
    """Return (new_text, num_substitutions). Only the frontmatter block is touched."""
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return text, 0
    open_fence, block, close_fence = m.group(1), m.group(2), m.group(3)
    total = 0
    for old, new in RENAMES.items():
        count = block.count(old)
        if count:
            block = block.replace(old, new)
            total += count
    if total == 0:
        return text, 0
    new_text = open_fence + block + close_fence + text[m.end():]
    return new_text, total


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Rename x-31c-* frontmatter namespace to x-heading-* across SKILL.md files."
    )
    parser.add_argument("--apply", action="store_true",
                        help="Write changes (default is a dry-run preview)")
    args = parser.parse_args()

    skills_dir = get_workspace_root() / ".claude" / "skills"
    if not skills_dir.exists():
        print(f"{YELLOW}skills directory not found:{RESET} {skills_dir}")
        return 2

    changed_files = 0
    total_subs = 0
    for skill_md in sorted(skills_dir.glob("*/SKILL.md")):
        text = skill_md.read_text(encoding="utf-8")
        new_text, subs = rewrite_frontmatter(text)
        if subs == 0:
            continue
        changed_files += 1
        total_subs += subs
        rel = skill_md.relative_to(get_workspace_root())
        if args.apply:
            skill_md.write_text(new_text, encoding="utf-8")
            print(f"{GREEN}rewrote{RESET} {rel} {GRAY}({subs} key(s)){RESET}")
        else:
            print(f"{YELLOW}would rewrite{RESET} {rel} {GRAY}({subs} key(s)){RESET}")

    mode = "applied" if args.apply else "dry-run"
    print(f"\n{BOLD}{mode}:{RESET} {changed_files} file(s), {total_subs} key rename(s).")
    if not args.apply and changed_files:
        print(f"{GRAY}re-run with --apply to write.{RESET}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
