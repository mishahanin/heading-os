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
import sys
from typing import Any, Callable, Dict, List, Optional, Tuple

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
        if value.startswith('"') and value.endswith('"') or value.startswith("'") and value.endswith("'"):
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
        if value.startswith('"') and value.endswith('"') or value.startswith("'") and value.endswith("'"):
            value = value[1:-1]
        return value
    return None


# ============================================================
# Markdown tables
# ============================================================

HEADER_SCAN_LINES = 20


def _stderr_warn(message: str) -> None:
    print(f"parse_md_table: {message}", file=sys.stderr)


def split_table_row(line: str) -> List[str]:
    """Cells of one markdown table row, empty cells preserved positionally.

    The two dashboard generators each carried `[c for c in cells if c != ""]`,
    which DELETED an empty cell rather than keeping its position. A radar row
    `| Alice | | Smith | 14 | RED |` came back one cell short, so every value
    after the blank shifted one column left: Owner showed the company, health
    showed a number. Splitting on the pipes and dropping only the two outside
    the first and last pipe keeps the columns lined up.
    """
    cells = line.split("|")
    if cells and not cells[0].strip():
        cells = cells[1:]
    if cells and not cells[-1].strip():
        cells = cells[:-1]
    return [c.strip() for c in cells]


def parse_md_table(text: str, header_pattern: Optional[str] = None, *,
                   source: str = "<text>",
                   warn: Optional[Callable[[str], None]] = None) -> List[Dict[str, str]]:
    """Rows of the first markdown table in `text`, as dicts keyed by header.

    `header_pattern` is a regex; the search for the table starts at the first
    line matching it. `source` names the file in warnings. `warn` overrides the
    stderr warning sink.

    Loud where the two script-local copies were silent. Both of those dropped
    any row with fewer cells than headers, so a pipeline deal with one empty
    Notes cell vanished from the deal count, the total value, the weighted
    value, the stage counts and the top-three, with nothing written anywhere.
    A short row is now padded and reported; the row survives.

    A blank line ends the table. The old copies did `if not line: continue`
    inside the row loop, so two tables separated by one blank line merged and
    the second table's header row was parsed as data of the first.
    """
    emit = warn or _stderr_warn
    lines = text.split("\n")
    start = 0
    if header_pattern:
        for i, line in enumerate(lines):
            if re.search(header_pattern, line):
                start = i
                break
        else:
            return []

    headers: Optional[List[str]] = None
    data_start: Optional[int] = None
    for i in range(start, min(start + HEADER_SCAN_LINES, len(lines))):
        line = lines[i].strip()
        if "|" in line and "---" not in line and not headers:
            headers = split_table_row(line)
            continue
        if headers and "---" in line:
            data_start = i + 1
            break

    if not headers or data_start is None:
        if header_pattern:
            # Silence here read as "the table is empty". It also covers "the
            # table is more than HEADER_SCAN_LINES below its heading", which
            # renders as "No data available" and looks legitimate.
            emit(f"{source}: no table found within {HEADER_SCAN_LINES} lines of "
                 f"/{header_pattern}/")
        return []

    rows: List[Dict[str, str]] = []
    for i in range(data_start, len(lines)):
        line = lines[i].strip()
        if not line or not line.startswith("|"):
            break
        cells = split_table_row(line)
        if len(cells) < len(headers):
            emit(f"{source} line {i + 1}: row has {len(cells)} cells, header has "
                 f"{len(headers)}; padding. Row: {line}")
        elif len(cells) > len(headers):
            emit(f"{source} line {i + 1}: row has {len(cells)} cells, header has "
                 f"{len(headers)}; extra cells dropped. Row: {line}")
        rows.append({h: cells[j] if j < len(cells) else ""
                     for j, h in enumerate(headers)})
    return rows

    return None
