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
| `_dispatch.py` | The consolidated PreToolUse guard. It runs eleven checks in one process, on five matchers. The next section names every one. |

### The dispatcher and its eleven checks

All three platform templates (`.claude/settings.local.{linux,macos,windows}.json`) register `_dispatch.py` on the same five PreToolUse matchers:

| Matcher | Covers |
|---|---|
| `Write\|Edit\|MultiEdit\|NotebookEdit` | the write family |
| `Bash` | every shell command |
| `Read\|Grep\|Glob` | the read family |
| `mcp__codegraph__.*` | the CodeGraph MCP tools |
| `Agent\|Task\|Workflow` | agent and workflow dispatch |

The `CHECKS` registry in `.claude/hooks/_dispatch.py` holds eleven entries, and the dispatcher runs them in this order. The first check that returns a block decision stops the tool call.

| # | Check | Blocks | What it refuses |
|---|---|---|---|
| 1 | `check_prevent_secrets` | always | A write that carries an API key or another credential pattern. |
| 2 | `check_release_gate` | always | A commit or a push the operator did not ask for in this turn. The next section covers it. |
| 3 | `check_protect_personal_threads` | always | A read of the CEO-only thread subtree, live or archived under a closed year. For `Grep` and `Glob` it also refuses an expression that can EXPAND into either. Name a business subtree instead. |
| 4 | `check_protect_corporate` | always | A write to read-only corporate content. |
| 5 | `check_protect_docs` | always | A direct edit to a `docs/` page that `sync-docs.py` overwrites from its template. |
| 6 | `check_cwd_anchor` | always | A workspace script started by a root-relative path from a drifted shell directory. It fires only when the path resolves from the root and not from that directory, and it answers with the anchored command. |
| 7 | `check_slow_shell` | always | A `Bash` call that runs the whole test suite in one process, or that waits in the foreground. It points at `scripts/run-tests.py` or at `run_in_background`. |
| 8 | `check_rate_limit` | at the hard cap | A daily `Write` and `Edit` cap. The soft cap warns. Loop detection (same tool, same file, short window) stays advisory and never blocks. `Bash` is excluded. |
| 9 | `check_graph_first` | always | The session's first lookup into source code, until a CodeGraph query is attempted. Any attempt unlocks the session, including one that errors or returns nothing. |
| 10 | `check_fanout_first` | always | More hand-reading past the distinct-file budget, when the session has dispatched no agent and no workflow. `scripts/fanout-note.py` records a reason and resets the budget. |
| 11 | `check_tool_budget` | at the hard cap | A total-tool-call cap in a 30-minute rolling window. It counts only the calls these five matchers deliver, so a loop built out of other tools stays invisible here. |

### The release gate

`check_release_gate` (entry 2 above) refuses a commit or a push the operator did not ask for in this turn. It is the workspace's control against an unauthorised release. It exists because the model crossed that boundary twice, and believed both times that permission existed.

The check reads the `Bash` command and classifies it with `release_action()`:

- **`push`** for a `git push` in any command segment, and for `gh release create` or `gh pr merge`. It also returns `push` for any of the eleven release scripts in `_PUSH_SCRIPTS`, among them `push-all.py`, `safe-push.py`, and `publish-corporate.py`.
- **`commit`** for a `git commit`, and for an annotated `git tag`. A bare `git tag` lists tags and passes.
- **`None`** for everything else, which returns control to the next check.

The check then reads the operator's most recent typed prompt from the session transcript. `prompt_authorises()` accepts the action when that prompt carries an authorising word in English or Russian, and refuses when it carries a negation anywhere. The check appends each authorised release to a log the operator can audit.

**The gate fails closed.** It refuses the release when it cannot read the transcript. A gate that opens when it cannot see is not a gate.

**Its coverage, stated exactly.** The gate sees `Bash` tool calls in a session that wires `_dispatch.py` on the `Bash` matcher. It does not see a commit or a push the operator types in their own terminal, and no claim here says otherwise. It is also not the push-time secret scan, which is separate code inside `scripts/push-all.py`. Read the [security model](SECURITY-MODEL.html) for how the two compose.

## PostToolUse (observe and correct)

| Hook | Purpose |
|------|---------|
| `post-write-sanitize.py` | Scans every written or edited file for invisible Unicode characters and flags contamination. Enforces the [hidden-characters rule](RULES-REFERENCE.html). |
| `prompt-guard.py` | Advisory detection of prompt-injection patterns in ingest-path files (`knowledge/`, `datastore/`, `crm/contacts/`), the paths where untrusted third-party text lands. |
| `sync-docs.py` | Auto-syncs `templates/` to `docs/` when documentation files change, keeping the two in step per the documentation rule. |

## SessionStart

| Hook | Purpose |
|------|---------|
| `session-start.py` | Surfaces urgent CRM contacts and data-freshness alerts at the start of a session. Also prints the active-threads panel, computed from the thread files. |
| `memory-inject.py` | Loads the auto-memory index (`MEMORY.md` pointers) into context on startup. **Dormant by default** -- `inject.enabled` is `false` in `config/memory-index.yaml`, superseded 2026-08-07 by `recall-inject.py` below. Wiring it as well gives you two memory injections per session, one of them date-ordered rather than relevance-ranked. |
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
| `turn-check.py` | `Stop` | Runs `scripts/turn-check.py` over the uncommitted Python edits and blocks the end of the turn if a lane fails. Compile, then import, then the test files that name the changed modules, plus any the module names for itself with a `Tests:` line at column 0 of its docstring; seconds, not the full suite. Files under `tests/contract/` are counted and skipped, because a frozen contract is red until its slice implements it. Tests marked `slow` are deselected and counted for the same reason: they sleep for real, so they belong to the once-per-push `scripts/run-tests.py`, not to a lane that runs after every answer. Silent when clean. |
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
