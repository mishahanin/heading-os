---
name: checkpoint
description: "Сохранить manual checkpoint текущей сессии в outputs/operations/handoff-archive/ без выполнения /compact. Используй когда хочешь зафиксировать состояние работы и иметь возможность вернуться позже с чистым контекстом. NEVER auto-trigger - вызывается ТОЛЬКО явной командой /checkpoint."
allowed-tools: "Write, Read, Bash(date:*)"
disable-model-invocation: true
argument-hint: "[опциональная заметка к checkpoint]"
metadata:
  author: Misha Hanin
  email: misha.hanin@odinix.com
  version: "1.0"
x-heading-orchestration:
  parallel_safe: false
  shared_state: ["outputs/operations/handoff-archive/", ".claude/state/checkpoint-state.json"]
  triggers: []
x-heading-capability:
  what: >
    Writes a manual session handoff (objective, current state, files touched,
    next steps, continuation prompt) to outputs/operations/handoff-archive/ so
    the session can be resumed later with clean context.
  how: >
    Explicit invocation only - type /checkpoint [optional note]; never
    auto-triggers. It writes one archive file plus updates the .latest/ pointer
    files the SessionStart inject hook reads, and does NOT run /compact or clear
    the session.
  when: >
    Use before switching tasks, before a risky action, or when the
    checkpoint-offer hook fires at 25/30 percent context. For reflective
    end-of-session capture use /calibrate; for cross-session memory consolidation
    use /dream.
x-heading-routing:
  category: Operations
  label: /checkpoint [note]
  triggers:
    - NEVER auto-trigger. Explicit `/checkpoint [optional note]` only. Saves manual session handoff to `outputs/operations/handoff-archive/` without running /compact. Surfaces from the two-tier checkpoint-offer hook at 25%/30% used context.
  exclusions:
    - Auto-resume after /compact handled by checkpoint-save.py (PostCompact)
    - reflective end-of-session -> /calibrate
    - cross-session memory consolidation -> /dream
  compound: 'No'
  router: manual
---

# /checkpoint

Save a manual session checkpoint without running `/compact` or clearing context.

## What this does

- Writes ONE combined handoff file to `outputs/operations/handoff-archive/`
- Updates pointer files at `outputs/operations/handoff-archive/.latest/{summary.md,prompt.md}` so the SessionStart inject hook picks them up next time
- Does NOT run `/compact`
- Does NOT clear the session
- Does NOT continue implementation after writing - wait for the user to direct next action

## When to use

- Soft / hard checkpoint offer fired (`Stop` hook surfaced 25%/30% threshold)
- About to switch to an unrelated task, want resume-ready snapshot
- Mid-implementation, want to save state before risky action
- Long session approaching natural pause point

## Procedure

### Step 1 - Determine archive paths

Use UTC timestamp `YYYY-MM-DD-HHMMSS`. Get it from:

```bash
date -u +'%Y-%m-%d-%H%M%S'
```

Build the archive filename:

```
outputs/operations/handoff-archive/{stamp}_handoff_manual_{session-slug}.md
```

Where `session-slug` is a short safe slug from the current session id (or "session" if not derivable). Example:

```
outputs/operations/handoff-archive/2026-05-25-143052_handoff_manual_a4b2c1.md
```

### Step 2 - Write the combined handoff file

The file MUST contain these sections in order. Keep prose concise - this is for resume, not a report:

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

### Step 3 - Update pointer files

The pointer files almost always already exist from a prior checkpoint, and the `Write` tool refuses to overwrite a file that has not been `Read` first in the current session - skipping the read produces `Error writing file`. So for each pointer file below: first `Read` it (ignore a not-found result on a first-ever checkpoint), then `Write` it.

`Read` then `Write` `outputs/operations/handoff-archive/.latest/summary.md`:

```markdown
# Latest handoff summary

Source: outputs/operations/handoff-archive/{archive filename}
Generated: {ISO UTC timestamp}
Trigger: manual-checkpoint

{Copy the "Objective" + "Current state" + "Next steps" sections from the archive file. Keep it short - this is what gets injected on resume.}
```

`Read` then `Write` `outputs/operations/handoff-archive/.latest/prompt.md`:

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

### Step 4 - Respond to the user

After writing all three files, reply with:

1. Full path of the archive file written
2. Confirmation that `.latest/summary.md` and `.latest/prompt.md` updated
3. One-line current state summary
4. Recommendation: "Run `/compact` manually if you want to free context now. Otherwise nothing else happens - checkpoint is preserved for resume."

Do NOT continue implementation, do NOT call `/compact`, do NOT clear the session. Wait for the user's next instruction.

## NEVER

- Never invoke `/compact` automatically as part of this skill
- Never clear context as part of this skill
- Never proceed with the next task unless the user explicitly says so after seeing the checkpoint report
- Never skip the pointer file updates - without them, the inject hook has nothing to surface
- Never write to `outputs/operations/handoff-archive/.latest/` archive subdirectory anything OTHER than `summary.md` and `prompt.md`
