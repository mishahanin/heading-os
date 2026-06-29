#!/usr/bin/env python3
"""
Resolve per-skill customization using a three-layer TOML merge.

Lets fleet execs customize shared corporate skills without forking them. Reads
customization from three layers (highest priority last / wins):

  1. {skill-root}/customize.toml                       (skill defaults, committed with the skill)
  2. config/skill-custom/{name}.toml                   (team/org, corporate-synced, committed)
  3. config/skill-custom/{name}.user.toml              (personal, gitignored, local to the exec)

Skill name is derived from the basename of the skill directory.

Outputs merged JSON to stdout. Errors go to stderr.

Requires Python 3.11+ (uses stdlib `tomllib`). No `uv`, no `pip install`,
no virtualenv — plain `python3` is sufficient.

  python3 scripts/resolve_customization.py --skill .claude/skills/deep-think
  python3 scripts/resolve_customization.py --skill ... --key workflow
  python3 scripts/resolve_customization.py --skill ... --key workflow.persistent_facts

Merge rules (purely structural — no field-name special-casing):
  - Scalars (string, int, bool, float): override wins
  - Tables: deep merge (recursively apply these rules)
  - Arrays of tables where every item shares the *same* identifier
    field (every item has `code`, or every item has `id`):
    merge by that key (matching keys replace, new keys append)
  - All other arrays — including arrays where only some items have
    `code` or `id`, or where items mix the two keys:
    append (base items followed by override items)

No removal mechanism — overrides cannot delete base items. To suppress
a default, fork the skill or override the item by code with a no-op
description/prompt.
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils.workspace import get_workspace_root  # noqa: E402

try:
    import tomllib
except ImportError:
    sys.stderr.write(
        "error: Python 3.11+ is required (stdlib `tomllib` not found).\n"
        "Install a newer Python or run the resolution manually per the\n"
        "fallback instructions in the skill's SKILL.md.\n"
    )
    sys.exit(3)


_MISSING = object()
_KEYED_MERGE_FIELDS = ("code", "id")


def load_toml(file_path: Path, required: bool = False) -> dict:
    if not file_path.exists():
        if required:
            sys.stderr.write(f"error: required customization file not found: {file_path}\n")
            sys.exit(1)
        return {}
    try:
        with file_path.open("rb") as f:
            parsed = tomllib.load(f)
        if not isinstance(parsed, dict):
            if required:
                sys.stderr.write(f"error: {file_path} did not parse to a table\n")
                sys.exit(1)
            return {}
        return parsed
    except tomllib.TOMLDecodeError as error:
        level = "error" if required else "warning"
        sys.stderr.write(f"{level}: failed to parse {file_path}: {error}\n")
        if required:
            sys.exit(1)
        return {}
    except OSError as error:
        level = "error" if required else "warning"
        sys.stderr.write(f"{level}: failed to read {file_path}: {error}\n")
        if required:
            sys.exit(1)
        return {}


def _detect_keyed_merge_field(items):
    """Return 'code' or 'id' if every table item carries that *same* field.

    All items must share the same identifier (all `code`, or all `id`).
    Mixed arrays — where some items use `code` and others use `id` —
    return None and fall through to append semantics. This is intentional:
    mixing identifier keys within one array is a schema smell, and
    append-fallback is safer than guessing which key should merge.
    """
    if not items or not all(isinstance(item, dict) for item in items):
        return None
    for candidate in _KEYED_MERGE_FIELDS:
        if all(item.get(candidate) is not None for item in items):
            return candidate
    return None


def _merge_by_key(base, override, key_name):
    result = []
    index_by_key = {}

    for item in base:
        if not isinstance(item, dict):
            continue
        if item.get(key_name) is not None:
            index_by_key[item[key_name]] = len(result)
        result.append(dict(item))

    for item in override:
        if not isinstance(item, dict):
            result.append(item)
            continue
        key = item.get(key_name)
        if key is not None and key in index_by_key:
            result[index_by_key[key]] = dict(item)
        else:
            if key is not None:
                index_by_key[key] = len(result)
            result.append(dict(item))

    return result


def _merge_arrays(base, override):
    """Shape-aware array merge. Base + override combined tables may opt into
    keyed merge if every item has `code` or `id`. Otherwise: append."""
    base_arr = base if isinstance(base, list) else []
    override_arr = override if isinstance(override, list) else []
    keyed_field = _detect_keyed_merge_field(base_arr + override_arr)
    if keyed_field:
        return _merge_by_key(base_arr, override_arr, keyed_field)
    return base_arr + override_arr


def deep_merge(base, override):
    """Recursively merge override into base using structural rules.
    - Table + table: deep merge
    - Array + array: shape-aware (keyed merge if all items have code/id, else append)
    - Anything else: override wins
    """
    if isinstance(base, dict) and isinstance(override, dict):
        result = dict(base)
        for key, over_val in override.items():
            if key in result:
                result[key] = deep_merge(result[key], over_val)
            else:
                result[key] = over_val
        return result
    if isinstance(base, list) and isinstance(override, list):
        return _merge_arrays(base, override)
    return override


def extract_key(data, dotted_key: str):
    parts = dotted_key.split(".")
    current = data
    for part in parts:
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return _MISSING
    return current


def resolve(skill_dir: Path) -> dict:
    """Merge the three customization layers for a skill directory and return the result.

    Layer 1 (defaults) is required: a skill that opts into customization ships a
    customize.toml. Layers 2 and 3 live under config/skill-custom/ at the workspace
    root and are optional — a missing team or user override is simply an empty layer.
    """
    skill_dir = skill_dir.resolve()
    skill_name = skill_dir.name
    defaults = load_toml(skill_dir / "customize.toml", required=True)

    custom_dir = get_workspace_root() / "config" / "skill-custom"
    team = load_toml(custom_dir / f"{skill_name}.toml")
    user = load_toml(custom_dir / f"{skill_name}.user.toml")

    merged = deep_merge(defaults, team)
    merged = deep_merge(merged, user)
    return merged


def write_json_stdout(output):
    """Write JSON as UTF-8 so Windows cp1252 stdout can carry emoji icons."""
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if reconfigure is not None:
        reconfigure(encoding="utf-8")
    sys.stdout.write(json.dumps(output, indent=2, ensure_ascii=False) + "\n")


def main():
    parser = argparse.ArgumentParser(
        description="Resolve per-skill customization using a three-layer TOML merge.",
        add_help=True,
    )
    parser.add_argument(
        "--skill", "-s", required=True,
        help="Path to the skill directory (must contain customize.toml)",
    )
    parser.add_argument(
        "--key", "-k", action="append", default=[],
        help="Dotted field path to resolve (repeatable). Omit for full dump.",
    )
    args = parser.parse_args()

    merged = resolve(Path(args.skill))

    if args.key:
        output = {}
        for key in args.key:
            value = extract_key(merged, key)
            if value is not _MISSING:
                output[key] = value
    else:
        output = merged

    write_json_stdout(output)


if __name__ == "__main__":
    main()
