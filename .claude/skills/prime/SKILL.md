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
x-heading-orchestration:
  parallel_safe: false
  shared_state: []
  triggers: []
x-heading-capability:
  what: >
    Initializes a working session - loads full workspace context (personal, business, strategy, current data, pipeline, key contacts, datastore index), runs CRM/knowledge/sync/daemon health checks in parallel, surfaces what needs attention, and recommends the day's skills.
  how: >
    Explicit-invocation only (disable-model-invocation) - type /prime. Health block runs in-process via scripts/prime-health-parallel.py; ends by asking what to work on today.
  when: >
    Use once at the start of every working session. Mid-session, for a next-step recommendation use /next; for the daily briefing alone use /dashboard.
x-heading-routing:
  category: Operations
  triggers:
    - NEVER auto-trigger. Explicit `/prime` or "prime" only.
  exclusions:
    - All natural language
  compound: 'No'
  router: manual
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

Read `outputs/operations/handoff-archive/.latest/summary.md`. It is the newest
handoff in this workspace, written by the checkpoint hooks. If the file is
absent, skip this section and do not mention handoffs.

The file carries four things this section needs. A `Source:` line names the full
archive. A `Generated:` line gives the ISO timestamp. A `Trigger:` line says what
caused the save. A `## Next steps` list follows them. Present:

> ## Previous session handoff
> Generated: {Generated}
> Trigger: {Trigger}
> Next: {the first bullet under `## Next steps`}
> Full record: {Source}
>
> Resume this work, or start fresh?

On **resume**, read the archive named on the `Source:` line — that file is the
record, and the summary here is a bounded copy of it. On **fresh**, say nothing
further and move on. Archive NOTHING: the handoff already IS the archive, and
`.latest/` holds pointers that the next save overwrites on its own.

This section covers a session started cold. After a compaction, a `/clear`, or a
`--resume`, `.claude/hooks/checkpoint-inject.py` injects the same handoff at
SessionStart. The compaction summary in context is fresher than the pointer.
Do not repeat it.

**This replaced a dead reader on 2026-08-20.** The section used to open a single
flat `handoff` file at the operations root. It parsed frontmatter fields named
`created`, `session_summary`, `task_progress`, `urgency` and `plan`. Nothing in
the engine writes that file or those fields. Measured across every script and
hook, the writer count was zero and the file was absent. So this section had
done nothing since the mechanism moved to the dated archive, while reading like
a working feature.

### 2.5-2.16 Parallel Health Block

Run the health checks in parallel via one helper:

```bash
python scripts/prime-health-parallel.py
```

**Run it from the workspace you launched in — do not navigate away.** In the HEADING OS split layout, scripts live in the ENGINE clone (`.heading-os`, your launch directory). The scripts themselves auto-resolve all data under the DATA root (`.heading-os-data`). So run the helper straight from the current directory. Do NOT `cd` into the data overlay, which has no `scripts/`. NEVER fall back to a different workspace such as `ceo-main`, because that reports a DIFFERENT workspace's data and silently misleads the briefing. If the helper errors, debug it in place; the correct invocation is always the bare `python scripts/prime-health-parallel.py` from the launch directory.

This single invocation dispatches every check in the `CHECKS` registry of `scripts/prime-health-parallel.py` to a `ThreadPoolExecutor(max_workers=8)` and prints the aggregated output in the order /prime expects. The registry is the source of truth for the list; `tests/test_prime_health_registry.py` holds this page to it. Today it carries twelve. They are CRM health, Knowledge health, Memory file scan, Email-Intel state, and Threads archive-scan. Then Fireside daemon, Sync-Exchange daemon, Odin cadence, Ops-radar, Reminders due, Dream-shadow worklist, and the component update check. The last five render nothing when they have nothing to say:

- **### 2.5 Relationship Radar** -- RED contacts (overdue), YELLOW contacts (approaching), Active commitments due in the next 7 days, Total contacts tracked / individual CRM files. CEO workspace also surfaces company-wide CRM from the operator's own aggregate at `<data-root>/crm/aggregated/`.
- **### 2.7 Knowledge Base Health** -- total notes and status breakdown (seeds / growing / evergreen). Also stale seeds (>7 days old still seed status), orphan notes count, and top 5 keywords.
- **### 2.9 Memory Health** -- count of memory files, MEMORY.md N/200 line budget, and files >45 days flagged for review. Also orphan files, which sit in the memory directory but are not linked from MEMORY.md. All clean = "Memory: N files, M/200 lines. All healthy."
- **### 2.10 Email Intelligence Status** -- last_run age vs 20-hour threshold, pending P1 task count from tasks.md. "Never run" surfaced when state.json missing.
- **### 2.11 Active Threads archive scan** -- dry-run results from `thread.py archive-scan`. Failure of this check never blocks /prime; the helper degrades the panel gracefully.
- **### 2.12 Fireside Daemon** -- daemon liveness check via `.fireside/daemon.pid`. If daemon is dead, pulse spawns a detached `fireside-bot-daemon.py daemon` automatically using the isolated venv. If alive, reports `started M/N`, `last poll X min ago`, and the next scheduled job time. Failure of auto-spawn surfaces an inline error with the manual fallback command.
- **### 2.13 Sync-Exchange Daemon** -- daemon liveness check via `.sync-exchange/daemon.pid`. If daemon is dead, pulse spawns a detached `sync-exchange-daemon.py daemon` cross-platform: `pythonw.exe` + `cmd /c start /B` on Windows, `start_new_session=True` on POSIX. If alive, reports pid + relative time of the last successful sync (parsed from `.sync-exchange/daemon.log`). The daemon runs `python scripts/sync-exchange.py --calendar --emails` every 2 hours; the first run fires immediately on daemon start. **Note:** on the always-on service host (Linux service VM, 2026-05-23+), this daemon runs as a systemd user unit. The local PID file may therefore be absent on the CEO machine even when Exchange sync is healthy. Check the service host's heartbeat in that case.

After the parallel block prints, render the **Active Threads panel**. This part is not in the parallel script, because it must read the per-thread files:

1. Read the live set from disk:

   ```bash
   python scripts/thread.py list --status active
   ```

   Each line is `[status] <type>/<slug> - <title> (last_touched: DATE)`. A quiet
   thread carries a suffix: `[quiet until DATE]` for a dated pause, or
   `[quiet indefinitely]` for a freeze with no end date. Never surface a thread
   that carries either suffix. If the command prints nothing, no threads are
   open: skip the panel silently and continue.

   This used to read a `## Active Threads` block out of MEMORY.md. The block was
   retired on 2026-08-20. Every row quoted a live status and a live date, which
   `memory-discipline.md` forbids in an index hook. It had also drifted: 30
   threads were active on disk against 29 listed, and one of the 29 was closed.
   Disk is the record; the index was a copy of it.

2. For each thread, read the file's `## Open follow-ups` section under `threads/`.
3. Count unchecked `- [ ]` items.
4. Render as:

```text
## Active Threads (N)

Business (M):
  - <title> - last <DATE> [K follow-ups open]
  ...

Personal (P) [CEO-ONLY]:
  - <title> - last <DATE> [K follow-ups open]
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

Also check sync output files. Three of them count: `outputs/_sync/calendar/`, `outputs/_sync/emails/`, and `outputs/operations/email-intelligence/state.json`. Flag any whose mtime is older than 48 hours RED, with the literal label "SYNC STALE". The line format is:

> SYNC STALE: outputs/_sync/calendar/upcoming.md last refreshed Ndays ago — run `python scripts/sync-exchange.py` (or expected automation has stopped).

This catches a silent sync failure within one /prime cycle rather than 2 weeks later. The 48-hour threshold is intentional - allows a weekend gap, surfaces anything longer. If all sync files are fresh, the section reports "Sync data: fresh (all <48h)."

### 6. Available Skills

The canonical skill registry lives in `.claude/rules/skill-router.md` (already loaded as an always-active rule, so no re-read needed). Surface a CEO-facing catalog by:

1. Pulling skill names + one-line triggers from the skill-router skill registry tables (Intel / Communication / Content / CRM / Design / Strategy / Operations).
2. Suggest the 2-3 most contextually relevant skills for today, drawing on the pipeline pulse and active threads loaded earlier.
3. For a state-aware next-step recommendation mid-session, point the CEO at `/next`. It reads what just happened and names the logical next command.

This section deliberately defers to the router rather than duplicating the catalog inline. The router is the single source of truth, and the only file that updates when a new skill is added. Drift between this catalog and the actual `.claude/skills/` directory is exactly the failure mode the workspace-deep-audit (2026-05-14) flagged. The registry tables are now generated from each skill's `x-heading-routing` frontmatter. In CI and pre-commit, `scripts/generate-skill-router.py --check` enforces that the router matches its source with no content drift.

### 7. Ready

Confirm ready to execute. Ask: "What are we working on today?"
