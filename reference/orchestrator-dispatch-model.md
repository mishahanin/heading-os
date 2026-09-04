# Orchestrator — dispatch model and pattern map

Consumed by: `.claude/rules/skill-orchestrator.md`.

Last Updated: 2026-09-04

Read this before dispatching any compound workflow. The always-on rule carries
the guarantees that must hold whatever you dispatch; this file carries the model
that decides WHAT may be dispatched, and the map of the seven patterns.

## Roles are files; guarantees are the rule

Four recurring roles are agent definitions in `.claude/agents/`, not prose:
`crm-reader`, `comms-scout`, `datastore-validator`, `draft-writer`. Each carries
its own model and its own tool list, and the tool list is the enforcement:
`draft-writer` has no `Bash`, so it cannot reach `scripts/send-email.py` whatever
a dispatch prompt says. Prose is interpreted and its breach is found afterwards.

What cannot live in an agent file — the approval gates, the rule that CRM and
pipeline writes are sequential and post-approval, the rule that two agents never
write the same contact file — stays in the always-on rule. A step with no agent
file named still lists its model inline.

## Parallel safety levels

| Level | Meaning | Behavior |
|---|---|---|
| `true` | Read-only, or writes to isolated unique output paths | Safe to dispatch as background agent |
| `partial` | Has safe phases (research/fetch) and unsafe phases (CRM/pipeline/state writes) | Parallelize research phases only. Serialize write phases post-approval. |
| `false` | Writes to shared state throughout, multi-repo ops, inherently sequential | Never parallelize. Run solo. |

## Conflict detection

Before dispatching:

1. Read SKILL.md frontmatter for each skill being dispatched. Look at
   `x-heading-orchestration.parallel_safe` and `x-heading-orchestration.shared_state`.
2. `parallel_safe: false` — run that skill solo.
3. `parallel_safe: partial` — only dispatch its research phase.
4. Check `shared_state` arrays for path overlaps between agents (substring
   matching: `crm/contacts/` conflicts with `crm/contacts/john-smith.md`).
5. If the `x-heading-orchestration` block is missing or has no parallel metadata,
   treat as `parallel_safe: false` (safe default). Log: "Skill [name] has no
   parallel metadata. Running sequentially."

### Defaults for missing metadata

If a SKILL.md lacks the `x-heading-orchestration` block (or any field):
`parallel_safe` defaults to `false`, `shared_state` to `["UNKNOWN"]`, `triggers`
to `[]`. Nothing breaks — the skill runs sequentially and is not auto-routable.

## The seven patterns

Before dispatching any compound pattern you MUST Read
`reference/orchestrator-patterns.md` at that pattern's `## Pattern N` heading.
Required, not an optimisation: every per-pattern detail lives there and nowhere
else — the agent-file role mapping, the per-agent DO-NOT lists, the approval-gate
wording, the write-phase ordering, the concurrency notes, the degradation
behaviour, and the agent-briefing prose. Dispatching without that Read means
dispatching without the DO-NOT list. Never paraphrase a safety constraint from
memory, and never reconstruct one from the table below — the table routes you to
the section, it does not replace it.

| Pattern | Trigger source | Agents | Approval gate | Reference anchor |
|---|---|---|---|---|
| 1 — Deep Meeting Prep | router § Compound Workflow Triggers | 4 parallel: `/osint` (Opus), `/voss` (Opus), `crm-reader`, `comms-scout` | None before the write phase. Brief is presented, then one CRM log entry written sequentially. | `## Pattern 1` |
| 2 — Morning Comms | router § Compound Workflow Triggers | 4 parallel: `/email-intel` (Sonnet), `/viraid` (Sonnet), `comms-scout` ×2 (channels `calendar`, `sentinel-queue`) | 1 HARD STOP before ANY write. | `## Pattern 2` |
| 3 — Post-Event Follow-ups | router § Compound Workflow Triggers | one `draft-writer` per contact, max 5 concurrent; optional Haiku image-prompt agents | 1 HARD STOP before any send or CRM write. | `## Pattern 3` |
| 4 — Weekly Content Production | router § Compound Workflow Triggers | `/linkedin-series` planning first (Opus, sequential), then 6 — 3 `draft-writer` + 3 Haiku image-prompt — batched in two waves under the cap of 5 | 2 HARD STOPs: Gate 1 on the plan, Gate 2 on the posts before any publish or image generation. | `## Pattern 4` |
| 5 — Full Deal Intelligence | router § Compound Workflow Triggers | 5 parallel: `/osint` (Opus), `/competitor-intel` (Sonnet), `/deep-think` (Opus), `crm-reader`, `datastore-validator`; then `/deal-strategy` synthesis | None — research package only. No CRM writes, no external actions. | `## Pattern 5` |
| 6 — Session Boot | explicit `/prime` only; no natural-language trigger | NONE. Not a dispatch pattern: `scripts/prime-health-parallel.py` runs its `CHECKS` registry in-process (`ThreadPoolExecutor`), read-only. Principle 5's cap does not apply. | None — read-only, no write phase. | `## Pattern 6` |
| 7 — Push & Backup | `/push-updates` invocation | corporate publish (Sonnet, sequential) first; then 2 Haiku tails in parallel — engine + data push, CRM aggregate | 1 HARD STOP before corporate publish. | `## Pattern 7` |

## The principles in full

1. **Always announce before dispatching.** Never silently launch background
   agents. State what's being dispatched and why.
2. **Never skip approval gates.** Approval gates are marked HARD STOP. CEO
   sovereignty is non-negotiable. Only explicit "send", "go", "approve", "yes"
   count as approval. Silence or ambiguity means WAIT.
3. **Respect shared state.** CRM writes (`crm/contacts/`), pipeline updates
   (`context/pipeline.md`), state files (`state.json`), and multi-repo operations
   are ALWAYS sequential, ALWAYS post-approval. Two agents must never write the
   same CRM contact file.
4. **Graceful degradation.** If any parallel agent fails, complete the others and
   note the failure. Offer retry. Never cascade-fail the whole workflow.
5. **Concurrency limits (wave-mode dispatch).** Maximum 5 parallel background
   agents per pattern. Dispatch happens in waves: Claude submits up to 5 Agent
   tool calls in a single assistant message, all five run in parallel, and the
   orchestrator waits for all to complete before dispatching the next wave. This
   is wave mode by construction (single-message dispatch equals simultaneous
   start). Rolling-mode (a sixth agent starts the instant any of the first five
   finishes) is NOT supported in single-turn dispatch; it would require
   multi-turn dispatch with `run_in_background: true` plus follow-up SendMessage
   as agents complete. That is out of scope for current patterns and noted as a
   future enhancement. For now, when a pattern has more than 5 items (e.g.
   Pattern 3 with 8 contacts), batch as wave 1 (items 1-5) and wave 2 (items
   6-8); wave 2 starts only after all of wave 1 completes.
6. **Agent briefing quality.** Each dispatched agent gets a complete,
   self-contained prompt: skill to invoke, all context (names, companies, dates),
   output format, and what NOT to do.
7. **Approval scope is narrow.** "Send the first one" means only the first one.
   Each action requires its own confirmation.
8. **No recursive orchestration.** An orchestrator pattern cannot trigger another
   orchestrator pattern. Sub-skills run as single skills.
