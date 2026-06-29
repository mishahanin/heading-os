---
name: linkedin-post
description: LinkedIn Post
argument-hint: "[topic]"
allowed-tools: "Read"
metadata:
  author: Misha Hanin
  email: misha.hanin@odinix.com
  version: "1.2"
x-31c-orchestration:
  parallel_safe: true
  shared_state: []
  triggers:
    - linkedin post
    - draft a post about
    - write a post
x-31c-capability:
  what: >
    Drafts a single LinkedIn post in Misha's authentic voice - standalone hook, observation-to-insight
    arc, and a thought-provoking close (no CTA). Returns alt openers, hashtags, and a posting-time suggestion.
  how: >
    Run /linkedin-post <topic>. Reads the voice guide and validates any facts against the datastore;
    produces the draft inline (read-only, does not publish).
  when: >
    Use for one post. For a multi-post plan across the week use /linkedin-series; to archive a post
    after publishing use /linkedin-archive.
---
# LinkedIn Post

Draft a LinkedIn post in Misha Hanin's authentic voice.

## Variables

topic: [What is this post about?]
angle: [What's the specific angle, insight, or counterintuitive point?]
context: [Any specific details, data points, events, or recent observations to include]
length: short (~150 words) | medium (~300 words) | long (~500 words) — default: medium

---

## Instructions

**Customization (optional, Phase 0).** This skill is customization-aware (pilot). Resolve any per-exec overrides first: `python scripts/resolve_customization.py --skill .claude/skills/linkedin-post`. Apply any `activation_steps_prepend`, `persistent_facts` (e.g. a default length or recurring narrative), and output-path overrides from the merged result. On any failure, proceed with the defaults below — never block. Layout + authoring guide: `config/skill-custom/README.md`.

Before drafting, read:
- `reference/misha-voice.md` — Complete voice guide (especially the LinkedIn section)
- `datastore/content/linkedin-archive/old-archive/goal-is-a-cage.md` — Best example of Misha's LinkedIn writing
- `context/business-info.md` — 31C positioning and ODUN.ONE context
- `datastore/INDEX.md` — If the post contains specific facts or numbers, validate against source documents

Draft a LinkedIn post that:
- Opens with a single, powerful sentence that works as a standalone hook
- Builds a narrative arc: observation → tension → insight → what it means
- Uses maritime metaphors only when they arise naturally (never forced)
- Connects to the category creation narrative (DPI → Deep Packet Intelligence, sovereignty, the incumbent vacuum) when relevant
- Ends with a thought-provoking statement — not a call to action, not a question asking for comments
- Reads like a human wrote it, not a marketing team
- Has paragraph breaks every 1-3 sentences (LinkedIn is read on mobile)

After the draft, provide:
- 2 alternative opening lines to consider
- 3-5 hashtags: always include some from #DataSovereignty #DeepPacketIntelligence #Cybersecurity #31Concept #ODUN
- Recommended posting time (Tuesday-Thursday, 7-9am UAE time typically performs best)
- One line on what to cut if shortening

## NEVER
- "I'm excited to share..."
- "Thrilled to announce..."
- "In today's rapidly evolving landscape..."
- Competitor names
- Military references
- Emojis in the body text
- Bullet-point lists as the main content structure
