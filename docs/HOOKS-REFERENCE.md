# Hooks reference

The code that runs at fixed points in a session: before a tool call, after a write, at session start, on stop and compaction. Hooks are where several of the [rules](RULES-REFERENCE.html) stop being prose and become enforcement.

## What a hook is

A hook is a script the Claude Code harness runs at a named event. HEADING OS ships its hooks in `.claude/hooks/` and wires them in the settings files:

- **`.claude/settings.json`** wires the one fully portable hook (`data-path-redirect.py`) with a self-locating launcher, so it works from any clone location.
- **`.claude/settings.local.{linux,macos,windows}.json`** wire the rest, per operating system. Copy the template for your platform to `.claude/settings.local.json` on a fresh clone.

Two hook events can block work; the rest observe, enrich, or record.

- **`PreToolUse`** runs before a tool call and can deny it. This is where blocking guards live (secret detection, the engine/data boundary, path redirection).
- **`PostToolUse`** runs after a tool call. It cannot un-write a file, so its guards are advisory or corrective (hidden-character scan, injection detection).
- **`SessionStart`**, **`Stop`**, **`PostCompact`**, and **`statusLine`** run around the session lifecycle: priming context, offering checkpoints, saving handoffs, rendering the status line.

## PreToolUse (can block)

| Hook | Purpose |
|------|---------|
| `data-path-redirect.py` | Redirects data-relative tool paths to the resolved data root, so a `Read`/`Write`/`Edit`/`Grep`/`Glob` aimed at a data path lands in the private overlay, not the engine tree. The one hook wired in the portable `settings.json`. |
| `_dispatch.py` | The consolidated PreToolUse guard on `Write`/`Edit`. It runs, in one process, the secret-detection block (API-key and credential patterns), the corporate-boundary block (no writes to read-only corporate content), the docs-protection block, and the personal-threads protection. |

`prevent-secrets.py`, `protect-corporate.py`, `protect-docs.py`, and `protect-personal-threads.py` are thin compatibility shims that delegate to `_dispatch.py`. They exist so older settings files that reference the individual script names keep working.

## PostToolUse (observe and correct)

| Hook | Purpose |
|------|---------|
| `post-write-sanitize.py` | Scans every written or edited file for invisible Unicode characters and flags contamination. Enforces the [hidden-characters rule](RULES-REFERENCE.html). |
| `prompt-guard.py` | Advisory detection of prompt-injection patterns in ingest-path files (`knowledge/`, `datastore/`, `crm/contacts/`), the paths where untrusted third-party text lands. |
| `context-monitor.py` | Warns when the context window approaches capacity, so long sessions get a checkpoint before they run out of room. |
| `sync-docs.py` | Auto-syncs `templates/` to `docs/` when documentation files change, keeping the two in step per the documentation rule. |

## SessionStart

| Hook | Purpose |
|------|---------|
| `session-start.py` | Surfaces urgent CRM contacts and data-freshness alerts at the start of a session. |
| `memory-inject.py` | Loads the auto-memory index (`MEMORY.md` pointers) into context on startup. |
| `memory-reconcile.py` | Reconciles the native harness memory store with the workspace auto-memory files. |
| `checkpoint-inject.py` | On `compact`, `clear`, or `resume`, injects the latest saved handoff so work continues across a context reset. |

## Session lifecycle

| Hook | Event | Purpose |
|------|-------|---------|
| `checkpoint-offer.py` | `Stop` | Offers to save a checkpoint at rising context-usage thresholds. |
| `checkpoint-save.py` | `PostCompact` | Saves a session handoff to the archive after a compaction, so the next window resumes cleanly. |
| `checkpoint-statusline.py` | `statusLine` | Renders the status line, including context-usage and checkpoint state. |
| `bridge-hook.py` | (router) | Feeds session events to the optional bridge dashboard daemon. Present only when the bridge is in use; harmless when it is not. |

## Adding or changing a hook

1. Write the script in `.claude/hooks/`. A hook reads the harness event JSON on stdin and, for a blocking `PreToolUse` hook, writes a decision to stdout.
2. Wire it in the settings file for its event: `settings.json` for a portable, self-locating hook, or the per-OS `settings.local.{os}.json` templates for the rest.
3. A `PreToolUse` hook that denies work must fail safe and fail loud: block on the dangerous case, and print a plain reason so the operator knows why.
4. Keep blocking logic in `PreToolUse`. A `PostToolUse` hook cannot prevent a write that already happened; use it to detect, correct, or record.

## Related

- [Rules reference](RULES-REFERENCE.html): the behavioural layer these hooks enforce.
- [Security model](SECURITY-MODEL.html): how the blocking hooks compose with the push-time scan and the send-gate.
- [Configuration](CONFIGURATION.html): the config files the hooks and guards read.
