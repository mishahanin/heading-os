---
name: voss
description: Tactical negotiation and conversation coaching engine based on Christopher Voss's FBI methodology. Produces situation-specific playbooks with accusation audits, calibrated questions, labeling scripts, and scenario maps. Use when preparing for any important conversation - deal negotiations, difficult internal discussions, investor pitches, partnership talks, conflict resolution, or any high-stakes communication. Trigger when the user says "voss", "negotiate", "prepare for negotiation", "tactical empathy", "how do I handle this conversation", "difficult conversation prep", "accusation audit", "prepare me for this call", "negotiation playbook", or asks for help approaching a sensitive or high-stakes interaction.
argument-hint: "[counterpart] [situation]"
allowed-tools: "Read"
metadata:
  author: Misha Hanin
  email: misha.hanin@odinix.com
  version: "1.1"
x-31c-orchestration:
  parallel_safe: true
  shared_state: []
  triggers:
    - negotiation prep
    - tactical empathy
    - accusation audit
    - difficult conversation
    - negotiation playbook
x-31c-capability:
  what: >
    Produces a situation-specific tactical playbook for a high-stakes
    conversation using Voss's FBI method - counterpart profile, accusation
    audit, calibrated questions, labeling scripts, scenario maps, Ackerman plan.
  how: >
    Run /voss [counterpart] [situation]. Loads people, pipeline, and any CRM
    file, then saves the playbook to outputs/negotiations/.
  when: >
    Use to prepare for a negotiation, difficult talk, or sensitive call. Note
    the Voss principles also run always-on across all outbound comms via the
    voss rule; this skill is the deep per-conversation prep.
---
# Voss Tactical Coaching Engine

Prepare a situation-specific tactical playbook for any important conversation, using Christopher Voss's FBI negotiation methodology.

## Variables

counterpart: [Who - name, title, company, role in this conversation]
situation: [What the conversation is about]
goal: [What you want to achieve - primary and secondary outcomes]
tension: [What makes this difficult or high-stakes - optional but valuable]

---

## Instructions

Before building the playbook, read:
- `reference/voss-negotiation.md` - Full Voss methodology reference
- `reference/misha-voice.md` - Ensure playbook language matches Misha's authentic voice
- `context/people.md` - Check if counterpart is listed; load relationship history
- `context/pipeline.md` - Check if this relates to an active deal or partnership
- `context/business-info.md` - 31C positioning relevant to this conversation

If the counterpart has a CRM file in `crm/contacts/`, read it for interaction history, communication preferences, and notes.

If a deal-strategy or meeting-prep output exists for this counterpart, read it for additional context.

---

## Phase 1: Intelligence & Profiling

**Counterpart profile:**
- Who they are and what drives them
- Their likely negotiator type: **Analyst** (data, preparation, silence) / **Accommodator** (relationship, rapport, chatty) / **Assertive** (direct, competitive, time-pressured)
- How to calibrate approach for their type
- Their probable emotional state coming into this conversation
- Their constraints - what they might not be able to say or do
- What "success" looks like from their side

**Power dynamics:**
- Who needs whom more in this conversation?
- Three leverage types available:
  - Positive: what can we offer them?
  - Negative: what happens if they don't engage?
  - Normative: what values, standards, or commitments of theirs align with our position?

---

## Phase 2: Accusation Audit

Generate a comprehensive list of every negative thing the counterpart might think or feel about Misha, 31C, or this situation. Be brutally honest.

For each item, craft a preemptive labeling statement:
- "You're probably thinking that [specific concern]..."
- "It might seem like [negative perception]..."
- "I wouldn't blame you for feeling [emotion] given [reason]..."

Order from most to least severe. Address the worst first - it only gets easier from there.

---

## Phase 3: Calibrated Questions

Generate 8-12 situation-specific questions. All "How" or "What" format.

**Opening questions** (discover their world):
- 3-4 questions that get them talking about their situation, needs, and constraints

**Mid-conversation questions** (guide toward your outcome):
- 3-4 questions that make them consider your solution as their idea

**Closing questions** (advance to next step):
- 2-3 questions that secure commitment without forcing "yes"

**Fallback questions** (when conversation derails):
- 2 questions to reset when things go sideways

---

## Phase 4: Tactical Playbook

### Opening Strategy
- Recommended voice tone to start (FM DJ / Positive / Assertive)
- First words - how to open the conversation
- Early accusation audit deployment (which items to address in first 2 minutes)

### Mirroring Map
- Key phrases the counterpart is likely to use
- Which ones to mirror and how
- When to deploy dynamic silence after mirroring

### Labeling Scripts
- 5-7 pre-built labels for probable emotions:
  - "It seems like..."
  - "It sounds like..."
  - "It looks like..."

### "That's Right" Pathway
- What summary would make them say "That's right"
- The paraphrase + label combination to get there
- What "That's Right" sounds like for this specific conversation

### Black Swan Hypotheses
- 2-3 hidden factors that could change everything
- Questions designed to surface them
- Signals that would confirm each hypothesis

### Ackerman Plan (if price/terms involved)
- Target number
- Opening offer (65% of target)
- Three raises: 85%, 95%, 100%
- Precise final number (non-round)
- Non-monetary item to include with final offer

---

## Phase 5: Scenario Mapping

### Best Case
- How the conversation flows when it goes well
- What to say when they agree (secure commitment with "How" questions)

### They Push Back
- Most likely objection and calibrated question response
- Second objection and response
- The "How am I supposed to do that?" deployment

### They Go Silent
- Mirror last statement, then wait
- Label: "It seems like you need time to process this."
- Offer space: "What would be helpful right now?"

### They Get Aggressive
- Drop to FM DJ voice, slow down
- Label: "It sounds like you feel strongly about this."
- Calibrated question: "Help me understand what's driving this."
- Never match aggression - absorb it with empathy

### Walk-Away
- Clear criteria for when to stop
- How to exit gracefully while keeping the door open
- "No deal is better than a bad deal" - know your limit before you start

---

## Output

Produce the tactical playbook in this exact structure. Write it in a tone Misha can quickly scan and internalize - direct, practical, no filler.

**Save to:** `outputs/negotiations/YYYY-MM-DD-{counterpart-name-slug}.md`

After creating the playbook:
1. Check if a CRM file exists for the counterpart in `crm/contacts/`
2. If yes: add a Note entry logging that a Voss tactical playbook was prepared, with date and context
3. If no: suggest creating one if the counterpart is significant

---

## NEVER
- Generic advice that could apply to anyone - every element must be specific to this counterpart and situation
- Corporate negotiation jargon - use Voss's language naturally
- Passive suggestions - this is a tactical playbook, not a theory lecture
- Round numbers in any pricing or terms recommendations
- "Win-win" framing - find what each side actually needs instead
