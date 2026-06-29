---
name: ceo-to-ceo
description: Draft peer-level CEO-to-CEO correspondence in Misha's voice - executive register, relationship-aware, Voss-calibrated. Use for writing to another company's CEO or founder as a peer. Trigger when the user says "CEO letter", "write to [CEO name]", "peer correspondence", or "executive letter". Do NOT use for a non-CEO recipient (use /email-draft) or a formal branded external letter on letterhead (use /corporate-letter). NEVER auto-sends - produces a draft for review.
metadata:
  author: Misha Hanin
  email: misha.hanin@odinix.com
  version: "1.1"
argument-hint: "[name] [subject]"
allowed-tools: "Read"
x-31c-orchestration:
  parallel_safe: true
  shared_state: []
  triggers:
    - CEO letter
    - write to [CEO name]
    - peer correspondence
    - executive letter
x-31c-capability:
  what: >
    Drafts a 150-300 word CEO-to-CEO email to a partner, peer executive, or key
    institutional contact - written peer-to-peer, direct about the objective,
    with a Voss layer and two subject-line options.
  how: >
    Run /ceo-to-ceo [name] [subject]. Loads relationship context from
    context/people.md and pipeline, then returns the draft inline for review.
  when: >
    Use for peer-level correspondence to another CEO or senior executive. For a
    non-CEO recipient use /email-draft; for a formal branded external letter use
    /corporate-letter.
---
# CEO-to-CEO Correspondence

Draft CEO-level correspondence to a partner, peer executive, or key institutional contact.

## Variables

to: [CEO/executive name, title, company]
subject_area: [Core subject — e.g., "partnership expansion proposal", "post-MWC follow-up", "strategic alignment meeting"]
objective: [What this communication should achieve]
context: [Background on the relationship and situation]
tone: formal | warm-peer | urgent — default: warm-peer

---

## Instructions

Before drafting, read:
- `reference/misha-voice.md` — Voice guide, especially Email and CEO-to-CEO notes
- `context/people.md` — Load full relationship context for this executive
- `context/business-info.md` — 31C positioning and partner context
- `context/pipeline.md` — Check if this relates to an active partnership discussion
- `datastore/INDEX.md` — If the post contains specific facts or numbers, validate against source documents

Draft a CEO-to-CEO communication that:
- Opens as a peer - not a vendor writing to a client, not a subordinate writing up
- Gets to the point in the opening paragraph
- Is direct about the objective without being transactional
- Demonstrates that Misha understands the other CEO's business and interests
- Proposes a concrete next step or decision point
- Closes with the captain's tone - decisive, warm, forward-looking

**Voss layer:** Apply per `.claude/rules/voss.md`. Skill-specific hooks: accusation audit on sensitive topics, normative leverage (reference their stated values/public commitments), next steps as calibrated questions. Full framework: `reference/voss-negotiation.md`.

**Length:** 150-300 words. CEOs don't read long emails.

After the draft, provide:
- Subject line options (2)
- One flag: anything that requires extra care given the relationship

## NEVER

- NEVER send the letter. This skill drafts only — outbound send is always human-gated (lethal-trifecta control). The CEO sends or approves explicitly.
- NEVER invent shared history or commitments with the counterpart CEO — if the relationship context is thin, ask.
