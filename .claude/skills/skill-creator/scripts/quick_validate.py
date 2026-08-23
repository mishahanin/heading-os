#!/usr/bin/env python3
"""
Quick validation script for skills - minimal version

FRONTMATTER ALLOWLIST (widened 2026-08-23). This file arrived carrying only the
six upstream Anthropic keys, and rejected every key this workspace adds on top
of them. Measured: 96 of 96 skills failed. `package_skill.package_skill()`
hard-gates on `validate_skill`, so packaging was broken for every real skill and
nothing reported it.

Two groups are now accepted beyond the upstream six:

- Harness keys Claude Code itself reads from SKILL.md frontmatter:
  `argument-hint`, `model`, `disable-model-invocation`, `context`, `background`,
  `effort`.
- Anything under the `x-heading-` namespace. `scripts/skill-metadata-check.py`
  is the workspace's real frontmatter contract and states the reason for the
  prefix: it signals "workspace extension, not part of Anthropic's standard
  SKILL.md spec" so future stricter validation does not strip it. A bare
  `heading-orchestration:` is still rejected — the prefix IS the contract.

Guarded by tests/test_skill_creator_validator_accepts_real_skills.py, which
checks both directions: every shipped skill passes, and a typo'd or
un-namespaced key still fails.
"""

import sys
import os
import re
import yaml
from pathlib import Path

# Upstream Anthropic keys, plus the harness keys Claude Code reads itself.
ALLOWED_PROPERTIES = {
    'name', 'description', 'license', 'allowed-tools', 'metadata',
    'compatibility',
    'argument-hint', 'model', 'disable-model-invocation', 'context',
    'background', 'effort',
}

# Workspace extension blocks. Namespaced on purpose; see the module docstring.
NAMESPACE_PREFIX = 'x-heading-'


def validate_skill(skill_path):
    """Basic validation of a skill"""
    skill_path = Path(skill_path)

    # Check SKILL.md exists
    skill_md = skill_path / 'SKILL.md'
    if not skill_md.exists():
        return False, "SKILL.md not found"

    # Read and validate frontmatter
    content = skill_md.read_text()
    if not content.startswith('---'):
        return False, "No YAML frontmatter found"

    # Extract frontmatter
    match = re.match(r'^---\n(.*?)\n---', content, re.DOTALL)
    if not match:
        return False, "Invalid frontmatter format"

    frontmatter_text = match.group(1)

    # Parse YAML frontmatter
    try:
        frontmatter = yaml.safe_load(frontmatter_text)
        if not isinstance(frontmatter, dict):
            return False, "Frontmatter must be a YAML dictionary"
    except yaml.YAMLError as e:
        return False, f"Invalid YAML in frontmatter: {e}"

    # Check for unexpected properties (excluding nested keys under metadata,
    # and anything under the x-heading- extension namespace)
    unexpected_keys = {
        k for k in frontmatter
        if k not in ALLOWED_PROPERTIES and not k.startswith(NAMESPACE_PREFIX)
    }
    if unexpected_keys:
        return False, (
            f"Unexpected key(s) in SKILL.md frontmatter: {', '.join(sorted(unexpected_keys))}. "
            f"Allowed properties are: {', '.join(sorted(ALLOWED_PROPERTIES))}, "
            f"plus any '{NAMESPACE_PREFIX}*' extension key"
        )

    # Check required fields
    if 'name' not in frontmatter:
        return False, "Missing 'name' in frontmatter"
    if 'description' not in frontmatter:
        return False, "Missing 'description' in frontmatter"

    # Extract name for validation
    name = frontmatter.get('name', '')
    if not isinstance(name, str):
        return False, f"Name must be a string, got {type(name).__name__}"
    name = name.strip()
    if name:
        # Check naming convention (kebab-case: lowercase with hyphens)
        if not re.match(r'^[a-z0-9-]+$', name):
            return False, f"Name '{name}' should be kebab-case (lowercase letters, digits, and hyphens only)"
        if name.startswith('-') or name.endswith('-') or '--' in name:
            return False, f"Name '{name}' cannot start/end with hyphen or contain consecutive hyphens"
        # Check name length (max 64 characters per spec)
        if len(name) > 64:
            return False, f"Name is too long ({len(name)} characters). Maximum is 64 characters."

    # Extract and validate description
    description = frontmatter.get('description', '')
    if not isinstance(description, str):
        return False, f"Description must be a string, got {type(description).__name__}"
    description = description.strip()
    if description:
        # Check for angle brackets
        if '<' in description or '>' in description:
            return False, "Description cannot contain angle brackets (< or >)"
        # Check description length (max 1024 characters per spec)
        if len(description) > 1024:
            return False, f"Description is too long ({len(description)} characters). Maximum is 1024 characters."

    # Validate compatibility field if present (optional)
    compatibility = frontmatter.get('compatibility', '')
    if compatibility:
        if not isinstance(compatibility, str):
            return False, f"Compatibility must be a string, got {type(compatibility).__name__}"
        if len(compatibility) > 500:
            return False, f"Compatibility is too long ({len(compatibility)} characters). Maximum is 500 characters."

    return True, "Skill is valid!"

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python quick_validate.py <skill_directory>")
        sys.exit(1)

    valid, message = validate_skill(sys.argv[1])
    print(message)
    sys.exit(0 if valid else 1)
