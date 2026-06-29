---
name: notebooklm
description: >
  Google NotebookLM CLI integration. Creates topic notebooks, adds sources
  (URLs, text, files), queries with grounded citations, generates audio
  overviews (AI podcast), runs research discovery, generates briefing
  reports, downloads artifacts. CEO-only - not synced to exec workspaces.
  Uses undocumented Google APIs via notebooklm-mcp-cli CLI tool.
  Triggers on "notebooklm", "audio overview", "podcast from sources",
  "create a notebook", "notebook research". Does NOT trigger on generic
  "research" (use /osint), "notes" (use /zk), or "podcast" without
  clear NotebookLM context (could be /yt-pulse).
argument-hint: "[status|create|add|query|audio|research|report|describe|download] [args]"
allowed-tools: "Read, Write, Bash(nlm:*), Bash(python3:*), Bash(python:*), Glob"
model: sonnet
metadata:
  author: Misha Hanin
  email: misha.hanin@odinix.com
  version: "1.0"
x-31c-orchestration:
  parallel_safe: true
  shared_state: []
  triggers:
    - "notebooklm"
    - "audio overview"
    - "podcast from sources"
    - "create a notebook"
    - "notebook research"
x-31c-capability:
  what: >
    Google NotebookLM control from the workspace - create topic notebooks, add sources (URLs, text, files), query with grounded citations, generate audio overviews (AI podcast), run research discovery, and produce briefing reports.
  how: >
    Run /notebooklm [status|create|add|query|audio|research|report|describe|download] [args]. CEO-only, not synced to execs. Outputs land under outputs/content/notebooklm/; requires nlm login first.
  when: >
    Use for NotebookLM-specific notebook work and AI audio overviews. For general target research use /osint; for atomic knowledge notes use /zk; for topic-based YouTube podcasts use /yt-pulse.
---
# NotebookLM Integration

CLI wrapper for Google NotebookLM via `notebooklm-mcp-cli`. Creates topic notebooks, ingests sources, queries with grounded citations, generates audio overviews, runs research discovery, and bridges to Odin for knowledge ingestion.

CEO-only. Not synced to exec workspaces. Uses undocumented Google APIs - may break without notice.

---

## CLI Access

The `nlm` CLI is installed but may not be on bash PATH directly on Windows. Use this exact invocation pattern for ALL commands:

```
NLM="$(command -v nlm 2>/dev/null \
  || ls "${APPDATA:-$USERPROFILE/AppData/Roaming}"/Python/Python*/Scripts/nlm.exe 2>/dev/null | head -1)"
NO_COLOR=1 PYTHONIOENCODING=utf-8 "$NLM" <subcommand> [flags]
```

The `command -v nlm` lookup prefers `nlm` on PATH (Linux, macOS, future). The fallback is for the Windows CEO machine where pip installs to a user-scoped Scripts directory not on Git Bash PATH: it derives the per-user Roaming location from `$APPDATA` (or `$USERPROFILE/AppData/Roaming` if `$APPDATA` is unset) so no username or Python minor version is hardcoded, then globs the `Python*/Scripts/nlm.exe` install path. On Linux/macOS `$APPDATA` and `$USERPROFILE` are unset and the glob matches nothing, so `command -v nlm` is authoritative there. Always set `NLM` as a variable at the start of each Bash call, then use `"$NLM"` for the command. The `NO_COLOR=1 PYTHONIOENCODING=utf-8` prefix forces UTF-8 stdio across platforms (required on Windows console; harmless on Linux/macOS).

---

## Variables

- `$ARGUMENTS` - Mode and parameters. Format: `[mode] [target/args]`
- Modes: `status`, `create`, `add`, `query`, `audio`, `research`, `report`, `describe`, `download`

## Phase 0: Auth Validation

Run before EVERY mode. No exceptions.

1. Run (Bash, timeout 15000):
   ```bash
   NLM="$(command -v nlm 2>/dev/null \
     || ls "${APPDATA:-$USERPROFILE/AppData/Roaming}"/Python/Python*/Scripts/nlm.exe 2>/dev/null | head -1)"
   NO_COLOR=1 PYTHONIOENCODING=utf-8 "$NLM" login --check
   ```
2. If exit code 0: proceed to requested mode
3. If exit code != 0: STOP. Display:

```
NotebookLM authentication expired. Run this in your terminal:

  nlm login          (Linux/macOS where nlm is on PATH)
  %APPDATA%\Python\Python<ver>\Scripts\nlm.exe login   (Windows CEO machine, user-scoped pip install)

This opens a browser window for Google sign-in. After login completes, retry your command.
```

Do NOT proceed with any mode. Do NOT attempt to auto-login (requires browser interaction).

## Phase 1: Mode Dispatch

Parse mode from `$ARGUMENTS` or natural language:

- **No arguments / empty / just "/notebooklm"** -> show mode menu
- "status" / "check" / "list notebooks" -> `status`
- "create" / "new notebook" -> `create`
- "add" / "sources" / URLs detected in args -> `add`
- "query" / "ask" / question mark in args -> `query`
- "audio" / "podcast" / "overview" -> `audio`
- "research" / "discover" / "find sources" -> `research`
- "report" / "briefing" / "study guide" -> `report`
- "describe" / "summarize notebook" -> `describe`
- "download" / "save artifact" / "get artifact" -> `download`

### Mode Menu (when no arguments given)

Display this and wait for selection:

```
## NotebookLM

| # | Mode | What it does |
|---|------|-------------|
| 1 | **status** | Auth check + list all notebooks |
| 2 | **create** | Create a new notebook |
| 3 | **add** | Add sources (URLs, text, files) to a notebook |
| 4 | **query** | Query a notebook with grounded citations |
| 5 | **audio** | Generate audio overview (AI podcast) |
| 6 | **research** | Run web discovery to find new sources on a topic |
| 7 | **report** | Generate a briefing doc from notebook sources |
| 8 | **describe** | Get AI summary of a notebook |
| 9 | **download** | Download any artifact from a notebook |

Usage: /notebooklm [mode] [args]
Examples:
- /notebooklm create "Middle East DPI Market"
- /notebooklm add <notebook-id> https://example.com/article1 https://example.com/article2
- /notebooklm query <notebook-id> What are the key regulatory trends?
- /notebooklm audio <notebook-id>
- /notebooklm research "sovereign AI governance"
```

---

## Phase 2: Mode Execution

Per-mode flow lives in `references/mode-catalog.md`. Read ONLY the section for the dispatched mode - do not load all nine. The catalogue carries the CLI command, polling rules, JSON parsing notes, output template, and handoff offers for each mode.

Modes covered: `status`, `create`, `add`, `query`, `audio`, `research`, `report`, `describe`, `download`.

Each mode reuses the `NLM` variable + `NO_COLOR=1 PYTHONIOENCODING=utf-8` prefix from the CLI Access section above. When the catalog shows a bare `"$NLM" ...` invocation, SKILL.md callers must still set the variable at the start of every Bash call.

Polling pattern (audio, report): 15-second interval, max 20 iterations (5 minutes), check `status` field for `in_progress` / `completed` / `failed`. Research mode uses its own intervals (15s fast / 30s deep) per the catalog.

---

## Phase 3: Output, Bridges, and Error Handling

Output routing (type -> subdirectory mapping), Odin/ZK bridge handoff points, and the full error -> recovery message table live in `references/output-and-errors.md`. Read on demand when an error fires or a download is queued.

Filename pattern across all modes: `YYYY-MM-DD-<slug>.<ext>` where `<slug>` is kebab-case, max 40 characters. All outputs land under `outputs/content/notebooklm/`.

---

## NEVER

1. **Never auto-login.** `nlm login` requires browser interaction. Always direct the user to run it manually.
2. **Never bypass Phase 0.** Auth check runs before every mode. No exceptions.
3. **Never write to shared state.** All outputs go to `outputs/content/notebooklm/` only. Never write to `crm/`, `context/`, `knowledge/`, or other shared paths.
4. **Never sync to execs.** This skill is CEO-only. Not in GETTING-STARTED.md, not in the corporate repo, not in classification overrides.
5. **Never create dependencies.** No other skill may require NotebookLM to function. This is additive.
6. **Never auto-upgrade the CLI.** Upgrades are manual and tested.
7. **Never invoke Odin/ZK directly.** Suggest handoffs. Let the CEO approve. Claude routes to the downstream skill.
8. **Never use the MCP server.** This skill uses CLI only. Do not add `notebooklm-mcp` to Claude Code settings or MCP config.

## Voice Rules

- Single hyphens `-` in prose, never double dashes
- ODUN.ONE when referencing the 31C platform
- DPI+ for deep packet intelligence
- Language matches the user's language (Russian question = Russian output)
