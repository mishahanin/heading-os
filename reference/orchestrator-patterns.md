# Compound Workflow Patterns — Full Agent Prompts

Consumed by: `.claude/rules/skill-orchestrator.md` (always-on). The rule's
Workflow Patterns table names one row per pattern and requires a Read of the
matching section here before any dispatch.

> Last Updated: 2026-08-20
> Source: `.claude/rules/skill-orchestrator.md` (full pattern prose extracted on 2026-05-11; per-agent model assignments + Patterns 6 and 7 added 2026-05-12 via perf-tuneup v2 Phase 5; per-pattern agent-file roles, per-pattern approval-gate statements, and Pattern 6's check list + check safety floor moved down from the rule on 2026-08-20)

Full agent-briefing prose for each of the 7 compound workflow patterns
dispatched by the orchestrator. The rule carries the cross-pattern safety
floor (the Parallelization Safety Model, Conflict Detection, and Orchestrator
Principles 1-8); this file carries every per-pattern detail — the agent-file
role mapping, the DO-NOT lists, the approval gates, the concurrency notes, and
the rich briefing text.

Reading the matching section here is MANDATORY before dispatching a pattern.
Never paraphrase a safety constraint from memory.

Section headings use `## Pattern N — <name>` so Claude can read a single
section per dispatch.

## Roles are agent files

Four recurring roles are agent definitions in `.claude/agents/`, not inline
prompts: `crm-reader`, `comms-scout`, `datastore-validator`, `draft-writer`.
Each carries its own model and its own tool list, and the tool list is the
enforcement: `draft-writer` has no `Bash`, so it cannot reach
`scripts/send-email.py` whatever a dispatch prompt says. Where a pattern below
names one of these roles, dispatch THAT agent — the inline prompt text is the
briefing to hand it, not a substitute for it. A step with no agent file named
runs on the model stated inline.

---

## Pattern 1 — Deep Meeting Prep

**Trigger:** Router detects "prepare for meeting" with depth signals.

**Announcement:**
> Running Deep Meeting Prep for [name/company]. Dispatching 4 parallel research agents - OSINT (Opus), Voss tactical prep (Opus), CRM history (Haiku), and counterpart comms scout (Haiku).

**Roles and models:**

- `/osint` scout — Opus (per CEO decision: /osint stays Opus)
- `/voss` prep — Opus (voice-grade)
- CRM history reader — the `crm-reader` agent
- Counterpart comms scout (Exchange + Telegram, last 30 days) — the `comms-scout` agent

**Safety floor (each agent):**

- Do NOT write to CRM files.
- Do NOT modify any workspace state.
- Comms scout is read-only — no message sending, no marking as read.

**Approval:** no hard gate before write phase — brief is presented first, then a single CRM log entry is written sequentially.

**Write phase:** sequential, after brief is presented to CEO. CRM log entry only.

**Agents dispatched:** 4. Global concurrency cap of 5 still applies per Principle 5.

**Execution:**

PARALLEL PHASE (4 background agents via Agent tool with `run_in_background: true`):

Agent 1 (Opus) prompt: "Run /osint on [person] and/or [company]. Context: [any user-provided context]. Output a complete intelligence brief to outputs/intel/osint/. Do NOT write to CRM files or modify any workspace state."

Agent 2 (Opus) prompt: "Run /voss tactical prep for a meeting with [counterpart name]. Context: [relationship context from CRM if available, counterpart type if known]. Voice grounding: reference/misha-voice.md. Output a tactical playbook to outputs/negotiations/. Do NOT write to CRM files."

Agent 3 (Haiku) prompt: "Read the CRM contact file for [name] from crm/contacts/. Summarize: last 5 interactions, relationship health score, any open items or commitments, last touch date. Return the summary inline. Do NOT modify the CRM file."

Agent 4 (Haiku) prompt: "Counterpart comms scout. Read the last 30 days of Exchange messages and Telegram DMs/group references involving [counterpart name] and/or [counterpart email] and/or [counterpart handle]. Use exchangelib for Exchange and the telegram skill's read tooling for Telegram. Build a single inline summary: subject lines / message snippets, dates, channel, and any open commitments or unanswered questions either side has raised. Do NOT send any message. Do NOT mark anything as read. Do NOT modify any state."

WAIT for all four to complete.

SYNTHESIS PHASE: Feed all four outputs into /meeting-prep as enriched context. /meeting-prep produces MD + HTML + PDF at outputs/operations/meeting-prep/.

WRITE PHASE (sequential, after brief is presented): CRM log entry for the meeting prep activity.

DEGRADATION: If any agent fails, the others still complete. /meeting-prep runs with whatever enrichment is available. If the comms scout returns empty (no prior touches in 30 days), note "first-touch meeting" in the brief.

---

## Pattern 2 — Morning Comms Processing

**Trigger:** Router detects "process my comms", "check everything", "morning", "what did I miss".

**Announcement:**
> Running Morning Comms. Fetching Exchange email (Sonnet), Telegram VIRAID channel (Sonnet), calendar (Haiku), and Sentinel queue (Haiku) in parallel.

**Roles and models:**

- `/email-intel` fetch — Sonnet
- `/viraid` fetch — Sonnet
- Calendar scout (today + next 3 days from Exchange) — the `comms-scout` agent, channel `calendar`
- Sentinel-queue scout (unprocessed urgent items) — the `comms-scout` agent, channel `sentinel-queue`

**Safety floor (each agent):**

- DO NOT execute any CRM writes.
- DO NOT update pipeline.
- DO NOT update state.json.
- DO NOT update task files.
- Calendar and Sentinel scouts are read-only.
- Returns digest only.

**Approval:** one hard gate before any writes.

**Write phase:** sequential, one CRM contact file at a time. State files (email-intel state.json, viraid state.json) update only after approval.

**Agents dispatched:** 4. Global concurrency cap of 5 still applies per Principle 5.

**Execution:**

PARALLEL PHASE (4 background agents):

Agent 1 (Sonnet) prompt: "Run /email-intel fetch and analyze phases ONLY. Fetch Exchange emails since last run. Analyze and categorize each message. Build a structured digest with proposed actions (CRM logs, pipeline updates, tasks). DO NOT execute any CRM writes. DO NOT update pipeline. DO NOT update state.json. Return the complete digest and proposed action list."

Agent 2 (Sonnet) prompt: "Run /viraid fetch and analyze phases ONLY. Fetch VIRAID Telegram channel messages since last run. Analyze and categorize each message. Build action proposals. DO NOT execute any CRM writes. DO NOT update task files. DO NOT update state.json. Return the complete digest and proposed action list."

Agent 3 (Haiku) prompt: "Calendar scout. Read today plus the next 3 days from the 31C Exchange calendar (ceo@31c.io, configured timezone). Return an inline summary: event title, start time (local), duration, attendees, location/Zoom. Flag any conflicts and any external counterparts that are in pipeline or CRM. Do NOT create, modify, or respond to any event."

Agent 4 (Haiku) prompt: "Sentinel-queue scout. Read the Sentinel daemon's unprocessed urgent queue from `.sentinel/state.json` and `.sentinel/sentinel.log`, under the engine workspace root. Return an inline summary of items the daemon flagged as urgent but that have not yet been triaged. If `.sentinel/` is absent the daemon has never run here: say so, and do not report an empty queue as a clear queue. Do NOT modify Sentinel state, do NOT acknowledge or dismiss items."

The path was `outputs/operations/sentinel/` until 2026-08-23, which
`scripts/sentinel.py:89` has never used: `RUNTIME_DIR = WORKSPACE_ROOT / ".sentinel"`.
No such directory exists in either repository, so the scout read nothing, found nothing,
and Morning Comms reported an empty urgent queue however full it was. Silent
under-reporting, in a pattern whose own header says never to paraphrase a constraint from
memory. `tests/test_orchestrator_paths_exist_in_code.py` now derives the path from the
daemon.

WAIT for all four to complete.

PRESENTATION PHASE (sequential):
- Present email digest: message count, categories, highlights, proposed actions
- Present Viraid digest: message count, categories, highlights, proposed actions
- Present calendar window: today + next 3 days, conflicts, pipeline/CRM matches
- Present Sentinel urgent queue: unprocessed items
- Combined: "X emails processed, Y Telegram messages, Z calendar events in the next 3 days, W urgent Sentinel items. N total actions proposed."

APPROVAL GATE - HARD STOP:
"Here are all proposed actions from both channels. Approve, modify, or reject each."
CEO must explicitly approve before ANY writes proceed.

WRITE PHASE (sequential, post-approval):
For each approved action:
- CRM logs (one contact file at a time)
- Pipeline updates (if any)
- State file updates (email-intel state.json, viraid state.json)
- Task file updates

DEGRADATION: If Exchange fetch fails, present Viraid results alone (and vice versa). Calendar and Sentinel scouts are read-only and degrade independently — if either returns empty or errors, note it but never block the other channels.

---

## Pattern 3 — Post-Event Follow-ups

**Trigger:** Router detects "follow up with everyone from [event]", "event follow-ups", "send all follow-ups".

**Pre-condition:** Either /event-debrief has produced a contact list, or the user provides names directly.

**Announcement:**
> Running Post-Event Follow-ups for [N] contacts. Drafting all follow-up emails in parallel (Sonnet per drafter, Haiku per image prompt if imagery is requested).

**Roles and models:**

- Drafter agents (one per contact) — the `draft-writer` agent
- Image-prompt agents (one per post, when imagery requested) — Haiku

**Safety floor (each agent):**

- DO NOT send the email.
- DO NOT write to CRM.

**Approval:** one hard gate before any sends or CRM writes.

**Write phase:** sequential per approved contact — send via scripts/send-email.py, then write CRM interaction log, then confirm. If >5 contacts, batch in groups of 5.

**Agents dispatched:** up to 5 (concurrency cap). Global concurrency cap of 5 applies per Principle 5.

**Execution:**

CONTACT LIST PHASE (if no debrief exists): Ask user to list the contacts, or run /event-debrief first. STOP until confirmed.

PARALLEL PHASE (up to 5 background agents):

For each contact, Agent N (Sonnet) prompt: "Draft a follow-up email for [contact name] from [company]. Event context: [event name, date, topics discussed]. CRM data: [paste relevant CRM contact data]. Use Misha's voice from reference/misha-voice.md. Apply Voss principles. Produce: subject line + email body. DO NOT send the email. DO NOT write to CRM."

OPTIONAL parallel image-prompt agents (Haiku, one per post requesting imagery): "Generate an image prompt using /image-prompt for the follow-up to [contact name] about [topic]. Return the prompt text inline."

WAIT for all to complete.

PRESENTATION: All N drafts labeled: TO: [Name] | [Company] | [Role]

APPROVAL GATE - HARD STOP:
"Here are [N] follow-up drafts. Approve all, approve selectively, edit any, or reject any."

SEND + LOG PHASE (sequential, post-approval):
For each approved: send via scripts/send-email.py, write CRM interaction log, confirm each.

DEGRADATION: If one draft fails, present the others. Offer retry.

CONCURRENCY LIMIT: Maximum 5 parallel agents. If >5 contacts, batch in groups of 5.

---

## Pattern 4 — Weekly Content Production

**Trigger:** Router detects "content for the week", "3 posts this week", "weekly LinkedIn", "plan and draft posts".

**Announcement:**
> Running Weekly Content Production. Planning first (Opus, voice-grade), then drafting all 3 posts in parallel (Sonnet per drafter) with image prompts (Haiku per prompt).

**Roles and models:**

- Planning phase (`/linkedin-series`) — Opus (content strategy is voice-grade)
- Post drafters (one per post) — the `draft-writer` agent
- Image-prompt generators (one per post) — Haiku

**Safety floor (each agent):**

- Agents save draft files to `outputs/content/linkedin/` only.
- DO NOT publish or post.
- DO NOT send anything externally.

**Approval:** two hard gates — Gate 1 approves the 3-post plan before drafting; Gate 2 approves individual posts before any publish or image generation.

**Write phase:** draft files written during parallel phase. Publishing and image generation only after Gate 2 approval.

**Agents dispatched:** up to 6 (post-Gate-1 parallel phase). Global concurrency cap of 5 per Principle 5 — if all 6 needed, batch posts and image prompts in two rounds.

**Execution:**

PLANNING PHASE (sequential, Opus): Run /linkedin-series to produce 3-post plan with themes, angles, key messages. Content strategy is voice-grade — Opus.

APPROVAL GATE #1: "Here's the 3-post plan. Approve before I draft all three?"
If rejected: revise plan. Do not proceed.

PARALLEL PHASE (up to 6 background agents):

Agent 1 (Sonnet) prompt: "Draft LinkedIn post #1 using /linkedin-post. Theme: [from plan]. Angle: [from plan]. Save to outputs/content/linkedin/YYYY-MM-DD-slug-1.md. Follow Misha's voice from reference/misha-voice.md."

Agent 2 (Sonnet) prompt: "Draft LinkedIn post #2 using /linkedin-post. Theme: [from plan]. Angle: [from plan]. Save to outputs/content/linkedin/YYYY-MM-DD-slug-2.md."

Agent 3 (Sonnet) prompt: "Draft LinkedIn post #3 using /linkedin-post. Theme: [from plan]. Angle: [from plan]. Save to outputs/content/linkedin/YYYY-MM-DD-slug-3.md."

Agent 4 (Haiku) prompt: "Generate an image prompt using /image-prompt for a LinkedIn post about: [Post 1 theme]. Return the prompt text inline."

Agent 5 (Haiku) prompt: "Generate an image prompt using /image-prompt for a LinkedIn post about: [Post 2 theme]. Return the prompt text inline."

Agent 6 (Haiku) prompt: "Generate an image prompt using /image-prompt for a LinkedIn post about: [Post 3 theme]. Return the prompt text inline."

WAIT for all to complete.

PRESENTATION: All 3 posts with their image prompts and saved file paths.

APPROVAL GATE #2: "Review all three. Edit any, approve all, or approve selectively."

OPTIONAL: "Generate images for approved posts? (Uses /flux-image)" If yes, dispatch /flux-image for each.

DEGRADATION: If one post fails, present the others. Offer retry.

---

## Pattern 5 — Full Deal Intelligence

**Trigger:** Router detects "how do we win [deal]", "full deal prep", "complete deal analysis", "win strategy for [prospect]".

**Announcement:**
> Running Full Deal Intelligence for [prospect]. Dispatching 5 parallel research agents - OSINT (Opus), competitive analysis (Sonnet), strategic reasoning (Opus), deal-context reader (Haiku), and datastore price/proof validator (Sonnet).

**Roles and models:**

- `/osint` — Opus (per CEO decision: /osint stays Opus)
- `/competitor-intel` — Sonnet (per Phase 1.1)
- `/deep-think` — Opus
- Deal-context reader (CRM contact files + pipeline.md entry) — the `crm-reader` agent
- Datastore price/proof validator — the `datastore-validator` agent

**Safety floor (each agent):**

- Do NOT modify any workspace state.
- Do NOT write to CRM files.
- Research agents return output inline or to `outputs/intel/` and `outputs/negotiations/` only.

**Approval:** no approval gate — this pattern produces a research package only; no CRM writes or external actions occur.

**Write phase:** none. Deal package saved to `outputs/intel/` and/or `outputs/negotiations/` after synthesis.

**Agents dispatched:** 5. Global concurrency cap of 5 applies per Principle 5 — exact ceiling, no wave-batching needed.

**Execution:**

PARALLEL PHASE (5 background agents):

Agent 1 (Opus) prompt: "Run /osint on [prospect organization]. Context: [deal context, known contacts]. Output a full intelligence brief to outputs/intel/osint/. Do NOT modify any workspace state."

Agent 2 (Sonnet) prompt: "Run /competitor-intel on competing vendors for [prospect's sector]. Known competitors: [list if available]. Technology requirements: [if known]. Return competitive analysis inline."

Agent 3 (Opus) prompt: "Run /deep-think structured reasoning on the [prospect] opportunity. Consider: deal context, prospect profile, 31C positioning, risks, Black Swans. Return structured reasoning inline."

Agent 4 (Haiku) prompt: "Deal-context reader. Read all CRM contact files in crm/contacts/ that match [prospect organization or any known contact at the prospect] and read the pipeline.md entry for [prospect / deal name]. Return inline: contact roster (name, role, last touch, health), pipeline stage, deal value (or TBD), notes, open commitments either side has made. Do NOT modify any file."

Agent 5 (Sonnet) prompt: "Datastore price/proof validator. Cross-reference all factual claims expected to appear in the deal package — ODUN.ONE pricing, modules, hardware specs, proof points, partner references — against the authoritative datastore/ tree (products/, corporate/, intelligence/, investment/, operations/). Return inline a list of validated claims, contradictions found, and any gaps where a claim has no source backing. Do NOT modify any datastore file."

WAIT for all five to complete.

SYNTHESIS PHASE: Feed all five into /deal-strategy. Produces: prospect intel summary, competitive positioning matrix, pricing recommendation (precise numbers per Voss), objection handling playbook, Voss tactical approach, next steps with timeline. Datastore validator output flags any claim the strategy must avoid or qualify.

OUTPUT: Deal package presented inline. Saved to outputs/intel/ and/or outputs/negotiations/.

DEGRADATION: If OSINT finds minimal data, /deal-strategy still runs with competitive and strategic inputs. If the deal-context reader finds no CRM or pipeline match, note "new opportunity, no prior context" and proceed. If the datastore validator finds gaps, /deal-strategy must explicitly flag those gaps in the package. Always produce the package.

---

## Pattern 6 — Session Boot Parallel

**Trigger:** Explicit `/prime` invocation only. No natural-language triggers — `/prime` is slash-command-only per the skill-router rules table.

**Reality (corrected 2026-06-08):** Pattern 6 does NOT dispatch subagents. Unlike Patterns 1–5 and 7, `/prime`'s health block runs **in-process** in `scripts/prime-health-parallel.py`, which executes its read-only checks concurrently in a `ThreadPoolExecutor(max_workers=8)` and renders each result as an output block. No subagent and no per-check model call is involved — each check shells out to an existing health script or reads a state file. The Principle-5 concurrency cap therefore does not apply to `/prime`. The list below documents the checks, not agent prompts. (This section previously described "5 parallel Haiku agents"; that was doc drift from an abandoned dispatch model.)

**Announcement:**
> Running session boot. Read-only health checks in-process (ThreadPoolExecutor) — CRM, knowledge, memory, email-intel state, threads, fireside, sync-exchange, Odin cadence, ops radar, reminders, dream shadow.

**Mechanism:** in-process `ThreadPoolExecutor(max_workers=8)`, one worker per check, aggregated by `run_all()`. A check that errors or times out is reported inline and never aborts the others.

**Checks (12, defined in the `CHECKS` registry).** `scripts/prime-health-parallel.py` is the source of truth. This list mirrors it, and saying it "can lag" is not a control: on 2026-08-23 it had lagged, at 11 against the registry's 12, while the `/prime` docs page said seven. `tests/test_prime_check_registry_matches_its_docs.py` now derives the count and the key list from the registry and fails here and on that page, so the drift is caught instead of disclaimed.

- `crm_health` — CRM health. `scripts/crm-health.py` (read-only): contact count, overdue-per-cadence, type-mismatch warnings.
- `knowledge_health` — knowledge-base health. Walks `knowledge/` (+ `knowledge/odin-brain/`): note counts, oldest unedited note, orphans.
- `memory_health` — auto-memory registry health (`memory/MEMORY.md` + per-key files): count, last consolidation, stale/contradictory entries.
- `email_intel_status` — Email Intelligence last-run posture. Reads email-intel `state.json`: last successful run, last error, unprocessed-message posture.
- `active_threads_archive_scan` — active threads, stale flag. Threads under `threads/business/` + `threads/personal/` (CEO-only): names, last-updated, stale (>30d) flags.
- `fireside_health` — Fireside daemon health.
- `sync_exchange_health` — Sync-Exchange daemon health.
- `odin_cadence` — Odin cadence nudge (ceo-only; renders nothing when empty).
- `ops_radar` — Ops-radar detector (ceo-only; renders nothing when all clear).
- `reminders_due` — durable reminders due/upcoming (renders nothing when empty).
- `dream_shadow` — dream-shadow nightly worklist (reads the latest report only, never runs the scan itself; renders nothing when empty).
- `updates` — component updates. Reports available updates; reads only.

**Safety floor (each check):**

- Ten of the twelve are read-only, and any check added here must be.
- Do NOT write to any workspace file.
- Do NOT modify state.json or any registry.
- **Two are not read-only, and the floor above never covered them.** `fireside_health`
  and `sync_exchange_health` shell out to `scripts/fireside-pulse.py` and
  `scripts/sync-exchange-pulse.py`, whose stated job is liveness check PLUS auto-start:
  finding the daemon down, each spawns a detached one that outlives the shell. That is a
  process and a PID file, not a workspace file, which is how it slipped under a floor
  written as "all checks are read-only" and left there through 2026-08-23. The exception
  is deliberate and stays; what changes is that it is now written down where the floor is
  read.

AGGREGATION: `run_all()` collects the results into /prime's normal context-load output. /prime then proceeds with its session-start sequence.

APPROVAL GATE: None — read-only.

WRITE PHASE: None.

AGENTS DISPATCHED: none — in-process threads, not subagents. Principle 5's cap is not engaged by `/prime`.

**Reference:** `scripts/prime-health-parallel.py` (`CHECKS` registry + `run_all()`).

DEGRADATION: A check that errors or times out is reported inline (`status: error`) and never aborts the others; `/prime` never blocks on a health-check failure.

---

## Pattern 7 — Push & Backup Parallel

**Trigger:** `/push-updates` invocation. See `.claude/rules/skill-router.md` § Compound Workflow Triggers.

**Announcement:**
> Running Push & Backup. Corporate publish runs sequentially first (Sonnet). After approval and successful publish, the engine + data push (Haiku) and CRM aggregate (Haiku) run in parallel.

**Roles and models:**

- Corporate publish (sequential) — Sonnet
- Engine + data push tail — Haiku
- CRM aggregate tail — Haiku

**Safety floor (each agent):**

- Each tail agent writes to ONE specific path; no overlap.
- The push tail writes only to the `origin/main` remote of the engine clone and
  of the data overlay, through `scripts/push-all.py`. It does NOT write to
  `ceo-main`: that legacy single workspace was retired on the 2026-06-15
  cutover to the two-part topology, and this line named it until 2026-08-23.
- CRM aggregate tail writes only to `../31c-crm-central/`.
- Tail agents do NOT touch the corporate repo, BUILD.json, or executive workspaces.

**Approval:** one hard gate before corporate publish.

**Write phase:** corporate publish first (serial, includes BUILD.json bump + corporate `git push`); then the engine + data push and the CRM aggregate launch as a parallel wave.

**Agents dispatched:** 2 in the parallel tail wave. Global concurrency cap of 5 per Principle 5 — well under the cap.

**Execution:**

PRE-PUBLISH PHASE (sequential, Sonnet):

Classify changed files per config/routing-map.yaml. Stage corporate-classified files. Present the changeset summary to the CEO.

APPROVAL GATE - HARD STOP:
"Here's the corporate publish changeset: [N files changed, M corporate-classified]. Approve to publish?"
CEO must explicitly approve before any publish proceeds.

CORPORATE PUBLISH PHASE (sequential, Sonnet, post-approval):

Run scripts/publish-corporate.py (or the equivalent). Commit + push to the corporate repo. Bump BUILD.json. Confirm the corporate `git push` succeeded.

PARALLEL TAIL PHASE (2 background agents, both Haiku, both write-isolated):

Agent 1 (Haiku) prompt: "Engine + data push tail. Run `python scripts/push-all.py` to commit and push BOTH repos — the engine clone and the data overlay — to their own `origin/main`. That script is the only sanctioned push path: it runs the pre-push secret scan and verifies each branch is level with its remote. Report its exit code and headline verbatim; exit 3 means at least one repo was skipped for a named reason. Do NOT touch the corporate repo, BUILD.json, or any executive workspace. Do NOT touch ../31c-crm-central/."

Agent 2 (Haiku) prompt: "CRM aggregate tail. Run scripts/aggregate-crm.py to refresh ../31c-crm-central/ from the per-exec CRM repos. Commit and push the result to the 31c-crm-central remote if there are changes. Do NOT touch the engine clone, the data overlay, the corporate repo, or any executive workspace."

WAIT for both to complete.

SYNTHESIS PHASE: Report the three results inline: corporate publish status, engine + data push status, CRM aggregate status. Note any per-exec sync acceleration (manual /sync) that may be needed.

WRITE PHASE: All writes occur within the agents; nothing further is written by the orchestrator after the tail completes.

DEGRADATION: If the corporate publish fails, abort — do NOT launch the tail. If only one tail agent fails, the other completes and the failure is reported. Offer retry for the failed tail.
