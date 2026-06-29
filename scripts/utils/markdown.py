#!/usr/bin/env python3
"""
Shared markdown parsing utilities for 31C workspace scripts.

Library module (snake_case per workspace naming convention) - importable
by any script that needs to parse YAML frontmatter or simple key:value
config blocks from markdown files.

Public surface:

- ``parse_frontmatter(text)`` -> ``(Dict, str)``
    Split ``---\\n{yaml}\\n---\\n{body}`` and return
    ``(parsed_yaml_dict, body_text)``. Uses ``yaml.safe_load`` when PyYAML
    is available (handles inline lists, block lists, quoted strings,
    booleans, numbers). Falls back to a regex parser otherwise. Returns
    ``({}, text)`` if no frontmatter is present.

- ``parse_frontmatter_str(text)`` -> ``(Dict[str, str], str)``
    String-coerced variant for legacy callers (crm-health.py, aggregate-crm.py,
    skill-metadata-check.py loose variant). All values become strings.

- ``parse_config(text, key)`` -> ``Optional[str]``
    Extract a single ``key: value`` pair from a ``## Config:`` (or similarly
    named) markdown block. No existing callers in the workspace use this
    today - it is provided for future scripts that adopt the convention.
    Returns ``None`` if not found.

Extracted in Phase 6.2 of the 2026-05-12 workspace performance tune-up.
Phase 6.2 mop-up (2026-05-12) migrated ``odin-brain-health.py`` and
``marp_render.py`` to thin wrappers around the shared util.

Intentionally NOT migrated (each script's local ``parse_frontmatter`` carries a
comment block at the call site explaining why):

- ``scripts/skill-metadata-check.py`` - the audit's value is its detailed error
  taxonomy (no opening fence, no closing fence, YAML parse error, empty,
  non-mapping). The shared util collapses all of these into ``({}, text)``,
  which would erase the diagnostics this script exists to surface.

- ``scripts/merge-contacts.py`` - paired with a naive ``serialize_frontmatter``
  that round-trips through ``f"{key}: {value}"``. Switching to ``yaml.safe_load``
  would surface native ``datetime.date``/``int``/``bool`` types that the
  serializer cannot stringify safely - the parser and serializer must migrate
  together or the merged CRM file would corrupt.

- ``scripts/promote-knowledge.py`` - returns the raw YAML block as a string
  (not a parsed dict) so ``inject_frontmatter_fields`` can do line-level edits
  that preserve the author's original quoting, comments, ordering, and
  whitespace byte-for-byte. The "promote without rewriting" contract is
  incompatible with round-tripping through PyYAML.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Optional, Tuple

try:
    import yaml
    HAS_YAML = True
except ImportError:
    HAS_YAML = False


_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*(?:\n|$)", re.DOTALL)


def _regex_parse_yaml(raw_yaml: str) -> Dict[str, Any]:
    """Minimal regex YAML parser used when PyYAML is not available.

    Handles ``key: value``, simple inline lists ``[a, b]``, booleans,
    and quoted strings. Does not handle block lists or nested mappings -
    callers that need those should ensure PyYAML is installed.
    """
    data: Dict[str, Any] = {}
    for line in raw_yaml.split("\n"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        colon_idx = line.find(":")
        if colon_idx == -1:
            continue
        key = line[:colon_idx].strip()
        value: Any = line[colon_idx + 1:].strip()
        if value.startswith('"') and value.endswith('"'):
            value = value[1:-1]
        elif value.startswith("'") and value.endswith("'"):
            value = value[1:-1]
        elif value.startswith("[") and value.endswith("]"):
            value = [v.strip().strip('"').strip("'") for v in value[1:-1].split(",") if v.strip()]
        elif value.lower() == "true":
            value = True
        elif value.lower() == "false":
            value = False
        data[key] = value
    return data


def parse_frontmatter(text: str) -> Tuple[Dict[str, Any], str]:
    """Parse YAML frontmatter from a markdown document.

    Returns ``(metadata_dict, body)``. Returns ``({}, text)`` if the
    document has no frontmatter.

    Uses ``yaml.safe_load`` when PyYAML is installed (preserves native
    types: lists, ints, bools, nested mappings). Falls back to a regex
    parser otherwise.
    """
    if not text:
        return {}, text

    match = _FRONTMATTER_RE.match(text)
    if not match:
        return {}, text

    raw_yaml = match.group(1)
    body = text[match.end():]

    if HAS_YAML:
        try:
            data = yaml.safe_load(raw_yaml)
            if isinstance(data, dict):
                return data, body
            return {}, body
        except yaml.YAMLError:
            pass  # Fall through to regex parser

    return _regex_parse_yaml(raw_yaml), body


def parse_frontmatter_str(text: str) -> Tuple[Dict[str, str], str]:
    """String-coerced variant of :func:`parse_frontmatter`.

    All values become strings (``None`` becomes ``""``). Used by callers
    that historically string-coerced everything (crm-health.py,
    aggregate-crm.py). New code should prefer :func:`parse_frontmatter`.
    """
    data, body = parse_frontmatter(text)
    coerced: Dict[str, str] = {}
    for k, v in data.items():
        if v is None:
            coerced[k] = ""
        elif isinstance(v, (list, dict)):
            # Best-effort string form for compatibility; complex types are
            # uncommon in CRM-style frontmatter but should not crash.
            coerced[k] = str(v)
        else:
            coerced[k] = str(v)
    return coerced, body


_CONFIG_BLOCK_RE = re.compile(
    r"##\s*Config(?:uration)?\s*:?\s*\n(?P<block>(?:.*\n)*?)(?:\n##|\Z)",
    re.IGNORECASE,
)


def parse_config(text: str, key: str) -> Optional[str]:
    """Extract a ``key: value`` from a ``## Config:`` markdown block.

    Convention: a section like::

        ## Config

        cadence: 14
        timezone: the configured timezone

    Returns the value as a string, or ``None`` if the block or the key
    is absent. The block ends at the next ``##`` heading or end of file.

    No existing workspace scripts use this convention today; this primitive
    is provided so future scripts can adopt a consistent pattern instead of
    inventing yet another parser.
    """
    if not text or not key:
        return None

    block_match = _CONFIG_BLOCK_RE.search(text)
    if not block_match:
        return None

    block = block_match.group("block")
    for line in block.split("\n"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        colon_idx = line.find(":")
        if colon_idx == -1:
            continue
        k = line[:colon_idx].strip()
        if k != key:
            continue
        value = line[colon_idx + 1:].strip()
        if value.startswith('"') and value.endswith('"'):
            value = value[1:-1]
        elif value.startswith("'") and value.endswith("'"):
            value = value[1:-1]
        return value

    return None
