---
name: meeting-prep
description: Produce a deep pre-meeting brief on an external counterpart - OSINT dossier, CRM relationship history, prior comms, a Voss tactical-empathy plan, and three calibrated questions. Use before any meeting with a named external person and/or company. Trigger when the user says "meeting prep for [counterpart]", "prepare for meeting with [person or company]", or "briefing for [named person + company]". Do NOT use for internal syncs without an external counterpart (just answer), generic briefings without a named target (use /market-brief or /dashboard); deep multi-signal prep escalates to the orchestrator's Deep Meeting Prep pattern.
argument-hint: "[with] [purpose]"
allowed-tools: "WebSearch, WebFetch, Read, Bash(python3:*)"
model: sonnet
metadata:
  author: Misha Hanin
  email: misha.hanin@odinix.com
  version: "1.4"
x-31c-orchestration:
  parallel_safe: partial
  shared_state:
    - crm/contacts/
  triggers:
    - meeting prep
    - prepare for meeting with
    - briefing for
x-31c-capability:
  what: >
    Produces a pre-meeting briefing - counterpart profile, their world right now,
    our objective, 5-7 talking points, anticipated questions, competitive context,
    meeting flow, a Voss tactical section, and a one-paragraph read-before-you-walk-in
    summary - in MD, HTML, and PDF.
  how: >
    Run /meeting-prep [with] [purpose]. Reads people.md, pipeline.md, strategy,
    and any /osint case file, appends a /brain-audit footer, and writes to
    outputs/operations/meeting-prep/. A deep request escalates to the orchestrator's
    parallel meeting-prep pattern.
  when: >
    Use to prepare for an external meeting with a named counterpart. For raw recon
    on a target use /osint; for a full win plan on a deal use /deal-strategy.
---
# Meeting Prep

Pre-meeting briefing with talking points, relationship context, and strategic objectives.

## Variables

with: [Who — name, title, company]
purpose: [Meeting purpose — partnership discussion, investor pitch, government meeting, customer demo, technical review]
date: [When — for timeline context]
format: in-person | video | phone — default: video
context: [Any additional context — how did this meeting come about, what happened last time, what's at stake]

---

## Instructions

Before preparing, read:
- `context/people.md` — Full relationship context for this person/organization
- `context/pipeline.md` — Check if this relates to an active deal, partnership, or investor conversation
- `context/strategy.md` — Strategic priorities and competitive positioning
- `context/business-info.md` — ODUN.ONE, company credentials, partner ecosystem
- `reference/dpi-market-intelligence.md` — Market data relevant to this meeting
- `reference/geopolitical-landscape.md` — Regional context if relevant
- `reference/search-domains.md` — Domain filtering for web searches
- `outputs/intel/cases/[target-slug].md` — IF a forensic case file exists for this counterpart or their company (written by `/osint` or `/competitor-intel`). Read it to inherit prior hypotheses and their evidence grades. This skill is a **read-only consumer** of the case file — surface the top graded claims, never write or modify it. Grading spec: `reference/forensic-evidence-grading.md`.

When you surface a claim drawn from the case file in "Their World Right Now" or "Competitive Context", carry its evidence grade so Misha knows what is Confirmed versus merely Hypothesized walking into the room.

---

## Briefing Structure

### 1. Who You're Meeting
- Name, title, organization
- Relationship history (from people.md or provided context)
- Their priorities and what they care about
- Communication style (from people.md or inferred from culture/role)
- Decision-making authority

### 2. Their World Right Now
- What's happening in their organization/market
- Recent news or developments (search if needed -- apply `blocked_domains` from `reference/search-domains.md`; for 31C-relevant sectors, also use `allowed_domains` from matching topic groups)
- Use `python scripts/firecrawl.py crawl` on the counterpart's company site for recent news, product changes, and leadership updates
- Pressures they're likely facing
- What success looks like for them in this conversation

### 3. Our Objective
- Primary outcome we want from this meeting
- Secondary outcome (the minimum acceptable result)
- What NOT to do (red lines, topics to avoid, sensitivities)

### 4. Talking Points (5-7 max)
Numbered, in priority order. For each:
- The point in one sentence
- Why it matters to them (not just to us)
- Supporting proof point or data if needed

### 5. Anticipate Their Questions
3-5 questions they're likely to ask, with prepared responses in Misha's voice.

### 6. Competitive Context
If relevant:
- Who else they might be talking to
- How to position against likely alternatives
- The one thing we have that nobody else does (for this specific meeting)

### 7. Meeting Flow Recommendation
- How to open (first 2 minutes)
- When to pivot from listening to presenting
- How to close and secure the next step

### 8. Tactical Approach (Voss)
Reference `reference/voss-negotiation.md` for the full framework. For this meeting:
- **Counterpart type:** Analyst / Accommodator / Assertive - and how to calibrate
- **Accusation audit:** 2-3 negative assumptions to address early
- **Calibrated questions:** 3 situation-specific "How" / "What" questions to have ready
- **Labels:** 2-3 pre-built "It seems like..." statements for likely emotions
- **Voice tone:** Recommended starting tone (FM DJ / Positive / Assertive)

### 9. One-Pager
A single paragraph Misha can read 5 minutes before walking in - the essence of everything above.

---

**Output:** Complete meeting brief in three formats:

1. **Markdown** (`briefing.md`) -- The source document. Concise, actionable, fits on 2 pages max.
2. **HTML** (`briefing.html`) -- Professional styled HTML version with color-coded tiers, phase cards, attention map grid, and print-optimized CSS. Use the same content as the MD file, formatted for visual review and sharing.
3. **PDF** (`briefing.pdf`) -- Generated from the HTML using `python scripts/html-to-pdf.py briefing.html briefing.pdf`. A4 format, ready to print or share digitally.

All three files go in the same output directory (e.g., `outputs/operations/meeting-prep/YYYY-MM-{meeting-name}/`).

## Phase 9.5: Relevant Odin principles (CEO workspace only)

Brain-gated. IF on the CEO workspace AND `knowledge/odin-brain/` exists, cite the Odin principles relevant to this counterpart against the live deal. Otherwise SKIP this phase entirely - no error, no mention (exec workspaces have no Odin brain, so the step is inert there).

1. Read the contact's `relevant_principles` frontmatter (if present), and/or run `python scripts/odin-principles.py --type <relationship_type> --stage <stage> --json` (stage derived from the pipeline row matched via `pipeline_company`).
2. Cite up to 3 of the returned principles inline by `title`, framed against the named counterpart: "For this <deal>, Odin's `<slug>` applies because ...". Place the block in `briefing.md` (and the HTML body) ahead of the Brain audit footer.
3. Fabrication floor: cite ONLY what the helper returns. If it returns nothing, write "No Odin principle matched this relationship domain" and move on - never invent a principle.

Single hyphens; ODUN.ONE / DPI+ / Tribe terminology.

## Phase 10: Brain Audit

After producing the meeting briefing, invoke `/brain-audit` with:

- `--sources`: comma-separated list of every file you read during prep (CRM contact, pipeline row, thread entries, OSINT outputs, etc.)
- `--entity`: the counterpart's full name (and company if relevant, e.g., "Sara Okonkwo (Nimbus)")

Append the returned three-section footer (`## Brain audit`) to the end of `briefing.md` and to the end of the HTML body. Do not duplicate the audit across the three output formats; the MD and HTML share the same audit block, and the PDF inherits it from the HTML render.

The audit reports newest-source dates, modality coverage for the counterpart, and disagreements among the cited sources. If it flags staleness or coverage gaps, mention them in the chat summary alongside the briefing file paths.

## CRM Auto-Log

After preparing the meeting brief:
1. Check if a CRM file exists for the meeting contact in `crm/contacts/`
2. If yes: add a Note entry logging that meeting prep was created, with date and purpose. Update `last_touch` in frontmatter.
3. If no: note this is a new contact and suggest creating a CRM file after the meeting with `/crm add`

## Knowledge Base

After meeting prep is complete, offer: "After the meeting, capture the key takeaways: `/odin log` records an episode in Odin's brain (CEO-only); `/zk distill` adds them to the knowledge base."
