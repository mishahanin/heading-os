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

# A CRM record filename stem: no separators, no dots, no traversal.
_CONTACT_SLUG_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*")
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
    """Extract YAML frontmatter from a contact .md file. Returns None when missing.

    NOT MIGRATED to ``scripts.utils.markdown.parse_frontmatter``. This parser is
    schema-aware, not generic: the value types it produces ARE what jsonschema
    then checks, so swapping it silently rewrites the validation result.
    Measured 2026-08-20 over the live 326-record corpus (165 contacts + 161
    address-book entities), counting records whose parsed values violate a type
    declared in config/schemas/:

      this parser                  0 / 326   (was 1 before the block-list fix
                                              below; a list at its key's own
                                              column read as an empty string)
      -> parse_frontmatter        326 / 326  (last_touch etc. become datetime.date
                                              where every schema declares string)
      -> parse_frontmatter_str    170 / 326  (tags become "['a', 'b']" where every
                                              schema declares array)

    Two more divergences the type count does not show: ``yaml.safe_load`` reads a
    ``#`` inside an unquoted value as a comment and truncates it (one record's
    ``source`` loses its trailing "#"-prefixed reference), and the int coercion
    below deliberately EXCLUDES
    phone/telegram/zip/postal_code so a numeric-looking phone stays a string for
    the string-typed schema field. Keep this parser paired with the schemas.
    """
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
            # `\s*`, not `\s+`: YAML lets a block list sit at its key's own
            # column, and one live record does. With `\s+` that record read as
            # `tags: ''` and the gate reported a type error against a file that
            # is correct on disk.
            while i < len(lines) and re.match(r'^\s*-\s+', lines[i]):
                item = re.sub(r'^\s*-\s+', '', lines[i]).strip()
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
        if value.startswith('"') and value.endswith('"') or value.startswith("'") and value.endswith("'"):
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
        # Still fail open, because the pre-commit hook and the migration
        # verifier both read the exit code and a hard failure here
        # blocks every commit until the dependency lands. But say so on EVERY
        # path, including --quiet and --json.
        #
        # --quiet used to suppress this line, so the pre-commit hook printed
        # nothing and exited 0. Measured 2026-08-20: `jsonschema` is not
        # installed in this venv and is declared in no manifest, so four
        # callers had been reporting a green CRM schema gate that never ran a
        # single validation. "Quiet" means do not list the passing files; it
        # never meant hide that nothing was checked
        # (see .claude/rules/scope-claims.md).
        note = "jsonschema not installed - NOTHING was validated. Install it to enforce."
        if args.json:
            print(json.dumps({"status": "skipped", "validated": 0, "reason": note}))
        else:
            print(f"{YELLOW}SKIP{RESET}: {note}", file=sys.stderr)
        return 0

    # Verify all three schemas are present before proceeding
    schemas_dir = ROOT / "config" / "schemas"
    for schema_file in ("crm-contact.schema.json", "crm-address-book.schema.json", "crm-relationship.schema.json"):
        if not (schemas_dir / schema_file).exists():
            print(f"{RED}ERROR{RESET}: schema not found at {schemas_dir / schema_file}", file=sys.stderr)
            return 2

    try:
        schemas = load_schemas()
    except (OSError, json.JSONDecodeError) as exc:
        # Exit 2, not 1. `main` checked the schema files EXIST but not that they
        # parse, so a truncated schema raised out as exit 1 -- which this
        # script's own contract defines as "one or more invalid records",
        # sending the reader to hunt for a data defect in healthy data.
        print(f"{RED}ERROR{RESET}: a schema file is unreadable or not valid JSON: "
              f"{exc}", file=sys.stderr)
        return 2
    validators = {name: jsonschema.Draft202012Validator(s) for name, s in schemas.items()}

    searched: list[Path] = []
    if args.contact:
        # A CONTACT SLUG, not a path fragment. `CONTACTS_DIR / f"{arg}.md"` with
        # `../../somewhere/thing` resolved outside the contacts directory and
        # validated an arbitrary .md file. The user already has a shell, so this
        # is not a privilege boundary -- the path handling was simply wrong.
        if not _CONTACT_SLUG_RE.fullmatch(args.contact):
            print(f"{RED}ERROR{RESET}: --contact must be a bare slug "
                  f"(letters, digits, hyphen, underscore); got {args.contact!r}",
                  file=sys.stderr)
            return 2
        # Single-contact mode: search contacts dir only
        searched = [CONTACTS_DIR]
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
        searched = [contacts_dir, address_book_dir]
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

    # An EMPTY corpus is a setup error, not a pass. `valid == len(paths) == 0`
    # printed "All 0 records pass schema." and exited 0 for a typo'd --dir, a
    # moved CRM tree, or a fresh clone with no contacts -- a fully green gate
    # over nothing validated. The comment block above records this exact
    # fail-open class as a measured incident; it was closed for the missing
    # jsonschema path and left open here.
    if not paths:
        if args.json:
            print(json.dumps({"total": 0, "valid": 0, "invalid": 0,
                              "error": "no records found", "results": []}, indent=2))
        else:
            print(f"{RED}No records found to validate.{RESET} Checked: "
                  f"{', '.join(str(d) for d in searched) or '(nothing)'}",
                  file=sys.stderr)
            print(f"{RED}Refusing to report a pass over an empty corpus.{RESET}",
                  file=sys.stderr)
        return 2

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
