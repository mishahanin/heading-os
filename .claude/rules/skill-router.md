<!-- version: 1.1.0 | last-updated: 2026-06-04 -->
---
paths: []
always_active: true
---

# Skill Router

Last Verified: 2026-09-04

Match a natural-language message to a skill. Always active. Rationale, worked
examples and what left this file: `reference/skill-router-notes.md`.

## Routing protocol

A typed `/slash-command` bypasses the router. Otherwise, in this EXACT order:

1. **Corporate-docs guardrail, superseding.** External letter →
   `/corporate-letter`; commercial proposal → `/proposal`; MOU/LOI/term sheet →
   `/partnership-doc`; resolution/formal notice → `/official-doc`; onepager →
   `/xpager`. Applies even when no template is named, and beats individual
   matching. Protocol: `.claude/rules/corporate-docs.md`.
2. **Compound triggers, before any single skill.** On a match, hand off to the
   orchestrator; do NOT fall through to individual matching.
3. **Individual skills**, against the registry below.

One clear match → announce and invoke ("Using /osint for this"); 2-3 candidates →
numbered menu with one-line descriptions, then wait; none → ordinary
conversation. Never force a skill: a false positive costs more than a missed
match. "Just [do the thing]" with no skill named still fires the router.
Prioritise action verbs over nouns, and let context decide — "prepare for the
board meeting" is `/meeting-prep`, not `/investor-update`.

**VPN pre-flight.** Any skill reaching a public site that blocks datacenter IPs -
`/yt-pulse`, `/playwright` on YouTube, `/osint`, `/x-pulse`, anything calling
`scripts/firecrawl.py` - runs the gate in `reference/vpn-preflight.md` before its
first network call and waits for an explicit answer. Proceeding past it
unanswered is a protocol violation. This line is resident because the obligation
has no path signal to trigger on; the gate's mechanics do not.

## Skill registry

Generated from each skill's `x-heading-routing` frontmatter by
`scripts/generate-skill-router.py`; never hand-edit this table or the category
files. Every skill has a row, but trigger vocabulary is spent only on the ones a
message can reach. A row whose Triggers cell reads `manual` fires on the typed
command and never from a message, so there is nothing to match it against; the
harness enforces that with `disable-model-invocation`, and the reason, the flags
and the argument grammar are in the Detail file.

**Before selecting a skill, read that category's Detail file** for the exclusions
and compound patterns that confirm or refute the match. It carries the full row
for every skill, including each explicit-only skill's flags, argument grammar and
reason.

<!-- BEGIN GENERATED REGISTRY (generate-skill-router.py; do not edit) -->
### Intel

Detail: `reference/skill-router/intel.md`

| Skill | Triggers |
|---|---|
| `/ceo-intel` | world intel, geopolitical brief, what's happening globally, global threats, CEO intelligence brief |
| `/competitor-intel` | competitor analysis, competing vendor, how does [company] compare to [competitor], competitive advantage vs [named competitor], competitive landscape for [sector] |
| `/deep-research-advance` | advanced deep research, deep research with verification on [topic] |
| `/docparse` | parse this document, extract from this PDF, document analysis with citations, visual citation report, show me where it says, parse with bounding boxes |
| `/intel-briefing-newsletter` | newsletter, intel briefing, publish intelligence brief, external intel brief |
| `/market-brief` | market intel, market for [sector], regional analysis, sector overview, TAM for [sector], market size for [sector] |
| `/notebooklm` | audio overview, podcast from sources, create a notebook, notebook research, add sources to notebook |
| `/osint` | investigate, research, dig into, dossier, background on, due diligence on, who is [named person], intelligence on [named company] |
| `/osint-advanced` | manual |
| `/x-pulse` | twitter pulse, what's on X, scan X for, X account monitor, what are [accounts] saying |
| `/yt-pulse` | youtube pulse, youtube trends, what's trending on YouTube, scan YouTube for |

### Communication

Detail: `reference/skill-router/communication.md`

| Skill | Triggers |
|---|---|
| `/ceo-to-ceo` | CEO letter, write to [CEO name], peer correspondence, executive letter |
| `/corporate-letter` | write a letter to, external letter, formal letter, letter of introduction, letter of interest, letter of thanks, letter to [recipient] |
| `/email-draft` | draft email to, write email to, email [person] about |
| `/email-intel` | process emails, process my inbox, email digest, check my email, triage my email, inbox |
| `/email-respond` | respond to this email, reply to this, draft reply |
| `/follow-up` | follow up with, send follow-up, follow-up email after |
| `/telegram` | send telegram to, read telegram, check telegram, what's new on telegram |
| `/translate` | [Russian text needing English], translate this to Russian/English |
| `/tribe-message` | message to the tribe, write to the tribe |
| `/tribe-monday` | monday message, weekly tribe message, monday tribe |

### Content

Detail: `reference/skill-router/content.md`

| Skill | Triggers |
|---|---|
| `/flux-image` | generate image, create image, make a picture, flux |
| `/image-prompt` | visualize this, generate image prompt |
| `/keynote-deck` | keynote, event presentation, conference slides, speaking deck |
| `/linkedin-archive` | i published this on linkedin, linkedin post is live, live on linkedin, опубликовал на linkedin, выложил на linkedin, запостил на linkedin |
| `/linkedin-post` | draft a post about, write a post |
| `/linkedin-series` | content series, plan posts for the week, 3 posts |

### CRM

Detail: `reference/skill-router/crm.md`

| Skill | Triggers |
|---|---|
| `/crm` | crm add, crm log, crm radar, crm find, crm update, check CRM, contact health |
| `/google-contacts` | look up contact number, add to google contacts |
| `/viraid` | check viraid, process viraid, viraid sweep |

### Design

Detail: `reference/skill-router/design.md`

| Skill | Triggers |
|---|---|
| `/design` | design social, design infographic, design mockup, design illustration, design logo |
| `/marp` | render as slides, turn this into slides, slides from this doc, render this as a deck, internal deck about, runbook deck, quick slides, md to slides |
| `/pencil-export` | export pencil deck, export the .pen deck, pencil deck to pdf, convert .pen to pdf, render pencil deck, editable version of a pencil deck, same-look editable pptx from a .pen, shareable flat pptx of a .pen |
| `/pptx-generator` | create slides, generate presentation, linkedin carousel, edit pptx |

### Strategy

Detail: `reference/skill-router/strategy.md`

| Skill | Triggers |
|---|---|
| `/council` | second opinion, consult the council, what would Gemini say, what would Grok say, what would Kimi say, stress-test with Gemini, stress-test with Grok, stress-test with Kimi, gemini council, kimi council, council vote, second opinion on |
| `/data-room` | due diligence, DD response, investor materials |
| `/deal-strategy` | how do we win, competitive positioning for [prospect], pricing strategy |
| `/deep-think` | think through this, break this down, reason through, what are we missing, analyze carefully |
| `/investor-pitch` | pitch deck, fundraising deck |
| `/investor-update` | board update, quarterly update |
| `/meeting-prep` | meeting prep for [named counterpart], prepare for meeting with [named person or company], briefing for [named person + company] |
| `/odin` | what would Odin say, ask Odin, Odin learn, Odin teach, Odin log, log this episode, Odin remember that happened, Odin collect, scan threads for episodes, harvest episodes, find episodes I forgot to log, Odin what do you think, Odin study this, Odin remember, what does Odin know, compile the brain, knowledge check, Odin compile, skill-proposal, propose a skill step from this principle, turn this principle into a checklist step |
| `/official-doc` | board resolution, formal notice, letter of position, certificate of authority, official document, official letter, corporate resolution |
| `/partnership-doc` | MOU, LOI, memorandum of understanding, letter of intent, term sheet, partnership agreement, partnership document |
| `/proposal` | write a proposal, partnership proposal, sales proposal, commercial proposal |
| `/recall` | what do we know about, where did we decide, search my memory for, have we touched [X] before, find what we said about, surface past notes on [X] |
| `/rfp-response` | tender response, bid response, government tender |
| `/state-check` | how are we doing, operational state, function health |
| `/voss` | negotiation prep, tactical empathy, accusation audit, difficult conversation, negotiation playbook |
| `/xpager` | onepager, one-pager, 1-pager, product one-pager, capability sheet |

### Operations

Detail: `reference/skill-router/operations.md`

| Skill | Triggers |
|---|---|
| `/align [N]` | manual |
| `/ast-grep` | structural code search, AST pattern, find code by structure |
| `/backup` | manual |
| `/brain-audit` | manual |
| `/bridge-health` | manual |
| `/burst [N]` | manual |
| `/calibrate [light]` | manual |
| `/canopus [note \| check \| probe]` | manual |
| `/census` | how many X have Y, count across the workspace, which pipeline rows have no card, which threads have not moved in N days, aggregate over the whole corpus, intersection of people and pipeline |
| `/checkpoint [note]` | manual |
| `/cold-sweep` | drain cold contacts, sweep overdue contacts, drain the red debt |
| `/context7` | look up docs for [library], library documentation |
| `/create-plan` | plan for [change], design the approach |
| `/dashboard` | morning dashboard, daily brief, bridge view |
| `/devil [N]` | manual |
| `/dream` | manual |
| `/editorial-review [file:<path>]` | editorial pass, structural review, review the structure of this, tighten this document, restructure this draft |
| `/evaluate` | grade, review quality, check this artifact |
| `/event-debrief` | post-event recap, debrief [event] |
| `/implement` | execute the plan, build it |
| `/interview-prep` | interview questions, hiring framework |
| `/memory-hygiene` | check memory health, memory rot, scan memory for defects |
| `/modem-tune` | manual |
| `/mullvad` | fastest mullvad server, switch mullvad server, check mullvad speed, mullvad connect |
| `/next` | what next, what should I do now, logical next step, where were we, recommend next |
| `/playwright` | screenshot this site, scrape this page, browse to, headless browser |
| `/prime` | manual |
| `/publish-corporate` | manual |
| `/push-updates` | manual |
| `/queue` | action queue, show my drafts, what's waiting to send, approve/send the first one, retry that failed send, dismiss a queued card |
| `/queue-draft` | manual |
| `/radar` | ops radar, what's overdue, what am I forgetting to run, what manual actions are due, ack a radar item, crunch mode on/off |
| `/request-skill` | manual |
| `/scrutinize [target] [--relentless] [--no-refute] [--include-low-confidence] [--include-ambiguous] [--no-code-review]` | manual |
| `/sentinel` | manual |
| `/setup-browser-cookies` | import cookies |
| `/setup-wizard` | manual |
| `/skill-creator` | manual |
| `/sync` | manual |
| `/thread` | open a thread, log to thread, close thread, hold thread, reopen, thread list, thread find, what threads are active |
| `/validate` | fact-check, verify claims |
| `/weekly-review` | end of week review, friday review |
| `/workspace-deep-audit` | manual |
| `/zk` | add a note, knowledge base, distill, garden, what do we know about |
<!-- END GENERATED REGISTRY -->

## Compound workflows

Seven patterns (Meeting depth, Morning comms, Post-event, Weekly content, Deal
depth, Session boot, Push & backup) hand off to the orchestrator instead of a
single skill. **Read `reference/skill-router-compound-patterns.md` before
dispatching one** - it carries the trigger table, the depth signals and the
channel-scope disambiguation.

## Two fallbacks

**Local skill not in the registry.** If nothing matches but the intent clearly
maps to a slash command present in `.claude/skills/`, invoke it anyway, then
note: "This skill isn't in the router registry yet. It should be added to
`.claude/rules/skill-router.md`."

**Plugin-namespaced skills (`plugin:skill`) are NEVER auto-routed from natural
language.** Invoke one by its namespaced name through the Skill tool when its own
metadata clearly applies, or when the operator types `/plugin:skill`. On a
bare-name collision the local `.claude/skills/` entry wins. Why, plus the roster
of what is installed and what each costs: `reference/skill-router-notes.md`.
