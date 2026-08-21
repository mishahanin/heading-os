# Hooks reference

The code that runs at fixed points in a session: before a tool call, after a write, at session start, on stop and compaction. Hooks are where several of the [rules](RULES-REFERENCE.html) stop being prose and become enforcement.

## What a hook is

A hook is a script the Claude Code harness runs at a named event. HEADING OS ships its hooks in `.claude/hooks/` and wires them in the settings files:

- **`.claude/settings.json`** wires the one fully portable hook (`data-path-redirect.py`) with a self-locating launcher, so it works from any clone location.
- **`.claude/settings.local.{linux,macos,windows}.json`** wire the rest, per operating system. Copy the template for your platform to `.claude/settings.local.json` on a fresh clone.

Some hook events can block work by protocol; the rest can only observe, enrich, or record. Whether a given event blocks in practice depends on the hook wired to it, not only on what the event allows.

- **`PreToolUse`** runs before a tool call and can deny it. This is where blocking guards live (secret detection, the engine/data boundary, path redirection).
- **`PostToolUse`** runs after a tool call. It cannot un-write a file, so its guards are advisory or corrective (hidden-character scan, injection detection).
- **`SessionStart`**, **`Stop`**, **`PreCompact`**, **`PostCompact`**, and **`statusLine`** run around the session lifecycle. They prime context, offer checkpoints, steer what a compaction keeps, save handoffs, and render the status line.
- **`UserPromptSubmit`** runs on every prompt, before the model starts to think, and by protocol can block the prompt (exit code 2, or `{"decision": "block"}`). Neither hook wired here exercises that: `recall-inject.py` only adds context and `unattended-resume.py` only touches state, and both stay silent and exit 0 on any error.

## PreToolUse (can block)

| Hook | Purpose |
|------|---------|
| `data-path-redirect.py` | Redirects data-relative tool paths to the resolved data root, so a `Read`/`Write`/`Edit`/`Grep`/`Glob` aimed at a data path lands in the private overlay, not the engine tree. The one hook wired in the portable `settings.json`. |
| `_dispatch.py` | The consolidated PreToolUse guard, registered on three matchers: the write tools, `Bash`, and `Read`. It runs, in one process, the secret-detection block (API-key and credential patterns), the corporate-boundary block (no writes to read-only corporate content), the docs-protection block, and the personal-threads protection. |

## PostToolUse (observe and correct)

| Hook | Purpose |
|------|---------|
| `post-write-sanitize.py` | Scans every written or edited file for invisible Unicode characters and flags contamination. Enforces the [hidden-characters rule](RULES-REFERENCE.html). |
| `prompt-guard.py` | Advisory detection of prompt-injection patterns in ingest-path files (`knowledge/`, `datastore/`, `crm/contacts/`), the paths where untrusted third-party text lands. |
| `sync-docs.py` | Auto-syncs `templates/` to `docs/` when documentation files change, keeping the two in step per the documentation rule. |

## SessionStart

| Hook | Purpose |
|------|---------|
| `session-start.py` | Surfaces urgent CRM contacts and data-freshness alerts at the start of a session. |
| `memory-inject.py` | Loads the auto-memory index (`MEMORY.md` pointers) into context on startup. |
| `memory-reconcile.py` | Reconciles the native harness memory store with the workspace auto-memory files. |
| `checkpoint-inject.py` | On `compact`, `clear`, or `resume`, injects the latest saved handoff so work continues across a context reset. |

## UserPromptSubmit

| Hook | Purpose |
|------|---------|
| `recall-inject.py` | Surfaces pointers (title, layer, path) to memory relevant to what the CEO just typed, ranked by the [recall](CONFIGURATION.html) index rather than by date. It does NOT block the prompt, does NOT read file content, and on any error, timeout, or missing index it stays silent and exits 0. Its internal 3.5-second timeout sits under the harness's own 8-second timeout for the hook, so it gives up on a cold model load rather than stall the prompt. Toggled off entirely via `recall_inject.enabled` in `config/memory-index.yaml`; a config it cannot read is treated as an unconfirmed switch, so it stays silent then too. Short conversational prompts (under `recall_inject.min_prompt_chars`, 25 by default) skip the backend entirely, and a below-threshold result is capped at three pointers. Supersedes the date-ordered `inject` snapshot (`memory-inject.py`, `SessionStart`), which stays in the codebase behind its own flag but defaults off. |
| `unattended-resume.py` | Clears a PAUSED unattended stretch the moment the operator sends an instruction, which is what `clear_unattended_window` and `checkpoint-paths.py --done` both promise ("your next instruction resumes it"). Before 2026-08-20 the clearing happened at the next `Stop` — the end of the turn that instruction opened — so the status bar read `⏸ unattended paused` for the whole of a turn that had already resumed. It never blocks, never prints, and never touches `session_unattended`: the switch is the operator's, and only the WINDOW is cleared. A session with no pause marker is one JSON read and an exit, so the common path writes nothing. The literal `/compact` is ignored, because the Stop hook queues that text itself to drive a compaction and counting it as the operator speaking would reset the continuation ceiling at every boundary. The Stop hook keeps its own `prompt_id` comparison as the fallback for clones where this hook is not wired, but since 2026-08-20 that fallback retires only a marker it has ALREADY acted on — one carrying `unattended_paused_at`. The comparison is on turn identity and never on age, so it could not tell last night's marker from one written seconds earlier in the turn it was ending, and it cleared both: `--done` printed `done recorded` and then continued the stretch anyway, on the first pause after any instruction the operator gave. Measured across two consecutive turns of a live session; the guard is `tests/test_unattended_done_survives_the_turn.py`. |

**After an update that adds a hook, wire it in your own settings file.** Your `.claude/settings.local.json` is gitignored. A `git pull` updates the per-OS templates and leaves your live file untouched. A hook event that exists only in the template does not run. Copy the `UserPromptSubmit` block from `.claude/settings.local.{linux,macos,windows}.json` into the `hooks` object of your `.claude/settings.local.json`. Merge the one new key rather than replacing the file, which carries your local edits. Confirm with `python3 -c "import json;print(list(json.load(open('.claude/settings.local.json'))['hooks']))"`.

## Session lifecycle

| Hook | Event | Purpose |
|------|-------|---------|
| `turn-check.py` | `Stop` | Runs `scripts/turn-check.py` over the uncommitted Python edits and blocks the end of the turn if a lane fails. Compile, then import, then the test files that name the changed modules; seconds, not the full suite. Files under `tests/contract/` are counted and skipped, because a frozen contract is red until its slice implements it. Silent when clean. |
| `checkpoint-offer.py` | `Stop` | Offers to save a checkpoint at rising context-usage thresholds. Below the hard threshold it stays silent when something already drives the Stop event: a scheduled wakeup, in-flight background work, or a ralph-loop that names this session. At or above it, that save is the last one before compaction, so the hook records the claimant and runs anyway. In unattended mode it waits for the operator instead of asking, then continues the turn if the wait passes in silence. In either autonomous mode, once the handoff is on disk above the hard threshold, it submits `/compact` to the session's own terminal through HERDR. |
| `checkpoint-precompact.py` | `PreCompact` | Tells the summariser what to keep and what to drop, and appends six facts read from the tree: the branch, the working tree, the last five commits, the files this session wrote, this session's handoff pointer, and the most recently modified plan file. Runs on an automatic compaction as well as a typed one. Writes nothing, and never blocks the compaction. |
| `checkpoint-save.py` | `PostCompact` | Saves a session handoff to the archive after a compaction, so the next window resumes cleanly. |
| `checkpoint-statusline.py` | `statusLine` | Renders the status line, including context-usage and checkpoint state. |
| `bridge-hook.py` | (router) | Feeds session events to the optional bridge dashboard daemon. Present only when the bridge is in use; harmless when it is not. |

## Adding or changing a hook

1. Write the script in `.claude/hooks/`. A hook reads the harness event JSON on stdin and, for a blocking `PreToolUse` hook, writes a decision to stdout.
2. Wire it in the settings file for its event. Use `settings.json` for a portable, self-locating hook, or the per-OS `settings.local.{os}.json` templates for the rest.
3. A `PreToolUse` hook that denies work must fail safe and fail loud. Block on the dangerous case, and print a plain reason so the operator knows why.
4. Keep blocking logic in `PreToolUse`. A `PostToolUse` hook cannot prevent a write that already happened; use it to detect, correct, or record.

## Related

- [Rules reference](RULES-REFERENCE.html): the behavioural layer these hooks enforce.
- [Security model](SECURITY-MODEL.html): how the blocking hooks compose with the push-time scan and the send-gate.
- [Configuration](CONFIGURATION.html): the config files the hooks and guards read.
