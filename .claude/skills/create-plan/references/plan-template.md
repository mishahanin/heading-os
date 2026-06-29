# Create-Plan — Plan Document Template

Consumed by: `.claude/skills/create-plan/SKILL.md` (Plan Format).
Last Updated: 2026-06-16

The exact structure every plan document must follow. The SKILL body holds the research
phase, quality standards, and report steps; this file holds the fill-in template and the
wave-grouping rules so the body stays under the inline budget. Spec Core field definitions
and the load-bearing test live in `reference/templates/spec-core.md`.

## Plan structure

Write the plan using this exact structure:

```markdown
# Plan: <descriptive title>

**Created:** <YYYY-MM-DD>
**Status:** Draft
**Request:** <one-line summary of what was requested>

---

## Overview

### What This Plan Accomplishes

<2-3 sentences describing the end result and why it matters>

### Why This Matters

<Connect this change to the project's goals or mission. How does this add value?>

---

## Spec Core

The five-field kernel this plan resolves against (definitions + the load-bearing test: `reference/templates/spec-core.md`). Keep it tight — the sections below expand on it; they do not contradict it.

- **Why:** <one line naming the force behind this work>
- **Capabilities:** <`CAP-1` | intent (what, not how) | success (a testable/demonstrable criterion); add `CAP-N` as needed>
- **Constraints:** <non-negotiables that bend the design; if it rules nothing out, drop it>
- **Non-Goals:** <at least one explicit out-of-scope item — mandatory>
- **Success Signal:** <one testable observable that defines done, not an aspiration>

---

## Current State

### Relevant Existing Structure

<List files, folders, or patterns that exist and relate to this change>

### Gaps or Problems Being Addressed

<What's missing, broken, or suboptimal that this plan fixes?>

---

## Proposed Changes

### Summary of Changes

<Bulleted list of all changes at a high level>

### New Files to Create

<List each new file with its full path and one-line description of purpose>

| File Path         | Purpose                            |
| ----------------- | ---------------------------------- |
| `path/to/file.md` | Description of what this file does |

### Files to Modify

<List each file being modified and summarize the changes>

| File Path         | Changes                      |
| ----------------- | ---------------------------- |
| `path/to/file.md` | Description of modifications |

### Files to Delete (if any)

<List any files being removed and why>

---

## Design Decisions

### Key Decisions Made

<List important design choices and the reasoning behind them>

1. **<Decision>**: <Rationale>
2. **<Decision>**: <Rationale>

### Alternatives Considered

<What other approaches were considered and why they were rejected?>

### Open Questions (if any)

<List any decisions that need user input before implementation>

---

## Step-by-Step Tasks

Execute these tasks in order during implementation.

**Optional: Wave grouping for parallel execution**

When a plan contains tasks that can run independently (touching different files with no shared dependencies), group them under wave headers:

- `### Wave N (parallel)` - tasks within this wave execute simultaneously via `/implement`
- `### Wave N` - tasks within this wave execute sequentially (useful for grouping)
- Tasks within waves use `####` headings instead of `###`
- Step numbering stays global across all waves
- Plans without wave headers execute sequentially as normal
- A plan must use either all-wave format or all-sequential format. Do not mix `### Wave` headers with bare `### Step` headers.

Only use waves when tasks are genuinely independent. If two tasks modify the same file or one depends on the other's output, they must be in different waves (dependent task in the later wave).

**Wave example:**

```
### Wave 1 (parallel)

#### Step 1: Create user model
**Files affected:** scripts/models/user.py

#### Step 2: Create product model
**Files affected:** scripts/models/product.py

### Wave 2

#### Step 3: Build integration test
**Files affected:** tests/test_integration.py (depends on Steps 1, 2)
```

### Step 1: <Task Title>

<Detailed description of what to do>

**Actions:**

- <Specific action>
- <Specific action>

**Files affected:**

- `path/to/file.md`

---

### Step 2: <Task Title>

<Detailed description of what to do>

**Actions:**

- <Specific action>
- <Specific action>

**Files affected:**

- `path/to/file.md`

---

<Continue with as many steps as needed. Be thorough. Include:>
<- Creating new files (with full content specifications)>
<- Modifying existing files (with before/after or specific edits)>
<- Updating cross-references>
<- Testing/validation steps>

---

## Connections & Dependencies

### Files That Reference This Area

<List any files that link to or depend on areas being changed>

### Updates Needed for Consistency

<List any documentation, references, or related files that need updating>

### Impact on Existing Workflows

<Describe how this affects existing commands, outputs, or processes>

---

## Validation Checklist

How to verify the implementation is complete and correct:

- [ ] <Verification step — e.g., "New skill runs without errors">
- [ ] <Verification step — e.g., "Output files created in correct location">
- [ ] <Verification step — e.g., "CLAUDE.md updated to reflect new structure">
- [ ] <Verification step — e.g., "Cross-references updated and valid">

---

## Success Criteria

The implementation is complete when:

1. The Spec Core **Success Signal** is observably met: <restate it here as a checkable outcome>
2. <Specific, measurable criterion>
3. <Specific, measurable criterion>

---

## Notes

<Any additional context, future considerations, or related ideas that might be useful>
```
