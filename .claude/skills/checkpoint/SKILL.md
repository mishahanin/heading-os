---
name: checkpoint
description: "Сохранить manual checkpoint текущей сессии в outputs/operations/handoff-archive/ без выполнения /compact. Используй когда хочешь зафиксировать состояние работы и иметь возможность вернуться позже с чистым контекстом. NEVER auto-trigger - вызывается ТОЛЬКО явной командой /checkpoint."
allowed-tools: "Write, Read, Bash(date:*), Bash(python scripts/checkpoint-paths.py:*), Bash(python3 scripts/checkpoint-paths.py:*)"
disable-model-invocation: true
argument-hint: "[заметка] | auto on|off|status | unattended on|off|status | compact-at N|status|off"
metadata:
  author: Misha Hanin
  email: misha.hanin@odinix.com
  version: "1.6"
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
    - NEVER auto-trigger. Explicit `/checkpoint [optional note]` only. Saves manual session handoff to `outputs/operations/handoff-archive/`, scoped to this session, without running /compact. Surfaces from the two-tier checkpoint-offer hook at the soft/hard thresholds (`CLAUDE_HANDOFF_SOFT_THRESHOLD` / `CLAUDE_HANDOFF_HARD_THRESHOLD`). Also carries three session switches. `auto on|off|status` makes the save silent and lets the Stop hook drive the compaction itself, through HERDR, once the handoff is on disk. `unattended on|off|status` adds continuing at a pause after a shown countdown instead of halting, and it already includes `auto`. `compact-at N|status|off` moves the hard threshold where this session compacts, with the soft reminder derived 5 below; an accepted number raises `unattended`, and `auto` with it, so one command is enough. Nothing lowers the mode except the operator - the assistant's done marker and the continuation ceiling stop a stretch and leave the switch up, and the status line shows its state on every render.
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
- Updates FOUR pointer files. This session's pair sits under
  `.latest/{session-slug}/{summary.md,prompt.md}`, which the SessionStart inject
  hook reads. The shared pair sits at `.latest/{summary.md,prompt.md}`, which
  `/next` reads as "the newest handoff in this workspace"
- Does NOT run `/compact`
- Does NOT clear the session
- Does NOT continue implementation after writing - wait for the user to direct next action

## When to use

- Soft / hard checkpoint offer fired (`Stop` hook surfaced the context threshold)
- About to switch to an unrelated task, want resume-ready snapshot
- Mid-implementation, want to save state before risky action
- Long session approaching natural pause point

## Procedure

### Step 0 - Handle a switch argument first

Split `$ARGUMENTS` on whitespace. If the FIRST token is exactly `auto`, exactly
`unattended`, or exactly `compact-at`, this is a switch. It is not a checkpoint.
Anything else is a note, including a word that merely begins with one of them.
Match the whole token, never a prefix. `compact-at` takes the SECOND token as its
value.

Run one of these, then stop. Do not write any file.

```bash
python scripts/checkpoint-paths.py --auto on
python scripts/checkpoint-paths.py --auto off
python scripts/checkpoint-paths.py --auto status
python scripts/checkpoint-paths.py --unattended on
python scripts/checkpoint-paths.py --unattended off
python scripts/checkpoint-paths.py --unattended status
python scripts/checkpoint-paths.py --compact-at 35
python scripts/checkpoint-paths.py --compact-at off
python scripts/checkpoint-paths.py --compact-at status
```

Report the command output in one line. For `auto on`, continue to Step 1 and
write the checkpoint as well. The operator asked at a threshold and expects this
one on disk. For `unattended on`, stop after the report. The operator is about to
leave, so a question here defeats the switch.

`compact-at` is also a one-word slash command, `/compact-at 35`, which is the
shorter route. The operator usually asks for it in prose. "Делаем compact на
пороге 35%" means `--compact-at 35`. The hard threshold moves to 35, and the
command derives the soft reminder at 30. An accepted number also raises
`unattended`, and that raises `auto` with it, so the hook compacts at the
threshold rather than asking. A refusal raises nothing, `status` and `off` raise
nothing, and a stretch already running is left untouched. Report the output and
stop, exactly as for `unattended on`. The threshold takes effect at the next
pause, and nothing restarts.

Both switches apply to this session only. Each one overrides its own environment
default in both directions. Neither needs cleanup. The state file carries a
session key, and the pruner removes it with the session.

### Step 1 - Get this session's paths

One command prints all of them. Add `--kind auto` when the Stop hook asked for
this save rather than the operator.

```bash
python scripts/checkpoint-paths.py
```

It emits `key=value` lines: `session_id`, `session_slug`, `stamp`,
`project_root`, `data_root`, `archive`, `summary_pointer`, `prompt_pointer`,
`shared_summary_pointer`, `shared_prompt_pointer`, `state`. Archive paths are
data-root-relative, which is the form the `@`-reference resolves. Use them
verbatim - never rebuild a path by hand, and never write into another session's
pointer directory.

If the script is unavailable, get the stamp from `date +'%Y-%m-%d-%H%M%S'`. Local
time, never `-u`. The script stamps the archive in the operator's own zone since
2026-08-20. A UTC fallback would file the night's work under yesterday.
Read the session id from the `CLAUDE_CODE_SESSION_ID` environment variable, which
Claude Code exports to every child process. Take the slug as the first 32
characters of that id.

### Step 2 - Write the combined handoff file

Read `references/handoff-template.md` before you write this file. It holds the
required section list, in order, and the exact archive template.

### Step 3 - Update the pointer files

Read `references/handoff-template.md` before you write these files. It holds the
summary text and the continuation prompt text.

Write the same two texts to two places each. Four files in total.

- `summary_pointer` and `prompt_pointer` from Step 1 hold this session's pair.
  The inject hook reads only these two.
- `shared_summary_pointer` and `shared_prompt_pointer` hold the workspace's
  newest handoff. `/next` reads these two.

For each of the four pointer files, `Read` it and then `Write` it. On a
first-ever checkpoint, ignore the not-found result of the `Read`. Keep the
summary short. The inject hook truncates it at 8000 characters.

### Step 4 - Respond to the user

After writing all five files, reply with:

1. Full path of the archive file written
2. Confirmation that you updated this session's pointer pair and the shared pair
3. One-line current state summary
4. Recommendation: "Run `/compact` manually if you want to free context now. Otherwise nothing else happens - the checkpoint stays on disk for resume."

Do NOT continue implementation, do NOT call `/compact`, do NOT clear the session. Wait for the user's next instruction.

## Auto mode

Auto mode makes the Stop hook run this skill's procedure with no prompt. Auto
mode is OFF by default. The hook also drives the compaction. It does that at or
above the hard threshold, once your `_handoff_auto_` archive is on disk, through
the same HERDR path unattended mode uses. When HERDR does not host this session
the hook cannot compact it at all. Claude Code's own auto-compact then frees the
context instead.

**For one session,** flip the switch while you work:

```bash
python scripts/checkpoint-paths.py --auto on
```

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

Keep the native compaction point ABOVE the soft threshold, so the checkpoint
lands before compaction frees the context. Where the harness actually fires
against a window is unmeasured here; `--compact-history` prints the configured
point and every firing recorded on this tree.

Read `references/mode-mechanics.md` for the hysteresis band, the post-compaction
resume instruction, and why the threshold offer stopped naming this switch.

## Unattended mode

Unattended mode decides what happens when the session pauses and nobody answers.
It is a SEPARATE switch from `auto`, and it is OFF by default.

```bash
python scripts/checkpoint-paths.py --unattended on
```

The slash command `/unattended on|off|status` is a shorter route to the same
switch. It runs the command above and nothing else.

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

At or above the hard threshold, and once your handoff is on disk, the hook asks
the harness to compact from outside, through HERDR. Read
`references/mode-mechanics.md` for that mechanism and for the two corrections
behind it.

The switch belongs to the operator. The assistant never lowers it.

The status line names its state on every render: `⏵ unattended`, `⏵ auto`, or
`⏸  manual`. Read it there. Turn the mode off yourself when you want the window
back:

```bash
python scripts/checkpoint-paths.py --unattended off
```

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
number with `CLAUDE_HANDOFF_UNATTENDED_MAX`.

**A stopped stretch is silent by design.** The hook records why it stopped, and
when, in the session state. It also sends one Telegram notice when you configured
a target. Read that state with `--unattended status`.

Below the hard threshold the mode stays quiet whenever something else already
drives the Stop event. Three signals claim it. A scheduled `/loop` wakeup claims
it. In-flight background work claims it. A ralph-loop that names this session
claims it. At or above the hard threshold the hook records the claimant and runs
anyway. That save is the last one before compaction frees the context.

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

**A wait outside 1 to 60 seconds is ignored, and you get 60.** This is not a
clamp. `env_int` returns the DEFAULT on any invalid value, so `WAIT=600` gives
60. `WAIT=0` also gives 60, not the nothing you asked for. Measured
2026-08-20. Raise the hook's own registered timeout first if you need a longer
grace period. Read `references/mode-mechanics.md` for the cost of a long wait.

**The wait can also be SHORTER than you set.** The hook bounds the grace period
against its own registered timeout, so a Stop that already spent time on other
work grants less. The continuation prints the number it actually gave you.

## NEVER

- Never invoke `/compact` automatically as part of this skill
- Never clear context as part of this skill
- Never proceed with the next task unless the user explicitly says so after seeing the checkpoint report
- Never skip the pointer file updates - without them, the inject hook has nothing to surface
- Never write into another session's pointer directory
- Never write anything into a pointer directory OTHER than `summary.md` and `prompt.md`
