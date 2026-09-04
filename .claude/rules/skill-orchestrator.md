<!-- audit-skip-start -->
<!-- version: 4.0.0 | last-updated: 2026-09-04 -->
<!-- audit-skip-end -->
---
paths: []
always_active: true
---

# Parallel Orchestrator

Last Verified: 2026-09-04

Detects compound workflows and dispatches parallel agents for research phases
while serializing write phases. Always active, because the signal it fires on is
a user MESSAGE: no path and no tool call can trigger its load.

**Before dispatching anything, Read `reference/orchestrator-dispatch-model.md`**
(the parallel-safety model, the conflict check, the seven-pattern map, the eight
principles written out) **and then `reference/orchestrator-patterns.md` at that
pattern's `## Pattern N` heading** for its per-agent DO-NOT lists and
approval-gate wording. Never paraphrase a safety constraint from memory.

## Roles are files; guarantees are this rule

Four recurring roles are agent definitions in `.claude/agents/`, not prose here:
`crm-reader`, `comms-scout`, `datastore-validator`, `draft-writer`. Each carries
its own model and its own tool list, and the tool list is the enforcement:
`draft-writer` has no `Bash`, so it cannot reach `scripts/send-email.py` whatever
a dispatch prompt says. What no agent file can express stays below.

## Resident guarantees — true before you have read anything

1. **Always announce before dispatching.** Never silently launch background
   agents. State what is being dispatched and why.
2. **Never skip approval gates.** They are marked HARD STOP. Only an explicit
   "send", "go", "approve" or "yes" counts; silence or ambiguity means WAIT. CEO
   sovereignty is non-negotiable, and approval scope is narrow — "send the first
   one" approves only the first one.
3. **Respect shared state.** CRM writes (`crm/contacts/`), pipeline updates
   (`context/pipeline.md`), state files and multi-repo operations are ALWAYS
   sequential and ALWAYS post-approval.
   Two agents must never write the same CRM contact file.
4. **A skill with no `x-heading-orchestration` metadata is never parallelized.**
   Missing metadata means `parallel_safe: false`; `partial` means its research
   phase only. Conflict detection intersects `shared_state` lists by substring.
5. **Maximum 5 parallel agents per pattern**, dispatched in waves; wave 2 starts
   only after every agent in wave 1 has completed.
6. **Graceful degradation.** One agent failing completes the others and reports
   the failure. Never cascade-fail the whole workflow.
7. **Agent briefing quality.** Each dispatched agent gets a complete,
   self-contained prompt, including what NOT to do.
8. **No recursive orchestration.** A pattern never triggers another pattern.
