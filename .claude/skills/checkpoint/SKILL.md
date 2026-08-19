---
name: checkpoint
description: "Сохранить manual checkpoint текущей сессии в outputs/operations/handoff-archive/ без выполнения /compact. Используй когда хочешь зафиксировать состояние работы и иметь возможность вернуться позже с чистым контекстом. NEVER auto-trigger - вызывается ТОЛЬКО явной командой /checkpoint."
allowed-tools: "Write, Read, Bash(date:*), Bash(python scripts/checkpoint-paths.py:*), Bash(python3 scripts/checkpoint-paths.py:*)"
argument-hint: "[заметка] | auto on|off|status | unattended on|off|status"
metadata:
  author: Misha Hanin
  email: misha.hanin@odinix.com
  version: "1.5"
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
    files the SessionStart inject hook reads. The skill itself never runs
    /compact. In auto or unattended mode the Stop hook asks the harness to
    compact after the archive lands, from outside, through HERDR.
  when: >
    Use before switching tasks, before a risky action, or when the
    checkpoint-offer hook fires at the soft/hard context thresholds. For
    reflective end-of-session capture use /calibrate; for cross-session memory
    consolidation use /dream.
x-heading-routing:
  category: Operations
  label: /checkpoint [note]
  triggers:
    - NEVER auto-trigger. Explicit `/checkpoint [optional note]` only. Saves manual session handoff to `outputs/operations/handoff-archive/`, scoped to this session, without running /compact. Surfaces from the two-tier checkpoint-offer hook at the soft/hard thresholds (`CLAUDE_HANDOFF_SOFT_THRESHOLD` / `CLAUDE_HANDOFF_HARD_THRESHOLD`). Also carries two session switches. `auto on|off|status` makes the save silent and lets the Stop hook drive the compaction itself, through HERDR, once the handoff is on disk. `unattended on|off|status` adds continuing at a pause after a shown 60-second countdown instead of halting, and it already includes `auto`. Only the stall and ceiling fuses lower the mode; the assistant never does, and the status line shows its state on every render.
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

The system keys every path by session id. Several sessions run on this workspace
at once, and a shared pointer is last-writer-wins. Before 2026-08-16 the inject
hook could hand a resumed session another session's handoff.

## When to use

- Soft / hard checkpoint offer fired (`Stop` hook surfaced the context threshold)
- About to switch to an unrelated task, want resume-ready snapshot
- Mid-implementation, want to save state before risky action
- Long session approaching natural pause point

## Procedure

### Step 0 - Handle a switch argument first

Split `$ARGUMENTS` on whitespace. If the FIRST token is exactly `auto` or exactly
`unattended`, this is a switch. It is not a checkpoint. Anything else is a note,
including a word that merely begins with one of them.

Match the whole token, never a prefix. The rule read "starts with `auto`" until
2026-08-19. On that day `/checkpoint autocompact fires at 584k` matched `auto
on`. The skill dropped the note, wrote no file, and switched a mode instead. A
prefix match cannot tell a switch from a subject.

Run one of these, then stop. Do not write any file.

```bash
python scripts/checkpoint-paths.py --auto on
python scripts/checkpoint-paths.py --auto off
python scripts/checkpoint-paths.py --auto status
python scripts/checkpoint-paths.py --unattended on
python scripts/checkpoint-paths.py --unattended off
python scripts/checkpoint-paths.py --unattended status
```

Report the command output in one line. For `auto on`, continue to Step 1 and
write the checkpoint as well. The operator asked at a threshold and expects this
one on disk. For `unattended on`, stop after the report. The operator is about to
leave, so a question here defeats the switch.

Both switches apply to this session only. Each one overrides its own environment
default in both directions. Neither needs cleanup. The state file carries a
session key, and the pruner removes it with the session.

### Step 1 - Get this session's paths

One command prints all of them. Add `--kind auto` when the Stop hook asked for
this save rather than the operator. The flag names the archive `_handoff_auto_`.
That name is the only thing that separates a save the system needed from one the
operator chose. The driven compaction looks for exactly that kind and does
nothing without it.

```bash
python scripts/checkpoint-paths.py
```

It emits `key=value` lines: `session_id`, `session_slug`, `stamp`,
`project_root`, `data_root`, `archive`, `summary_pointer`, `prompt_pointer`,
`shared_summary_pointer`, `shared_prompt_pointer`, `state`. Archive paths are data-root-relative, which is the form the
`@`-reference resolves. Use them verbatim - never rebuild a path by hand, and
never write into another session's pointer directory.

If the script is unavailable, get the stamp from `date -u +'%Y-%m-%d-%H%M%S'`.
Get the session id from `echo "$CLAUDE_CODE_SESSION_ID"`. Take the slug as the
first 32 characters of that id.

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

Write the same two texts to two places each. Four files in total.

- `summary_pointer` and `prompt_pointer` from Step 1 hold this session's pair.
  The inject hook reads only these two.
- `shared_summary_pointer` and `shared_prompt_pointer` hold the workspace's
  newest handoff. `/next` reads these two.

A prior checkpoint almost always left these four files in place. The `Write`
tool refuses to overwrite a file that you did not `Read` first in this session,
and it answers with `Error writing file`.

So for each pointer file below, `Read` it and then `Write` it. On a first-ever
checkpoint, ignore the not-found result of the `Read`.

Keep the summary short. The inject hook truncates at 8000 characters, so it
writes anything past that and no session ever reads it.

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
2. Confirmation that you updated this session's pointer pair and the shared pair
3. One-line current state summary
4. Recommendation: "Run `/compact` manually if you want to free context now. Otherwise nothing else happens - the checkpoint stays on disk for resume."

Do NOT continue implementation, do NOT call `/compact`, do NOT clear the session. Wait for the user's next instruction.

## Auto mode

Auto mode makes the Stop hook run this skill's procedure with no prompt. It
fires once per 5% band, not at every pause, because the same hysteresis that
governs the question governs the silent save. After a compaction, the
SessionStart hook then tells the session to continue the unfinished task; that
instruction is printed only when auto is on. Auto mode is OFF by default.

Nothing here triggers compaction. A hook cannot start a compaction, so Claude
Code's own auto-compact still decides when to free the context. Auto mode only
guarantees that the checkpoint lands first.

**For one session,** flip the switch while you work:

```bash
python scripts/checkpoint-paths.py --auto on
```

The threshold offer no longer gives this switch a numbered option. It names the
switch only for the operator who stays at the keyboard, because `unattended`
below is the better answer for the operator who leaves. Auto mode keeps one
property that `unattended` gives up: it hands the turn back immediately, with no
wait. The choice belongs to the current window and dies with it.

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

## Unattended mode

Unattended mode decides what happens when the session pauses and nobody answers.
It is a SEPARATE switch from `auto`, and it is OFF by default.

```bash
python scripts/checkpoint-paths.py --unattended on
```

The threshold offer names this switch as its one standing option, because it is
the only command you need: `--unattended on` already sets `auto`.

Above the soft threshold, the Stop hook then waits instead of asking. The wait
shows a countdown in the HERDR label, so a still terminal is visibly a wait and
not a hang. Type anything inside it, and the turn goes back to you within one
poll. Stay silent for the whole wait, and the hook tells the assistant to carry
on.

Use it for work that runs past the time you are at the keyboard: overnight, or
across a weekend. Turn unattended off and `auto` returns to whatever it was
before, including to having been unset.

Two sentences here were false until 2026-08-19 and are corrected rather than
deleted, because the correction is the point. The Stop hook DOES write in this
mode: once per hysteresis bucket at or above the hard threshold. And a hook CAN
trigger a compaction, just not from inside Claude Code.

The harness exposes no internal way to compact on demand, so the hook does it
from outside. Above the hard threshold, and once your handoff is on disk, the
hook submits the literal text `/compact` to this session's own pane. It reaches
that pane through HERDR, the terminal manager that hosts it. The harness then
parses the text exactly as if you had typed it.

If HERDR is not hosting the session, none of that happens and Claude
Code's own auto-compact frees the context instead. The offer tells you which of
those two it is, and says so plainly when it could not find out.

The switch belongs to the operator. The assistant never lowers it.

The status line names its state on every render: `⏵ unattended`, `⏵ auto`, or
`⏸  manual`. Read it there. Turn the mode off yourself when you want the window
back:

```bash
python scripts/checkpoint-paths.py --unattended off
```

This rule replaced "turn it off when the work is finished" on 2026-08-19. That
instruction defeated itself. Work reaches the operator's decision at the end of
nearly every stretch. So the switch went down each time before the hook wrote any
`_handoff_auto_` file. The compaction path needs two conditions at once: the
switch up, and that file on disk. The mechanism ran twice in one session. It
compacted zero times.

**Nothing lowers the switch except you.** The bounds below stop a stretch. They
hand the turn back and leave the mode on, so your next instruction resumes it.

### How a night ends

The assistant declares the plan finished. This is the primary signal, and it is
explicit. Run this at the end of the work:

```bash
python scripts/checkpoint-paths.py --done "plan X: 7 of 7 items"
```

The Stop hook reads session state. It never reads prose. So an assistant that
writes "the work is finished" and stops has told the mechanism nothing, and the
next pause continues it again. The continuation message names this command at
every pause for that reason.

Your next instruction clears the marker on its own. You do not run a command to
resume.

The ceiling is the backstop, for the case where the marker never arrives. It
stops a stretch after 100 continuations. Measure one night before you move that
number with `CLAUDE_HANDOFF_UNATTENDED_MAX`. No run has yet reached the end of
its work, so the count a real night needs is unknown.

A fingerprint heuristic held this job until 2026-08-19. It watched the files the
session wrote, and it called three unchanged readings a finished plan. It cannot
tell a finished plan from a night of reading and thinking. It stopped all three
unattended runs ever attempted, at three and five continuations. None came near
the ceiling.

**A stopped stretch is silent by design.** The hook records why it stopped, and
when, in the session state. It also sends one Telegram notice when you configured
a target. Read that state with `--unattended status`.

The mode stays quiet whenever something else already drives the Stop event.
Three signals claim it. A scheduled `/loop` wakeup claims it. In-flight
background work claims it. A ralph-loop that names this session claims it.

`/goal` is the one case the mode cannot see. The harness holds that state in
memory, so no hook reaches it. Claude Code limits the cost itself. After the
goal's own hook blocks once, `stop_hook_active` suppresses this hook for the
rest of the turn.

Environment defaults, for the whole workspace rather than one session:

```json
"env": {
  "CLAUDE_HANDOFF_UNATTENDED": "1",
  "CLAUDE_HANDOFF_UNATTENDED_WAIT": "10",
  "CLAUDE_HANDOFF_UNATTENDED_POLL": "2",
  "CLAUDE_HANDOFF_UNATTENDED_MAX": "100"
}
```

The wait costs you time on every pause of a long run. Sixty seconds against fifty
pauses is fifty minutes of a night spent waiting. This workspace runs 10 seconds.
You lose no control: Claude Code queues anything you type at any moment, and the
hook reads that queue.

**The wait is clamped at 60 seconds, whatever you set.** Claude Code discards the
output of a hook that times out. A wait at or above the registered timeout
therefore loses the continuation in silence. The shipped registration allows 90
seconds, and the clamp leaves room for the work that follows the wait. Raise the
registration first if you need a longer grace period.

## NEVER

- Never invoke `/compact` automatically as part of this skill
- Never clear context as part of this skill
- Never proceed with the next task unless the user explicitly says so after seeing the checkpoint report
- Never skip the pointer file updates - without them, the inject hook has nothing to surface
- Never write into another session's pointer directory
- Never write anything into a pointer directory OTHER than `summary.md` and `prompt.md`
