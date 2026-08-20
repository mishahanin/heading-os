# Checkpoint - mode mechanics and design history

Consumed by: `.claude/skills/checkpoint/SKILL.md`, sections "Auto mode" and
"Unattended mode". Nothing here is an instruction. It records HOW the two
switches work underneath, and WHY several of the rules in SKILL.md read the way
they do. Read it when a switch behaves in a way the skill body does not explain.

Last Updated: 2026-08-20

---

## Why paths are keyed by session id

The system keys every path by session id. Several sessions run on this workspace
at once, and a shared pointer is last-writer-wins. Before 2026-08-16 the inject
hook could hand a resumed session another session's handoff.

## Why the switch token is matched whole, never as a prefix

The rule read "starts with `auto`" until 2026-08-19. On that day `/checkpoint
autocompact fires at 584k` matched `auto on`. The skill dropped the note, wrote
no file, and switched a mode instead. A prefix match cannot tell a switch from a
subject.

## Why `--kind auto` matters

The flag names the archive `_handoff_auto_`. That name is the only thing that
separates a save the system needed from one the operator chose. The driven
compaction looks for exactly that kind and does nothing without it.

## Why the `Read` before each pointer `Write`

A prior checkpoint almost always left the four pointer files in place. The
`Write` tool refuses to overwrite a file that you did not `Read` first in this
session, and it answers with `Error writing file`. On a first-ever checkpoint,
ignore the not-found result of the `Read`.

The inject hook truncates the summary at 8000 characters, so anything past that
is written to disk and never read into a session. The hook's own saves bound the
pointer lower still, at the 6000 of `checkpoint_paths.MAX_POINTER_SUMMARY`.

## Auto mode - hysteresis and scope

Auto mode makes the Stop hook run this skill's procedure with no prompt. It
fires once per 5% band, not at every pause, because the same hysteresis that
governs the question governs the silent save. After a compaction, the
SessionStart hook then tells the session to continue the unfinished task; that
instruction is printed only when auto is on.

The threshold offer no longer gives this switch a numbered option. It names the
switch only for the operator who stays at the keyboard, because `unattended` is
the better answer for the operator who leaves. Auto mode keeps one property that
`unattended` gives up: it hands the turn back immediately, with no wait. The
choice belongs to the current window and dies with it.

Auto mode also reaches the driven compaction. `_request_compaction` gates on
`auto_mode(state) OR unattended_mode(state)`, so the switch alone is enough. The
Stop hook submits `/compact` through HERDR at or above the hard threshold, with
an `_handoff_auto_` archive on disk. The two modes differ at the pause, never at
the compaction.

Keep the compaction percentage ABOVE the soft threshold, so the checkpoint lands
before compaction frees the context. Where the harness fires against a token
window is unmeasured on this tree; `--compact-history` prints the configured
point beside every firing it has recorded.

## Unattended mode - what was false, and what is true

Two sentences in this skill were false until 2026-08-19 and were corrected
rather than deleted, because the correction is the point. The Stop hook DOES
write in this mode: once per hysteresis bucket at or above the hard threshold.
And a hook CAN trigger a compaction, just not from inside Claude Code.

The harness exposes no internal way to compact on demand, so the hook does it
from outside. Above the hard threshold, and once your handoff is on disk, the
hook submits the literal text `/compact` to this session's own pane. It reaches
that pane through HERDR, the terminal manager that hosts it. The harness then
parses the text exactly as if you had typed it.

If HERDR is not hosting the session, none of that happens and Claude Code's own
auto-compact frees the context instead. The offer tells you which of those two
it is, and says so plainly when it could not find out.

## Why nothing but the operator lowers the switch

The rule "nothing lowers the switch except you" replaced "turn it off when the
work is finished" on 2026-08-19. That instruction defeated itself. Work reaches
the operator's decision at the end of nearly every stretch. So the switch went
down each time before the hook wrote any `_handoff_auto_` file. The compaction
path needs two conditions at once: the switch up, and that file on disk. The
mechanism ran twice in one session. It compacted zero times.

## Why the done marker replaced a heuristic

A fingerprint heuristic held this job until 2026-08-19. It watched the files the
session wrote, and it called three unchanged readings a finished plan. It cannot
tell a finished plan from a night of reading and thinking. It stopped all three
unattended runs ever attempted, at three and five continuations. None came near
the ceiling.

Measure one night before you move the ceiling with
`CLAUDE_HANDOFF_UNATTENDED_MAX`. No run has yet reached the end of its work, so
the count a real night needs is unknown.

## Why the wait is short, and why 60 is the ceiling

The wait costs you time on every pause of a long run. Sixty seconds against
fifty pauses is fifty minutes of a night spent waiting. This workspace runs 10
seconds. You lose no control: Claude Code queues anything you type at any
moment, and the hook reads that queue.

Claude Code discards the output of a hook that times out. A wait at or above the
registered timeout therefore loses the continuation in silence. The shipped
registration allows 90 seconds, and the 60-second ceiling leaves room for the
work that follows the wait. Raise the registration first if you need a longer
grace period.

Two corrections to how that ceiling was described until 2026-08-20. It is not a
clamp: `env_int` returns the DEFAULT on any value outside 1 to 60, so `WAIT=600`
gives 60 and `WAIT=0` gives 60 as well, not the nothing you asked for. And 60
plus the work after it does not fit on its own — measured end to end with a slow
HERDR, a full 60-second wait produced a 92.0-second hook against a 90-second
registration. The hook now bounds the grace period against its own process
clock, so the ceiling is an upper limit and the number you actually get can be
lower. The continuation prints the one it gave you.
