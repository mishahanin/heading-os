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

### Intel

| Skill | Triggers | Exclusions | Compound |
|---|---|---|---|
| `/osint` | investigate, research, dig into, dossier, background on, due diligence on, who is [named person], intelligence on [named company] | "validate" -> /validate; "brief" without target -> /market-brief; "competitor" -> /competitor-intel; "market for [sector]" -> /market-brief; "world intel"/"global" -> /ceo-intel; competitor earnings/quarterly -> /competitor-intel | Yes: Meeting Prep, Deal Intel |
| `/osint-advanced` | NEVER auto-trigger. Explicit `/osint-advanced` only. | All natural language | No |
| `/competitor-intel` | competitor analysis, competing vendor, how does [company] compare to [competitor], competitive advantage vs [named competitor], competitive landscape for [sector] | Target is a person -> /osint; generic "vs" without explicit 2nd party -> ask for disambiguation; market sizing -> /market-brief | Yes: Deal Intel |
| `/market-brief` | market intel, market for [sector], regional analysis, sector overview, TAM for [sector], market size for [sector] | Specific named company -> /competitor-intel or /osint; global/geopolitical -> /ceo-intel | No |
| `/ceo-intel` | world intel, geopolitical brief, what's happening globally, global threats, CEO intelligence brief | Specific company/person -> /osint | No |
| `/intel-briefing-newsletter` | newsletter, intel briefing, publish intelligence brief, external intel brief | Internal-only brief -> /ceo-intel | No |
| `/yt-pulse` | youtube pulse, youtube trends, what's trending on YouTube, scan YouTube for | Non-YouTube research -> /osint | No |
| `/x-pulse` | x-pulse, twitter pulse, what's on X, scan X for, X account monitor, what are [accounts] saying | Topic-based YouTube -> /yt-pulse; brand mention monitoring (future) -> separate skill | No |
| `/notebooklm` | notebooklm, audio overview, podcast from sources, create a notebook, notebook research, add sources to notebook | "research [company]" -> /osint; "add a note" -> /zk; "podcast" alone -> /yt-pulse; "presentation" -> /pptx-generator | No |
| `/docparse` | parse this document, extract from this PDF, docparse, document analysis with citations, visual citation report, show me where it says, parse with bounding boxes | Plain text extraction -> datastore-extract.py; email analysis -> /email-intel; web scraping -> /playwright | No |
| `/deep-research-advance` | deep-research-advance, advanced deep research, deep research with verification on [topic] | Private/internal topic -> /recall or /odin; quick lookup -> /osint or WebSearch; Odin-brain recall -> /odin | No |

### Communication

| Skill | Triggers | Exclusions | Compound |
|---|---|---|---|
| `/email-draft` | draft email to, write email to, email [person] about | Responding to existing email -> /email-respond | No |
| `/email-respond` | respond to this email, reply to this, draft reply | New outbound email -> /email-draft | No |
| `/email-intel` | process emails, process my inbox, email digest, check my email, triage my email, inbox | Single email draft/reply -> /email-draft or /email-respond; channel-agnostic "check everything"/"what came in"/"process my comms" (email + telegram together) -> Morning Comms (Pattern 2) | Yes: Morning Comms |
| `/follow-up` | follow up with, send follow-up, follow-up email after | Mass follow-ups from event -> Orchestrator Pattern 3 | Yes: Post-Event |
| `/ceo-to-ceo` | CEO letter, write to [CEO name], peer correspondence, executive letter | Non-CEO recipient -> /email-draft; formal external letter -> /corporate-letter | No |
| `/corporate-letter` | write a letter to, external letter, formal letter, letter of introduction, letter of interest, letter of thanks, letter to [recipient] | Peer CEO correspondence -> /ceo-to-ceo; email body (not letter) -> /email-draft; proposal with pricing -> /proposal; MOU/LOI -> /partnership-doc | No |
| `/telegram` | telegram, send telegram to, read telegram, check telegram, what's new on telegram | Viraid channel -> /viraid | No |
| `/tribe-message` | tribe message, message to the tribe, write to the tribe | Monday-specific -> /tribe-monday | No |
| `/tribe-monday` | monday message, weekly tribe message, monday tribe | Not Monday-specific -> /tribe-message | No |
| `/translate` | translate, [Russian text needing English], translate this to Russian/English | N/A | No |

### Content

| Skill | Triggers | Exclusions | Compound |
|---|---|---|---|
| `/linkedin-post` | linkedin post, draft a post about, write a post | Multi-post planning -> /linkedin-series | Yes: Weekly Content |
| `/linkedin-series` | linkedin series, content series, plan posts for the week, 3 posts | Single post -> /linkedin-post | Yes: Weekly Content (trigger) |
| `/linkedin-archive` | i published this on linkedin, linkedin post is live, live on linkedin, опубликовал на linkedin, выложил на linkedin, запостил на linkedin | Drafting -> /linkedin-post; analytics question -> just answer; profile/banner topic -> just answer | No |
| `/keynote-deck` | keynote, event presentation, conference slides, speaking deck | Investor-specific -> /investor-pitch | No |
| `/image-prompt` | image prompt, visualize this, generate image prompt | Actual image generation -> /flux-image | No |
| `/flux-image` | generate image, create image, make a picture, flux | Prompt generation only -> /image-prompt | No |

### CRM

| Skill | Triggers | Exclusions | Compound |
|---|---|---|---|
| `/crm` | crm add, crm log, crm radar, crm find, crm update, check CRM, contact health | N/A | No |
| `/viraid` | viraid, check viraid, process viraid, viraid sweep | General telegram -> /telegram | Yes: Morning Comms |
| `/google-contacts` | google contacts, look up contact number, add to google contacts | CRM operations -> /crm | No |

### Design

| Skill | Triggers | Exclusions | Compound |
|---|---|---|---|
| `/design` | design social, design infographic, design mockup, design illustration, design logo | Presentations -> /pptx-generator; image from prompt -> /flux-image | No |
| `/pptx-generator` | create slides, generate presentation, linkedin carousel, edit pptx | Non-slide design -> /design | No |
| `/marp` | marp, render as slides, turn this into slides, slides from this doc, render this as a deck, internal deck about, runbook deck, quick slides, md to slides | Brand-heavy client deck -> /pptx-generator; Carousel -> /pptx-generator | No |

### Strategy

| Skill | Triggers | Exclusions | Compound |
|---|---|---|---|
| `/deep-think` | think through this, break this down, reason through, what are we missing, analyze carefully | Simple question -> just answer it | Yes: Deal Intel |
| `/council` | second opinion, consult the council, what would Gemini say, what would Grok say, what would Kimi say, stress-test with Gemini, stress-test with Grok, stress-test with Kimi, gemini council, kimi council, council vote, second opinion on | Reasoning alone -> /deep-think; Claude + curated knowledge brain -> /odin | No |
| `/deal-strategy` | deal strategy, how do we win, competitive positioning for [prospect], pricing strategy | General market intel -> /market-brief | Yes: Deal Intel (synthesis) |
| `/investor-pitch` | investor pitch, pitch deck, fundraising deck | Existing investor update -> /investor-update | No |
| `/investor-update` | investor update, board update, quarterly update | New investor pitch -> /investor-pitch | No |
| `/proposal` | write a proposal, partnership proposal, sales proposal, commercial proposal | RFP/tender -> /rfp-response; MOU/LOI/term sheet -> /partnership-doc; formal letter without pricing -> /corporate-letter | No |
| `/partnership-doc` | MOU, LOI, memorandum of understanding, letter of intent, term sheet, partnership agreement, partnership document | Commercial proposal with pricing -> /proposal; RFP -> /rfp-response; letter of introduction -> /corporate-letter | No |
| `/official-doc` | board resolution, formal notice, letter of position, certificate of authority, official document, official letter, corporate resolution | External partner letter -> /corporate-letter; commercial proposal -> /proposal | No |
| `/xpager` | xPager, x-pager, onepager, one-pager, 1-pager, product one-pager, capability sheet | Multi-page client deck -> /pptx-generator; simple render -> /marp | No |
| `/rfp-response` | RFP response, tender response, bid response, government tender | Informal proposal -> /proposal | No |
| `/data-room` | data room, due diligence, DD response, investor materials | Pitch deck -> /investor-pitch | No |
| `/voss` | negotiation prep, tactical empathy, accusation audit, difficult conversation, negotiation playbook | N/A | Yes: Meeting Prep, Deal Intel |
| `/state-check` | state check, how are we doing, operational state, function health | Dashboard -> /dashboard | No |
| `/meeting-prep` | meeting prep for [named counterpart], prepare for meeting with [named person or company], briefing for [named person + company] | Depth signals present -> Orchestrator Pattern 1; internal sync without external counterpart -> just answer; generic "briefing" without named target -> /market-brief or /dashboard | Yes: Meeting Depth |
| `/odin` | Odin, what would Odin say, ask Odin, Odin learn, Odin teach, Odin log, log this episode, Odin remember that happened, Odin collect, scan threads for episodes, harvest episodes, find episodes I forgot to log, Odin what do you think, Odin study this, Odin remember, what does Odin know, compile the brain, knowledge check, Odin compile, skill-proposal, propose a skill step from this principle, turn this principle into a checklist step | "think through" without Odin address -> /deep-think; "add a note" -> /zk; "research [company]" -> /osint | No |
| `/recall` | recall, what do we know about, where did we decide, search my memory for, have we touched [X] before, find what we said about, surface past notes on [X] | Odin-brain-only advice / episode dedup -> /odin recall (brain-scoped); external/world intel on a company or person -> /osint; capture a NEW note -> /zk; exact-string file search -> Grep. CEO-only, not synced to execs. | No |

### Operations

| Skill | Triggers | Exclusions | Compound |
|---|---|---|---|
| `/prime` | NEVER auto-trigger. Explicit `/prime` or "prime" only. | All natural language | No |
| `/dashboard` | dashboard, morning dashboard, daily brief, bridge view | Full prime -> /prime | No |
| `/next` | what next, what should I do now, logical next step, where were we, recommend next | Full context load -> /prime; function health -> /state-check; morning briefing -> /dashboard; weekly review -> /weekly-review. Read-only: names the next command, never runs it. | No |
| `/radar` | radar, ops radar, what's overdue, what am I forgetting to run, what manual actions are due, ack a radar item, crunch mode on/off | Morning brief -> /dashboard or /prime; single next action -> /next; function-by-function health -> /state-check. Detector that FEEDS those; never executes a manual action. CEO-only data, fleet-safe code. | No |
| `/queue` | queue, action queue, show my drafts, what's waiting to send, approve/send the first one, retry that failed send, dismiss a queued card | Workspace-wide overdue -> /radar; draft cold nudges -> /cold-sweep; inbox triage -> /email-intel; new outbound draft -> /email-draft. Terminal-native approve/send surface (synchronous, daemon-free); the send-gate holds. | No |
| `/weekly-review` | weekly review, end of week review, friday review | Single function -> /state-check | No |
| `/dream` | dream, consolidate memories, memory cleanup, reflect | N/A | No |
| `/memory-hygiene` | memory hygiene, check memory health, memory rot, scan memory for defects | Consolidate/merge/delete memory -> /dream; recall a fact -> /recall; function health -> /state-check | No |
| `/backup` | backup, push to github, save workspace | Corporate publish -> /publish-corporate | No |
| `/sync` | sync, full corp sync, pull updates | Push to execs -> /push-updates | No |
| `/push-updates` | push updates, update all executives, sync to everyone | Personal backup -> /backup | No |
| `/publish-corporate` | publish corporate, publish to executives, push to corporate | Full push with CRM -> /push-updates | No |
| `/promote-corporate` | NEVER auto-trigger. Explicit `/promote-corporate [--force] [--dry-run]` only. CEO-only R16 Layer 2 gate: runs canary soak/freshness/smoke gates, then `--ff-only` merges corporate `staging` -> `main`. Never bumps BUILD.json. | All natural language (`disable-model-invocation: true`); routine publish -> /push-updates | No |
| `/rollback-corporate` | NEVER auto-trigger. Explicit `/rollback-corporate [--dry-run]` only. CEO-only R16 Layer 2: forward-revert corporate `main` to the previous BUILD (no force-push), execs pull the reverted state next sync. | All natural language (`disable-model-invocation: true`) | No |
| `/create-plan` | create plan, plan for [change], design the approach | Execute plan -> /implement | No |
| `/pre-impl` | pre-implementation gate, gate before implement, are we ready to implement, before we implement, stress-test plan before building, pre-impl check | Trivial one-liner fixes -> skip; typo corrections -> skip; config-only changes -> skip. For non-trivial work: run after /create-plan approval, before /implement. Full chain: /create-plan -> /pre-impl -> /implement -> /scrutinize | No |
| `/implement` | implement, execute the plan, build it | Planning -> /create-plan | No |
| `/evaluate` | evaluate, grade, review quality, check this artifact | Fact-check -> /validate | No |
| `/scrutinize [target] [--relentless] [--no-refute] [--include-low-confidence] [--include-ambiguous]` | scrutinize, principal review, stress-test this, audit what you just did, validate and improve, ultrathink review. Target may be `plan` / `execution` / `file:<path>` / `dir:<path>` / `workspace` / `trajectory:<run_id>` (audit a past `/implement` run). `--relentless`: auto-apply fixes and re-scrutinize until two consecutive zero-findings iterations OR two consecutive marginal-improvement iterations OR 10-iteration cap OR check-failure OR oscillation. Triggers: "relentless scrutinize", "scrutinize until clean", "loop until fixed", "keep fixing until none left" | Artifact grading only -> /evaluate; fact-check drafts -> /validate; reasoning on a decision -> /deep-think | No |
| `/workspace-deep-audit` | NEVER auto-trigger. Explicit `/workspace-deep-audit` or "deep audit"/"run a full audit"/"audit the entire workspace"/"do the same deep audit" only. Produces v1/v2-equivalent 8-section comprehensive workspace audit. Flags: `--mode={full\|quick\|focus}`, `--focus={skills\|rules\|deps\|security\|architecture}`, `--vs=<prev_audit>` | All general "audit" requests without explicit invocation -> /scrutinize or /state-check; single-skill review -> /evaluate; specific fix scrutiny -> /scrutinize | No |
| `/calibrate [light]` | calibrate, self-improve agent, end of session capture | Cross-session memory hygiene -> /dream; quality grade on a single artifact -> /evaluate | No |
| `/checkpoint [note]` | NEVER auto-trigger. Explicit `/checkpoint [optional note]` only. Saves manual session handoff to `outputs/operations/handoff-archive/` without running /compact. Surfaces from the two-tier checkpoint-offer hook at 25%/30% used context. | Auto-resume after /compact handled by checkpoint-save.py (PostCompact); reflective end-of-session -> /calibrate; cross-session memory consolidation -> /dream | No |
| `/align [N]` | NEVER auto-trigger. Explicit `/align [N]` only. | All natural language | No |
| `/devil [N]` | NEVER auto-trigger. Explicit `/devil [N]` or `/devil [N]: <claim>` only. | All natural language | No |
| `/burst [N]` | NEVER auto-trigger. Explicit `/burst [N]` or `/burst [N]: <seed>` only. | All natural language | No |
| `/validate` | validate, fact-check, verify claims | Quality grade -> /evaluate | No |
| `/editorial-review [file:<path>]` | editorial pass, structural review, review the structure of this, tighten this document, restructure this draft | Sentence-level prose / "make this human" -> humanization.md; typo or grammar fix -> sanitize-text + humanization-check; fact-check -> /validate; artifact grade -> /evaluate; atomic note -> /zk. Document-structure only; hands all prose work to humanization.md. | No |
| `/sentinel` | sentinel, start sentinel, stop sentinel, comms monitor | N/A | No |
| `/cold-sweep` | cold sweep, cold-sweep, drain cold contacts, sweep overdue contacts, drain the red debt | Single follow-up -> /follow-up; pipeline review -> /crm; sending -> human-approved executor (never this skill). CEO-only, not synced to executives. | No |
| `/brain-audit` | NEVER auto-trigger from natural language. Invoked by composing synthesis skills (`/meeting-prep`, `/odin consult`, `/deal-strategy`) via the Skill tool, or explicitly by CEO for ad-hoc audits. | All natural language | No |
| `/bridge-health` | NEVER auto-trigger. Explicit `/bridge-health [--stale N] [--gate] [--json]` only. Wraps `scripts/daemon-fleet-health.py` + `scripts/bridge-daemon.py --health` + the `/telemetry/summary` endpoint. Use when the sync-pill is amber/red, the dashboard feels stale, or before scaling Phase 1 -> Phase 2 (need `--gate`). CEO-only, not synced to executives. | All natural language | No |
| `/thread` | open a thread, log to thread, close thread, hold thread, reopen, thread list, thread find, what threads are active | Single email -> /email-draft; knowledge note -> /zk; CRM log -> /crm | No |
| `/mullvad` | /mullvad, fastest mullvad server, switch mullvad server, check mullvad speed, mullvad connect | Generic VPN questions -> just answer; Mullvad help page lookup -> WebFetch | No |
| `/modem-tune` | NEVER auto-trigger. Explicit `/modem-tune [status \| revert]` only. Changes the reported IMEI on the GL.iNet GL-XE300 travel router over SSH. CEO-only, never synced to executives. | All natural language (`disable-model-invocation: true`) | No |
| `/playwright` | screenshot this site, scrape this page, browse to, headless browser | N/A | No |
| `/ast-grep` | structural code search, AST pattern, find code by structure, ast-grep | Plain text search -> Grep; semantic question -> just answer | No |
| `/setup-browser-cookies` | setup browser cookies, import cookies | N/A | No |
| `/context7` | context7, look up docs for [library], library documentation | N/A | No |
| `/skill-creator` | create a skill, improve this skill, eval this skill | N/A | No |
| `/request-skill` | request skill, I need a new skill | Create directly -> /skill-creator (CEO only) | No |
| `/setup-wizard` | set up my workspace, configure my workspace, onboard me, setup wizard, finish setup | Refuses to run on CEO master workspace; intended for fresh exec workspaces and HEADING OS clones | No |
| `/event-debrief` | event debrief, post-event recap, debrief [event] | N/A | Yes: Post-Event (trigger) |
| `/interview-prep` | interview prep, interview questions, hiring framework | N/A | No (deliberate; no compound depth pattern yet -- CEO approval required to promote) |
| `/zk` | zk, add a note, knowledge base, distill, garden, what do we know about | Primary capture tool for the executive fleet. On the CEO workspace `/zk` is dormant -- durable CEO capture flows to `/odin log`, `/thread`, and auto-memory. | No |

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
