# Checkpoint - handoff file and pointer file templates

Consumed by: `.claude/skills/checkpoint/SKILL.md`, Step 2 (the archive file) and
Step 3 (the four pointer files). Read this file before you write either.

Last Updated: 2026-08-20

---

## The archive file

Write this to the `archive` path that `scripts/checkpoint-paths.py` printed. The
file MUST contain these sections in this order. Keep the prose concise - this is
for resume, not a report.

```markdown
# Handoff - manual checkpoint

Generated: {ISO UTC timestamp}
Trigger: manual-checkpoint
Session: {session id if known}

## Objective

What is the current task aiming to accomplish? One paragraph.

## Acceptance criteria

How will we know the task is done? Bullet list.

## Constraints

What boundaries, deadlines, dependencies, or invariants must hold? Bullet list.

## Decisions

Key choices made so far in this session, with one-line reasoning. Bullet list.

## Files touched / inspected

Absolute or workspace-relative paths grouped by role (read / written / planned). Bullet list.

## Current state

Where are we right now? What is the last action completed? One paragraph.

## Commands / tests

Commands that should be re-run on resume (tests, validators, manual checks). Bullet list.

## Open issues

Known problems, blockers, or questions awaiting answers. Bullet list.

## Next steps

The exact next 1-3 actions to take when resuming. Ordered list.

## Continuation prompt

Continue this Claude Code session from the saved handoff.

First read:

@outputs/operations/handoff-archive/{stamp}_handoff_manual_{session-slug}.md

Then continue the latest unfinished task.

Rules:
1. Treat repository state as authoritative.
2. Do not redo broad discovery unless the summary is insufficient.
3. Before making changes, briefly restate the current objective, constraints, files involved, and next concrete action.
4. Continue implementation from the current repo state.

## User note

$ARGUMENTS
```

If `$ARGUMENTS` is empty, omit the "User note" section.

## The summary pointer text

Write this same text to `summary_pointer` AND `shared_summary_pointer`.

```markdown
# Latest handoff summary

Source: outputs/operations/handoff-archive/{archive filename}
Generated: {ISO UTC timestamp}
Trigger: manual-checkpoint

{Copy the "Objective" + "Current state" + "Next steps" sections from the archive file. Keep it short - this is what gets injected on resume.}
```

## The continuation prompt pointer text

Write this same text to `prompt_pointer` AND `shared_prompt_pointer`.

```
Continue this Claude Code session from the saved handoff.

First read:

@outputs/operations/handoff-archive/{archive filename}

Then continue the latest unfinished task.

Rules:
1. Treat repository state as authoritative.
2. Do not redo broad discovery unless the summary is insufficient.
3. Before making changes, briefly restate the current objective, constraints, files involved, and next concrete action.
4. Continue implementation from the current repo state.
```
