---
name: google-contacts
description: >
  Search, add, edit, and manage contacts in Google Contacts via the People API.
  Use for any task involving looking up contact details, adding new contacts
  to Google, updating phone numbers or emails, or browsing the contact list.
  Trigger when the user says "google contacts", "look up contact",
  "add to contacts", "add to google contacts", "find their number",
  "update their email", "search contacts for", or any reference to managing
  contacts in Google. This is a STANDALONE connector -- completely separate
  from the workspace CRM system. Do NOT trigger for CRM operations (use /crm
  for that). Do NOT trigger for Exchange/Outlook contacts.
argument-hint: "[search|add|edit] [name]"
allowed-tools: "Bash(python3:*)"
model: haiku
metadata:
  author: Misha Hanin
  email: misha.hanin@odinix.com
  version: "1.1"
x-31c-orchestration:
  parallel_safe: partial
  shared_state: []
  triggers:
    - google contacts
    - look up contact number
    - add to google contacts
x-31c-capability:
  what: >
    Search, add, edit, list, get, and delete contacts in Google Contacts via
    the People API. A standalone connector, completely separate from the
    workspace CRM.
  how: >
    Run /google-contacts [search|add|edit] <name>. Drives
    scripts/google-contacts.py over OAuth (first run opens a browser consent);
    all commands support --json.
  when: >
    Use for managing contacts inside Google. For workspace relationship
    tracking and interaction logs use /crm; not for Exchange or Outlook
    contacts.
---
# Google Contacts

Search, add, edit, list, get, and delete contacts in Google Contacts. Standalone connector via the Google People API -- NOT connected to the workspace CRM.

## Prerequisites

1. **Install packages:**
   ```bash
   pip install google-api-python-client google-auth-httplib2 google-auth-oauthlib python-dotenv
   ```

2. **Set up OAuth credentials:**
   - Go to [Google Cloud Console](https://console.cloud.google.com/apis/credentials)
   - Create a project (or use existing)
   - Enable the **People API** (APIs & Services -> Library -> search "People API")
   - Go to Credentials -> Create Credentials -> OAuth 2.0 Client ID
   - Application type: **Desktop application**
   - Download the JSON file
   - Save as `.sessions/google/credentials.json`

3. **First run:** Any command opens a browser for OAuth consent. Sign in and approve. 2FA is handled by Google's login flow. Token is cached automatically.

## Script Location

```
scripts/google-contacts.py
```

## Quick Reference

| Command | Description |
|---------|-------------|
| `search "query"` | Prefix search on names, emails, phones (max 30 results) |
| `add --name "Name" [--email] [--phone] [--company] [--title] [--notes]` | Create a new contact |
| `get people/cXXX` | Get full detail for a single contact |
| `edit people/cXXX [--email] [--phone] [--company] [--title] [--notes]` | Update fields on existing contact |
| `list [--limit N]` | List contacts sorted by last modified (default: 100) |
| `delete people/cXXX` | Permanently delete a contact |

All commands support `--json` for machine-readable output.

## Usage Examples

### Search
```bash
python scripts/google-contacts.py search "John"
python scripts/google-contacts.py search "acme.com"
python scripts/google-contacts.py search "John" --json
```

### Add Contact
```bash
python scripts/google-contacts.py add --name "John Doe" --email "john@acme.com" --company "Acme Corp" --title "CTO"
python scripts/google-contacts.py add --name "Jane Smith" --phone "+1-555-0100" --notes "Met at MWC 2026"
```

### Get Contact Detail
```bash
python scripts/google-contacts.py get people/c1234567890
python scripts/google-contacts.py get people/c1234567890 --json
```

### Edit Contact
```bash
python scripts/google-contacts.py edit people/c1234567890 --phone "+1-555-9999"
python scripts/google-contacts.py edit people/c1234567890 --company "New Corp" --title "VP Engineering"
```

### List Contacts
```bash
python scripts/google-contacts.py list
python scripts/google-contacts.py list --limit 50
python scripts/google-contacts.py list --json
```

### Delete Contact
```bash
python scripts/google-contacts.py delete people/c1234567890
```

## Add Command Flags

| Flag | Required | Description |
|------|----------|-------------|
| `--name, -n` | Yes | Full name (split into first/last) |
| `--email, -e` | No | Email address |
| `--phone, -p` | No | Phone number |
| `--company, -c` | No | Company name |
| `--title, -t` | No | Job title |
| `--notes` | No | Notes / biography |
| `--address` | No | Mailing address |
| `--url` | No | Website URL |
| `--json` | No | Output as JSON |

## Workflow for Claude

When Misha asks to find/add/edit a Google contact:

1. **Search first** to check if the contact already exists
2. **Get** the full record to see details and obtain the resource name
3. **Edit** with specific field flags to update
4. **Add** only if the contact does not exist

Use `--json` when you need to parse fields programmatically (e.g., extracting a resource name from search results to pass to edit).

## Important Notes

- **Resource names** look like `people/c1234567890` -- unique identifiers for get/edit/delete
- **First run** opens a browser for Google OAuth. 2FA is handled by Google's login flow.
- **Token refresh** is automatic. If auth fails, delete `.sessions/google/token.json` and retry.
- **This is NOT the CRM.** This tool manages Google Contacts only. For the workspace CRM, use `/crm`.
- **Search warmup** is handled transparently -- no special action needed.
- **Notes** are stored in the `biographies` field in Google's API.

## Security

- NEVER output or log the OAuth token contents
- NEVER commit `.sessions/` or `.env` to git
- Token file: `.sessions/google/token.json` (gitignored)
- Credentials file: `.sessions/google/credentials.json` (gitignored)
