---
name: create-plan
description: Design a structured implementation plan for a non-trivial change before any code is written - objective, scope, critical files, step sequence, success criteria, and risks. Use before significant additions or multi-file work. Trigger when the user says "create plan", "plan for [change]", or "design the approach". Do NOT use to execute an existing plan (use /implement).
metadata:
  author: Misha Hanin
  email: misha.hanin@odinix.com
  version: "1.2"
argument-hint: "[describe what to plan]"
allowed-tools: "Read, Glob, Grep"
x-heading-orchestration:
  parallel_safe: false
  shared_state:
    - plans/
  triggers:
    - create plan
    - plan for
    - design the approach
x-heading-capability:
  what: >
    Researches the workspace and produces a thorough implementation plan
    document - Spec Core, current state, proposed changes, step-by-step tasks,
    and success criteria - ready for /implement to execute without ambiguity.
  how: >
    Run /create-plan [describe what to plan]. Writes the plan to
    plans/YYYY-MM-DD-{name}.md; it plans only and never makes the changes.
  when: >
    Use before any significant or structural workspace change. To carry the
    plan out use /implement; to stress-test it before approval use /scrutinize.
x-heading-routing:
  category: Operations
  triggers:
    - create plan
    - plan for [change]
    - design the approach
  exclusions:
    - Execute plan -> /implement
  compound: 'No'
  router: auto
---
# Plan

Create a detailed implementation plan for changes to this workspace. Plans are thorough documents that capture the full context, rationale, and step-by-step tasks needed to execute a change with complete alignment across the project.

## Variables

request: $ARGUMENTS (describe what you want to plan — new command, new workflow, structural change, template update, etc.)

---

## Instructions

- **IMPORTANT:** You are creating a PLAN, not implementing changes. Research thoroughly, think deeply, then output a comprehensive plan document.
- Use your reasoning capabilities to think hard about the request, workspace structure, and best approach.
- Research the workspace to understand existing patterns, conventions, and how this change fits.
- Create the plan in the `plans/` directory with filename: `YYYY-MM-DD-{descriptive-name}.md`
  - Use today's date
  - Replace `{descriptive-name}` with a short, kebab-case name (e.g., "add-guest-research-command", "restructure-outputs", "create-outreach-workflow")
- Fill out every section of the Plan Format below. Replace all `<placeholders>` with specific, actionable content.
- Be thorough — this plan will be executed by `/implement` and needs enough detail to execute without ambiguity.
- Follow existing patterns. Study similar files in the workspace before proposing new structures.

---

## Research Phase

Before writing the plan, investigate:

1. **Read core reference files:**
   - `CLAUDE.md` — workspace overview
   - `context/` — background context on the user and project

2. **Explore relevant areas:**
   - If creating a skill: read existing skills in `.claude/skills/`
   - If modifying outputs: explore `outputs/` structure and examples
   - If updating templates: check `reference/` for existing patterns
   - If adding scripts: review `scripts/` for conventions

3. **Understand connections:**
   - How does this change relate to existing workflows?
   - What files reference or depend on areas being changed?
   - Are there naming conventions to follow?

---

## Plan Format

Write the plan using the **exact structure in `references/plan-template.md`** — a fill-in template with these top-level sections in order: title + `Created`/`Status`/`Request` header; Overview (What This Plan Accomplishes + Why This Matters); **Spec Core** (Why, Capabilities, Constraints, Non-Goals, Success Signal — definitions in `reference/templates/spec-core.md`); Current State; Proposed Changes (with New/Modify/Delete file tables); Design Decisions (incl. Open Questions); Step-by-Step Tasks; Connections & Dependencies; Validation Checklist; Success Criteria; Notes.

The reference also carries the **wave-grouping rules** for parallel execution (`### Wave N (parallel)` vs `### Wave N`, `####` step headings, global step numbering, no mixing wave and bare-step formats) and a wave example. Read it before writing the plan and reproduce the structure verbatim, replacing every `<placeholder>` with specific content.

---

## Quality Standards

- **Completeness:** Every section filled out with specific content, no generic placeholders left
- **Spec Core complete:** Non-Goals lists at least one item; the Success Signal is a single testable observable, not a vague aspiration (see `reference/templates/spec-core.md`)
- **Actionability:** Steps are detailed enough that `/implement` can execute without asking questions
- **Consistency:** Follows existing workspace patterns and naming conventions
- **Clarity:** Someone unfamiliar with the project could understand and execute the plan
- **Traceability:** Changes are connected back to goals and rationale

---

## Report

After creating the plan:

1. Provide a brief summary of what the plan covers
2. List any open questions that need user input before implementation
3. Provide the full path to the plan file: `plans/YYYY-MM-DD-{name}.md`
4. Remind user to run `/implement plans/YYYY-MM-DD-{name}.md` to execute
5. Offer a pre-approval scrutiny: `"Plan ready. Run /scrutinize before approval? (recommended for high-stakes or structural plans)"`

---

## Voice

- The plan is a working document, not a pitch. Concrete and specific over polished.
- Every step traces to a Spec Core capability; no speculative scope.
- Use hyphens (`-`), never double dashes (`--`); ODUN.ONE and DPI+ styled correctly.

## NEVER

- Never implement changes from this skill — it plans only; execution is `/implement`.
- Never leave a `<placeholder>` unfilled or a section generic; an ambiguous plan blocks `/implement`.
- Never write the plan with an empty Non-Goals or a vague (non-testable) Success Signal.
- Never invent files, paths, or conventions — research the workspace first and follow what exists.
