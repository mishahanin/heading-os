<!-- version: 1.1.0 | last-updated: 2026-05-15 -->

# Workspace Architecture

> Educational narrative: how the 31C CEO workspace is organized, how the agent army works, what CEO activities it supports, and how the pieces fit together. **This file is the narrative.** For the runtime catalog of every skill, rule, hook, script, and reference file, see [`workspace-overview.md`](workspace-overview.md) - the catalog is the single source of truth for counts and inventories.
>
> Last Updated: 2026-05-15
> Last Verified: 2026-05-15

---

## What This Workspace Is

Operational workspace for **Misha Hanin, Founder & CEO of 31 Concept (31C)** - building the ODUN.ONE sovereign deep packet intelligence platform. This is not a template. It is the real, living workspace that runs the company. Every file, every contact, every skill - built for one person, one company, one mission: making nations sovereign over their own data.

Built on [Claude Code](https://docs.anthropic.com/en/docs/claude-code). Claude operates as a strategic assistant across sessions - it knows who Misha is, what 31 Concept does, who the key players are, and what the current priorities look like. It picks up where the last session left off.

---

## How It Works

### Starting a session

`/prime` loads the full context stack: personal info, 31C organization, strategy, current metrics, key contacts, pipeline, datastore index. After loading, it surfaces what needs attention and suggests relevant skills for the session.

### Using a skill

Type `/` plus the skill name, or speak natural language and let the skill router in `.claude/rules/skill-router.md` pick the right one. Each skill's definition (in `.claude/skills/{name}/SKILL.md`) tells Claude exactly what context to read, what research to run, what format to produce, and where to save the output.

### Compound workflows

For multi-skill patterns (deep meeting prep, morning comms, post-event follow-ups, weekly content, deal intelligence), the parallel orchestrator in `.claude/rules/skill-orchestrator.md` dispatches research agents in parallel and serializes state-changing writes behind approval gates.

### Two-way updates

When Misha changes context files or the pipeline, the next session picks it up. When Claude produces outputs, skill metadata decides where they land (`outputs/` for personal work, corporate repo for shared docs).

---

## CEO Activities

This is what Misha works on. Each activity maps to specific skills and context files.

### Business Strategy
Market positioning, competitive analysis, category creation (DPI to DPI+), long-term strategic direction, operational state model application. Pull from `context/strategy.md`, `reference/billion-growth-playbook.md`, `reference/dpi-market-intelligence.md`.

### Sales
Pipeline development, deal strategy, pricing negotiations, proposal support, competitive displacement. Pull from `context/strategy.md` and `context/current-data.md`.

### Business Growth
Geographic expansion planning, new market entry, partner channel activation, target-valuation path. Pull from `reference/billion-growth-playbook.md` and `reference/geopolitical-landscape.md`.

### Investor Relations
Existing investor updates, board reporting, strategic alignment. Pull from `context/current-data.md` for metrics and `context/strategy.md` for framing.

### New Investor Onboarding
Pitch materials, data room preparation, due diligence, valuation narratives, investment thesis. Pull from `reference/billion-growth-playbook.md` and `context/business-info.md`.

### Tribe Communications
Monday messages to the 31C Tribe. Crunch Mode updates. Personnel announcements. Policy changes. Culture reinforcement. Operational state vocabulary and maritime metaphors. Never "team," "family," or "crew" - always "Tribe."

### Business & Partner Correspondence
CEO-to-CEO communications with PartnerCo, DistributorCo, AllianceCo, government contacts. Banking compliance. Executive outreach. Pull from `context/business-info.md` and `context/current-data.md`.

### LinkedIn & Thought Leadership
Story-driven posts in Misha's authentic voice. Category creation narrative (DPI to Deep Packet Intelligence). Intrigue around launches and milestones. No negative messaging. No corporate filler. Reference `datastore/content/linkedin-archive/goal-is-a-cage.md` for voice.

### Official Documents
Board resolutions, contracts, certificates, partnership agreements, pricing models, Data Room materials. Formal but authentic - adjust formality, not personality.

### Presentations & Keynotes
VIP event materials, TradeExpo, regional expos, government meetings, investor presentations. Slides, speaker notes, talking points. Pull from `reference/dpi-market-intelligence.md` for market data.

### Hiring & Tribe Scaling
Interview standards (1,500+ interviews for ~20 hires), onboarding materials, culture documentation. The hiring bar never drops. Pull from `reference/billion-growth-playbook.md` (Section 9: Scaling the Tribe).

### Product & Technical Direction
ODUN.ONE positioning, architecture discussions, module/blade strategy, Research Lab oversight, roadmap priorities, patent strategy. Pull from `context/business-info.md`.

### Translations
English and Russian, particularly for personal communications and business correspondence. Russian for personal and emotional messages.

---

## The Agent Army

The workspace includes a suite of specialized AI agents grouped by purpose:

- **Fast operational commands** - single-call skills for daily work (drafting, intel briefs, meeting prep, dashboards, state checks)
- **Multi-step workflows** - skills that compose research, generation, and review (deal strategy, investor pitch, design pipelines, OSINT, advisors)
- **Operations, sync, and automation** - infrastructure skills (sync, dream, CRM, push-updates, scheduled monitors)

The complete inventory with current descriptions and tiers lives in [`workspace-overview.md`](workspace-overview.md). The natural-language routing logic is in [`.claude/rules/skill-router.md`](../.claude/rules/skill-router.md). The compound workflow patterns are in [`.claude/rules/skill-orchestrator.md`](../.claude/rules/skill-orchestrator.md).

When skills run, they load supporting context automatically: voice guides, contact radars, pipeline state, the DataStore index. Skills declare their context dependencies in their own SKILL.md frontmatter rather than relying on always-on includes.

---

## Workflow Chains

Common multi-step sequences:

- **Meeting cycle:** `/meeting-prep` -> meeting -> `/crm log` -> `/follow-up`
- **Deep meeting prep:** orchestrator fans out `/osint` + `/voss` + CRM history in parallel -> synthesizes via `/meeting-prep`
- **Morning comms:** orchestrator runs `/email-intel` + `/viraid` in parallel -> approval gate -> CRM writes
- **Content production:** `/linkedin-post` -> `/image-prompt` -> `/flux-image`
- **Weekly content:** `/linkedin-series` (plan) -> parallel `/linkedin-post` x3 + `/image-prompt` x3
- **Event cycle:** `/meeting-prep` (each) -> event -> `/event-debrief` -> parallel `/follow-up` (each)
- **Complex decision:** `/deep-think` -> [any downstream skill]
- **Deal progression:** `/deep-think` -> `/competitor-intel` -> `/deal-strategy` -> `/proposal` -> `/follow-up`
- **Full deal intel:** orchestrator runs `/osint` + `/competitor-intel` + `/deep-think` in parallel -> synthesizes via `/deal-strategy`
- **Investor workflow:** `/investor-pitch` -> `/data-room` -> `/meeting-prep` -> `/investor-update`
- **Fact validation:** any draft -> `/validate` (cross-reference against DataStore)
- **Quality gate:** any finished work -> `/scrutinize [--relentless]` -> fix -> commit

---

## Directory Structure

```
ceo-main/
├── CLAUDE.md                           # Always-on workspace bootstrap (60 lines)
├── .claude/
│   ├── rules/                          # Auto-loaded rule files (catalog in workspace-overview.md)
│   ├── skills/                         # Skills in {name}/SKILL.md format (catalog in workspace-overview.md)
│   ├── hooks/                          # SessionStart + PreToolUse + PostToolUse hooks
│   ├── settings.json                   # Project settings (plugin enablement, $schema)
│   └── settings.local.json             # CEO-only machine overrides (permissions, hook wiring)
│
├── context/                            # Living project state
│   ├── personal-info.md                # Who Misha is
│   ├── business-info.md                # 31C org + ODUN.ONE
│   ├── strategy.md                     # Strategic priorities
│   ├── current-data.md                 # Current metrics + workstreams
│   ├── pipeline.md                     # Active deals + investor tracker
│   ├── people.md                       # Key contacts + CRM health radar
│   ├── partners.md                     # Partner ecosystem
│   ├── customers.md                    # Customer deployments
│   └── hiring-pipeline.md              # Open roles + candidates
│
├── crm/                                # Personal CRM
│   ├── contacts/                       # One .md file per contact, with health scoring
│   └── config.md                       # Cadence rules
│
├── knowledge/                          # Zettelkasten second brain + Odin's brain
│   ├── odin-brain/                     # Odin advisor brain (principles, positions, sources)
│   └── shared/                         # Promoted notes visible to execs (corporate-classified)
│
├── reference/                          # Enduring reference material
│   ├── workspace-overview.md           # Runtime catalog (loaded on demand by /prime)
│   ├── workspace-architecture.md       # This file - educational narrative
│   ├── misha-voice.md                  # Master voice guide
│   ├── voss-negotiation.md             # Full Chris Voss framework
│   ├── hidden-characters.md            # Invisible Unicode policy + character list
│   ├── search-domains.md               # Curated domain lists for web research
│   ├── newsletter-guide.md             # Intel Briefing newsletter production guide
│   ├── email-signature.html            # 31C branded HTML signature
│   ├── ceo-calendar-policy.md          # Meeting scheduling rules (configured timezone, default Zoom)
│   ├── ceo-operating-rhythm.md         # Daily/weekly/monthly/quarterly cadence
│   ├── state-check-guide.md            # 15-min operational diagnostics
│   ├── content-calendar.md             # Publishing cadence
│   ├── billion-growth-playbook.md      # Valuation mechanics + category creation
│   ├── dpi-market-intelligence.md      # DPI market sizing + competitive landscape
│   ├── geopolitical-landscape.md       # Regional context ([priority regions])
│   ├── condition-framework-template.md # Condition-based decision framework
│   ├── development-checklist.md        # Quality checklist for new artifacts
│   ├── 31c-typeface-usage.md           # Font hierarchy
│   ├── osint-advanced-toolkit.md       # OSINT tool registry
│   ├── vps-deployment-guide.md         # Ubuntu VPS setup for Sentinel
│   └── ...                             # More reference files - see workspace-overview.md
│
├── datastore/                          # Source-of-truth documents (restructured 2026-04-20)
│   ├── INDEX.md                        # Master manifest
│   ├── products/
│   │   ├── odun-one/                   # ODUN.ONE product docs, datasheets, presentations
│   │   └── trustone/                   # TrustONE DLP module
│   ├── architecture/                   # Technical architecture, solution descriptions
│   ├── corporate/
│   │   └── presentations/              # 31C corporate brief, company overview decks
│   ├── investment/
│   │   └── decks/                      # Investor decks (master + archive)
│   ├── intelligence/                   # Competitor docs (competing vendors, state-aligned vendors)
│   ├── operations/                     # Operational state artifacts
│   ├── events/                         # industry trade shows contact databases, materials
│   ├── content/                        # LinkedIn archive, published articles
│   └── brand/
│       ├── assets/logos/               # 31C logo variants
│       ├── fonts/                      # GT Standard corporate fonts
│       ├── templates/                  # PPTX + DOCX brand templates
│       └── examples/                   # Production document examples
│
├── outputs/                            # Everything Claude generates (ceo-only by default)
│   ├── operations/                     # Dashboard, state checks, scrutiny reports, email-intel
│   ├── deliverables/                   # Meeting prep, proposals, presentations (client-facing)
│   ├── intel/                          # OSINT dossiers, briefs, newsletters, YouTube pulse
│   ├── content/                        # LinkedIn drafts, follow-ups, notebooklm outputs
│   ├── design/                         # Visual assets (HTML Studio + Replicate outputs)
│   ├── books/                          # Long-form writing (HEADING memoir)
│   ├── browser/                        # Playwright screenshots, cookies, Firecrawl cache
│   ├── thinking/                       # Deep-think reasoning sessions
│   ├── negotiations/                   # Voss playbooks
│   └── _temp/                          # Transient working files
│
├── plans/                              # Active implementation plans (completed -> plans/archive/)
├── scripts/                            # Workspace automation (sentinel, dashboard, sync, CRM, etc.)
├── config/                             # routing-map.yaml, exec-registry.json, admin.json
├── docs/                               # Shared docs (synced from templates/: GETTING-STARTED) + DEPLOYMENT.md / QUICKSTART.md (canonical install guides)
├── templates/                          # Source for shared docs + provisioning templates
└── tests/                              # Security + integration test suites
```

---

## Rules That Load Automatically

Rules live in `.claude/rules/`. Most auto-load every session; a few are path-scoped via `paths:` frontmatter and load only when matching files are touched (Claude Code natively supports this). Rules govern voice, terminology, classification, security, skill routing, document templates, hidden-character policy, and humanisation.

**Hierarchy of authority:**

1. User instructions (CLAUDE.md, direct messages) - highest
2. Rules (`.claude/rules/*.md`) - override default Claude behavior where they apply
3. Default Claude Code behavior - lowest

Rules are non-negotiable on the things they govern, but they do not override the user. When two rules conflict, the more specific rule wins; when both apply equally, the user resolves.

For the current rule inventory with one-line descriptions of each rule's enforcement scope, see [`workspace-overview.md`](workspace-overview.md). For full per-rule text, read the file in `.claude/rules/` directly - rules are short by design.

---

## Hooks

Hooks fire at session boundaries and around every tool call. They enforce things rules cannot enforce by themselves: blocking edits to protected paths, scanning for secrets and hidden characters, syncing auto-generated docs, advising on context-window pressure.

The pattern: PreToolUse hooks **prevent** (block writes to corporate/secure/docs paths, catch secrets); PostToolUse hooks **react** (sanitize, sync, monitor). SessionStart surfaces stale state.

For the current hook inventory with file paths, matchers, and timeouts, see [`workspace-overview.md`](workspace-overview.md). For the wiring that loads them, see `.claude/settings.local.json`. For Anthropic's hook spec, see [code.claude.com/docs/en/hooks](https://code.claude.com/docs/en/hooks).

---

## Protection Systems

### Hidden character protection
Every piece of text Claude produces is scanned for invisible Unicode (zero-width spaces, ZWJ, soft hyphens, non-breaking spaces, directional marks, word joiners, BOM). PostToolUse hook auto-scans every write. `scripts/sanitize-text.py` can scan any file on demand. Every deliverable shown to Misha includes "Hidden characters: clean" confirmation. Treated as a defect on par with fabricating facts.

### DataStore validation
Before any factual claim about 31C in external-facing content: read `datastore/INDEX.md`, load the source (or its `-extract.md` companion for binary files), validate the claim, flag unverified. DataStore wins over context files on conflict.

### Sensitive sessions (SENSITIVE_MODE)
The `_secure/` vault was removed in Plan 5. A sensitive session is now a fail-closed flag, `SENSITIVE_MODE` (`scripts/utils/sensitive.py` `is_sensitive()`): observability/Langfuse tracing is suppressed and design skills sanitize external-API prompts whenever sensitivity is not explicitly cleared. Telemetry is opt-in — a missing or garbage flag degrades to "no telemetry," never the reverse. Credentials live only in `.env` (gitignored).

### Secret detection
Pre-commit hook scans every staged file for known secret patterns. `.claude/hooks/prevent-secrets.py` runs PreToolUse Write|Edit|Bash. Rotate credentials immediately on incident, scrub history, force-push.

---

## Multi-Executive Architecture

Hub-and-spoke. CEO workspace (this one) is the master. A corporate repo at `../31c-corporate/` holds content shared with all execs. Executive workspaces (one per provisioned executive) pull from corporate on sync. `config/routing-map.yaml` decides what is ceo-only vs corporate (the single classification input). Skills published via `/publish-corporate` or `/push-updates`. CRM aggregated across execs via `../31c-crm-central/`. Full admin workflow in `docs/CEO-ADMIN-GUIDE.md` (CEO-only).

### Two-stage propagation (staging branch + canary exec)

Designed 2026-05-15 (plan: `plans/2026-05-15-corporate-staging-branch-and-canary-exec.md`). Layer 1 CEO-side infrastructure is in place; the publish flip and canary-side activation follow in subsequent sessions.

The corporate repo carries two branches: `main` (production, what most execs pull) and `staging` (canary pre-flight). One designated exec - **the canary exec** (a chosen slug marked `canary: true`) - pulls from `staging` instead of `main`. The branch-switch on pull was previously driven by `workspace-sync.py --branch`; that engine is retired (see `plans/2026-06-26-retire-workspace-sync-disk-import.md`), so the canary branch selection now lives in `canary-smoke.py` itself: its `ensure_on_staging()` does a plain `git fetch origin staging` + `git checkout staging` on the canary clone (gated on `identity.canary`, best-effort, never `-B`) before the checks read the branch. `scripts/canary-smoke.py` still runs four deterministic post-pull checks (skill-router sync, CRM schema, workspace-health, hidden-character scan on `corporate/CLAUDE.md`) and writes `status/canary-{slug}.json` to the staging branch so the CEO can read canary health locally without a network call to the canary machine.

The end-state flow:

```text
CEO /publish-corporate -> staging branch -> canary 4h soak (smoke + Layer 3 evals)
  -> /promote-corporate (Layer 2) fast-forward merges staging -> main
  -> non-canary execs pull main on next hourly sync
```

**Current rollout state (2026-05-15):**

- Layer 1 CEO-side: implemented. `staging` branch exists on origin; `scripts/canary-smoke.py` ships to every exec workspace (M6 guard exits early on non-canary) and now owns the branch-switch via `ensure_on_staging()` (the retired `workspace-sync.py --branch` auto-track was replaced 2026-06-26, git-native); `scripts/provision-exec.py --canary` flag wired; the canary exec flagged in the fleet registry.
- Layer 1 canary-side: pending. The canary exec's `.workspace-identity.json` needs `canary: true` set; their scheduled task needs `canary-smoke.py` invocation post-sync.
- Layer 1 publish flip: deliberately deferred. `/publish-corporate` and `/push-updates` still push to `main` for now, so the non-canary execs continue to receive updates without interruption. The flip happens in the canary-side session, coordinated with Layer 2's `/promote-corporate` skill so the gate is in place before production traffic moves to staging.
- Layers 2-4: pending (`/promote-corporate`, `/rollback-corporate`, `scripts/canary-eval.py`, dashboard surface).

---

## Communication Style

Follow these rules for **all** written output:

- **Authentic over corporate** - no "Dear Valued Team Members" or similar filler
- **Direct and action-oriented** - get to the point
- **Warm but authoritative** - the Captain's voice
- **Story-driven when appropriate** - open with lessons, build narrative
- **Maritime metaphors welcome** - storms, navigation, diving, sailing. Never military references.
- **Brevity always** - keep drafts tight. Expect 2-4 iteration rounds to shorten and tighten
- **No excessive emojis** - professional but approachable
- **Russian for personal and emotional messages**
- **Voss active on all external comms** - label before logic, calibrated questions, precise numbers

Full voice guide: [`misha-voice.md`](misha-voice.md). Full Voss framework: [`voss-negotiation.md`](voss-negotiation.md).

---

## Technical Notes

- **Platform:** Windows 11 Pro + cross-platform support for macOS exec workspaces
- **Model:** Claude Opus 4.7 with 1M context window
- **Deployment:** Windows master workspace + 5 GitHub repos. VPS (Ubuntu 24.04 LTS Hostinger) runs Sentinel as a systemd service for 24/7 comms monitoring
- **Timezone:** configured local timezone (`HEADING_OS_TZ`; UTC default) for all timestamps, calendar, scheduling
- **Email:** Always via `scripts/send-email.py` with inline CID-attached branded signature. Never raw exchangelib Message()
- **Email addresses:** Verified from Exchange Global Address List before sending. @31c.io for Tribe
- **Default Zoom:** stored in `reference/ceo-calendar-policy.md`
- **Web research escalation:** WebFetch (simple pages) -> Firecrawl scrape (JS/anti-bot) -> Firecrawl batch/crawl -> Playwright (interaction/auth) -> Agent Browser (complex browsing)
- **Design standards:** HTML/PDF/visual output follows the 31C design system. CSS custom properties, GT Standard font, card-based layouts, gradient headers. No generic HTML
- **Secrets:** Never in tracked files. `.env` (gitignored) + password manager (1Password/Bitwarden) + `.sessions/` (auto-managed)
