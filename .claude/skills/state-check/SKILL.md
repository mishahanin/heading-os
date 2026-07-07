---
name: state-check
description: Diagnostic assessment of a workspace operational function or the whole system - a 15-minute State Check in the Navigation Principle sense, reporting operational state, drift, and course corrections rather than rigid pass/fail targets. Use when the user says "state check", "how are we doing", "operational state", or "function health". Do NOT use for the full morning briefing (use /dashboard), full context load (use /prime), or end-of-week review (use /weekly-review).
argument-hint: "[function]"
allowed-tools: "Read"
model: sonnet
metadata:
  author: Misha Hanin
  email: misha.hanin@odinix.com
  version: "1.1"
x-heading-orchestration:
  parallel_safe: true
  shared_state: []
  triggers:
    - state check
    - how are we doing
    - operational state
    - function health
x-heading-capability:
  what: >
    Runs a structured Operational State Check for one function (sales, product, partnerships, tribe, research) or company-wide - Sea State, Current State, Heading Check, and behavioral Course Corrections, in one page max.
  how: >
    Run /state-check [function]; it reads the operational-state-model, current-data, and pipeline, then answers the four State Check questions with an on-heading / drift assessment.
  when: >
    Use to diagnose whether a function is holding its operational state. For the morning briefing use /dashboard; for the full context load use /prime; for the end-of-week review use /weekly-review.
x-heading-routing:
  category: Strategy
  triggers:
    - state check
    - how are we doing
    - operational state
    - function health
  exclusions:
    - Dashboard -> /dashboard
  compound: 'No'
  router: auto
---
# State Check

Run a structured Operational State Check for a function or the full company.

## Variables

function: sales | product | partnerships | tribe | research | company-wide
context: [Brief situational context — what's happening that prompted this check?]

---

## Instructions

Before running, read:
- `outputs/operations/workspace/31c-operational-state-model.md` — Core operational states for each function
- `reference/state-check-guide.md` — State Check format and four questions
- `context/current-data.md` — Current metrics and active workstreams
- `context/pipeline.md` — Active pipeline context

Run a structured State Check using the four questions from the State Check Guide:

**1. Sea State (2 min)**
What external conditions have changed since last check? What's the environment doing?

**2. Current State (3 min)**
What operational state is [function] actually in right now? Not what we want — what's true?
Evidence: [list observable signals]

**3. Heading Check (2 min)**
Are we on heading? Have we drifted?
Assessment: [on heading / slight drift / significant drift]

**4. Course Corrections (3 min)**
If drift detected: what specific, behavioral adjustments restore the state?
Course corrections are behaviors, not goals. "Do X daily" not "achieve Y."

**Output format:**

---
**Function:** [name]
**Current State:** [one sentence assessment]
**On Heading:** Yes / Slight drift / Significant drift
**Sea State Summary:** [2-3 sentences]
**Course Corrections:** [bulleted list, max 3]
**Next State Check:** [recommended timing]

---

Total output: 1 page max. The point is clarity, not comprehensiveness.
