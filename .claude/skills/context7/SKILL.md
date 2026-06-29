---
name: context7
description: Fetch up-to-date, version-specific library documentation from Context7 for accurate API references, code examples, and usage patterns. Use when working with any library or framework and you need current documentation instead of relying on training data. Trigger when the user says "context7", "/context7", "look up docs for [library]", "get documentation for [library]", or when you need to validate code against current library APIs. Also use proactively when writing code that depends on external libraries to ensure correctness.
metadata:
  author: Misha Hanin
  email: misha.hanin@odinix.com
  version: "1.1"
argument-hint: "[library] [query]"
allowed-tools: "Bash(python3:*), Read"
model: haiku
x-31c-orchestration:
  parallel_safe: true
  shared_state: []
  triggers:
    - context7
    - look up docs for
    - library documentation
x-31c-capability:
  what: >
    Fetches live, version-specific documentation for any library or framework from the Context7 API, formatted for accurate API reference and code validation.
  how: >
    Run /context7 <library> <query> - it calls scripts/context7.py with the workspace CONTEXT7_API_KEY. Flags: --list, --version, --json, --limit. Returns docs inline.
  when: >
    Use when writing or validating code against an external library and you need current docs instead of training data. Not for general web research (use /osint) or workspace notes (use /zk).
---
# Context7 - Library Documentation

Fetch live, version-specific documentation for any library or framework via the Context7 REST API. Returns authoritative docs formatted for LLM consumption.

> **Naming note.** A user-level Claude Code plugin also exposes a skill called `context7:context7` (namespaced form). When the user types `/context7` without the namespace, the local skill in this file wins per Claude Code's bare-name lookup precedence (local > plugin). To force the plugin variant, type the namespaced form explicitly. Both skills do similar things; the local one uses the workspace's own CONTEXT7_API_KEY and `scripts/context7.py`, the plugin uses its own MCP-style backend.

## Prerequisites

`CONTEXT7_API_KEY` should be set in the workspace `.env` file. The script auto-loads it via `python-dotenv`.

```
# In the workspace root .env file:
CONTEXT7_API_KEY=ctx7sk-your-key-here
```

Get a free key at: https://context7.com/dashboard

The API works without a key but with lower rate limits.

## Workflow

1. Parse the user input: first word is the library name, remainder is the query. If only a library name is given, use "documentation" as the default query.

2. Run the script:

```bash
python scripts/context7.py "<library_name>" "<query>"
```

Examples:
- `/context7 react hooks` -> `python scripts/context7.py "react" "hooks"`
- `/context7 nextjs app router middleware` -> `python scripts/context7.py "nextjs" "app router middleware"`
- `/context7 python-pptx` -> `python scripts/context7.py "python-pptx" "documentation"`

3. Use the returned documentation as authoritative context. Prefer it over training data when writing or validating code.

4. If the library is not found, try alternative names (e.g., "next.js" instead of "nextjs", "react-dom" instead of "reactdom").

## Script Flags

| Flag | Purpose |
|------|---------|
| `--list` | List matching libraries without fetching docs |
| `--version v15` | Pin to a specific library version |
| `--json` | Output structured JSON instead of text |
| `--limit 5000` | Cap returned token count |
