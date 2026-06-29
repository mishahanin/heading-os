#!/usr/bin/env python3
"""validate-crm-schema.py - Enforce CRM schemas on every contact and address-book file.

Closes P2.4 from the 2026-05-14 workspace deep audit. Replaces implicit template-by-convention
enforcement (scripts/crm-health.py emitted warnings without blocking) with explicit schema-
validator enforcement. Designed to be called from:

- scripts/aggregate-crm.py before aggregation (blocks records that would corrupt crm-central)
- pre-commit hook (planned) when crm/contacts/*.md files are staged
- ad-hoc via CLI for spot-checks

Dispatches to one of three schemas based on the record shape detected in the frontmatter:
- crm-contact.schema.json       legacy contacts (crm/contacts/*.md) - back-compat shim
- crm-address-book.schema.json  entity records (crm/address-book/*.md)
- crm-relationship.schema.json  per-exec relationship records (crm/contacts/*.md with entity_ref)

Usage:
    python scripts/validate-crm-schema.py                      # all contacts
    python scripts/validate-crm-schema.py --contact leo-marsh
    python scripts/validate-crm-schema.py --quiet              # exit code only
    python scripts/validate-crm-schema.py --json               # JSON report

Exit codes: 0 all valid, 1 one or more invalid, 2 setup error.

Falls back gracefully when `jsonschema` is not installed - emits "skipped" with exit 0 so
fresh clones that haven't run pip install don't break the pre-commit gate.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.utils.colors import GREEN, YELLOW, RED, RESET  # noqa: E402
from scripts.utils.workspace import (  # noqa: E402
    get_workspace_root, get_crm_contacts_dir, get_corporate_root,
)

ROOT = get_workspace_root()
CONTACTS_DIR = get_crm_contacts_dir()
ADDRESS_BOOK_DIR = get_corporate_root() / "crm" / "address-book"


def pick_schema(frontmatter: dict) -> str:
    """Return 'address-book' if entity-shape, 'relationship' if relationship-shape,
    else 'contact' for the back-compat shim."""
    # The triple-field test (slug + canonical_email + canonical_owner) is
    # intentional: any one alone could appear in a legacy contact via field drift,
    # but the combination is unique to the address-book entity record shape.
    if "slug" in frontmatter and "canonical_email" in frontmatter and "canonical_owner" in frontmatter:
        return "address-book"
    if "entity_ref" in frontmatter and "relationship_type" in frontmatter:
        return "relationship"
    return "contact"


def load_schemas() -> dict:
    """Load all three CRM schemas keyed by short name."""
    schemas_dir = ROOT / "config" / "schemas"
    return {
        "address-book": json.loads((schemas_dir / "crm-address-book.schema.json").read_text(encoding="utf-8")),
        "relationship": json.loads((schemas_dir / "crm-relationship.schema.json").read_text(encoding="utf-8")),
        "contact": json.loads((schemas_dir / "crm-contact.schema.json").read_text(encoding="utf-8")),
    }


def parse_frontmatter(path: Path) -> dict | None:
    """Extract YAML frontmatter from a contact .md file. Returns None when missing."""
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return None
    if not text.startswith("---\n"):
        return None
    end = text.find("\n---\n", 4)
    if end == -1:
        return None
    fm_raw = text[4:end]
    # Line-based YAML parser - handles flat key:value AND YAML array fields.
    # Supports:
    #   key: []                    -> []  (inline empty array)
    #   key: [a, b]                -> ["a", "b"]  (inline array)
    #   key:                       -> []  (multi-line array, items follow)
    #     - item1
    #     - item2
    # Does NOT handle nested objects (not present in CRM frontmatter).
    result: dict = {}
    lines = fm_raw.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].rstrip()
        i += 1
        if not line or line.startswith("#"):
            continue
        m = re.match(r'^([a-zA-Z_][a-zA-Z0-9_]*):\s*(.*)$', line)
        if not m:
            # Could be a list item continuation - already consumed by array logic below
            continue
        key, value = m.group(1), m.group(2).strip()

        # Inline empty array: key: []
        if value == "[]":
            result[key] = []
            continue

        # Inline populated array: key: [a, b, c]
        if value.startswith("[") and value.endswith("]"):
            inner = value[1:-1].strip()
            if inner:
                items = [s.strip().strip('"').strip("'") for s in inner.split(",")]
                result[key] = [it for it in items if it]
            else:
                result[key] = []
            continue

        # Multi-line array: key: (empty value) followed by "  - item" lines
        if value == "":
            # Peek ahead for list items
            arr: list = []
            while i < len(lines) and re.match(r'^\s+-\s+', lines[i]):
                item = re.sub(r'^\s+-\s+', '', lines[i]).strip()
                item = item.strip('"').strip("'")
                arr.append(item)
                i += 1
            if arr:
                result[key] = arr
                continue
            # Empty value with no list items - fall through to store as ""
            result[key] = ""
            continue

        # String values - strip surrounding quotes
        if value.startswith('"') and value.endswith('"'):
            value = value[1:-1]
        elif value.startswith("'") and value.endswith("'"):
            value = value[1:-1]

        # Coerce obvious integers - but NOT phone/telegram fields which may look numeric
        if key not in ("phone", "telegram", "zip", "postal_code") and re.fullmatch(r'-?\d+', value):
            try:
                result[key] = int(value)
                continue
            except ValueError:
                pass
        result[key] = value
    return result


def validate_one(path: Path, validator, schema_name: str = "contact", fm: dict | None = None) -> tuple[bool, list[str], str]:
    """Validate one contact file. Returns (ok, errors, schema_name)."""
    if fm is None:
        fm = parse_frontmatter(path)
    if fm is None:
        return False, ["missing or malformed YAML frontmatter"], schema_name
    errors = []
    for err in validator.iter_errors(fm):
        # Build a concise location like "type: 'badvalue' is not one of [...]"
        loc = ".".join(str(p) for p in err.absolute_path) or "<root>"
        errors.append(f"[{schema_name}] {loc}: {err.message}")
    return (len(errors) == 0), errors, schema_name


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    parser.add_argument("--contact", help="Validate only the named contact (slug, no .md)")
    parser.add_argument("--quiet", action="store_true", help="Emit only the failure summary")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    parser.add_argument("--dir", default=None,
                        help="Override base directory for validation (used for staged migration files). "
                             "Validates {dir}/contacts/ and {dir}/address-book/ if they exist.")
    args = parser.parse_args()

    try:
        import jsonschema
    except ImportError:
        if not args.quiet:
            print(f"{YELLOW}SKIP{RESET}: jsonschema not installed - run `pip install jsonschema` to enforce")
        return 0  # Fail open - don't block commits on missing dev dep

    # Verify all three schemas are present before proceeding
    schemas_dir = ROOT / "config" / "schemas"
    for schema_file in ("crm-contact.schema.json", "crm-address-book.schema.json", "crm-relationship.schema.json"):
        if not (schemas_dir / schema_file).exists():
            print(f"{RED}ERROR{RESET}: schema not found at {schemas_dir / schema_file}", file=sys.stderr)
            return 2

    schemas = load_schemas()
    validators = {name: jsonschema.Draft202012Validator(s) for name, s in schemas.items()}

    if args.contact:
        # Single-contact mode: search contacts dir only
        paths = [CONTACTS_DIR / f"{args.contact}.md"]
        if not paths[0].exists():
            print(f"{RED}ERROR{RESET}: {paths[0]} not found", file=sys.stderr)
            return 2
    else:
        # Collect all CRM files: contacts + address-book (address-book may not exist yet)
        # --dir overrides the default directories so that staged migration files
        # can be validated before they replace live data.
        if args.dir:
            base = Path(args.dir)
            contacts_dir = base / "contacts"
            address_book_dir = base / "address-book"
        else:
            contacts_dir = CONTACTS_DIR
            address_book_dir = ADDRESS_BOOK_DIR
        paths = sorted(contacts_dir.glob("*.md")) if contacts_dir.exists() else []
        if address_book_dir.exists():
            paths = paths + sorted(address_book_dir.glob("*.md"))

    results = []
    valid = 0
    for path in paths:
        fm = parse_frontmatter(path)
        if fm is None:
            schema_name = "contact"
            ok, errors, schema_name = False, ["missing or malformed YAML frontmatter"], schema_name
        else:
            schema_name = pick_schema(fm)
            validator = validators[schema_name]
            ok, errors, schema_name = validate_one(path, validator, schema_name, fm=fm)
        results.append({"contact": path.stem, "schema": schema_name, "valid": ok, "errors": errors})
        if ok:
            valid += 1
            if not args.quiet and not args.json:
                print(f"  {GREEN}OK{RESET}  {path.stem}  ({schema_name})")
        else:
            if not args.json:
                print(f"  {RED}FAIL{RESET}  {path.stem}  ({schema_name})")
                for err in errors:
                    print(f"    - {err}")

    if args.json:
        print(json.dumps({"total": len(paths), "valid": valid, "invalid": len(paths) - valid, "results": results}, indent=2))
    elif not args.quiet:
        print()
        if valid == len(paths):
            print(f"{GREEN}All {len(paths)} records pass schema.{RESET}")
        else:
            print(f"{RED}{len(paths) - valid} of {len(paths)} records fail schema.{RESET}")

    return 0 if valid == len(paths) else 1


if __name__ == "__main__":
    sys.exit(main())
