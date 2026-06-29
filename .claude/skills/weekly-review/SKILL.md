---
name: weekly-review
description: >
  CEO weekly review -- sea state, heading, pipeline movement, Tribe state,
  content cadence, priorities, and continuity tracking. Use at the end of each
  week (Friday) or start of the next (Sunday/Monday). Saves the completed
  review to outputs/operations/reviews/ for longitudinal tracking.
argument-hint: "[week date]"
allowed-tools: "Read, Write, Edit, Bash(python3:*), Glob"
model: sonnet
metadata:
  author: Misha Hanin
  email: misha.hanin@odinix.com
  version: "2.1"
x-31c-orchestration:
  parallel_safe: partial
  shared_state:
    - outputs/operations/reviews/
  triggers:
    - weekly review
    - end of week review
    - friday review
x-31c-capability:
  what: >
    Produces the CEO weekly review - sea state, heading, pipeline movement, Tribe
    state, content cadence, top-3 priorities, and course corrections - with
    week-over-week continuity against the prior review.
  how: >
    Type /weekly-review [week date]. It reads the context and pipeline files,
    analyzes movement and content cadence, and saves the 2-3 page review to
    outputs/operations/reviews/YYYY-MM-DD-weekly-review.md.
  when: >
    Use at the end of the week (Friday) or start of the next. For a single
    function's health use /state-check; for the daily briefing use /dashboard.
---
# Weekly Review

CEO weekly review -- sea state, heading, pipeline movement, Tribe state,
content cadence, priorities, course corrections, and week-over-week continuity.

## Variables

week_of: [Date of the week being reviewed, e.g. 2026-03-22]
notes: [Anything specific on your mind going into this review -- optional]

---

## Phase 0 -- Previous Week Continuity

Before building this week's review, check for a prior review.

1. Scan `outputs/operations/reviews/` for the most recent `*-weekly-review.md` file.
2. If found, read it and extract:
   - **Top 3 Priorities** from the previous week.
   - **Course Corrections** that were prescribed.
3. For each priority, assess: accomplished, partially accomplished, or not addressed.
   Use evidence from `context/current-data.md`, `context/pipeline.md`, CRM activity,
   and any other observable signals.
4. For each course correction, assess: executed, in progress, or not started.
5. Summarize the continuity status as a short block to include at the top of the
   new review (see output template below).

If no previous review exists, note "First review -- no prior baseline" and proceed.

---

## Phase 1 -- Context Loading

Read these files to build the review:

- `context/current-data.md` -- Current metrics, milestones, active workstreams
- `context/pipeline.md` -- Active deals and investor conversations
- `context/strategy.md` -- Strategic priorities and 3-year arc
- `context/people.md` -- Key contacts and Tribe state
- `outputs/operations/workspace/31c-operational-state-model.md` -- Operational states and vocabulary
- `reference/state-check-guide.md` -- State Check framework

Also scan:
- `outputs/content/linkedin/` -- LinkedIn posts published this week (files dated within the review week)
- `outputs/intel/newsletters/` -- Newsletter editions published this week
- `context/*.md` -- Check "Last verified" or "Last updated" dates in all context files

---

## Phase 2 -- Analysis & Synthesis

### Pipeline Movement Analysis

Read `context/pipeline.md` and compare against the previous review's pipeline snapshot
(if available) or against observable signals:

- **Deals that advanced stages** -- any deal that moved forward (e.g., Discovery -> Demo, Demo -> NDA)
- **Deals that stalled** -- any deal with no recorded movement in >14 days
- **New deals added** -- deals not present in the prior review
- **Deals won or lost** -- any closed outcomes
- **Weighted pipeline value** -- sum of Est. Value columns where available, weighted by stage:
  - Discovery: 10%, Demo: 20%, NDA/Workshop: 30%, Preparing/Proposal: 50%, Closed-Won: 100%
  - Use "TBD" aggregate if most values are unknown

### Content Cadence Analysis

- Count LinkedIn posts in `outputs/content/linkedin/` dated within this review week.
  Target: 3 posts/week.
- Check `outputs/intel/newsletters/` for editions dated within this review week.
  Target: 1 newsletter/week.
- Flag if behind cadence with specific gap.

### Context Freshness Scan

For each file in `context/`:
- Extract the "Last verified" or "Last updated" date.
- Flag any file where that date is >14 days old.
- Compile a list for the Context Refresh Prompt section.

---

## Phase 3 -- Review Output

Produce the following review document. Keep total output to 2-3 pages -- actionable,
not exhaustive.

```markdown
# CEO WEEKLY REVIEW -- Week of [date]

---

## Previous Week Continuity

[If prior review exists:]

**Priorities from [prior week date]:**
1. [Priority 1] -- [Accomplished / Partially accomplished / Not addressed]. [Brief evidence.]
2. [Priority 2] -- [Status]. [Brief evidence.]
3. [Priority 3] -- [Status]. [Brief evidence.]

**Course Corrections prescribed:**
- [Correction 1] -- [Executed / In progress / Not started]
- [Correction 2] -- [Status]

Continuity grade: [Strong / Mixed / Weak]

[If no prior review: "First review -- no prior baseline."]

---

## Sea State

External conditions this week: [market, geopolitical, competitive dynamics --
what is the environment doing?]

---

## Heading Confirmation

Are we on heading? [Yes / Slight drift / Significant drift]
Key evidence: [2-3 observable signals]

---

## Pipeline Movement

**Advances:**
- [Deal] moved from [Stage A] to [Stage B]

**Stalled (>14 days no movement):**
- [Deal] -- last activity [date/description]

**New this week:**
- [Deal] -- [Region] -- [Stage]

**Won/Lost:**
- [Deal] -- [outcome]

**Weighted pipeline value:** [calculated value or "Insufficient data -- most values TBD"]

**1-2 pipeline actions this week:**
- [Action 1]
- [Action 2]

---

## Tribe State

- Overall operational state: [assessment]
- Any signals of drift to address
- Any wins to acknowledge

---

## Content Pulse

| Metric | Target | Actual | Status |
|--------|--------|--------|--------|
| LinkedIn posts | 3/week | [N] | [On track / Behind] |
| Intelligence Briefing newsletter | 1/week | [N] | [On track / Behind] |

[If behind cadence, note specific gap and suggested remedy.]

---

## This Week's Top 3 Priorities

1. [Priority 1 -- outcome + owner]
2. [Priority 2 -- outcome + owner]
3. [Priority 3 -- outcome + owner]

---

## Course Corrections

[Any adjustments to make to heading or operational states]

---

## Suggested Skills This Week

[Which /skills from the skill army are most relevant this week, and why]

---

## Context Refresh Prompt

[If any context/ files have "Last verified" dates >14 days old:]

These context files need refreshing:
- `context/[file].md` -- last verified [date] ([N] days ago)
- ...

Want me to draft updates for these files?

[If all files are current: "All context files verified within the last 14 days."]

---

## Knowledge Capture

- Any decisions this week that warrant a `/deep-think` session?
- Any insights worth capturing? (`/odin log` for CEO; `/zk add` to the knowledge base)
```

---

## Phase 3.5 -- Odin Reflection Cadence (CEO workspace only)

Existence guard FIRST: run the Bash test `test -d knowledge/odin-brain && test -f scripts/odin-cadence.py`. If it exits non-zero, silently skip this ENTIRE phase - say nothing about it. On an exec workspace both are absent, so this phase is a provable no-op. (This is a Bash test the model runs, NOT a Python code guard; the graceful-degradation precedent is `/odin` SKILL.md Rules 9 and 13 - degrade quietly, never fail the operation.)

If the guard passes:

1. Run `python3 scripts/odin-cadence.py --json`. Treat any non-zero exit, empty output, or unparseable JSON as "no clusters - skip silently".
2. Parse `reflect_clusters` from the JSON. If `reflect_clusters >= 1` AND `knowledge/odin-brain/.last-reflect` is absent or its date is older than the current week, surface to the CEO: "N episode cluster(s) ready to mature - run `/odin reflect`?"
3. ONLY on an explicit CEO go-ahead, invoke `/odin reflect` via the Skill tool. Reflect's own per-graduation CEO gate then applies - nothing enters the brain without per-candidate approval, and reflect advances `.last-reflect` on a confirmed pass.

NEVER auto-invoke `/odin reflect` without an explicit CEO go-ahead. NEVER run this phase on a workspace that lacks the brain.

---

## Phase 4 -- Save Review

1. Ensure `outputs/operations/reviews/` directory exists (create if needed).
2. Save the completed review to:
   `outputs/operations/reviews/YYYY-MM-DD-weekly-review.md`
   where `YYYY-MM-DD` is the `week_of` date.
3. Confirm save path to Misha.

---

## Voice & Style

- Use maritime vocabulary: Sea State, Heading, Course Corrections, Drift.
- Hyphens, not em dashes.
- ODUN.ONE (always stylized).
- Tribe (never "team").
- Be direct. No filler. Every line should be actionable or diagnostic.
- Numbers are precise (Voss: $347,850 not $350,000).

## NEVER

- Fabricate pipeline data. If a deal's status is unclear, say so.
- Skip the continuity check. Week-over-week tracking is the backbone.
- Produce more than 3 pages. Compress ruthlessly.
- Use "team" instead of "Tribe."
- Use em dashes. Use hyphens.
