---
name: interview-prep
description: Produce an interview framework with position-specific questions and scoring rubric aligned to 31C Five Core Principles. Generates 20-question bank, 1-5 scoring rubric, and post-interview scorecard. Use when preparing to interview candidates or building evaluation frameworks. Trigger when the user says "interview prep", "interview questions", "hiring framework", "candidate evaluation", or asks to prepare for an upcoming interview.
argument-hint: "[position] [level]"
allowed-tools: "Read"
model: sonnet
metadata:
  author: Misha Hanin
  email: misha.hanin@odinix.com
  version: "1.1"
x-31c-orchestration:
  parallel_safe: false
  shared_state: []
  triggers:
    - interview prep
    - interview questions
    - hiring framework
x-31c-capability:
  what: >
    Builds a complete interview kit for a role - a 20-question bank (warm-up,
    technical, Five Core Principles behavioral, culture), a 1-5 scoring rubric,
    and a post-interview scorecard template aligned to the 31C hiring standard.
  how: >
    Run /interview-prep [position] [level]. Returns the question bank, rubric,
    and scorecard inline, ready for any interviewer on the Tribe.
  when: >
    Use when preparing to interview a candidate or building a hiring evaluation
    framework. Not for general research on a person - use /osint for that.
---
# Interview Prep

Produce an interview framework with position-specific questions and scoring rubric.

## Variables

position: [Job title and department]
level: junior | mid | senior | leadership
context: [What specifically this role needs to do; any team or project context]
candidate: [Candidate name, background summary if pre-interview — optional]

---

## Instructions

Before drafting, read:
- `context/business-info.md` — 31C company, Tribe culture, hiring standard (1,500+ interviews for ~20 hires)
- `reference/billion-growth-playbook.md` — Section 9: Scaling the Tribe (hiring philosophy, culture carriers)
- `outputs/operations/workspace/31c-operational-state-model.md` — Five Core Principles (what we're screening for behaviorally)
- `reference/misha-voice.md` — The voice of 31C (screen for culture fit)

---

## The 31C Hiring Standard

The bar never drops. Every hire is a culture carrier. We've done 1,500+ interviews for ~20 hires. What we're looking for:
- Evidence of delivery under pressure (not intent — proof)
- Intellectual honesty (can they say they were wrong?)
- Alignment with Data Sovereignty (do they understand why it matters?)
- Partnership orientation (do they build relationships or execute transactions?)
- Navigation instinct (do they adapt or freeze when conditions change?)

---

## Phase 1: Role-Specific Question Bank (20 questions)

**Opening questions (warm-up, 2-3):**
[Calibrated to position and level — make them feel like a conversation, not an interrogation]

**Technical/functional questions (8-10):**
[Role-specific; drawn from the position requirements — depth over breadth]

**Behavioral questions — Five Core Principles (5):**
- Proof of Value: "Tell me about a time you killed a project because it wasn't creating real value."
- Partnership for Life: "Describe a relationship you maintained even when it was difficult."
- Integrity: "Tell me about a time you delivered bad news to a superior. What happened?"
- Deliver Under Pressure: "What's the hardest operational situation you've been in? What did you do?"
- Data Sovereignty: "How do you think about data privacy and client trust in your work?"

**Culture/philosophy questions (3-5):**
[Testing for Navigation Principle alignment, operational state thinking, Tribe fit]

**Candidate questions (2):**
Standard closing — what do they ask? What they ask reveals who they are.

---

## Phase 2: Scoring Rubric

For each question category, 1-5 scoring guide:
- **5:** Exceptional — clear evidence, specific, compelling, unexpected depth
- **4:** Strong — solid evidence, specific, meets the bar
- **3:** Adequate — evidence present but generic or surface-level
- **2:** Weak — vague, no real evidence, or concerning signals
- **1:** Disqualifying — red flag, dishonesty, or fundamental misalignment

**Minimum threshold:** No score below 3 in any category. Any 1 is automatic no-hire.

---

## Phase 3: Post-Interview Scorecard Template

| Category | Score (1-5) | Evidence / Notes |
|----------|-------------|-----------------|
| Technical/functional depth | | |
| Proof of Value | | |
| Partnership for Life | | |
| Integrity | | |
| Deliver Under Pressure | | |
| Data Sovereignty | | |
| Culture fit | | |
| Navigation instinct | | |
| **Overall** | | |

**Recommendation:** Hire / Strong hire / Pass / Strong pass
**One-line rationale:**

---

**Output:** Complete interview kit. Question bank, rubric, and scorecard. Ready for Misha or any interviewer on the Tribe.
