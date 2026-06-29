# Skill Router — Compound Workflow Patterns

Full compound-trigger table, depth-signal examples, and channel-scope disambiguation
for the skill router's compound workflow detection.

**Consumed by:** `.claude/rules/skill-router.md` — Compound Workflow Triggers section.
Last Updated: 2026-06-16
Last Verified: 2026-06-16

---

## Compound Workflow Triggers

When these patterns are detected, hand off to the orchestrator instead of invoking a single skill.

| Pattern | Trigger Phrases | Orchestrator Target |
|---|---|---|
| Meeting depth | "prepare for meeting" + ANY of: named person AND company, "thorough"/"full prep"/"deep"/"important", explicit request for OSINT/Voss/research, company is in pipeline or CRM. WITHOUT these signals, invoke `/meeting-prep` alone. | Pattern 1: Deep Meeting Prep |
| Morning comms | "process my comms", "check everything", "morning", "what did I miss", "what's new", "check what's new", "what came in" (across channels), "catch me up", "inbox + telegram", "check comms", "anything new", "what happened". NOT a single-channel email/inbox request - those route to `/email-intel`; see the channel-scope note below. | Pattern 2: Morning Comms |
| Post-event | "follow up with everyone from [event]", "event follow-ups", "send all follow-ups" | Pattern 3: Post-Event Follow-ups |
| Weekly content | "content for the week", "3 posts this week", "weekly LinkedIn", "plan and draft posts" | Pattern 4: Weekly Content |
| Deal depth | "how do we win [deal]", "full deal prep", "complete deal analysis", "win strategy for [prospect]" | Pattern 5: Deal Intelligence |
| Session boot | `/prime` invocation only (slash-command-only, never auto-routed from natural language) | Pattern 6: Session Boot Parallel |
| Push & backup | `/push-updates` invocation; also "update all executives", "push updates", "sync to everyone", "publish to executives" when paired with the CEO's full push intent | Pattern 7: Push & Backup Parallel |

## Depth signal examples (Meeting)

- HAS depth: "Prepare thoroughly for meeting with Sara Okonkwo from Nimbus" (named person + company)
- HAS depth: "Full prep for the ExampleTelco meeting" (company in pipeline + "full prep")
- HAS depth: "I have an important meeting with [name], research them" (explicit research request)
- NO depth: "Prepare for the meeting tomorrow" (no person/company, no depth keyword)
- NO depth: "Meeting prep for the internal sync" (internal, not external counterpart)

## Channel-scope disambiguation (Morning Comms vs `/email-intel`)

- Morning Comms (Pattern 2) fires only on channel-agnostic or explicitly multi-channel intent: "comms", "everything", "what's new", "what did I miss", "what came in" across channels, or "inbox + telegram" together.
- A request that names email or the inbox as the single channel routes to `/email-intel` alone: "process my inbox", "triage the emails that came in today", "run email intelligence on my inbox", "summarize my unread email", "email digest". The bare word "inbox" is an `/email-intel` trigger; only "inbox + telegram" (or "+ comms") escalates to the compound pattern.
