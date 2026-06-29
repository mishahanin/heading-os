#!/usr/bin/env python3
"""
google-contacts.py -- Google Contacts CLI for Claude Code

Standalone connector for Google Contacts. Search, add, edit, list,
get, and delete contacts via the Google People API.

Prerequisites:
    pip install google-api-python-client google-auth-httplib2 google-auth-oauthlib python-dotenv

Setup:
    1. Go to Google Cloud Console -> APIs & Services -> Credentials
    2. Create OAuth 2.0 Client ID (Desktop application type)
    3. Enable the People API (APIs & Services -> Library -> search "People API")
    4. Download the credentials JSON file
    5. Place at .sessions/google/credentials.json
       (or set GOOGLE_CONTACTS_CREDENTIALS_PATH in .env)
    6. Run any command -- first run opens browser for OAuth consent (handles 2FA)
    7. Token is cached at .sessions/google/token.json for subsequent runs

Usage:
    python scripts/google-contacts.py search "John"
    python scripts/google-contacts.py add --name "John Doe" --email "john@example.com" --company "Acme"
    python scripts/google-contacts.py get people/c1234567890
    python scripts/google-contacts.py edit people/c1234567890 --phone "+1-555-0100"
    python scripts/google-contacts.py list --limit 50
    python scripts/google-contacts.py delete people/c1234567890

Environment:
    GOOGLE_CONTACTS_CREDENTIALS_PATH  Path to credentials.json (optional)
                                       Default: .sessions/google/credentials.json
"""

import argparse
import io
import json
import os
import sys

# ---------------------------------------------------------------------------
# Windows console encoding fix
# ---------------------------------------------------------------------------
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# ---------------------------------------------------------------------------
# Workspace root resolution
# ---------------------------------------------------------------------------
from pathlib import Path

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
WORKSPACE_ROOT = os.path.dirname(SCRIPT_DIR)

# Session paths
SESSION_DIR = str(Path(WORKSPACE_ROOT) / ".sessions" / "google")
TOKEN_PATH = str(Path(SESSION_DIR) / "token.json")

# Load .env via central loader
sys.path.insert(0, WORKSPACE_ROOT)
from scripts.utils.workspace import load_env  # noqa: E402

load_env(Path(WORKSPACE_ROOT))

# ---------------------------------------------------------------------------
# ANSI colors
# ---------------------------------------------------------------------------
# Shared palette from scripts/utils/colors.py; DIM is not in that module, so it
# stays local. (2026-06-09 audit #43 — removed the duplicated color block.)
from scripts.utils.colors import GREEN, YELLOW, RED, CYAN, BOLD, RESET  # noqa: E402
DIM = "\033[2m"

# ---------------------------------------------------------------------------
# Google People API constants
# ---------------------------------------------------------------------------
SCOPES = ["https://www.googleapis.com/auth/contacts"]
PERSON_FIELDS = "names,emailAddresses,phoneNumbers,organizations,biographies,addresses,urls,birthdays,events,metadata"


# ===========================================================================
# Dependency check
# ===========================================================================
def _check_dependencies():
    missing = []
    try:
        from google.oauth2.credentials import Credentials  # noqa: F401
    except ImportError:
        missing.append("google-api-python-client")
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow  # noqa: F401
    except ImportError:
        missing.append("google-auth-oauthlib")
    try:
        from google.auth.transport.requests import Request  # noqa: F401
    except ImportError:
        if "google-api-python-client" not in missing:
            missing.append("google-auth-httplib2")
    if missing:
        print(f"{RED}[ERROR] Missing packages: {', '.join(missing)}{RESET}", file=sys.stderr)
        print(
            "        Run: pip install google-api-python-client google-auth-httplib2 "
            "google-auth-oauthlib python-dotenv",
            file=sys.stderr,
        )
        sys.exit(1)


_check_dependencies()


# ===========================================================================
# Authentication
# ===========================================================================
def _get_credentials_path():
    """Resolve path to OAuth credentials.json."""
    env_path = os.environ.get("GOOGLE_CONTACTS_CREDENTIALS_PATH")
    if env_path:
        resolved = env_path if os.path.isabs(env_path) else str(Path(WORKSPACE_ROOT) / env_path)
        if os.path.exists(resolved):
            return resolved

    default_path = str(Path(SESSION_DIR) / "credentials.json")
    if os.path.exists(default_path):
        return default_path

    print(f"{RED}[ERROR] Google OAuth credentials.json not found.{RESET}", file=sys.stderr)
    print("", file=sys.stderr)
    print("  Setup steps:", file=sys.stderr)
    print("  1. Go to https://console.cloud.google.com/apis/credentials", file=sys.stderr)
    print("  2. Create OAuth 2.0 Client ID (Desktop application)", file=sys.stderr)
    print("  3. Enable the People API (Library -> search 'People API')", file=sys.stderr)
    print("  4. Download the JSON file", file=sys.stderr)
    print(f"  5. Save it as: {default_path}", file=sys.stderr)
    print("     Or set GOOGLE_CONTACTS_CREDENTIALS_PATH in .env", file=sys.stderr)
    sys.exit(1)


def authenticate():
    """Authenticate with Google and return a People API service object.

    First run opens browser for OAuth consent (handles 2FA transparently).
    Token is cached and auto-refreshed for subsequent runs.
    """
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    os.makedirs(SESSION_DIR, mode=0o700, exist_ok=True)
    creds = None

    # Load cached token
    if os.path.exists(TOKEN_PATH):
        creds = Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)

    # Refresh or re-authenticate
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception:
                creds = None
        if not creds:
            creds_path = _get_credentials_path()
            flow = InstalledAppFlow.from_client_secrets_file(creds_path, SCOPES)
            creds = flow.run_local_server(port=0)
            print(f"{GREEN}[OK] Authenticated with Google{RESET}", file=sys.stderr)

        with open(TOKEN_PATH, "w", encoding="utf-8") as f:
            f.write(creds.to_json())
        os.chmod(TOKEN_PATH, 0o600)

    return build("people", "v1", credentials=creds)


# ===========================================================================
# Search warmup
# ===========================================================================
def _warmup_search(service):
    """Perform the required warmup call for searchContacts API.

    Google People API requires an initial empty-query search before
    real searches return results. Called once per invocation.
    """
    try:
        service.people().searchContacts(query="", readMask="names").execute()
    except Exception as e:
        # Warmup is best-effort; real searches handle their own errors.
        # We log to stderr so the failure is visible but non-fatal.
        print(f"[debug] searchContacts warmup fallback: {e}", file=sys.stderr)


# ===========================================================================
# Output formatting
# ===========================================================================
def _format_summary(person):
    """Format a contact as a summary dict for table display."""
    resource = person.get("resourceName", "")
    names = person.get("names", [])
    name = names[0].get("displayName", "(no name)") if names else "(no name)"
    emails = person.get("emailAddresses", [])
    email = emails[0].get("value", "") if emails else ""
    phones = person.get("phoneNumbers", [])
    phone = phones[0].get("value", "") if phones else ""
    orgs = person.get("organizations", [])
    company = orgs[0].get("name", "") if orgs else ""
    title = orgs[0].get("title", "") if orgs else ""
    role = f"{title}, {company}" if title and company else title or company
    return {
        "resourceName": resource,
        "name": name,
        "email": email,
        "phone": phone,
        "role": role,
    }


def _print_table(contacts):
    """Print contacts as an aligned table."""
    if not contacts:
        print(f"{YELLOW}No contacts found.{RESET}")
        return
    print(f"\n{BOLD}{'Name':<30} {'Email':<35} {'Phone':<18} {'Role':<30}{RESET}")
    print(f"{'-'*30} {'-'*35} {'-'*18} {'-'*30}")
    for c in contacts:
        print(f"{c['name'][:29]:<30} {c['email'][:34]:<35} {c['phone'][:17]:<18} {c['role'][:29]:<30}")
    print(f"\n{DIM}{len(contacts)} contact(s){RESET}")


def _print_detail(person):
    """Print a single contact in detailed format."""
    names = person.get("names", [])
    name = names[0].get("displayName", "(no name)") if names else "(no name)"
    resource = person.get("resourceName", "")
    etag = person.get("etag", "")

    print(f"\n{BOLD}{CYAN}{name}{RESET}")
    print(f"{DIM}Resource: {resource}{RESET}")
    print(f"{DIM}ETag: {etag}{RESET}")
    print()

    emails = person.get("emailAddresses", [])
    if emails:
        print(f"  {BOLD}Email:{RESET}")
        for e in emails:
            label = e.get("type", "other")
            print(f"    {e['value']}  ({label})")

    phones = person.get("phoneNumbers", [])
    if phones:
        print(f"  {BOLD}Phone:{RESET}")
        for p in phones:
            label = p.get("type", "other")
            print(f"    {p['value']}  ({label})")

    birthdays = person.get("birthdays", [])
    if birthdays:
        print(f"  {BOLD}Birthday:{RESET}")
        for b in birthdays:
            d = b.get("date", {})
            parts = []
            if d.get("year"):
                parts.append(str(d["year"]))
            if d.get("month"):
                parts.append(f"{d['month']:02d}")
            if d.get("day"):
                parts.append(f"{d['day']:02d}")
            print(f"    {'-'.join(parts)}")

    events = person.get("events", [])
    if events:
        print(f"  {BOLD}Events:{RESET}")
        for ev in events:
            d = ev.get("date", {})
            label = ev.get("formattedType", ev.get("type", ""))
            parts = []
            if d.get("year"):
                parts.append(str(d["year"]))
            if d.get("month"):
                parts.append(f"{d['month']:02d}")
            if d.get("day"):
                parts.append(f"{d['day']:02d}")
            print(f"    {'-'.join(parts)}  ({label})")

    orgs = person.get("organizations", [])
    if orgs:
        print(f"  {BOLD}Organization:{RESET}")
        for o in orgs:
            title = o.get("title", "")
            company = o.get("name", "")
            parts = [x for x in [title, company] if x]
            print(f"    {' at '.join(parts)}" if parts else "    (no details)")

    addresses = person.get("addresses", [])
    if addresses:
        print(f"  {BOLD}Address:{RESET}")
        for a in addresses:
            formatted = a.get("formattedValue", a.get("streetAddress", ""))
            label = a.get("type", "other")
            print(f"    {formatted}  ({label})")

    urls = person.get("urls", [])
    if urls:
        print(f"  {BOLD}URLs:{RESET}")
        for u in urls:
            print(f"    {u.get('value', '')}")

    bios = person.get("biographies", [])
    if bios:
        print(f"  {BOLD}Notes:{RESET}")
        for b in bios:
            print(f"    {b.get('value', '')}")

    print()


# ===========================================================================
# Commands
# ===========================================================================
def cmd_search(service, query, as_json=False):
    """Search contacts by name, email, phone, etc."""
    _warmup_search(service)

    results = service.people().searchContacts(
        query=query,
        readMask=PERSON_FIELDS,
        pageSize=30,
    ).execute()

    people = [r.get("person", {}) for r in results.get("results", [])]

    if as_json:
        print(json.dumps(people, indent=2, ensure_ascii=False))
    else:
        summaries = [_format_summary(p) for p in people]
        _print_table(summaries)
    return people


def cmd_add(service, name, email=None, phone=None, company=None, title=None,
            notes=None, address=None, url=None, as_json=False):
    """Create a new contact."""
    parts = name.strip().split(None, 1)
    body = {"names": [{"givenName": parts[0]}]}
    if len(parts) > 1:
        body["names"][0]["familyName"] = parts[1]

    if email:
        body["emailAddresses"] = [{"value": email}]
    if phone:
        body["phoneNumbers"] = [{"value": phone}]
    if company or title:
        org = {}
        if company:
            org["name"] = company
        if title:
            org["title"] = title
        body["organizations"] = [org]
    if notes:
        body["biographies"] = [{"value": notes, "contentType": "TEXT_PLAIN"}]
    if address:
        body["addresses"] = [{"formattedValue": address}]
    if url:
        body["urls"] = [{"value": url}]

    result = service.people().createContact(
        body=body,
        personFields=PERSON_FIELDS,
    ).execute()

    if as_json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        display_name = result.get("names", [{}])[0].get("displayName", name)
        resource = result.get("resourceName", "")
        print(f"{GREEN}[OK] Contact created: {display_name}{RESET}")
        print(f"     Resource: {resource}")
    return result


def cmd_get(service, resource_name, as_json=False):
    """Get a single contact by resource name."""
    result = service.people().get(
        resourceName=resource_name,
        personFields=PERSON_FIELDS,
    ).execute()

    if as_json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        _print_detail(result)
    return result


def cmd_edit(service, resource_name, name=None, email=None, phone=None,
             company=None, title=None, notes=None, address=None, url=None,
             as_json=False):
    """Update an existing contact. Fetches current state first (etag required)."""
    current = service.people().get(
        resourceName=resource_name,
        personFields=PERSON_FIELDS,
    ).execute()

    etag = current.get("etag")
    if not etag:
        print(f"{RED}[ERROR] Could not get etag for {resource_name}{RESET}", file=sys.stderr)
        sys.exit(1)

    update_fields = []
    body = {"etag": etag, "resourceName": resource_name}

    if name:
        parts = name.strip().split(None, 1)
        body["names"] = [{"givenName": parts[0], "familyName": parts[1] if len(parts) > 1 else ""}]
        update_fields.append("names")
    if email:
        body["emailAddresses"] = [{"value": email}]
        update_fields.append("emailAddresses")
    if phone:
        body["phoneNumbers"] = [{"value": phone}]
        update_fields.append("phoneNumbers")
    if company or title:
        org = current.get("organizations", [{}])[0].copy() if current.get("organizations") else {}
        if company:
            org["name"] = company
        if title:
            org["title"] = title
        body["organizations"] = [org]
        update_fields.append("organizations")
    if notes:
        body["biographies"] = [{"value": notes, "contentType": "TEXT_PLAIN"}]
        update_fields.append("biographies")
    if address:
        body["addresses"] = [{"formattedValue": address}]
        update_fields.append("addresses")
    if url:
        body["urls"] = [{"value": url}]
        update_fields.append("urls")

    if not update_fields:
        print(f"{YELLOW}No fields specified to update.{RESET}", file=sys.stderr)
        sys.exit(1)

    result = service.people().updateContact(
        resourceName=resource_name,
        body=body,
        updatePersonFields=",".join(update_fields),
        personFields=PERSON_FIELDS,
    ).execute()

    if as_json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        display_name = result.get("names", [{}])[0].get("displayName", resource_name)
        print(f"{GREEN}[OK] Updated: {display_name}{RESET}")
        _print_detail(result)
    return result


def cmd_list(service, limit=100, as_json=False):
    """List contacts sorted by last modified."""
    all_contacts = []
    page_token = None

    while True:
        page_size = min(limit - len(all_contacts), 1000)
        if page_size <= 0:
            break

        results = service.people().connections().list(
            resourceName="people/me",
            pageSize=page_size,
            personFields=PERSON_FIELDS,
            pageToken=page_token,
            sortOrder="LAST_MODIFIED_DESCENDING",
        ).execute()

        connections = results.get("connections", [])
        all_contacts.extend(connections)

        page_token = results.get("nextPageToken")
        if not page_token or len(all_contacts) >= limit:
            break

    if as_json:
        print(json.dumps(all_contacts, indent=2, ensure_ascii=False))
    else:
        summaries = [_format_summary(c) for c in all_contacts]
        _print_table(summaries)
    return all_contacts


def cmd_delete(service, resource_name):
    """Delete a contact permanently."""
    try:
        current = service.people().get(
            resourceName=resource_name,
            personFields="names",
        ).execute()
        display_name = current.get("names", [{}])[0].get("displayName", resource_name)
    except Exception:
        display_name = resource_name

    service.people().deleteContact(resourceName=resource_name).execute()
    print(f"{GREEN}[OK] Deleted: {display_name} ({resource_name}){RESET}")


# ===========================================================================
# CLI entry point
# ===========================================================================
def main():
    parser = argparse.ArgumentParser(
        description="Google Contacts CLI -- search, add, edit, list, get, delete contacts",
        epilog=(
            "Examples:\n"
            '  python scripts/google-contacts.py search "John Doe"\n'
            '  python scripts/google-contacts.py add --name "Jane Smith" --email "jane@co.com"\n'
            "  python scripts/google-contacts.py get people/c1234567890\n"
            '  python scripts/google-contacts.py edit people/c1234567890 --phone "+1-555-0100"\n'
            "  python scripts/google-contacts.py list --limit 50\n"
            "  python scripts/google-contacts.py delete people/c1234567890\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", help="Command to run")

    # search
    p_search = sub.add_parser("search", help="Search contacts by name, email, phone")
    p_search.add_argument("query", help="Search query (prefix matching)")
    p_search.add_argument("--json", action="store_true", help="Output as JSON")

    # add
    p_add = sub.add_parser("add", help="Create a new contact")
    p_add.add_argument("--name", "-n", required=True, help="Full name")
    p_add.add_argument("--email", "-e", help="Email address")
    p_add.add_argument("--phone", "-p", help="Phone number")
    p_add.add_argument("--company", "-c", help="Company name")
    p_add.add_argument("--title", "-t", help="Job title")
    p_add.add_argument("--notes", help="Notes / biography")
    p_add.add_argument("--address", help="Mailing address")
    p_add.add_argument("--url", help="Website URL")
    p_add.add_argument("--json", action="store_true", help="Output as JSON")

    # get
    p_get = sub.add_parser("get", help="Get a single contact by resource name")
    p_get.add_argument("resource", help="Resource name (e.g., people/c1234567890)")
    p_get.add_argument("--json", action="store_true", help="Output as JSON")

    # edit
    p_edit = sub.add_parser("edit", help="Update an existing contact")
    p_edit.add_argument("resource", help="Resource name (e.g., people/c1234567890)")
    p_edit.add_argument("--name", "-n", help="Full name")
    p_edit.add_argument("--email", "-e", help="Email address")
    p_edit.add_argument("--phone", "-p", help="Phone number")
    p_edit.add_argument("--company", "-c", help="Company name")
    p_edit.add_argument("--title", "-t", help="Job title")
    p_edit.add_argument("--notes", help="Notes / biography")
    p_edit.add_argument("--address", help="Mailing address")
    p_edit.add_argument("--url", help="Website URL")
    p_edit.add_argument("--json", action="store_true", help="Output as JSON")

    # list
    p_list = sub.add_parser("list", help="List all contacts")
    p_list.add_argument("--limit", "-l", type=int, default=100, help="Max contacts (default: 100)")
    p_list.add_argument("--json", action="store_true", help="Output as JSON")

    # delete
    p_delete = sub.add_parser("delete", help="Delete a contact permanently")
    p_delete.add_argument("resource", help="Resource name (e.g., people/c1234567890)")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    # Authenticate
    try:
        service = authenticate()
    except Exception as e:
        print(f"{RED}[ERROR] Authentication failed: {e}{RESET}", file=sys.stderr)
        sys.exit(1)

    # Dispatch
    try:
        if args.command == "search":
            cmd_search(service, args.query, as_json=args.json)
        elif args.command == "add":
            cmd_add(service, args.name, email=args.email, phone=args.phone,
                    company=args.company, title=args.title, notes=args.notes,
                    address=args.address, url=args.url, as_json=args.json)
        elif args.command == "get":
            cmd_get(service, args.resource, as_json=args.json)
        elif args.command == "edit":
            cmd_edit(service, args.resource, name=args.name, email=args.email,
                     phone=args.phone, company=args.company, title=args.title,
                     notes=args.notes, address=args.address, url=args.url,
                     as_json=args.json)
        elif args.command == "list":
            cmd_list(service, limit=args.limit, as_json=args.json)
        elif args.command == "delete":
            cmd_delete(service, args.resource)
    except Exception as e:
        error_str = str(e)
        if "HttpError 404" in error_str:
            res = getattr(args, "resource", "")
            print(f"{RED}[ERROR] Contact not found: {res}{RESET}", file=sys.stderr)
        elif "HttpError 400" in error_str:
            print(f"{RED}[ERROR] Bad request -- check your arguments. Details: {e}{RESET}", file=sys.stderr)
        elif "HttpError 403" in error_str:
            print(f"{RED}[ERROR] Permission denied (403).{RESET}", file=sys.stderr)
            print(f"{DIM}{e}{RESET}", file=sys.stderr)
            print("", file=sys.stderr)
            print("  Likely causes:", file=sys.stderr)
            print("  1. People API not enabled -- go to:", file=sys.stderr)
            print("     https://console.cloud.google.com/apis/library/people.googleapis.com", file=sys.stderr)
            print("     and click 'Enable'", file=sys.stderr)
            print(f"  2. Stale token -- delete {TOKEN_PATH}", file=sys.stderr)
            print("     and re-run to re-authenticate", file=sys.stderr)
        elif "HttpError 429" in error_str:
            print(f"{RED}[ERROR] Rate limited by Google. Wait a moment and retry.{RESET}", file=sys.stderr)
        else:
            print(f"{RED}[ERROR] {e}{RESET}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
