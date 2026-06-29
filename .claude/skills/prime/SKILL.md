---
name: prime
description: Initialize a working session. Loads full workspace context (personal info, business, strategy, current data, pipeline, key contacts, datastore index), runs CRM and sync health checks, surfaces what needs attention, and lists available skills with recommendations for the day. Use at the start of every working session. NEVER auto-trigger from natural language - this is an explicit "/prime" command only.
argument-hint: "(no arguments)"
disable-model-invocation: true
allowed-tools: "Read, Bash(python3:*)"
model: sonnet
metadata:
  author: Misha Hanin
  email: misha.hanin@odinix.com
  version: "1.3"
x-31c-orchestration:
  parallel_safe: false
  shared_state: []
  triggers: []
x-31c-capability:
  what: >
    Initializes a working session - loads full workspace context (personal, business, strategy, current data, pipeline, key contacts, datastore index), runs CRM/knowledge/sync/daemon health checks in parallel, surfaces what needs attention, and recommends the day's skills.
  how: >
    Explicit-invocation only (disable-model-invocation) - type /prime. Health block runs in-process via scripts/prime-health-parallel.py; ends by asking what to work on today.
  when: >
    Use once at the start of every working session. Mid-session, for a next-step recommendation use /next; for the daily briefing alone use /dashboard.
---
# Prime

> Initialize the session. Load full context. Surface priorities. Recommend today's agents.

---

## Read

First, read `.workspace-identity.json` to determine workspace type.

CLAUDE.md is already auto-loaded by the Claude Code harness (visible in the system prompt's `claudeMd` block) - do NOT re-read it.

Load the following files in this order (paths depend on workspace type):

**CEO workspace** (flat paths):
1. `context/personal-info.md` - who Misha is
2. `context/business-info.md` - 31C organization and ODUN.ONE
3. `context/strategy.md` - strategic priorities and arc
4. `context/current-data.md` - current metrics, milestones, active workstreams
5. `context/people.md` - **read only the "Top 10 Active Contacts (summary)" section.** Full file remains available - Read `context/people.md` when a specific named contact requires deeper context.
6. `context/pipeline.md` - active deals and investor conversations
7. `reference/workspace-overview.md` - **read only the H1 + "## Index" section.** Defer the per-section detail to lazy-read when a specific tool, script, or system needs deeper context.
8. `datastore/INDEX.md` - **read only the H1 + "## Top-Level Structure (summary)" section.** Defer the per-document tables to lazy-read when a specific fact needs validating.

**Exec workspace** (shared content under `corporate/`, personal under `personal/`):
1. `personal/context/personal-info.md` - who the exec is
2. `corporate/context/business-info.md` - 31C organization and ODUN.ONE
3. `corporate/context/strategy.md` - strategic priorities and arc
4. `corporate/context/current-data.md` - current metrics, milestones, active workstreams
5. `corporate/context/people.md` - **read only the "Top 10 Active Contacts (summary)" section** when present. Full file remains available for lazy-read.
6. `corporate/context/pipeline.md` - active deals and investor conversations
7. `corporate/datastore/INDEX.md` - **read only the H1 + "## Top-Level Structure (summary)" section** when present.

---

## Summary

After reading, provide a structured session brief:

## Setup-wizard status check (first line of brief when incomplete)

If `.workspace-identity.json` does NOT have `type: "ceo-master"`, run:

`python3 scripts/apply-wizard-answers.py --status`

Parse the returned `completion_pct`. If it is `< 100`, prepend this line to the brief above every other section:

> Setup is `<completion_pct>`% complete - `<required.pending + required.skipped>` required questions open. Run /setup-wizard to finish.

Skip entirely when:
- `.workspace-identity.json` has `type: "ceo-master"`, OR
- `--status` exits non-zero, OR
- `completion_pct >= 100`.

### 1. Context Confirmation
Brief statement: who the user is, what 31C is, where we are (post-launch, deployments in progress), current operational state. Include workspace type (CEO or exec) as detected from `.workspace-identity.json`.

### 2. Pipeline Pulse
Summary of active deals, investor conversations, and partnership discussions from pipeline.md. Flag anything requiring immediate attention.

### 2.1 Previous Session Handoff
Check if `outputs/operations/handoff.md` exists. If it does:
- Read the file and extract the YAML frontmatter (`created`, `session_summary`, `task_progress`, `urgency`) and the `## Next Action` section body
- Present to the user:

> ## Previous Session Handoff
> Created: {created}
> Summary: {session_summary}
> Progress: {task_progress} tasks
> Next action: {next_action text from body}
>
> Resume this work, or start fresh?

- If user says **resume**: load the referenced plan file (from `plan` frontmatter field, if present) and pick up from the next action described in the handoff
- If user says **fresh** or moves on to a different topic: move the handoff file to `outputs/operations/handoff-archive/YYYY-MM-DD-HHmm-{slug}.md` where slug is derived from `session_summary` (lowercase, spaces to hyphens, truncated to 40 chars). Create the `handoff-archive/` directory if it doesn't exist.
- If the `urgency` field is `high`, flag the handoff prominently: "**URGENT handoff from previous session - review before starting new work.**"

If `outputs/operations/handoff.md` does not exist, skip this section entirely (do not mention handoffs).

### 2.5-2.13 Parallel Health Block

Run the seven health checks in parallel via one helper:

```bash
python scripts/prime-health-parallel.py
```

**Run it from the workspace you launched in — do not navigate away.** In the HEADING OS
split layout, scripts live in the ENGINE clone (`.heading-os`, your launch directory) and
all data is auto-resolved under the DATA root (`.heading-os-data`) by the scripts
themselves. So run the helper straight from the current directory. Do NOT `cd` into the
data overlay (`.heading-os-data` has no `scripts/`), and NEVER fall back to a different
workspace such as `ceo-main` — that would report a DIFFERENT workspace's data and silently
mislead the briefing. If the helper errors, debug it in place; the correct invocation is
always the bare `python scripts/prime-health-parallel.py` from the launch directory.

This single invocation dispatches CRM health, Knowledge health, Memory file scan, Email-Intel state check, Threads archive-scan, Fireside daemon health, and Sync-Exchange daemon health to a `ThreadPoolExecutor(max_workers=7)` and prints aggregated output in the order /prime expects:

- **### 2.5 Relationship Radar** -- RED contacts (overdue), YELLOW contacts (approaching), Active commitments due in the next 7 days, Total contacts tracked / individual CRM files. CEO workspace also surfaces company-wide CRM from crm-central.
- **### 2.7 Knowledge Base Health** -- total notes and status breakdown (seeds / growing / evergreen), stale seeds (>7 days old still seed status), orphan notes count, top 5 keywords.
- **### 2.9 Memory Health** -- count of memory files, MEMORY.md N/200 line budget, files >45 days flagged for review, orphan files (in memory directory but not linked from MEMORY.md). All clean = "Memory: N files, M/200 lines. All healthy."
- **### 2.10 Email Intelligence Status** -- last_run age vs 20-hour threshold, pending P1 task count from tasks.md. "Never run" surfaced when state.json missing.
- **### 2.11 Active Threads archive scan** -- dry-run results from `thread.py archive-scan`. Failure of this check never blocks /prime; the helper degrades the panel gracefully.
- **### 2.12 Fireside Daemon** -- daemon liveness check via `.fireside/daemon.pid`. If daemon is dead, pulse spawns a detached `fireside-bot-daemon.py daemon` automatically using the isolated venv. If alive, reports `started M/N`, `last poll X min ago`, and the next scheduled job time. Failure of auto-spawn surfaces an inline error with the manual fallback command.
- **### 2.13 Sync-Exchange Daemon** -- daemon liveness check via `.sync-exchange/daemon.pid`. If daemon is dead, pulse spawns a detached `sync-exchange-daemon.py daemon` cross-platform: `pythonw.exe` + `cmd /c start /B` on Windows, `start_new_session=True` on POSIX. If alive, reports pid + relative time of the last successful sync (parsed from `.sync-exchange/daemon.log`). The daemon runs `python scripts/sync-exchange.py --calendar --emails` every 2 hours; the first run fires immediately on daemon start. **Note:** on the always-on service host (Linux service VM, 2026-05-23+), this daemon runs as a systemd user unit, so the local PID file may be absent on the CEO machine even when Exchange sync is healthy — check the service host's heartbeat in that case.

After the parallel block prints, render the **Active Threads panel** (this part is not part of the parallel script - it requires reading MEMORY.md and per-thread files):

1. Read `## Active Threads` from MEMORY.md.
   - If the `## Active Threads` section is absent from MEMORY.md (no threads have been opened yet), skip the panel silently and continue.
2. For each thread line, parse the link target and read the file's `## Open follow-ups` section.
3. Count unchecked `- [ ]` items.
4. Render as:

```text
## Active Threads (N)

Business (M):
  - <title> - <hook> [K follow-ups open]
  ...

Personal (P) [CEO-ONLY]:
  - <title> - <hook> [K follow-ups open]
  ...
```

The archive-scan results from the parallel block above feed this section: if candidates exist, surface them with "Archive candidates: <list>. Run `python scripts/thread.py archive-scan --apply` to archive." If no candidates and no error, render no panel (silent success).

A failing health check in the parallel block never blocks the others; one failure is reported inline and /prime continues.

### 3. Upcoming Events & Deadlines
From current-data.md: any events, meetings, or deadlines in the next 30 days.

### 4. Strategic Heading
Current strategic heading and any drift signals visible from context.

### 5. Data Freshness
Check context file headers for `> Last verified: YYYY-MM-DD` dates. Flag any context file older than 30 days as stale. Note if DataStore has key documents loaded or is still sparse.

Also check sync output files - any file in `outputs/_sync/calendar/`, `outputs/_sync/emails/`, or `outputs/operations/email-intelligence/state.json` whose mtime is older than 48 hours is flagged RED with the literal label "SYNC STALE". The line format is:

> SYNC STALE: outputs/_sync/calendar/upcoming.md last refreshed Ndays ago — run `python scripts/sync-exchange.py` (or expected automation has stopped).

This catches a silent sync failure within one /prime cycle rather than 2 weeks later. The 48-hour threshold is intentional - allows a weekend gap, surfaces anything longer. If all sync files are fresh, the section reports "Sync data: fresh (all <48h)."

### 6. Available Skills

The canonical skill registry lives in `.claude/rules/skill-router.md` (already loaded as an always-active rule, so no re-read needed). Surface a CEO-facing catalog by:

1. Pulling skill names + one-line triggers from the skill-router skill registry tables (Intel / Communication / Content / CRM / Design / Strategy / Operations).
2. Suggest the 2-3 most contextually relevant skills for today, drawing on the pipeline pulse and active threads loaded earlier.
3. For a state-aware next-step recommendation mid-session, point the CEO at `/next` (reads what just happened and names the logical next command).

This section deliberately defers to the router rather than duplicating the catalog inline - the router is the single source of truth and the only file that updates when a new skill is added. Drift between this catalog and the actual `.claude/skills/` directory is exactly the failure mode the workspace-deep-audit (2026-05-14) flagged; `scripts/check-skill-router-sync.py` now enforces that every directory in `.claude/skills/` has a matching router entry.

### 7. Ready

Confirm ready to execute. Ask: "What are we working on today?"
