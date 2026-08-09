# Compound Workflow Patterns — Full Agent Prompts

> Last Updated: 2026-05-12
> Source: `.claude/rules/skill-orchestrator.md` (full pattern prose extracted on 2026-05-11; per-agent model assignments + Patterns 6 and 7 added 2026-05-12 via perf-tuneup v2 Phase 5)

Full agent-briefing prose for each of the 7 compound workflow patterns
dispatched by the orchestrator. The rule itself carries the safety floor
(DO-NOT lists, approval-gate counts, concurrency caps); this file carries
the rich briefing text the rule's prefatory paragraph instructs Claude
to Read when dispatching.

Section headings use `## Pattern N — <name>` so Claude can read a single
section per dispatch.

---

## Pattern 1 — Deep Meeting Prep

**Trigger:** Router detects "prepare for meeting" with depth signals.

**Announcement:**
> Running Deep Meeting Prep for [name/company]. Dispatching 4 parallel research agents - OSINT (Opus), Voss tactical prep (Opus), CRM history (Haiku), and counterpart comms scout (Haiku).

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

**Execution:**

PARALLEL PHASE (4 background agents):

Agent 1 (Sonnet) prompt: "Run /email-intel fetch and analyze phases ONLY. Fetch Exchange emails since last run. Analyze and categorize each message. Build a structured digest with proposed actions (CRM logs, pipeline updates, tasks). DO NOT execute any CRM writes. DO NOT update pipeline. DO NOT update state.json. Return the complete digest and proposed action list."

Agent 2 (Sonnet) prompt: "Run /viraid fetch and analyze phases ONLY. Fetch VIRAID Telegram channel messages since last run. Analyze and categorize each message. Build action proposals. DO NOT execute any CRM writes. DO NOT update task files. DO NOT update state.json. Return the complete digest and proposed action list."

Agent 3 (Haiku) prompt: "Calendar scout. Read today plus the next 3 days from the 31C Exchange calendar (ceo@31c.io, configured timezone). Return an inline summary: event title, start time (local), duration, attendees, location/Zoom. Flag any conflicts and any external counterparts that are in pipeline or CRM. Do NOT create, modify, or respond to any event."

Agent 4 (Haiku) prompt: "Sentinel-queue scout. Read the Sentinel daemon's unprocessed urgent queue (logs and state under outputs/operations/sentinel/ or the configured Sentinel state path). Return an inline summary of items the daemon flagged as urgent but that have not yet been triaged. Do NOT modify Sentinel state, do NOT acknowledge or dismiss items."

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

**Reality (corrected 2026-06-08):** Pattern 6 does NOT dispatch subagents. `/prime`'s health block runs **in-process** in `scripts/prime-health-parallel.py`, which executes its checks concurrently in a `ThreadPoolExecutor(max_workers=8)` and renders each result as an output block. There are no Haiku agents and no per-check model calls — each check shells out to an existing health script or reads a state file. The list below documents the eight checks, not agent prompts. (This section previously described "5 parallel Haiku agents"; that was doc drift from an abandoned dispatch model.)

**Announcement:**
> Running session boot. Eight read-only health checks in-process (ThreadPoolExecutor) — CRM, knowledge, memory, email-intel state, threads, fireside, sync-exchange, Odin cadence.

**Checks (the `CHECKS` registry):**

- `crm_health` — `scripts/crm-health.py` (read-only): contact count, overdue-per-cadence, type-mismatch warnings.
- `knowledge_health` — walks `knowledge/` (+ `knowledge/odin-brain/`): note counts, oldest unedited note, orphans.
- `memory_health` — auto-memory registry (`memory/MEMORY.md` + per-key files): count, last consolidation, stale/contradictory entries.
- `email_intel_status` — reads email-intel `state.json`: last successful run, last error, unprocessed-message posture.
- `active_threads_archive_scan` — active threads under `threads/business/` + `threads/personal/` (CEO-only): names, last-updated, stale (>30d) flags.
- `fireside_health` — Fireside daemon health.
- `sync_exchange_health` — Sync-Exchange daemon health.
- `odin_cadence` — Odin cadence nudge (ceo-only; renders nothing when empty).

AGGREGATION: `run_all()` collects the eight results into /prime's normal context-load output. /prime then proceeds with its session-start sequence.

APPROVAL GATE: None — read-only.

WRITE PHASE: None.

DEGRADATION: A check that errors or times out is reported inline (`status: error`) and never aborts the others; `/prime` never blocks on a health-check failure.

---

## Pattern 7 — Push & Backup Parallel

**Trigger:** `/push-updates` invocation. See `.claude/rules/skill-router.md` § Compound Workflow Triggers.

**Announcement:**
> Running Push & Backup. Corporate publish runs sequentially first (Sonnet). After approval and successful publish, ceo-main git push (Haiku) and CRM aggregate (Haiku) run in parallel.

**Execution:**

PRE-PUBLISH PHASE (sequential, Sonnet):

Classify changed files per config/routing-map.yaml. Stage corporate-classified files. Present the changeset summary to the CEO.

APPROVAL GATE - HARD STOP:
"Here's the corporate publish changeset: [N files changed, M corporate-classified]. Approve to publish?"
CEO must explicitly approve before any publish proceeds.

CORPORATE PUBLISH PHASE (sequential, Sonnet, post-approval):

Run scripts/publish-corporate.py (or the equivalent). Commit + push to the corporate repo. Bump BUILD.json. Confirm the corporate `git push` succeeded.

PARALLEL TAIL PHASE (2 background agents, both Haiku, both write-isolated):

Agent 1 (Haiku) prompt: "ceo-main git push tail. Stage any CEO-only changes in ceo-main, commit with the matching push-updates commit message, and push to the ceo-main `origin/main` remote. Confirm the push succeeded. Do NOT touch the corporate repo, BUILD.json, or any executive workspace. Do NOT touch ../31c-crm-central/."

Agent 2 (Haiku) prompt: "CRM aggregate tail. Run scripts/aggregate-crm.py to refresh ../31c-crm-central/ from the per-exec CRM repos. Commit and push the result to the 31c-crm-central remote if there are changes. Do NOT touch ceo-main or the corporate repo or any executive workspace."

WAIT for both to complete.

SYNTHESIS PHASE: Report the three results inline: corporate publish status, ceo-main push status, CRM aggregate status. Note any per-exec sync acceleration (manual /sync) that may be needed.

WRITE PHASE: All writes occur within the agents; nothing further is written by the orchestrator after the tail completes.

DEGRADATION: If the corporate publish fails, abort — do NOT launch the tail. If only one tail agent fails, the other completes and the failure is reported. Offer retry for the failed tail.
