<!-- version: 1.1.0 | last-updated: 2026-06-04 -->
---
paths: []
always_active: true
---

# Skill Router

Last Verified: 2026-05-28

Automatically match natural-language user messages to the right skill. This rule is always active.

## Routing Protocol

When the user sends a natural-language message (not a `/slash-command`), evaluate it in this EXACT order:

### Corporate Documents Guardrail (always active)

Five document types ALWAYS route through their locked-template skills, even when the user does not name a template: external letters (`/corporate-letter`), commercial proposals (`/proposal`), partnership documents (`/partnership-doc`), official documents (`/official-doc`), and OnePagers (`/xpager`). Full trigger and classification protocol: `.claude/rules/corporate-docs.md`. The guardrail supersedes individual-skill matching when a request matches any of these five types.

### Step 1: Check Compound Workflow Triggers FIRST

Before matching any individual skill, check the Compound Workflow Triggers table below. If the message matches a compound pattern, hand off to the orchestrator immediately. Do NOT fall through to individual skill matching.

This step takes priority because compound patterns involve multiple skills and the user benefits from parallel execution. A message like "check what's new" should trigger Morning Comms (email + viraid in parallel), not just /email-intel alone.

### Step 2: Match Individual Skills

Only if no compound pattern matched, evaluate the message against the skill registry.

| Tier | Condition | Behavior |
|---|---|---|
| High | One clear skill match, no ambiguity | Announce and invoke: "Using /osint for this." |
| Medium | 2-3 plausible candidates | Present numbered menu with 1-line descriptions, wait for selection |
| None | No skill match | Proceed as normal conversation - do not force a skill |

### Rules

- ALWAYS check compound triggers before individual skills. Never short-circuit to a single skill when a compound pattern matches.
- Never force a skill where none fits. False positives are worse than missed matches.
- If the user types a `/slash-command` directly, bypass the router - they know what they want.
- If the user says "just [do the thing]" without naming a skill, the router still fires.
- For medium-confidence matches, present skills in order of relevance with 1-line descriptions.
- When matching, prioritize action verbs over nouns. "Investigate ExampleTelco" is stronger signal than "ExampleTelco".
- Context matters: "prepare for the board meeting" is `/meeting-prep`, not `/investor-update`, unless the user says "update the board".

## Skill Registry

This registry is delivered in two layers (progressive disclosure). The compact index below carries every skill's **triggers** for first-pass matching — enough to route the common case without reading a file. The full **exclusions** and **compound** patterns for each category live in `reference/skill-router/<category>.md`; before selecting a skill, read the relevant category file to check its exclusions and confirm the match. Both layers are generated from each skill's `x-heading-routing` frontmatter by `scripts/generate-skill-router.py` — never hand-edit either.

<!-- BEGIN GENERATED REGISTRY (generate-skill-router.py; do not edit) -->
### Intel

Full triggers, exclusions, and compound patterns: `reference/skill-router/intel.md`

| Skill | Triggers |
|---|---|
| `/ceo-intel` | world intel, geopolitical brief, what's happening globally, global threats, CEO intelligence brief |
| `/competitor-intel` | competitor analysis, competing vendor, how does [company] compare to [competitor], competitive advantage vs [named competitor], competitive landscape for [sector] |
| `/deep-research-advance` | deep-research-advance, advanced deep research, deep research with verification on [topic] |
| `/docparse` | parse this document, extract from this PDF, docparse, document analysis with citations, visual citation report, show me where it says, parse with bounding boxes |
| `/intel-briefing-newsletter` | newsletter, intel briefing, publish intelligence brief, external intel brief |
| `/market-brief` | market intel, market for [sector], regional analysis, sector overview, TAM for [sector], market size for [sector] |
| `/notebooklm` | notebooklm, audio overview, podcast from sources, create a notebook, notebook research, add sources to notebook |
| `/osint` | investigate, research, dig into, dossier, background on, due diligence on, who is [named person], intelligence on [named company] |
| `/osint-advanced` | NEVER auto-trigger. Explicit `/osint-advanced` only. |
| `/x-pulse` | x-pulse, twitter pulse, what's on X, scan X for, X account monitor, what are [accounts] saying |
| `/yt-pulse` | youtube pulse, youtube trends, what's trending on YouTube, scan YouTube for |

### Communication

Full triggers, exclusions, and compound patterns: `reference/skill-router/communication.md`

| Skill | Triggers |
|---|---|
| `/ceo-to-ceo` | CEO letter, write to [CEO name], peer correspondence, executive letter |
| `/corporate-letter` | write a letter to, external letter, formal letter, letter of introduction, letter of interest, letter of thanks, letter to [recipient] |
| `/email-draft` | draft email to, write email to, email [person] about |
| `/email-intel` | process emails, process my inbox, email digest, check my email, triage my email, inbox |
| `/email-respond` | respond to this email, reply to this, draft reply |
| `/follow-up` | follow up with, send follow-up, follow-up email after |
| `/telegram` | telegram, send telegram to, read telegram, check telegram, what's new on telegram |
| `/translate` | translate, [Russian text needing English], translate this to Russian/English |
| `/tribe-message` | tribe message, message to the tribe, write to the tribe |
| `/tribe-monday` | monday message, weekly tribe message, monday tribe |

### Content

Full triggers, exclusions, and compound patterns: `reference/skill-router/content.md`

| Skill | Triggers |
|---|---|
| `/flux-image` | generate image, create image, make a picture, flux |
| `/image-prompt` | image prompt, visualize this, generate image prompt |
| `/keynote-deck` | keynote, event presentation, conference slides, speaking deck |
| `/linkedin-archive` | i published this on linkedin, linkedin post is live, live on linkedin, опубликовал на linkedin, выложил на linkedin, запостил на linkedin |
| `/linkedin-post` | linkedin post, draft a post about, write a post |
| `/linkedin-series` | linkedin series, content series, plan posts for the week, 3 posts |

### CRM

Full triggers, exclusions, and compound patterns: `reference/skill-router/crm.md`

| Skill | Triggers |
|---|---|
| `/crm` | crm add, crm log, crm radar, crm find, crm update, check CRM, contact health |
| `/google-contacts` | google contacts, look up contact number, add to google contacts |
| `/viraid` | viraid, check viraid, process viraid, viraid sweep |

### Design

Full triggers, exclusions, and compound patterns: `reference/skill-router/design.md`

| Skill | Triggers |
|---|---|
| `/design` | design social, design infographic, design mockup, design illustration, design logo |
| `/marp` | marp, render as slides, turn this into slides, slides from this doc, render this as a deck, internal deck about, runbook deck, quick slides, md to slides |
| `/pencil-export` | export pencil deck, export the .pen deck, pencil deck to pdf, convert .pen to pdf, render pencil deck, editable version of a pencil deck, same-look editable pptx from a .pen, shareable flat pptx of a .pen |
| `/pptx-generator` | create slides, generate presentation, linkedin carousel, edit pptx |

### Strategy

Full triggers, exclusions, and compound patterns: `reference/skill-router/strategy.md`

| Skill | Triggers |
|---|---|
| `/council` | second opinion, consult the council, what would Gemini say, what would Grok say, what would Kimi say, stress-test with Gemini, stress-test with Grok, stress-test with Kimi, gemini council, kimi council, council vote, second opinion on |
| `/data-room` | data room, due diligence, DD response, investor materials |
| `/deal-strategy` | deal strategy, how do we win, competitive positioning for [prospect], pricing strategy |
| `/deep-think` | think through this, break this down, reason through, what are we missing, analyze carefully |
| `/investor-pitch` | investor pitch, pitch deck, fundraising deck |
| `/investor-update` | investor update, board update, quarterly update |
| `/meeting-prep` | meeting prep for [named counterpart], prepare for meeting with [named person or company], briefing for [named person + company] |
| `/odin` | Odin, what would Odin say, ask Odin, Odin learn, Odin teach, Odin log, log this episode, Odin remember that happened, Odin collect, scan threads for episodes, harvest episodes, find episodes I forgot to log, Odin what do you think, Odin study this, Odin remember, what does Odin know, compile the brain, knowledge check, Odin compile, skill-proposal, propose a skill step from this principle, turn this principle into a checklist step |
| `/official-doc` | board resolution, formal notice, letter of position, certificate of authority, official document, official letter, corporate resolution |
| `/partnership-doc` | MOU, LOI, memorandum of understanding, letter of intent, term sheet, partnership agreement, partnership document |
| `/proposal` | write a proposal, partnership proposal, sales proposal, commercial proposal |
| `/recall` | recall, what do we know about, where did we decide, search my memory for, have we touched [X] before, find what we said about, surface past notes on [X] |
| `/rfp-response` | RFP response, tender response, bid response, government tender |
| `/state-check` | state check, how are we doing, operational state, function health |
| `/voss` | negotiation prep, tactical empathy, accusation audit, difficult conversation, negotiation playbook |
| `/xpager` | xPager, x-pager, onepager, one-pager, 1-pager, product one-pager, capability sheet |

### Operations

Full triggers, exclusions, and compound patterns: `reference/skill-router/operations.md`

| Skill | Triggers |
|---|---|
| `/align [N]` | NEVER auto-trigger. Explicit `/align [N]` only. |
| `/ast-grep` | structural code search, AST pattern, find code by structure, ast-grep |
| `/backup` | NEVER auto-trigger. Explicit `/backup` only. |
| `/brain-audit` | NEVER auto-trigger from natural language. Invoked by composing synthesis skills (`/meeting-prep`, `/odin consult`, `/deal-strategy`) via the Skill tool, or explicitly by CEO for ad-hoc audits. |
| `/bridge-health` | NEVER auto-trigger. Explicit `/bridge-health [--stale N] [--gate] [--json]` only. Wraps `scripts/daemon-fleet-health.py` + `scripts/bridge-daemon.py --health` + the `/telemetry/summary` endpoint. Use when the sync-pill is amber/red, the dashboard feels stale, or before scaling Phase 1 -> Phase 2 (need `--gate`). CEO-only, not synced to executives. |
| `/burst [N]` | NEVER auto-trigger. Explicit `/burst [N]` or `/burst [N]: <seed>` only. |
| `/calibrate [light]` | NEVER auto-trigger. Explicit `/calibrate [light]` only. |
| `/checkpoint [note]` | NEVER auto-trigger. Explicit `/checkpoint [optional note]` only. Saves manual session handoff to `outputs/operations/handoff-archive/` without running /compact. Surfaces from the two-tier checkpoint-offer hook at 25%/30% used context. |
| `/cold-sweep` | cold sweep, cold-sweep, drain cold contacts, sweep overdue contacts, drain the red debt |
| `/context7` | context7, look up docs for [library], library documentation |
| `/create-plan` | create plan, plan for [change], design the approach |
| `/dashboard` | dashboard, morning dashboard, daily brief, bridge view |
| `/devil [N]` | NEVER auto-trigger. Explicit `/devil [N]` or `/devil [N]: <claim>` only. |
| `/dream` | NEVER auto-trigger. Explicit `/dream` only. |
| `/editorial-review [file:<path>]` | editorial pass, structural review, review the structure of this, tighten this document, restructure this draft |
| `/evaluate` | evaluate, grade, review quality, check this artifact |
| `/event-debrief` | event debrief, post-event recap, debrief [event] |
| `/implement` | implement, execute the plan, build it |
| `/interview-prep` | interview prep, interview questions, hiring framework |
| `/memory-hygiene` | memory hygiene, check memory health, memory rot, scan memory for defects |
| `/modem-tune` | NEVER auto-trigger. Explicit `/modem-tune [status \| revert]` only. Changes the reported IMEI on the GL.iNet GL-XE300 travel router over SSH. CEO-only, never synced to executives. |
| `/mullvad` | /mullvad, fastest mullvad server, switch mullvad server, check mullvad speed, mullvad connect |
| `/next` | what next, what should I do now, logical next step, where were we, recommend next |
| `/playwright` | screenshot this site, scrape this page, browse to, headless browser |
| `/pre-impl` | NEVER auto-trigger. Explicit `/pre-impl` only. |
| `/prime` | NEVER auto-trigger. Explicit `/prime` or "prime" only. |
| `/promote-corporate` | NEVER auto-trigger. Explicit `/promote-corporate [--force] [--dry-run]` only. CEO-only R16 Layer 2 gate: runs canary soak/freshness/smoke gates, then `--ff-only` merges corporate `staging` -> `main`. Never bumps BUILD.json. |
| `/publish-corporate` | NEVER auto-trigger. Explicit `/publish-corporate` only. |
| `/push-updates` | NEVER auto-trigger. Explicit `/push-updates` only. |
| `/queue` | queue, action queue, show my drafts, what's waiting to send, approve/send the first one, retry that failed send, dismiss a queued card |
| `/queue-draft` | NEVER auto-trigger. Explicit `/queue-draft` only. Deposits one GATED draft card into the Action Queue; never approves or sends. |
| `/radar` | radar, ops radar, what's overdue, what am I forgetting to run, what manual actions are due, ack a radar item, crunch mode on/off |
| `/request-skill` | NEVER auto-trigger. Explicit `/request-skill` only. |
| `/rollback-corporate` | NEVER auto-trigger. Explicit `/rollback-corporate [--dry-run]` only. CEO-only R16 Layer 2: forward-revert corporate `main` to the previous BUILD (no force-push), execs pull the reverted state next sync. |
| `/scrutinize [target] [--relentless] [--no-refute] [--include-low-confidence] [--include-ambiguous]` | NEVER auto-trigger. Explicit `/scrutinize [target] ...` only. |
| `/sentinel` | NEVER auto-trigger. Explicit `/sentinel` only. |
| `/setup-browser-cookies` | setup browser cookies, import cookies |
| `/setup-wizard` | NEVER auto-trigger. Explicit `/setup-wizard` only. |
| `/skill-creator` | NEVER auto-trigger. Explicit `/skill-creator` only. |
| `/sync` | NEVER auto-trigger. Explicit `/sync` only. |
| `/thread` | open a thread, log to thread, close thread, hold thread, reopen, thread list, thread find, what threads are active |
| `/validate` | validate, fact-check, verify claims |
| `/weekly-review` | weekly review, end of week review, friday review |
| `/workspace-deep-audit` | NEVER auto-trigger. Explicit `/workspace-deep-audit` or "deep audit"/"run a full audit"/"audit the entire workspace"/"do the same deep audit" only. Produces v1/v2-equivalent 8-section comprehensive workspace audit. Flags: `--mode={full\|quick\|focus}`, `--focus={skills\|rules\|deps\|security\|architecture}`, `--vs=<prev_audit>` |
| `/zk` | zk, add a note, knowledge base, distill, garden, what do we know about |
<!-- END GENERATED REGISTRY -->

## Compound Workflow Triggers

Full compound-trigger table, depth-signal examples, and channel-scope
disambiguation: `reference/skill-router-compound-patterns.md`.

Summary: 7 compound patterns (Meeting depth, Morning comms, Post-event,
Weekly content, Deal depth, Session boot, Push & backup) hand off to the
orchestrator instead of a single skill. Read the reference file before
dispatching any compound workflow.

## Trigger Regression Tests

The router is a markdown rule the model interprets, so a new skill's triggers can silently hijack another skill's queries. `scripts/skill-trigger-test.py` is an LLM-judge harness that regression-tests this: it feeds the router rules plus a target skill's description to a judge model and checks whether each query in `.claude/skills/{name}/triggers.json` routes as expected (`should_trigger`). Run `python scripts/skill-trigger-test.py --all` (or `--skill NAME`, or `--changed [--base REF]` to test only skills whose `SKILL.md`/`triggers.json` changed since the base, default `origin/main` - a `skill-router.md` change widens scope to all); it is **advisory** by default (non-deterministic judge) and gates only under `--strict --threshold`. `/push-updates` Phase 0 runs `--changed --strict --threshold 0.85` as a **soft gate** (surfaces routing regressions on changed skills; the CEO confirms to override; not a hard block yet, per audit #63-2). 24 routing-sensitive skills carry `triggers.json` today. When adding or re-scoping a skill, add or update its `triggers.json` and re-run the harness.

## Scheduled & Background Tasks

Durable scheduled tasks created via the `CronCreate` tool are persisted in `.claude/scheduled_tasks.json`. The file has no frontmatter and is managed by the Claude Code runtime.

To view active scheduled tasks: `cat .claude/scheduled_tasks.json | python -m json.tool` (or just read the file directly).

To cancel a task: use `CronDelete` with the task ID shown in the JSON. Editing the file by hand is not supported - the runtime will overwrite changes.

If the file grows large or contains orphaned tasks (e.g., after long periods between sessions), list them via `CronList` and prune with `CronDelete`. There is no automatic cleanup.

Scheduled tasks are machine-local - they do NOT sync to corporate or execs. Each machine maintains its own scheduled set.

## Fallback for Unregistered Skills

If no registry match is found but the user's intent clearly maps to a slash command present in `.claude/skills/`, invoke it anyway. After invocation, note: "This skill isn't in the router registry yet. It should be added to `.claude/rules/skill-router.md`."

This fallback applies only to local skills in `.claude/skills/`. See the next section for plugin-namespaced skills.

## Archived Skills Convention

`.claude/skills/archive/{date-slug}/SKILL.md` is the workspace convention for retired skills. The parent `archive/` directory has no SKILL.md of its own and is intentionally inert - Claude Code's skill discovery is single-level and does not auto-load nested skills. Archived skills do not appear in the registry above and are never invoked unless explicitly retrieved (`git mv` back into `.claude/skills/{name}/`). Do NOT create a stub SKILL.md inside `archive/` itself; that would shadow the convention and risk false routing.

## Plugin-Namespaced Skills (External, Never Auto-Routable)

Plugins shipped via the Claude Code plugin system expose skills under a `plugin:skill` namespace. Enablement lives in two tiers: workspace-level `.claude/settings.json` `enabledPlugins`, and user-level `~/.claude/settings.json` `enabledPlugins`. Currently enabled:

- `superpowers:*` v5.1.0 - 14 skills: brainstorming, writing-plans, executing-plans, subagent-driven-development, using-git-worktrees, test-driven-development, systematic-debugging, verification-before-completion, receiving-code-review, requesting-code-review, finishing-a-development-branch, writing-skills, using-superpowers, dispatching-parallel-agents - workspace-level. v5.1.0 (2026-04-30) removed the legacy `/brainstorm`, `/write-plan`, `/execute-plan` slash-command stubs and the `superpowers:code-reviewer` named agent; invoke each skill by its namespaced name (`superpowers:brainstorming`) via the Skill tool. The `using-superpowers` skill bootstraps the set at SessionStart via the plugin's own hook.
- `skill-creator:skill-creator` - workspace-level
- `claude-md-management:revise-claude-md`, `claude-md-management:claude-md-improver` - workspace-level
- `frontend-design:frontend-design` - workspace-level
- `code-review:code-review` - workspace-level. Code review pass on the active branch / pending changes. Invoke explicitly when a major project step is completed and needs review against the original plan and coding standards.
- `code-simplifier:code-simplifier` - workspace-level. Refines code for clarity, consistency, and maintainability while preserving functionality. Invoke explicitly after implementing a non-trivial change when you want a simplification pass.
- `andrej-karpathy-skills:karpathy-guidelines` - user-level
- `context7:context7` - user-level, mirrors local `/context7` skill (local wins on bare-name lookup)

**Routing rule:** These skills are **never auto-routable from natural language**. The router does not match them against any trigger. They require one of:

1. Explicit slash-command form typed by the user (e.g., `/superpowers:brainstorming`)
2. Explicit Skill tool invocation by Claude when the plugin's own metadata says it applies (e.g., `using-superpowers` fires at session start per its own description)
3. Direct invocation by another skill that references it

**Why:** Plugin content evolves independently of this workspace. Auto-routing based on local keyword guesses would produce false positives against skills whose actual purpose may drift. When a plugin skill clearly applies, Claude invokes it explicitly; otherwise, local registry wins.

**Local-skill naming collision:** If a local `.claude/skills/{name}` ever collides with a plugin skill name (e.g., workspace has `/skill-creator` and plugin exposes `skill-creator:skill-creator`), the local skill wins on bare-name lookup. Use the namespaced form to force the plugin variant.
