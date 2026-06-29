<!-- audit-skip-start -->
<!-- version: 2.1.0 | last-updated: 2026-06-08 -->
<!-- audit-skip-end -->
---
paths: []
always_active: true
---

# Parallel Orchestrator

Last Verified: 2026-06-08

Detects compound workflows and dispatches parallel agents for research phases while serializing write phases. This rule is always active. Invoked by the skill router when compound workflow triggers are detected.

## Parallelization Safety Model

Before dispatching any parallel pattern, read the SKILL.md frontmatter of each skill you plan to dispatch. Orchestration metadata lives under the namespaced `x-31c-orchestration:` block. Decide parallelization based on `x-31c-orchestration.parallel_safe` and `x-31c-orchestration.shared_state`.

The `x-` prefix marks this as a workspace extension, not part of Anthropic's standard SKILL.md spec. See `.claude/rules/development-standards.md` for the full frontmatter contract and an example shape.

### Parallel Safety Levels

| Level | Meaning | Behavior |
|---|---|---|
| `true` | Read-only, or writes to isolated unique output paths | Safe to dispatch as background agent |
| `partial` | Has safe phases (research/fetch) and unsafe phases (CRM/pipeline/state writes) | Parallelize research phases only. Serialize write phases post-approval. |
| `false` | Writes to shared state throughout, multi-repo ops, inherently sequential | Never parallelize. Run solo. |

### Conflict Detection

Before dispatching:

1. Read SKILL.md frontmatter for each skill being dispatched. Look at `x-31c-orchestration.parallel_safe` and `x-31c-orchestration.shared_state`.
2. If `x-31c-orchestration.parallel_safe: false` - run that skill solo
3. If `x-31c-orchestration.parallel_safe: partial` - only dispatch its research phase
4. Check `x-31c-orchestration.shared_state` arrays for path overlaps between agents (substring matching: `crm/contacts/` conflicts with `crm/contacts/john-smith.md`)
5. If the `x-31c-orchestration` block is missing or has no parallel metadata - treat as `parallel_safe: false` (safe default). Log: "Skill [name] has no parallel metadata. Running sequentially."

### Default Values for Missing Metadata

If a SKILL.md lacks the `x-31c-orchestration` block (or any of its fields):
- `parallel_safe` defaults to `false`
- `shared_state` defaults to `["UNKNOWN"]`
- `triggers` defaults to `[]`

Nothing breaks - the skill runs sequentially and is not auto-routable.

## Workflow Patterns

Before dispatching any compound pattern, Read `reference/orchestrator-patterns.md`
at the matching `## Pattern N` heading to obtain the canonical agent briefing
prose. The inline safety constraints in each pattern block below are the
non-negotiable floor; the reference file carries the richer briefing context
(CRM-data injection cues, voice references, output paths, synthesis-phase
wording, degradation behaviour) that improves dispatch quality. Dispatching
without consulting the reference is permitted but produces a degraded
briefing — never paraphrase the safety constraints, always Read them when
in doubt.

### Pattern 1 — Deep Meeting Prep

**Triggers:** see `.claude/rules/skill-router.md` § Compound Workflow Triggers.

**Dispatches:** `/osint`, `/voss` tactical prep, CRM history reader, and counterpart comms scout in parallel as 4 background agents.

**Models per agent:**

- `/osint` scout — Opus (per CEO decision: /osint stays Opus)
- `/voss` prep — Opus (voice-grade)
- CRM history reader — Haiku
- Counterpart comms scout (Exchange + Telegram, last 30 days) — Haiku

**Safety floor (each agent):**

- Do NOT write to CRM files.
- Do NOT modify any workspace state.
- Comms scout is read-only — no message sending, no marking as read.

**Approval:** no hard gate before write phase — brief is presented first, then a single CRM log entry is written sequentially.

**Write phase:** sequential, after brief is presented to CEO. CRM log entry only.

**Agents dispatched:** 4. Global concurrency cap of 5 still applies per Principle 5.

**Full agent prompts:** `reference/orchestrator-patterns.md#pattern-1`.

### Pattern 2 — Morning Comms

**Triggers:** see `.claude/rules/skill-router.md` § Compound Workflow Triggers.

**Dispatches:** `/email-intel` fetch, `/viraid` fetch, calendar scout, and Sentinel-queue scout in parallel as 4 background agents.

**Models per agent:**

- `/email-intel` fetch — Sonnet
- `/viraid` fetch — Sonnet
- Calendar scout (today + next 3 days from Exchange) — Haiku
- Sentinel-queue scout (unprocessed urgent items) — Haiku

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

**Full agent prompts:** `reference/orchestrator-patterns.md#pattern-2`.

### Pattern 3 — Post-Event Follow-ups

**Triggers:** see `.claude/rules/skill-router.md` § Compound Workflow Triggers.

**Dispatches:** one draft agent per contact in parallel (up to 5 concurrent), with optional per-post image-prompt agents.

**Models per agent:**

- Drafter agents (one per contact) — Sonnet
- Image-prompt agents (one per post, when imagery requested) — Haiku

**Safety floor (each agent):**

- DO NOT send the email.
- DO NOT write to CRM.

**Approval:** one hard gate before any sends or CRM writes.

**Write phase:** sequential per approved contact — send via scripts/send-email.py, then write CRM interaction log, then confirm. If >5 contacts, batch in groups of 5.

**Agents dispatched:** up to 5 (concurrency cap). Global concurrency cap of 5 applies per Principle 5.

**Full agent prompts:** `reference/orchestrator-patterns.md#pattern-3`.

### Pattern 4 — Weekly Content Production

**Triggers:** see `.claude/rules/skill-router.md` § Compound Workflow Triggers.

**Dispatches:** `/linkedin-series` planning phase first (sequential), then up to 6 agents in parallel — 3 post drafters + 3 image-prompt generators.

**Models per agent:**

- Planning phase (`/linkedin-series`) — Opus (content strategy is voice-grade)
- Post drafters (one per post) — Sonnet
- Image-prompt generators (one per post) — Haiku

**Safety floor (each agent):**

- Agents save draft files to `outputs/content/linkedin/` only.
- DO NOT publish or post.
- DO NOT send anything externally.

**Approval:** two hard gates — Gate 1 approves the 3-post plan before drafting; Gate 2 approves individual posts before any publish or image generation.

**Write phase:** draft files written during parallel phase. Publishing and image generation only after Gate 2 approval.

**Agents dispatched:** up to 6 (post-Gate-1 parallel phase). Global concurrency cap of 5 per Principle 5 — if all 6 needed, batch posts and image prompts in two rounds.

**Full agent prompts:** `reference/orchestrator-patterns.md#pattern-4`.

### Pattern 5 — Full Deal Intelligence

**Triggers:** see `.claude/rules/skill-router.md` § Compound Workflow Triggers.

**Dispatches:** `/osint`, `/competitor-intel`, `/deep-think`, deal-context reader, and datastore price/proof validator as 5 parallel research agents, then synthesis via `/deal-strategy`.

**Models per agent:**

- `/osint` — Opus (per CEO decision: /osint stays Opus)
- `/competitor-intel` — Sonnet (per Phase 1.1)
- `/deep-think` — Opus
- Deal-context reader (CRM contact files + pipeline.md entry) — Haiku
- Datastore price/proof validator (cross-references claims against `datastore/`) — Sonnet

**Safety floor (each agent):**

- Do NOT modify any workspace state.
- Do NOT write to CRM files.
- Research agents return output inline or to `outputs/intel/` and `outputs/negotiations/` only.

**Approval:** no approval gate — this pattern produces a research package only; no CRM writes or external actions occur.

**Write phase:** none. Deal package saved to `outputs/intel/` and/or `outputs/negotiations/` after synthesis.

**Agents dispatched:** 5. Global concurrency cap of 5 applies per Principle 5 — exact ceiling, no wave-batching needed.

**Full agent prompts:** `reference/orchestrator-patterns.md#pattern-5`.

### Pattern 6 — Session Boot Parallel

**Triggers:** explicit `/prime` invocation only. No natural-language triggers — `/prime` is a slash-command-only skill per the skill-router rules table.

Pattern 6 does **not** dispatch subagents. Unlike Patterns 1–5 and 7, `/prime`'s health block runs **in-process**: `scripts/prime-health-parallel.py` executes its read-only checks concurrently in a `ThreadPoolExecutor(max_workers=8)` and renders each result as an output block. No subagent and no per-check model call is involved — each check shells out to an existing health script or reads a state file. The Principle-5 concurrency cap therefore does not apply to `/prime`. (This block previously described "5 read-only health-check agents"; that was doc drift from an abandoned dispatch model, corrected 2026-06-08.)

**Mechanism:** in-process `ThreadPoolExecutor(max_workers=8)`, one worker per check, aggregated by `run_all()`. A check that errors or times out is reported inline and never aborts the others.

**Checks (8, defined in the `CHECKS` registry):**

- `crm_health` — CRM health
- `knowledge_health` — knowledge-base health
- `memory_health` — auto-memory registry health
- `email_intel_status` — Email Intelligence last-run posture
- `active_threads_archive_scan` — active threads, stale flag
- `fireside_health` — Fireside daemon health
- `sync_exchange_health` — Sync-Exchange daemon health
- `odin_cadence` — Odin cadence nudge (ceo-only; renders nothing when empty)

**Safety floor (each check):**

- All checks are read-only.
- Do NOT write to any workspace file.
- Do NOT modify state.json or any registry.

**Approval:** none (read-only).

**Write phase:** none.

**Agents dispatched:** none — in-process threads, not subagents. Principle 5's cap is not engaged by `/prime`.

**Reference:** `scripts/prime-health-parallel.py` (`CHECKS` registry + `run_all()`).

### Pattern 7 — Push & Backup Parallel

**Triggers:** `/push-updates` invocation. See `.claude/rules/skill-router.md` § Compound Workflow Triggers.

**Dispatches:** sequential corporate publish phase first; then 2 parallel tail agents — ceo-main git push and CRM aggregate.

**Models per agent:**

- Corporate publish (sequential) — Sonnet
- ceo-main git push tail — Haiku
- CRM aggregate tail — Haiku

**Safety floor (each agent):**

- Each tail agent writes to ONE specific path; no overlap.
- ceo-main push tail writes only to the `origin/main` remote of ceo-main.
- CRM aggregate tail writes only to `../31c-crm-central/`.
- Tail agents do NOT touch the corporate repo, BUILD.json, or executive workspaces.

**Approval:** one hard gate before corporate publish.

**Write phase:** corporate publish first (serial, includes BUILD.json bump + corporate `git push`); then ceo-main push + CRM aggregate launch as a parallel wave.

**Agents dispatched:** 2 in the parallel tail wave. Global concurrency cap of 5 per Principle 5 — well under the cap.

**Full agent prompts:** `reference/orchestrator-patterns.md#pattern-7`.

## Orchestrator Principles

1. **Always announce before dispatching.** Never silently launch background agents. State what's being dispatched and why.

2. **Never skip approval gates.** Approval gates are marked HARD STOP. CEO sovereignty is non-negotiable. Only explicit "send", "go", "approve", "yes" count as approval. Silence or ambiguity means WAIT.

3. **Respect shared state.** CRM writes (crm/contacts/), pipeline updates (context/pipeline.md), state files (state.json), and multi-repo operations are ALWAYS sequential, ALWAYS post-approval. Two agents must never write the same CRM contact file.

4. **Graceful degradation.** If any parallel agent fails, complete the others and note the failure. Offer retry. Never cascade-fail the whole workflow.

5. **Concurrency limits (wave-mode dispatch).** Maximum 5 parallel background agents per pattern. Dispatch happens in waves: Claude submits up to 5 Agent tool calls in a single assistant message, all five run in parallel, and the orchestrator waits for all to complete before dispatching the next wave. This is wave mode by construction (single-message dispatch equals simultaneous start). Rolling-mode (a sixth agent starts the instant any of the first five finishes) is NOT supported in single-turn dispatch; it would require multi-turn dispatch with `run_in_background: true` plus follow-up SendMessage as agents complete. That is out of scope for current patterns and noted as a future enhancement. For now, when a pattern has more than 5 items (e.g., Pattern 3 with 8 contacts), batch as wave 1 (items 1-5) and wave 2 (items 6-8); wave 2 starts only after all of wave 1 completes.

6. **Agent briefing quality.** Each dispatched agent gets a complete, self-contained prompt: skill to invoke, all context (names, companies, dates), output format, and what NOT to do.

7. **Approval scope is narrow.** "Send the first one" means only the first one. Each action requires its own confirmation.

8. **No recursive orchestration.** An orchestrator pattern cannot trigger another orchestrator pattern. Sub-skills run as single skills.
