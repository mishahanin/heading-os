#!/usr/bin/env python3
"""
checkpoint-offer.py - Claude Code Stop hook.

Reads THIS session's .claude/state/checkpoint-<session-slug>.json (written by
checkpoint-statusline.py). If the state indicates a checkpoint offer is due
(soft or hard level, with hysteresis bucket not yet announced), emits
{"decision": "block", "reason": ...}. Otherwise exits silently.

The state file is per session. It was shared until 2026-08-16, and a shared file
means a sibling session's context usage blocks this session's turns: measured
with session A at 46% and session B idle, B got the offer and A got nothing.

THREE behaviours, chosen by two independent switches. `session_auto` (or
CLAUDE_HANDOFF_AUTO for the workspace) decides whether a checkpoint saves without
asking. `session_unattended` (or CLAUDE_HANDOFF_UNATTENDED) decides whether a
pause hands the turn back to the operator at all:

  - auto off, unattended off (the default): surface the /checkpoint vs /compact
    vs continue choice and wait for the operator. Nothing is written.
  - auto on: drive the assistant to save the checkpoint silently and resume.
  - unattended on: ask nothing. Save at a hard-threshold bucket crossing, then
    wait out a grace period, hand the turn back the moment the operator types,
    and otherwise tell the assistant to carry on. It engages ABOVE the soft
    threshold, or below it once this session has compacted at least once - a
    session that has not filled up yet still halts at a pause, and one that
    emptied itself by compacting does not.

The offer prompt names `unattended` as its one standing option, because
`--unattended on` already sets `session_auto` - the two are nested, not siblings,
and offering both made the list read as a choice between them.

What changed on 2026-08-19: this hook used to write nothing at all in unattended
mode, and the docstring said so as if it were a design property. It was the
defect. The mode now saves once per hysteresis bucket at or above the hard
threshold, and then asks the harness to compact - see the driven block in
`main()`. The PostCompact hook still writes its own archive on top of that,
whatever either switch says.

Two guards stand ahead of all three. `stop_hook_active` is honoured, per 2.1.228's
own warning to a hook that blocks eight consecutive times, EXCEPT on a turn this
hook itself continued in unattended mode. And the hook stays silent whenever
something else already drives the Stop event: a scheduled wakeup, in-flight
background work, or a ralph-loop naming this session. See
`checkpoint_paths.continuation_claimant`, including why /goal is not detectable.

Compaction: no hook can trigger one from INSIDE Claude Code, and this file does
not try. It reaches the same end from outside. Once a handoff is on disk above
the hard threshold, `main()` submits the literal text `/compact` to the terminal
that hosts this session, through HERDR (`scripts/utils/herdr_agent.py`), and the
harness parses it exactly as it would from the keyboard. That path is proven, not
assumed: session 10f49ae5 on 2026-08-19 produced a real `compact_boundary` this
way. It also depends entirely on HERDR hosting the session, and it degrades in
silence when it does not - which is why the harness's own auto-compact stays
armed behind it.
"""

import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

_BOOT = Path(__file__).resolve()
_ROOT = _BOOT.parent
for _candidate in [_BOOT.parent, *_BOOT.parents]:
    if (_candidate / "scripts" / "utils" / "checkpoint_paths.py").is_file():
        sys.path.insert(0, str(_candidate))
        _ROOT = _candidate
        break
from scripts.utils import checkpoint_paths as CP  # noqa: E402
from scripts.utils import herdr_agent as HA  # noqa: E402
from scripts.utils.colors import GREEN, RESET, supports_ansi  # noqa: E402

CP.force_utf8()

# Colour on ONE word of the menu, and it is an experiment rather than a
# established feature. The status line is a surface Claude Code documents as
# ANSI-capable; this reason block is not, so whether the escape renders or prints
# raw is unknown until an operator crosses a threshold and looks. It degrades to
# plain text on a terminal that cannot colour, and the word survives either way -
# the recommendation is carried by the word, never by the colour.
# One expression, not an opening constant and a closing one. Written as a pair,
# an edit to either half leaves the other applied - which produced a real
# `RECOMMENDED\x1b[0m`, an unbalanced escape, the first time a test disabled the
# colour. There is no half-coloured state to reach now.
RECOMMENDED = f"{GREEN}RECOMMENDED{RESET}" if supports_ansi() else "RECOMMENDED"

# The countdown redraws on this cadence, not on the 2-second poll: 12 calls
# across a 60-second wait instead of 30, for a figure nobody reads more finely.
LABEL_REFRESH_SECONDS = 5

SKILL_REF = ".claude/skills/checkpoint/SKILL.md"


# The Stop timeout this hook is REGISTERED with in .claude/settings.local.json.
# Claude Code discards the output of a hook that outruns its timeout, so a
# continuation printed at 92s is a continuation the operator never receives: no
# block decision, no state write, no stall notice, and a session that halts in
# silence having been told in writing that it would carry on.
#
# `CP.UNATTENDED_WAIT_MAX` (60) exists to keep the GRACE PERIOD under this
# number, and its comment justifies 60 by adding three out-of-loop HERDR calls
# to it - the pane lookup, the final label overrun, the `clear_label` - for a
# worst case "near 79 seconds". That arithmetic omits the two HERDR calls
# `_request_compaction` makes BEFORE the wait on the same Stop. Measured
# 2026-08-20 with a `herdr` answering just inside each of its own timeouts
# (`agent list` 9.5s, `agent prompt` 9.5s, `agent rename` 1.9s) and the wait at
# its 60s ceiling: the hook took 92.0s end to end. Two seconds over, and the
# whole continuation lost.
#
# So the wait is bounded against the hook's OWN clock rather than against the
# configured number alone. Everything upstream - both compaction calls, the
# pane lookup, the state reads - is charged automatically, because the budget is
# measured from process start and not from the moment the wait begins.
#
# Read from the environment because the number is DATA, not a property of this
# file: the 90 lives in the `Stop` registration in .claude/settings.local.json,
# and a plugin bundle registering this hook with a different budget would leave
# a hardcoded constant quietly describing somebody else's timeout. The default
# is the shipped registration.
HOOK_TIMEOUT_SECONDS = CP.env_int(
    "CLAUDE_HANDOFF_HOOK_TIMEOUT", 90, minimum=10, maximum=600
)

# What has to happen AFTER the wait returns and still fit: the final in-loop
# `set_label` can overrun the deadline by `HA.LABEL_TIMEOUT` (2s), the `finally`
# spends another `HA.LABEL_TIMEOUT` on `clear_label`, and the state write plus
# the print follow. Measured worst case for that tail is about 4.1s; 8 leaves
# room for a slower disk without eating a wait an operator can otherwise have.
POST_WAIT_RESERVE_SECONDS = 8

# Process start, as close to it as this module can observe. Read only by
# `_effective_wait`.
_HOOK_STARTED = time.monotonic()


def _effective_wait(configured: float, started: float, now: float) -> float:
    """The part of the configured grace period that still fits in the timeout.

    Pure arithmetic on three numbers so the boundary is testable without
    spending a minute of wall time proving it.

    Returning 0 means "no room left": the caller then skips the wait and prints
    its continuation immediately, which is the direction that keeps the run
    alive. Halting instead would trade a discarded continuation for a silent
    one, which is the same loss with better paperwork.
    """
    room = HOOK_TIMEOUT_SECONDS - POST_WAIT_RESERVE_SECONDS - (now - started)
    return max(0.0, min(float(configured), room))


# The four options are IDENTICAL in both bodies and only the framing line above
# them differs. They are two-tiered on purpose: option 2 carries five facts the
# operator needs before choosing it, and written inline it ran seven lines and
# visually crushed the other three. Four one-line options are scanned; the detail
# block is read only by whoever picked the option it belongs to.
#
# The old list named `auto on` and `unattended on` as sibling options. They are
# not siblings - `--unattended on` already sets `session_auto` - and presenting a
# containment as a choice is what made the list confusing. One command now.
OPTIONS = """\
1. `/checkpoint` - save the work now. Frees no context.
2. `/checkpoint unattended on` - {recommended}. The hook then saves and compacts \
automatically, at this threshold and every one after.
3. `/compact` - compact now, once. The question returns at the next threshold.
4. Continue as is - Claude Code compacts by itself at {native}.{detail}"""


# Shown ONCE per session, appended to the options above. It explains the
# mechanism rather than the choice, so it is worth reading the first time and
# is pure noise on the fifth. The operator raised exactly that on 2026-08-19,
# looking at a threshold offer he had already read four times that evening.
OPTIONS_DETAIL = """

About option 2: at this threshold and every one after, the hook waits {wait} seconds \
and shows a countdown. Type anything and the turn comes back to you in about two \
seconds. Stay silent and it saves the handoff, compacts by itself, and stops \
asking. From then on the session also works through ordinary pauses instead of \
halting to ask you something. Outbound sends still wait for your approval - that \
gate is code, not this switch. The mode stays on until you turn it off; the status \
line shows whether it is live. Only the continuation ceiling lowers it for you; the done marker ends the work and leaves the switch up.

How the hook compacts: {compaction}"""


SOFT_BODY = """\
Context is about {used:.0f}% used (~{remaining:.0f}% remaining).
Consider checkpointing now so you can resume later with a fresh context.

Options:
""" + OPTIONS


HARD_BODY = """\
Context is about {used:.0f}% used - hard threshold reached.
Strongly recommend settling this before continuing further.

Recommended options:
""" + OPTIONS


REASON_WRAPPER = """\
Ask the user, briefly, in the language they are speaking, and wait for their decision. Do not act instead of asking: do not run /compact yourself, and write no file until they approve one.

{body}"""


AUTO_WRAPPER = """\
Context is about {used:.0f}% used (~{remaining:.0f}% remaining), which crossed the {level} checkpoint threshold. AUTO MODE is on.

Do this now, without asking:
1. Save a checkpoint silently, following @{skill} exactly: run `python scripts/checkpoint-paths.py --kind auto` for this session's stamp and paths, write the archive it names, then update the two pointer files it names. Those paths are scoped to this session - never write into another session's pointer directory. The `--kind auto` matters: it names the archive `_handoff_auto_`, which is how this hook, and the compaction probe, tell a save the system asked for from one you chose. Without it the compaction below will not fire.
2. Print ONE line naming the archive path written.
3. If you were mid-task, resume it where you left off. If you had finished and were waiting for the user, stop after that line.

Do NOT run /compact yourself. Once your checkpoint is on disk, this hook submits \
it for you when the turn ends. {compaction}"""


# The harness's own trigger, derived from the configured window. Decoded from the
# 2.1.235 binary on 2026-08-19: `Hve()` subtracts the output reserve, `CRa()`
# takes the lower of a buffer fraction and a flat floor margin.
NATIVE_OUTPUT_RESERVE = 20000
NATIVE_BUFFER_FRACTION = 0.2
NATIVE_FLOOR_MARGIN = 13000


def _native_phrase() -> str:
    """Roughly where the harness's own compaction fires, hedged on purpose.

    This printed the CONFIGURED WINDOW as if it were the firing point until
    2026-08-19, so a 750000-token window was announced to the operator as
    "Claude Code compacts by itself at 750000 tokens". It does not. The window is
    the ceiling; the harness reserves output tokens off it and then takes a
    buffer fraction on top, which at 750000 puts the real trigger near 584000 -
    166000 tokens earlier than the sentence claimed. An operator planning around
    the printed number would have planned around the wrong one.

        effective = window - 20000
        trigger   = min(effective - round(effective * 0.2), effective - 13000)

    Said as "roughly", and that hedge is not modesty. The buffer fraction is a
    REMOTE-CONFIG value - gate `tengu_amber_moleskin`, falling back to
    `tengu_amber_rokovoko`, falling back to the scalar 0.2 - so a server-side
    change moves this number with no version bump and nothing local to notice.
    A figure printed as exact would assert a precision the method cannot support
    (.claude/rules/scope-claims.md). The same applies to the window itself: the
    harness takes the smaller of the configured value and the model's own
    window, and this hook cannot see the second one.
    """
    point = CP.compact_point()
    if point is None:
        return "a point this hook cannot determine"
    kind, value = point
    if kind == "percent":
        return f"roughly {value}% used"
    # `compact_point` returns the raw environment STRING, having only checked
    # that it is all digits. Every caller before this one interpolated it and
    # never did arithmetic on it, so the type never mattered.
    try:
        window = int(value)
    except (TypeError, ValueError):
        return "a point this hook cannot determine"
    effective = window - NATIVE_OUTPUT_RESERVE
    trigger = min(
        effective - round(effective * NATIVE_BUFFER_FRACTION),
        effective - NATIVE_FLOOR_MARGIN,
    )
    return f"roughly {trigger} tokens (from the {window} window)"


def _herdr_status(state: dict, state_path: Path, session: str) -> tuple[str, str]:
    """("hosted", pane) / ("not-hosted", "") / ("unknown", ""), cached per session.

    Three outcomes and never a guess. "Not hosted" and "could not tell" are
    different facts, and reporting the second as the first is the exact defect
    `.claude/rules/scope-claims.md` was written for.

    A resolved pane and a definite not-hosted are stable for a session and are
    cached, so the sentence costs one `herdr agent list` per session rather than
    one per Stop. "Unknown" is deliberately NOT cached: it is a transient failure
    and caching it would make one bad moment permanent for the window.
    """
    cached = state.get("compact_host")
    if cached == "not-hosted":
        return ("not-hosted", "")
    if isinstance(cached, str) and cached and cached != "unknown":
        return ("hosted", cached)

    try:
        pane = HA.resolve_pane(session)
    except HA.HerdrUnavailable as exc:
        print(f"checkpoint-offer: herdr lookup failed: {exc}", file=sys.stderr)
        return ("unknown", "")
    if pane is None:
        _persist(
            state_path,
            compact_host="not-hosted",
            compact_host_checked_at=CP.utc_now().isoformat(),
        )
        return ("not-hosted", "")
    _persist(
        state_path,
        compact_host=pane,
        compact_host_checked_at=CP.utc_now().isoformat(),
    )
    return ("hosted", pane)


def _compaction_sentence(state: dict, state_path: Path, session: str) -> str:
    """How compaction will actually happen for THIS session, honestly.

    Every option in the offer ends by pointing here, so this sentence carries the
    honesty load for the whole menu. It may claim the driven path only when
    `resolve_pane` has actually matched this session's id.
    """
    carry = (
        "Its PreCompact hook steers what that summary keeps and its PostCompact "
        "hook saves a handoff. What crosses "
        "a compaction is the SUMMARY: the handoff on disk is not re-injected "
        "afterwards, and is what a NEW session resumes from instead."
    )
    native = _native_phrase()
    status, pane = _herdr_status(state, state_path, session)

    if status == "hosted":
        return (
            "Claude Code has no internal way to compact on demand, so this hook "
            "does it from outside: it submits the literal text /compact to this "
            f"session's own terminal through HERDR, the terminal manager hosting "
            f"it (pane {pane}). The harness then parses it exactly as if it were "
            f"typed. Native auto-compact at {native} stays armed as the fallback. "
            f"{carry}"
        )
    if status == "not-hosted":
        return (
            "HERDR is not hosting this session, so this hook cannot compact it - "
            "Claude Code has no internal way to compact on demand. Native "
            f"auto-compact at {native} is what will free the context. {carry}"
        )
    return (
        "This hook could not determine whether HERDR is hosting this session, so "
        "it cannot say whether it will be able to compact. Native auto-compact at "
        f"{native} is armed either way. {carry}"
    )


def build_reason(
    level: str, used: float, remaining: float,
    state: dict, state_path: Path, session: str,
) -> str:
    """Render the offer reason, in English only.

    The reason text is emitted on stderr and the operator sees it, so every
    duplicate is a duplicate the operator reads. It carried a full Russian
    section beside the English one, and the assistant's own answer made a third
    rendering of the same three lines. English alone is the right single
    language here: this hook ships in a public engine, and the wrapper asks for
    the reply in whatever language the operator is actually speaking.

    The wrapper opened with the percentage and the threshold it crossed, one line
    above a body that says both again. That is the same defect one layer up, and
    the operator caught it by reading the expanded hook output: the harness shows
    him the WHOLE reason, so a line addressed to the assistant costs him a line.
    What is left of the wrapper is only what the body cannot carry - ask rather
    than act, in their language - because the body is the text he reads.
    """
    body = HARD_BODY if level == "hard" else SOFT_BODY
    if state.get("offer_detail_shown"):
        detail = ""
    else:
        # `wait` is interpolated, never written as a literal: this tree runs
        # CLAUDE_HANDOFF_UNATTENDED_WAIT=10 while the text said 60, so the one
        # paragraph the operator reads to decide was wrong by 50 seconds about
        # the thing it was explaining.
        detail = OPTIONS_DETAIL.format(
            wait=CP.wait_seconds(),
            compaction=_compaction_sentence(state, state_path, session),
        )
        _persist(state_path, offer_detail_shown=True)
    return REASON_WRAPPER.format(
        body=body.format(
            used=used,
            remaining=remaining,
            recommended=RECOMMENDED,
            native=_native_phrase(),
            detail=detail,
        )
    )


def build_auto_reason(
    level: str, used: float, remaining: float,
    state: dict, state_path: Path, session: str,
) -> str:
    """The hands-off variant: save, say where, carry on.

    It POINTS AT the skill rather than restating its section list. The nexi
    plugin inlined the whole contract into the hook text, which is a second copy
    of a format that only one file should define; the copy that stops being
    updated is the one the model reads.
    """
    return AUTO_WRAPPER.format(
        used=used,
        remaining=remaining,
        level=level,
        skill=SKILL_REF,
        compaction=_compaction_sentence(state, state_path, session),
    )


# Deliberately four lines, and it was twenty-five until 2026-08-19.
#
# The harness prints a blocking Stop hook's whole message to the OPERATOR's
# transcript as well as feeding it to the assistant, and this one fires at every
# pause of an unattended run. So each sentence here is a sentence he re-reads,
# once per continuation, forever. He asked why, holding a screenshot of it.
#
# What was cut, and where it lives now: the history of why the assistant must
# not lower the switch (this file, above `_pause_unattended`, and the Unattended
# section of the checkpoint skill); the HERDR and native-auto-compact mechanics
# (`_compaction_sentence`, still shown once per session in `OPTIONS_DETAIL`).
# What stays is only what changes the assistant's next action.
#
# `build_reason`'s docstring records the same defect one layer up. Two rounds of
# it in one file is the signal: prose addressed to the assistant is not free,
# and the default should be to cut rather than to add.
#
# The one paragraph ADDED back, on the same day, is the done-marker instruction.
# It earns its lines by being the only way a night can end on purpose: this hook
# reads state, not prose, so an assistant that writes "the work is finished" and
# stops has said nothing the mechanism can hear, and the next pause continues it
# again. The sentence it replaced told the assistant to do exactly that.
UNATTENDED_WRAPPER = """\
{used:.0f}% used, unattended on, {wait}s grace passed, so the turn continues. \
Resume the unfinished task. Decide alone. Never invent work. \
Do not touch the unattended switch, and do not run /compact.

Finished, or left only a judgement the operator owns? Run `python \
scripts/checkpoint-paths.py --done "<one line>"` and stop; this hook reads \
state, never prose. Continuation {done} of {maximum}."""

# The repeat form, from the second continuation of a window onward.
#
# The full text above is four instructions, and three of them are standing rules
# that do not change between one pause and the next. The assistant read them at
# continuation 1 of THIS window and they are still in its context; reprinting
# them buys nothing and costs the operator another screen of prose, which is the
# complaint that shortened this template twice already.
#
# What the repeat keeps is the two things that DO change - the counter, and the
# one command the mechanism can hear. A stretch that cannot be ended is worse
# than a verbose one.
#
# `_context_was_rebuilt` puts the full form back after a compaction, which is
# the one moment inside a window when "you already read it" stops being true:
# the block message the assistant read at continuation 1 is gone with the rest
# of the pre-compaction context, and only the summary remains.
UNATTENDED_WRAPPER_REPEAT = """\
{used:.0f}% used, unattended on, continuation {done} of {maximum}. Resume the \
task, or end the stretch with `python scripts/checkpoint-paths.py --done \
"<one line>"` and stop."""


def _context_was_rebuilt(state: dict, previous_at: str | None) -> bool:
    """Did a compaction land between the previous continuation and this one?

    The repeat form leans on "the assistant already read the standing rules this
    window". A compaction is the one event inside a window that makes that
    false: the block message carrying them is discarded with the rest of the
    pre-compaction context, and the summary that replaces it is not obliged to
    keep hook prose.

    Unparseable or missing timestamps answer YES. The two failure directions are
    not symmetric - printing the full text when it was not needed costs the
    operator four lines, while withholding it can leave a post-compaction
    assistant unable to name the command that ends the stretch.
    """
    compacted_at = state.get("last_compact_at")
    if not compacted_at:
        return False
    if not previous_at:
        return True
    try:
        return datetime.fromisoformat(str(compacted_at)) > datetime.fromisoformat(str(previous_at))
    except (TypeError, ValueError):
        return True


def _used_percentage(state: dict) -> float | None:
    raw = state.get("used_percentage")
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _remaining_percentage(state: dict, used: float) -> float:
    raw = state.get("remaining_percentage")
    try:
        remaining = float(raw) if raw is not None else 100.0 - used
    except (TypeError, ValueError):
        remaining = 100.0 - used
    return max(remaining, 0.0)


def _operator_spoke(fresh: str, session: str) -> bool:
    """Does this slice of transcript carry something the operator typed?

    The load-bearing signal is `queue-operation` / `enqueue`. Pressing Enter
    during a turn queues the message and clears the input line, so the SCREEN
    becomes indistinguishable from silence; the transcript does not. Measured on
    this workspace's own transcript on 2026-08-17: the enqueue line was on disk
    at 13:00:18 and the matching `remove` only at 13:01:12, so the signal existed
    54 seconds before anything consumed it.

    A `user` line carrying real text is accepted as a second, weaker signal. Note
    that most `user` lines are tool results rather than the operator, so the
    check is for a text block and never for the role alone.
    """
    # `split("\n")`, not `splitlines()`. The latter also breaks on U+0085,
    # U+2028 and U+2029, which `json.dumps` writes into a transcript literally -
    # the live 88 MB transcript here holds 22 of them. A record cut on one of
    # those parses as neither half, so the enqueue this function exists to see
    # would be invisible and the hook would continue over a message the operator
    # had already typed.
    for line in fresh.split("\n"):
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if not isinstance(entry, dict):
            continue
        if entry.get("type") == "queue-operation":
            if (
                entry.get("operation") == "enqueue"
                and entry.get("content") != HA.COMPACT_COMMAND
                and (not session or entry.get("sessionId") in (None, session))
            ):
                return True
            # `content != HA.COMPACT_COMMAND` because `_request_compaction`
            # submits that literal through HERDR on this very Stop, and the
            # harness records the queueing as an ordinary enqueue. Without the
            # test the hook hands the turn back the instant it asks for its own
            # compaction, reading its own request as the operator speaking.
            continue
        if entry.get("type") != "user" or entry.get("isMeta"):
            continue
        content = (entry.get("message") or {}).get("content")
        if isinstance(content, str) and content.strip():
            return True
        if isinstance(content, list) and any(
            isinstance(block, dict)
            and block.get("type") == "text"
            and str(block.get("text") or "").strip()
            for block in content
        ):
            return True
    return False


def _queue_pending(path: Path, session: str) -> bool:
    """Is a message already queued and not yet consumed?

    Counted rather than matched by content, because a repeated message would
    defeat matching. The case this catches is the expensive one: the operator
    typed twenty seconds before the pause, the harness will hand that message over
    the moment the turn ends, and continuing here would overwrite an instruction
    he has already sent.

    FOUR operations, not two. The first version counted `enqueue` against
    `remove` alone, on the belief that those were the only two records. Measured
    across all 44 transcripts for this project afterwards: 660 enqueue, 422
    remove, 231 `dequeue`, 1 `popAll`. Ignoring the last two makes the balance
    falsely positive in 28 of the 44 sessions - so in the MAJORITY of real
    sessions the hook would read a phantom queued message, return early, and halt
    the very run it was turned on to keep going, leaving no continuation, no
    stall record and no notice. The contract test that covers this passed because
    its fixture was captured from a session before that session's own first
    dequeue.
    """
    # Streamed, and split on newlines only. This runs on EVERY Stop, not just at
    # a compaction, so materialising the transcript costs the peak twice over on
    # the largest ones (795 MB measured on the 88 MB file, against 19 MB
    # streamed). `splitlines()` was also cutting records on U+0085 / U+2028 /
    # U+2029, which appear 22 times in that same transcript - and a cut record
    # is a `queue-operation` this counter never sees, which is the difference
    # between halting for the operator and talking over them.
    try:
        handle = path.open(encoding="utf-8", errors="replace")
    except OSError:
        return True
    pending = 0
    with handle:
        for line in handle:
            if '"queue-operation"' not in line:
                continue
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if not isinstance(entry, dict) or entry.get("type") != "queue-operation":
                continue
            if session and entry.get("sessionId") not in (None, session):
                continue
            if entry.get("content") == HA.COMPACT_COMMAND:
                # OUR OWN submission. `_request_compaction` sends this literal
                # through HERDR, and the harness records the queueing as an
                # ordinary `queue-operation`, indistinguishable from the operator
                # pressing Enter mid-turn. Counting it means the hook reads its
                # own request as a waiting message from him - and because the
                # request only clears at a turn boundary the block prevents, the
                # miscount is permanent. Observed live 2026-08-20: two such
                # enqueues, no matching remove, and `_queue_pending` true from
                # then on.
                continue
            operation = entry.get("operation")
            if operation == "enqueue":
                pending += 1
            elif operation == "popAll":
                pending = 0
            elif operation in ("remove", "dequeue"):
                pending -= 1
    return pending > 0


def _wait_out_the_grace(payload: dict, session: str) -> tuple[bool, float]:
    """(spoke, seconds actually granted).

    True when the operator spoke, False when the window passed in silence.

    Unknown counts as spoke. An absent or unreadable transcript means silence
    cannot be told from a message, and the safe direction there is to hand the
    turn back rather than to continue blind.

    The second element is what the continuation message reports. It is the wait
    this call actually granted, never `CLAUDE_HANDOFF_UNATTENDED_WAIT`: the two
    differ whenever `_effective_wait` had to shorten the window, and a sentence
    saying "60s grace passed with no input" after a 31-second wait would be the
    same class of defect as the rest of this file exists to keep out.
    """
    raw_path = payload.get("transcript_path")
    if not raw_path:
        return (True, 0.0)
    path = Path(raw_path)
    if not path.is_file():
        return (True, 0.0)
    if _queue_pending(path, session):
        return (True, 0.0)

    wait = CP.wait_seconds()
    poll = CP.env_int("CLAUDE_HANDOFF_UNATTENDED_POLL", 2, minimum=1, maximum=60)
    try:
        offset = path.stat().st_size
    except OSError:
        return (True, 0.0)

    # The countdown surface. Sixty seconds of an unchanging terminal reads as a
    # hang, and an operator who believes the session hung interrupts it - which
    # delivers exactly the input this wait is watching for, given for the wrong
    # reason. The label is written through HERDR rather than to the terminal
    # itself: /dev/tty would fight Claude Code's TUI for the same lines, and this
    # hook's stdout is shown only after it returns, which is after the wait it
    # would have described.
    #
    # Never load-bearing. Any failure here leaves the wait running its full
    # duration unchanged; a missing countdown is a worse experience, a shortened
    # wait would be a defect.
    #
    # Charged against the same budget as the wait itself, and skipped when there
    # is none left: `resolve_pane` costs up to `HA.LIST_TIMEOUT` (10s), and ten
    # seconds spent resolving a pane for a countdown that has no time left to
    # draw is ten seconds taken off a continuation that is already at the edge.
    #
    # `> 0` and not `> HA.LIST_TIMEOUT`, deliberately, and this was tried the
    # other way on 2026-08-20. The review claim was that a lookup started with
    # 0 < room < 10s can spend all ten and push the hook past its timeout. It
    # cannot: `granted` is recomputed from the process clock AFTER this block
    # (see the next paragraph), so the lookup's cost comes OUT of the wait
    # rather than on top of it, and the total stays bounded either way.
    # Tightening the gate to `HA.LIST_TIMEOUT` only removed the countdown from
    # every wait of 10 seconds or less — including the 10 this workspace
    # configures — which `test_the_wait_shows_a_countdown_and_always_clears_it`
    # caught immediately. The bound is the recomputation, not this gate.
    pane = None
    if _effective_wait(wait, _HOOK_STARTED, time.monotonic()) > 0:
        try:
            pane = HA.resolve_pane(session)
        except HA.HerdrUnavailable as exc:
            print(f"checkpoint-offer: countdown unavailable: {exc}", file=sys.stderr)

    labels_alive = pane is not None
    next_label = 0.0

    # Recomputed here rather than reused from above, so the pane lookup's own
    # cost is inside the budget instead of on top of it.
    now = time.monotonic()
    granted = _effective_wait(wait, _HOOK_STARTED, now)
    deadline = now + granted
    try:
        while time.monotonic() < deadline:
            now = time.monotonic()
            if labels_alive and now >= next_label:
                remaining = int(deadline - now)
                try:
                    HA.set_label(
                        pane, f"waiting for operator - {remaining}s -> auto-continue"
                    )
                except HA.HerdrUnavailable as exc:
                    # One failure stops all of them. Retrying a broken socket
                    # every five seconds would spend the wait on the thing that
                    # is only decoration.
                    labels_alive = False
                    print(
                        f"checkpoint-offer: countdown stopped: {exc}", file=sys.stderr
                    )
                next_label = now + LABEL_REFRESH_SECONDS

            time.sleep(min(poll, max(deadline - time.monotonic(), 0.1)))
            try:
                size = path.stat().st_size
            except OSError:
                return (True, granted)
            if size <= offset:
                continue
            try:
                with path.open("r", encoding="utf-8", errors="replace") as handle:
                    handle.seek(offset)
                    fresh = handle.read()
            except OSError:
                return (True, granted)
            offset = size
            if _operator_spoke(fresh, session):
                return (True, granted)
        return (False, granted)
    finally:
        # Both exits, always. A label frozen at "12s" outlives the wait and is
        # worse than never having shown one.
        if pane is not None:
            try:
                HA.clear_label(pane)
            except HA.HerdrUnavailable as exc:
                print(
                    f"checkpoint-offer: countdown not cleared: {exc}", file=sys.stderr
                )


def _notify_stall(reason: str) -> None:
    """Tell the operator his unattended run stopped. Best effort, never fatal.

    A notification to the operator's own bot, which is established practice in
    this workspace; nothing here gains the ability to reach anyone else. The
    import is deferred until a target is actually configured, so an engine with
    no Telegram set up never pays for it.
    """
    target = ""
    for var in (
        "CHECKPOINT_TELEGRAM_TARGET",
        "OPS_RADAR_TELEGRAM_TARGET",
        "ODIN_CADENCE_TELEGRAM_TARGET",
    ):
        if os.environ.get(var, "").strip():
            target = os.environ[var].strip()
            break
    if not target:
        return
    try:
        from scripts.utils import telegram_notify

        telegram_notify.notify(target, f"HEADING OS: unattended run stopped. {reason}")
    except Exception as exc:  # noqa: BLE001 - a missed notice never blocks the stop
        print(f"checkpoint-offer: stall notice failed: {exc}", file=sys.stderr)


def _persist(state_path: Path, _mutate=None, **updates) -> None:
    """Apply our own keys to what is on disk NOW, never a whole stale copy.

    The statusline rewrites this file after every turn. Writing back the copy this
    hook read at entry would clobber whatever it recorded in between, and
    unattended mode writes at every pause above the threshold rather than once per
    5% bucket, so that window stopped being rare. Only the keys named by the caller
    move.

    `_mutate` is for the changes a keyword cannot express - one that REMOVES a
    key, or one whose new value depends on the fresh copy rather than on the copy
    the caller read. It runs against the fresh state after `updates` are applied.
    Added 2026-08-19 so the fuse stop could call `CP.lower_unattended`, which pops
    keys; expressing that as keywords would have meant writing the popped names
    back as nulls.
    """
    # Under `CP.locked_state`, not a bare read-then-write: the statusline writes
    # this same file on every render, and the read-to-write span here is exposed
    # in the same way its own was. `_mutate` may REPLACE the dict (that is what
    # `CP.lower_unattended` does), so the result is applied back onto the locked
    # object rather than rebound.
    try:
        with CP.locked_state(state_path) as fresh:
            fresh.update(updates)
            if _mutate is not None:
                mutated = _mutate(dict(fresh))
                fresh.clear()
                fresh.update(mutated)
    except Exception as exc:  # noqa: BLE001 - a lost note is not worth a broken turn
        print(f"checkpoint-offer: state write failed: {exc}", file=sys.stderr)


def _stamp(iso: str) -> str:
    """An ISO timestamp in the archive filename's own format, for comparison.

    Filenames, never mtime: `checkpoint-save.py` truncates its stamp to %H%M%S,
    and mtime moves with any later touch, so the name is the only stable record
    of when an archive was written.

    **Converted into the filename's clock first.** The stored timestamps this is
    fed (`last_offer_at`) are UTC by convention; since 2026-08-20 the archive
    FILENAME is stamped in the operator's local zone, because a filename is a
    calendar day a person reads. Stripping the offset and comparing the two wall
    clocks as strings therefore compares a local clock against a UTC one, and on
    a UTC+4 operator that accepts a handoff written up to four hours BEFORE the
    offer as one written after it. The consequence is not cosmetic: the driven
    compaction is gated on this, so a stale handoff would let the boundary fire
    with the session's real work unsaved.

    Falls back to the old string surgery when the input carries no parseable
    offset - a naive timestamp has no zone to convert from, and refusing it
    outright would return "" and block the compaction forever.
    """
    raw = str(iso or "").strip()
    if not raw:
        return ""
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        parsed = None
    if parsed is not None and parsed.tzinfo is not None:
        return parsed.astimezone(CP.local_now().tzinfo).strftime("%Y-%m-%d-%H%M%S")

    raw = raw.replace("Z", "")
    try:
        date, clock = raw.split("T", 1)
    except ValueError:
        return ""
    clock = clock.split(".", 1)[0].split("+", 1)[0].replace(":", "")
    return f"{date}-{clock[:6]}"


def _handoff_since(project: Path, session: str, since_iso: str | None) -> bool:
    """Is a PRE-compaction handoff for this session on disk, newer than `since`?

    Kind `auto` only. The PostCompact hook writes `compact-auto` and
    `compact-manual` archives AFTER every compaction, so accepting those would
    make this condition true forever after the first one - the ordering this
    whole path exists to guarantee, satisfied by a file the compaction itself
    produced.

    Resolved through `CP.handoff_dir()` rather than a relative path: the archive
    lives in the private data overlay while this hook lives in the engine, so a
    bare relative path resolves against the wrong tree. The root passed is
    `_ROOT`, the engine this hook was loaded from - passing `project` instead
    resolves the archive project-locally and finds nothing, which is a silent
    "no handoff yet" rather than an error.
    """
    floor = _stamp(since_iso) if since_iso else ""
    if not floor:
        return False
    try:
        directory = CP.handoff_dir(project, _ROOT)
    except Exception as exc:  # noqa: BLE001 - an unresolvable overlay blocks, never raises
        print(f"checkpoint-offer: handoff dir unresolved: {exc}", file=sys.stderr)
        return False
    if not directory.is_dir():
        return False
    needle = f"_handoff_auto_{CP.safe_slug(session)}"
    for path in directory.glob("*.md"):
        name = path.name
        if needle in name and name[:17] > floor:
            return True
    return False


def _driven_pending(state: dict) -> bool:
    """Cheap pre-check: could the driven block have anything to do this Stop?

    Read before `used` is resolved, only to decide whether the `stop_hook_active`
    guard may be crossed. Everything it skips is re-checked properly inside
    `_request_compaction`.
    """
    if not (CP.auto_mode(state) or CP.unattended_mode(state)):
        return False
    bucket = int(state.get("offer_bucket") or state.get("current_bucket") or 0)
    return state.get("compact_requested_bucket") != bucket


def _request_compaction(
    payload: dict, state: dict, state_path: Path, project: Path, used: float
) -> bool:
    """Ask the harness to compact, from outside, once the handoff is on disk.

    This is the whole point of the plan this function came from. Claude Code has
    no in-process entry point for `/compact`, so the request is made by
    submitting the literal text to the terminal hosting this session, through
    HERDR. See `scripts/utils/herdr_agent.py`.

    It NEVER blocks and never raises. Submitting is only half the mechanism: the
    prompt is queued while this turn is still running and executes when the turn
    ends, so a hook that blocked here would deadlock its own request. And a
    compaction helper that broke a turn would be worse than no compaction at all
    - the native auto-compact is armed behind this for exactly that reason.
    """
    if not (CP.auto_mode(state) or CP.unattended_mode(state)):
        return False
    if used < CP.config(state)["hard"]:
        return False
    bucket = int(state.get("offer_bucket") or state.get("current_bucket") or 0)
    if state.get("compact_requested_bucket") == bucket:
        return False

    session = CP.session_id(payload)
    # Ordering is the point: handoff first, boundary second. `last_offer_at` is
    # the field the save path stamps when it marks the offer delivered, and it
    # is the only recorded moment of the hard-threshold crossing.
    if not _handoff_since(project, session, state.get("last_offer_at")):
        return False

    try:
        pane = HA.resolve_pane(session)
    except HA.HerdrUnavailable as exc:
        # "Could not tell" is not "not hosted", and the state file must not
        # record it as though it were (.claude/rules/scope-claims.md).
        _persist(
            state_path,
            compact_request_error=str(exc),
            compact_request_error_at=CP.utc_now().isoformat(),
        )
        return False

    if pane is None:
        _persist(
            state_path,
            compact_host="not-hosted",
            compact_host_checked_at=CP.utc_now().isoformat(),
        )
        return False

    try:
        HA.submit_compact(pane)
    except HA.HerdrUnavailable as exc:
        _persist(
            state_path,
            compact_request_error=str(exc),
            compact_request_error_at=CP.utc_now().isoformat(),
        )
        return False

    now = CP.utc_now().isoformat()

    def _record(fresh: dict) -> dict:
        # The append-only list, not just the scalars. The probe correlates EVERY
        # boundary in a session against a request, and a scalar holds only the
        # most recent one - a session that compacts twice could never prove the
        # first.
        entries = fresh.get("compact_requests")
        if not isinstance(entries, list):
            entries = []
        entries.append({"at": now, "bucket": bucket, "pane": pane})
        fresh["compact_requests"] = entries[-CP.COMPACT_HISTORY_MAX:]
        return fresh

    _persist(
        state_path,
        compact_requested_at=now,
        compact_requested_bucket=bucket,
        compact_request_count=int(state.get("compact_request_count") or 0) + 1,
        compact_host=pane,
        _mutate=_record,
    )
    return True


def _pause_unattended(state: dict, state_path: Path, reason: str) -> int:
    """Stop continuing this stretch, and leave the operator's switch alone.

    Returning 0 IS the stop. Nothing continues unless this hook prints a block
    decision, so handing the turn back ends the run as completely as anything
    could; the session then sits idle until the operator speaks.

    Until 2026-08-19 this also called `CP.lower_unattended`, and that was the
    defect rather than the safety. The switch is the operator's statement that he
    is away. A hook that lowers it decides on his behalf that he has come back,
    and he has not - he is asleep. He reads the state from the status line in the
    morning and turns it off himself if the answer is off, which is the whole of
    what the automatic lowering ever bought. What it COST was the compaction
    path: that needs the switch up AND an `_handoff_auto_` file on disk, and the
    switch went down first every time.

    The record is written ONCE. Every later pause reaches this function again, and
    re-stamping would move the one fact the operator reads it for: `--unattended
    status` presents `unattended_paused_at` as the moment the stretch stopped, so
    a re-stamp turns an 03:00 finish into whatever time he happens to look.
    """
    if state.get("unattended_paused_at"):
        return 0

    _persist(
        state_path,
        unattended_paused_at=CP.utc_now().isoformat(),
        unattended_stop_reason=reason,
    )
    _notify_stall(reason)
    return 0


def unattended_turn(
    payload: dict,
    state: dict,
    state_path: Path,
    used: float,
    turn: str,
) -> int:
    """Wait for the operator, then either hand the turn back or continue it.

    Two things end a stretch FROM INSIDE THIS FUNCTION. The sentence used to
    read "two things end a stretch, and only these two", and that was measurably
    false: `main()`'s soft-threshold gate is a third, it leaves no record at
    all, and this docstring is what a reader consults before trusting the pair
    below to be exhaustive. See the gate's own comment in `main()`.

    The DONE MARKER is the primary one, and it is explicit: the assistant writes
    it with `scripts/checkpoint-paths.py --done "<note>"` when the plan is
    finished, and the continuation prose tells it to. It replaced a fingerprint
    heuristic on 2026-08-19. That heuristic asked whether any file had changed
    across three continuations and read a night of reading, research and thinking
    as a finished plan; it stopped all three unattended runs ever attempted, at
    three and five continuations, none of them anywhere near the ceiling. An
    explicit signal from the one party that knows the answer beats a proxy for it.

    The CEILING is the backstop for the marker never being written, and it is
    deliberately dumb. It stays at 100 pending one measured night: no run has ever
    reached the end of its work, so the number a real night needs is unknown, and
    `CLAUDE_HANDOFF_UNATTENDED_MAX` moves it without a code change.

    NEITHER lowers the operator's switch. See `_pause_unattended`.
    """
    maximum = CP.env_int(
        "CLAUDE_HANDOFF_UNATTENDED_MAX", 100, minimum=1, maximum=10000
    )

    # FIRST, before either end is consulted. A Stop whose `prompt_id` is not the
    # one this hook continued belongs to a turn the OPERATOR started, and his
    # instruction is a new stretch: it retires a done marker describing a plan he
    # has just replaced, and it resets a ceiling half-spent last night that would
    # otherwise cut tonight short.
    #
    # `prompt_id` is the signal because it is the only one that survives to the
    # Stop that matters. The operator typing during the grace period is visible to
    # `_wait_out_the_grace`, but that pause ends with the turn handed back and the
    # window uncleared; his message then opens a NEW turn, and by the Stop that
    # closes it the queue entry is long consumed. The turn identity is not.
    #
    # An EMPTY `turn` clears nothing. Without a prompt_id the comparison cannot
    # tell a fresh instruction from a continuation, and the fail-safe direction is
    # to leave the counters alone: clearing on every Stop would retire the ceiling
    # altogether, which is the one bound with no other backstop behind it.
    if turn and state.get("unattended_turn_id") != turn:
        def _new_window(fresh: dict) -> dict:
            CP.clear_unattended_window(fresh)
            return fresh

        _persist(state_path, _mutate=_new_window)
        state = CP.read_json(state_path)

    done = int(state.get("unattended_continuations") or 0)

    # Both ends are checked BEFORE the wait. A stretch that has already finished
    # should hand the turn back at once, not hold it for the grace period first.
    if state.get("unattended_done_at"):
        note = state.get("unattended_done_note") or "no note given"
        return _pause_unattended(state, state_path, f"the plan is finished: {note}")
    if done >= maximum:
        return _pause_unattended(
            state, state_path, f"reached the ceiling of {maximum} continuations"
        )

    session = CP.session_id(payload)
    spoke, granted = _wait_out_the_grace(payload, session)
    if spoke:
        return 0

    # Read BEFORE the persist below overwrites it: the question is whether a
    # compaction landed between the PREVIOUS continuation and this one.
    rebuilt = _context_was_rebuilt(state, state.get("unattended_last_at"))

    done += 1
    _persist(
        state_path,
        unattended_continuations=done,
        unattended_turn_id=turn,
        unattended_last_at=CP.utc_now().isoformat(),
    )

    # `remaining` and `compaction` were dropped from the template on 2026-08-19
    # and their arguments went with them, rather than staying as dead kwargs
    # `str.format` would silently accept.
    #
    # `wait` is what the wait ACTUALLY granted, not `CP.wait_seconds()`. The two
    # part company whenever `_effective_wait` shortened the window against the
    # registered hook timeout, and the sentence is read by the operator.
    # The standing rules go out once per window, not once per pause. See the
    # comment on UNATTENDED_WRAPPER_REPEAT.
    if done == 1 or rebuilt:
        reason = UNATTENDED_WRAPPER.format(
            used=used,
            wait=int(granted),
            done=done,
            maximum=maximum,
        )
    else:
        reason = UNATTENDED_WRAPPER_REPEAT.format(
            used=used,
            done=done,
            maximum=maximum,
        )
    print(json.dumps({"decision": "block", "reason": reason}, ensure_ascii=False))
    return 0


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0

    project = CP.project_root(payload)
    state_path = CP.state_path(project, CP.session_slug(payload))
    state = CP.read_json(state_path)
    unattended = CP.unattended_mode(state)

    # Anti-loop guard, mandatory for Stop hooks, with ONE exception. 2.1.228
    # warns a hook that blocks eight consecutive times and names this field as
    # the thing to check, and `stop_hook_active` stays true for the remainder of
    # a turn once anything blocked it. Unattended mode has to survive that or it
    # would continue exactly once per operator turn and then halt, which is the
    # behaviour it exists to end. So it ignores the flag ONLY on a turn this hook
    # itself continued, identified by the payload's own prompt_id. An absent
    # prompt_id is never a match: without it the comparison would be None
    # against None, which is a fail-open into an unbounded loop.
    #
    # The driven-compaction block needs the SAME exemption, by a different key,
    # and finding that out was the whole value of the 2026-08-19 scrutiny pass.
    # The block must fire on the Stop that FOLLOWS the handoff save, and on that
    # Stop `stop_hook_active` is true while `ours` is false, so this guard
    # returned before anything else ran and the capability was dead in both
    # modes. Crossing the guard for it is safe in a way crossing it generally is
    # not: `compaction_only` makes the hook evaluate that one block and return
    # WITHOUT printing a decision, so it cannot contribute to the consecutive
    # blocks this guard exists to bound.
    turn = str(payload.get("prompt_id") or "")
    ours = bool(turn) and state.get("unattended_turn_id") == turn
    compaction_only = False
    if payload.get("stop_hook_active") and not (unattended and ours):
        if not _driven_pending(state):
            return 0
        compaction_only = True

    # Resolved BEFORE the claimant check since 2026-08-19, because the claimant
    # decision now depends on it. Nothing else about this read changed.
    used = _used_percentage(state)
    if used is None:
        return 0

    # Something else already drives this Stop event: stay out of its way. Checked
    # before either path, and before the hysteresis marker further down, because
    # an offer that was never delivered must not be recorded as delivered - the
    # operator would lose that threshold's notice for good. See
    # `continuation_claimant` for the three signals, and for why /goal is not one.
    #
    # The courtesy has ONE limit, added 2026-08-19. Below the hard threshold a
    # claimant silences this hook entirely, and that is right: a scheduled wakeup
    # or in-flight background work should not have a second voice talking over it
    # for a notice that can wait a turn. At or above hard the notice cannot wait a
    # turn, because it is the last save before compaction frees the context. So
    # the claimant is still RECORDED and the hook continues rather than returning.
    # The unattended run that reached 617k tokens with nothing on disk left
    # through this return.
    claimant = CP.continuation_claimant(payload, project)
    if claimant:
        _persist(
            state_path,
            continuation_claimant=claimant,
            continuation_seen_at=CP.utc_now().isoformat(),
        )
        if used < CP.config(state)["hard"]:
            return 0

    # Evaluated HERE, and the placement is load-bearing rather than tidy. This is
    # the only point on the path that both modes reach on every Stop: below it
    # the unattended branch returns for the whole unattended path, and the
    # `needs_compact_offer` check returns for auto mode on precisely the turn
    # after a save - which is the turn this block exists for.
    submitted = _request_compaction(payload, state, state_path, project, used)
    if compaction_only:
        return 0

    # A submitted compaction ENDS this Stop, and that is the whole mechanism
    # rather than a courtesy.
    #
    # `HA.submit_compact` does not compact. It queues the literal `/compact` into
    # this session's own input, and the harness runs a queued prompt when the
    # current turn ENDS. Printing a block decision below is what stops the turn
    # from ending, so a hook that submits and then blocks has just guaranteed its
    # own request will never run - and the next Stop, on the next bucket,
    # submits another one behind it.
    #
    # Observed live on 2026-08-20 before this line existed: `compact_requests`
    # held two entries (07:41:02 bucket 55, 08:07:10 bucket 60), both to pane
    # w39:p1, neither with an error, and `compact_history` still ended at the
    # previous day's `trigger=auto` boundary. The transcript showed both
    # `enqueue` records with no matching `remove`, while every operator message
    # in the same file cleared within seconds. The mechanism could not compact
    # itself in auto mode, by construction, and reported success twice.
    if submitted:
        return 0

    if unattended:
        # The threshold itself is the gate here, not the once-per-bucket flag.
        # A bucket fires once per 5%, so a session would halt at the very next
        # pause and sleep until morning; the purpose of this mode is not to
        # announce a threshold once, it is to not halt.
        #
        # And the floor is SPENT once this session has actually compacted. The
        # skill documents the mode as engaging above the soft threshold, which
        # is right for a session that has not filled up yet and wrong for one
        # that already has. Measured end to end on 2026-08-20 with SOFT=40 /
        # HARD=45: the mode saved at 46%, drove its own compaction through
        # HERDR, the PostCompact hook reset the hysteresis, the statusline then
        # read 11% used - and the very next pause returned HERE, silently. No
        # `unattended_paused_at`, no `unattended_stop_reason`, no Telegram
        # notice, nothing for `--unattended status` to report. The mode died at
        # the exact moment it succeeded, and `OPTIONS_DETAIL` had told the
        # operator that from that threshold on "the session also works through
        # ordinary pauses".
        #
        # `last_compact_at` is the signal because it is the one that SURVIVES:
        # checkpoint-save.py resets `last_offered_bucket` to 0 and clears the
        # offer keys, and `clear_unattended_window` pops the continuation count
        # on the new prompt_id that the submitted `/compact` creates, so every
        # in-window counter is back to zero by the time this line runs again.
        #
        # What is deliberately NOT done here: the remaining pre-compaction case
        # still returns SILENTLY, without `_pause_unattended`'s record and
        # notice. Measured before deciding: this gate is reached at every Stop
        # of every unattended session below soft, and routing it through
        # `_pause_unattended` would fire one Telegram notice and stamp
        # `unattended_stop_reason` on a stretch that has not begun, which
        # `--unattended status` would then report all evening. Silence is
        # correct for a stretch that never started; it was only wrong for one
        # that had.
        if used < CP.config(state)["soft"] and not state.get("last_compact_at"):
            return 0

        # The one thing this mode never did. Until 2026-08-19 the line below
        # returned straight into `unattended_turn`, so `build_auto_reason` - the
        # only path in this file that causes a checkpoint to be written - was
        # unreachable in the one mode that exists for an absent operator. The
        # mode's own docstring said so plainly, and that sentence was the defect
        # rather than a caveat.
        #
        # Hard threshold only, and once per hysteresis bucket. This mode pauses
        # constantly; saving at every pause would write dozens of near-identical
        # archives. The bucket marker is the same one the attended path uses to
        # fire once per 5% band.
        if state.get("needs_compact_offer") and used >= CP.config(state)["hard"]:
            level = state.get("offer_level")
            # An `offer_level` that is neither falls through to the continuation
            # below WITHOUT consuming the bucket, so the save is retried at the
            # next pause and at every pause after. Measured 2026-08-20 with a
            # hand-set `offer_level: "bogus"` at 46% used: three consecutive
            # Stops all continued, `needs_compact_offer` stayed true, and
            # `last_offer_at` stayed unset - which also keeps `_handoff_since`
            # false, so the driven compaction never fires either. Left as is.
            # Only the statusline writes this key and it writes "soft" or "hard"
            # whenever it sets `needs_compact_offer`, so the state is reachable
            # only by hand-editing; and the alternative - picking a level here -
            # would invent the one fact the corrupt state failed to record.
            if level in ("soft", "hard"):
                bucket = int(
                    state.get("offer_bucket") or state.get("current_bucket") or 0
                )
                _persist(
                    state_path,
                    needs_compact_offer=False,
                    offer_level=None,
                    last_offered_bucket=bucket,
                    last_offer_at=CP.utc_now().isoformat(),
                    # Claims this turn for the driven-compaction block, which is
                    # carved out of the `stop_hook_active` guard exactly as this
                    # mode is. Without this key `ours` is False on the follow-up
                    # Stop and the guard returns before the block is reached, so
                    # the compaction never fires. The two are one mechanism.
                    unattended_turn_id=turn,
                )
                remaining = _remaining_percentage(state, used)
                reason = build_auto_reason(
                    level, used, remaining, state, state_path,
                    CP.session_id(payload),
                )
                print(
                    json.dumps(
                        {"decision": "block", "reason": reason}, ensure_ascii=False
                    )
                )
                return 0

        return unattended_turn(payload, state, state_path, used, turn)

    if not state.get("needs_compact_offer"):
        return 0

    level = state.get("offer_level")
    if level not in ("soft", "hard"):
        # Statusline always sets a valid level when needs_compact_offer=True;
        # missing here means stale state from before the contract - skip.
        return 0

    bucket = int(state.get("offer_bucket") or state.get("current_bucket") or 0)

    # Mark the offer delivered (hysteresis), through the same read-modify-write
    # every other write in this file uses. This path wrote back the whole copy it
    # read at entry, which is precisely what `_persist` exists to stop: the
    # statusline rewrites this file after every turn, so a whole-copy write can
    # undo whatever it recorded in between.
    _persist(
        state_path,
        needs_compact_offer=False,
        offer_level=None,
        last_offered_bucket=bucket,
        last_offer_at=CP.utc_now().isoformat(),
    )

    remaining = _remaining_percentage(state, used)

    # `state` is the entry-time copy and is only READ from here on, so the
    # operator's `session_auto` is still in hand.
    build = build_auto_reason if CP.config(state)["auto"] else build_reason
    reason = build(
        level, used, remaining, state, state_path, CP.session_id(payload)
    )

    print(json.dumps({"decision": "block", "reason": reason}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
