---
name: checkpoint
description: "Сохранить manual checkpoint текущей сессии в outputs/operations/handoff-archive/ без выполнения /compact. Используй когда хочешь зафиксировать состояние работы и иметь возможность вернуться позже с чистым контекстом. NEVER auto-trigger - вызывается ТОЛЬКО явной командой /checkpoint."
allowed-tools: "Write, Read, Bash(date:*), Bash(python scripts/checkpoint-paths.py:*), Bash(python3 scripts/checkpoint-paths.py:*)"
disable-model-invocation: true
argument-hint: "[заметка] | auto on | auto off | auto status"
metadata:
  author: Misha Hanin
  email: misha.hanin@odinix.com
  version: "1.2"
x-heading-orchestration:
  parallel_safe: false
  shared_state: ["outputs/operations/handoff-archive/", ".claude/state/"]
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
    checkpoint-offer hook fires at the soft/hard context thresholds. For
    reflective end-of-session capture use /calibrate; for cross-session memory
    consolidation use /dream.
x-heading-routing:
  category: Operations
  label: /checkpoint [note]
  triggers:
    - NEVER auto-trigger. Explicit `/checkpoint [optional note]` only. Saves manual session handoff to `outputs/operations/handoff-archive/`, scoped to this session, without running /compact. Surfaces from the two-tier checkpoint-offer hook at the soft/hard thresholds (`CLAUDE_HANDOFF_SOFT_THRESHOLD` / `CLAUDE_HANDOFF_HARD_THRESHOLD`).
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
- Updates FOUR pointer files: this session's pair under
  `.latest/{session-slug}/{summary.md,prompt.md}`, which the SessionStart inject
  hook reads, and the shared pair at `.latest/{summary.md,prompt.md}`, which
  `/next` reads as "the newest handoff in this workspace"
- Does NOT run `/compact`
- Does NOT clear the session
- Does NOT continue implementation after writing - wait for the user to direct next action

Every path is keyed by session id. Several sessions run on this workspace at
once, and a shared pointer is last-writer-wins: before 2026-08-16 a resumed
session could be injected another session's handoff.

## When to use

- Soft / hard checkpoint offer fired (`Stop` hook surfaced the context threshold)
- About to switch to an unrelated task, want resume-ready snapshot
- Mid-implementation, want to save state before risky action
- Long session approaching natural pause point

## Procedure

### Step 0 - Handle the `auto` argument first

If `$ARGUMENTS` starts with `auto`, this is a switch and not a checkpoint.

Run one of these, then stop. Do not write any file.

```bash
python scripts/checkpoint-paths.py --auto on
python scripts/checkpoint-paths.py --auto off
python scripts/checkpoint-paths.py --auto status
```

Report the command output in one line. For `auto on`, continue to Step 1 and
write the checkpoint as well, because the operator asked at a threshold and
expects this one to be saved.

The switch applies to this session only. It overrides `CLAUDE_HANDOFF_AUTO` in
both directions. It needs no cleanup, because the state file is keyed by session
and pruned with the session.

### Step 1 - Get this session's paths

One command prints all of them:

```bash
python scripts/checkpoint-paths.py
```

It emits `key=value` lines: `stamp`, `archive`, `summary_pointer`,
`prompt_pointer`, `shared_summary_pointer`, `shared_prompt_pointer`,
`session_id`. Archive paths are data-root-relative, which is the form the
`@`-reference resolves. Use them verbatim - never rebuild a path by hand, and
never write into another session's pointer directory.

If the script is unavailable, fall back to `date -u +'%Y-%m-%d-%H%M%S'` for the
stamp and `echo "$CLAUDE_CODE_SESSION_ID"` for the session id, then take the
slug as the first 32 characters of that id.

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

### Step 3 - Update the pointer files

Write the same two texts to two places each: the `summary_pointer` /
`prompt_pointer` paths from Step 1 (this session's, the ones that get injected)
and the `shared_summary_pointer` / `shared_prompt_pointer` paths (the workspace's
newest, which `/next` reads). Four files.

The pointer files almost always already exist from a prior checkpoint, and the `Write` tool refuses to overwrite a file that has not been `Read` first in the current session - skipping the read produces `Error writing file`. So for each pointer file below: first `Read` it (ignore a not-found result on a first-ever checkpoint), then `Write` it.

Keep the summary short. It is what gets injected on resume, and the inject hook
truncates at 8000 characters, so anything past that is written and never read.

`Read` then `Write` the summary text to `summary_pointer` AND `shared_summary_pointer`:

```markdown
# Latest handoff summary

Source: outputs/operations/handoff-archive/{archive filename}
Generated: {ISO UTC timestamp}
Trigger: manual-checkpoint

{Copy the "Objective" + "Current state" + "Next steps" sections from the archive file. Keep it short - this is what gets injected on resume.}
```

`Read` then `Write` the continuation prompt to `prompt_pointer` AND `shared_prompt_pointer`:

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

After writing all five files, reply with:

1. Full path of the archive file written
2. Confirmation that this session's pointer pair and the shared pair were updated
3. One-line current state summary
4. Recommendation: "Run `/compact` manually if you want to free context now. Otherwise nothing else happens - checkpoint is preserved for resume."

Do NOT continue implementation, do NOT call `/compact`, do NOT clear the session. Wait for the user's next instruction.

## Auto mode

Auto mode makes the Stop hook run this skill's procedure with no prompt at each
threshold. The SessionStart hook then resumes the task by itself. Auto mode is
OFF by default.

Nothing here triggers compaction. A hook cannot start a compaction, so Claude
Code's own auto-compact still decides when the context is freed. Auto mode only
guarantees that the checkpoint lands first.

**For one session,** flip the switch while you work:

```bash
python scripts/checkpoint-paths.py --auto on
```

The threshold offer names this switch as an option, so you can choose it from
the list. The choice belongs to the current window and dies with it.

**For the whole workspace,** set the environment default in
`.claude/settings.local.json`:

```json
"env": {
  "CLAUDE_HANDOFF_AUTO": "1",
  "CLAUDE_HANDOFF_SOFT_THRESHOLD": "40",
  "CLAUDE_HANDOFF_HARD_THRESHOLD": "45",
  "CLAUDE_AUTOCOMPACT_PCT_OVERRIDE": "50"
}
```

Keep the compaction percentage ABOVE the soft threshold. The checkpoint then
always lands before compaction frees the context.

## NEVER

- Never invoke `/compact` automatically as part of this skill
- Never clear context as part of this skill
- Never proceed with the next task unless the user explicitly says so after seeing the checkpoint report
- Never skip the pointer file updates - without them, the inject hook has nothing to surface
- Never write into another session's pointer directory
- Never write anything into a pointer directory OTHER than `summary.md` and `prompt.md`
