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
  - unattended on: ask nothing. Wait out a grace period, hand the turn back the
    moment the operator types, and otherwise tell the assistant to carry on.

The offer prompt carries the `auto` switch itself as a fourth option, because the
operator is already reading the list at the moment they decide the work is going
to be long. A command they have to remember is one they will not use.

Two guards stand ahead of all three. `stop_hook_active` is honoured, per 2.1.228's
own warning to a hook that blocks eight consecutive times, EXCEPT on a turn this
hook itself continued in unattended mode. And the hook stays silent whenever
something else already drives the Stop event: a scheduled wakeup, in-flight
background work, or a ralph-loop naming this session. See
`checkpoint_paths.continuation_claimant`, including why /goal is not detectable.

Compaction is NOT touched, here or anywhere: no hook in Claude Code can trigger
one. What unattended mode changes is whether the session is still RUNNING when
the harness's own auto-compact fires, which is the only reason a compaction lands
mid-work rather than never.
"""

import json
import os
import sys
import time
from pathlib import Path

_BOOT = Path(__file__).resolve()
for _candidate in [_BOOT.parent, *_BOOT.parents]:
    if (_candidate / "scripts" / "utils" / "checkpoint_paths.py").is_file():
        sys.path.insert(0, str(_candidate))
        break
from scripts.utils import checkpoint_paths as CP  # noqa: E402

CP.force_utf8()

SKILL_REF = ".claude/skills/checkpoint/SKILL.md"


SOFT_BODY = """\
Context is about {used:.0f}% used (~{remaining:.0f}% remaining).
Consider checkpointing now so you can resume later with a fresh context.

Options:
1. `/checkpoint` - save a summary and continuation prompt under outputs/operations/handoff-archive/, no compact.
2. `/checkpoint unattended on` - for work that runs past the time the operator is at the keyboard: saves silently, and at each pause waits for them and then carries on alone.
   Name `/checkpoint auto on` as well ONLY if they want the question to stop while STAYING at the keyboard: it saves silently and still hands the turn straight back, with no wait.
3. `/compact` - run a manual compact now; the post-compact hook will save the compact summary.
4. continue without compact - keep working as is.

Tell them this too, because it decides how much any of it matters: {compaction}"""


HARD_BODY = """\
Context is about {used:.0f}% used - hard threshold reached.
Strongly recommend a checkpoint or compact before continuing further.

Recommended options:
1. `/checkpoint` - save a summary and continuation prompt (preserves work; does not free context).
2. `/checkpoint unattended on` - if the operator is about to leave: saves silently, and at each pause waits for them and then carries on alone.
   Name `/checkpoint auto on` as well ONLY if they want the question to stop while STAYING at the keyboard: it saves silently and still hands the turn straight back, with no wait.
3. `/compact` - run a manual compact now; the post-compact hook will save the compact summary and free context.

Tell them this too, because it decides how much any of it matters: {compaction}

Do not offer "continue without compact"."""


REASON_WRAPPER = """\
Ask the user, briefly, in the language they are speaking, and wait for their decision. Do not act instead of asking: do not run /compact yourself, and write no file until they approve one.

{body}"""


AUTO_WRAPPER = """\
Context is about {used:.0f}% used (~{remaining:.0f}% remaining), which crossed the {level} checkpoint threshold. AUTO MODE is on.

Do this now, without asking:
1. Save a checkpoint silently, following @{skill} exactly: run `python scripts/checkpoint-paths.py` for this session's stamp and paths, write the archive it names, then update the two pointer files it names. Those paths are scoped to this session - never write into another session's pointer directory.
2. Print ONE line naming the archive path written.
3. If you were mid-task, resume it where you left off. If you had finished and were waiting for the user, stop after that line.

Do NOT run /compact yourself. {compaction}"""


def _compaction_sentence() -> str:
    """What this hook actually knows about where compaction fires.

    Nothing here can trigger compaction, and nothing here can observe the
    harness default either. Naming a percentage the environment never set is an
    assertion the method does not support, so an unconfigured environment gets
    said so instead (.claude/rules/scope-claims.md).
    """
    carry = (
        "Its PreCompact hook steers what that summary keeps and its PostCompact "
        "hook saves a handoff, so the session carries on by itself. What crosses "
        "a compaction is the SUMMARY: the handoff on disk is not re-injected "
        "afterwards, and is what a NEW session resumes from instead."
    )
    point = CP.compact_point()
    if point is None:
        return (
            "Native compaction is not configured here (neither "
            "CLAUDE_AUTOCOMPACT_PCT_OVERRIDE nor CLAUDE_CODE_AUTO_COMPACT_WINDOW "
            f"is set), so this hook cannot say when it will fire. {carry}"
        )
    kind, value = point
    if kind == "percent":
        return f"Native auto-compact is configured to fire near {value}% used. {carry}"
    return (
        f"Native auto-compact is configured at {value} tokens (measured against "
        f"the smaller of that and the model's own window). {carry}"
    )


def build_reason(level: str, used: float, remaining: float) -> str:
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
    return REASON_WRAPPER.format(
        body=body.format(
            used=used,
            remaining=remaining,
            compaction=_compaction_sentence(),
        )
    )


def build_auto_reason(level: str, used: float, remaining: float) -> str:
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
        compaction=_compaction_sentence(),
    )


UNATTENDED_WRAPPER = """\
Context is about {used:.0f}% used (~{remaining:.0f}% remaining). Unattended mode \
is on for this window, and the {wait}-second grace period passed with no input, \
so the turn continues rather than halting.

Decide this yourself, and do not put the question to anyone:
- If an unfinished task from the current objective remains, resume it now and \
carry on working.
- If the work is finished and verified, or you were waiting on a judgement only \
the operator can make, stop here and say so in one line.

A halted night is recoverable. Work invented to look busy is not, so stopping is \
the right answer whenever there is no real next action.

Do NOT run /compact. {compaction}

Continuation {done} of {maximum} for this window. Nothing was written on your \
behalf and the handoff on disk is unchanged."""


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
    for line in fresh.splitlines():
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
            if entry.get("operation") == "enqueue" and (
                not session or entry.get("sessionId") in (None, session)
            ):
                return True
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

    Counted rather than matched by content, because the enqueue and its remove
    are the only two records and a repeated message would defeat matching. The
    case this catches is the expensive one: the operator typed twenty seconds
    before the pause, the harness will hand that message over the moment the turn
    ends, and continuing here would overwrite an instruction he has already sent.
    """
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return True
    pending = 0
    for line in raw.splitlines():
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
        if entry.get("operation") == "enqueue":
            pending += 1
        elif entry.get("operation") == "remove":
            pending -= 1
    return pending > 0


def _wait_out_the_grace(payload: dict, session: str) -> bool:
    """True when the operator spoke, False when the window passed in silence.

    Unknown counts as spoke. An absent or unreadable transcript means silence
    cannot be told from a message, and the safe direction there is to hand the
    turn back rather than to continue blind.
    """
    raw_path = payload.get("transcript_path")
    if not raw_path:
        return True
    path = Path(raw_path)
    if not path.is_file():
        return True
    if _queue_pending(path, session):
        return True

    wait = CP.env_int("CLAUDE_HANDOFF_UNATTENDED_WAIT", 60, minimum=1, maximum=600)
    poll = CP.env_int("CLAUDE_HANDOFF_UNATTENDED_POLL", 2, minimum=1, maximum=60)
    try:
        offset = path.stat().st_size
    except OSError:
        return True

    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        time.sleep(min(poll, max(deadline - time.monotonic(), 0.1)))
        try:
            size = path.stat().st_size
        except OSError:
            return True
        if size <= offset:
            continue
        try:
            with path.open("r", encoding="utf-8", errors="replace") as handle:
                handle.seek(offset)
                fresh = handle.read()
        except OSError:
            return True
        offset = size
        if _operator_spoke(fresh, session):
            return True
    return False


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


def _persist(state_path: Path, **updates) -> None:
    """Apply our own keys to what is on disk NOW, never a whole stale copy.

    The statusline rewrites this file after every turn. Writing back the copy this
    hook read at entry would clobber whatever it recorded in between, and
    unattended mode writes at every pause above the threshold rather than once per
    5% bucket, so that window stopped being rare. Only the keys named by the caller
    move.
    """
    fresh = CP.read_json(state_path)
    fresh.update(updates)
    try:
        CP.write_json_atomic(state_path, fresh)
    except Exception as exc:  # noqa: BLE001 - a lost note is not worth a broken turn
        print(f"checkpoint-offer: state write failed: {exc}", file=sys.stderr)


def _stop_unattended(state: dict, state_path: Path, reason: str) -> int:
    """End the mode's autonomy and leave a record that says why."""
    first = not state.get("unattended_stalled_at")
    _persist(
        state_path,
        unattended_stalled_at=CP.utc_now().isoformat(),
        unattended_stop_reason=reason,
    )
    if first:
        _notify_stall(reason)
    return 0


def unattended_turn(
    payload: dict,
    state: dict,
    state_path: Path,
    project: Path,
    used: float,
    turn: str,
) -> int:
    """Wait for the operator, then either hand the turn back or continue it.

    Two bounds stand between this and a run that never ends, and they catch
    different failures. The no-progress fuse catches work that stopped moving
    while still answering "yes, there is more to do". The ceiling catches work
    that keeps moving and never converges, which is what the ralph-loop plugin
    bounds with --max-iterations for the same reason.
    """
    stall_limit = CP.env_int(
        "CLAUDE_HANDOFF_UNATTENDED_STALL", 3, minimum=1, maximum=100
    )
    maximum = CP.env_int(
        "CLAUDE_HANDOFF_UNATTENDED_MAX", 100, minimum=1, maximum=10000
    )
    done = int(state.get("unattended_continuations") or 0)

    # Both ceilings are checked BEFORE the wait. A window that has already spent
    # its autonomy should hand the turn back at once, not hold it for a minute
    # first.
    if done >= maximum:
        return _stop_unattended(
            state, state_path, f"reached the ceiling of {maximum} continuations"
        )
    if int(state.get("unattended_stall") or 0) >= stall_limit:
        return _stop_unattended(
            state,
            state_path,
            f"no progress across {stall_limit} consecutive continuations",
        )

    session = CP.session_id(payload)
    if _wait_out_the_grace(payload, session):
        return 0

    fingerprint = CP.progress_fingerprint(project, payload)
    moved = fingerprint != state.get("unattended_fingerprint")
    stall = 0 if moved else int(state.get("unattended_stall") or 0) + 1

    if stall >= stall_limit:
        _persist(state_path, unattended_stall=stall,
                 unattended_fingerprint=fingerprint)
        return _stop_unattended(
            state,
            state_path,
            f"no progress across {stall_limit} consecutive continuations",
        )

    done += 1
    _persist(
        state_path,
        unattended_stall=stall,
        unattended_fingerprint=fingerprint,
        unattended_continuations=done,
        unattended_turn_id=turn,
        unattended_last_at=CP.utc_now().isoformat(),
    )

    reason = UNATTENDED_WRAPPER.format(
        used=used,
        remaining=_remaining_percentage(state, used),
        wait=CP.env_int("CLAUDE_HANDOFF_UNATTENDED_WAIT", 60, minimum=1, maximum=600),
        done=done,
        maximum=maximum,
        compaction=_compaction_sentence(),
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
    turn = str(payload.get("prompt_id") or "")
    ours = bool(turn) and state.get("unattended_turn_id") == turn
    if payload.get("stop_hook_active") and not (unattended and ours):
        return 0

    # Something else already drives this Stop event: stay out of its way. Checked
    # before either path, and before the hysteresis marker further down, because
    # an offer that was never delivered must not be recorded as delivered - the
    # operator would lose that threshold's notice for good. See
    # `continuation_claimant` for the three signals, and for why /goal is not one.
    claimant = CP.continuation_claimant(payload, project)
    if claimant:
        _persist(
            state_path,
            continuation_claimant=claimant,
            continuation_seen_at=CP.utc_now().isoformat(),
        )
        return 0

    used = _used_percentage(state)
    if used is None:
        return 0

    if unattended:
        # The threshold itself is the gate here, not the once-per-bucket flag.
        # A bucket fires once per 5%, so a session would halt at the very next
        # pause and sleep until morning; the purpose of this mode is not to
        # announce a threshold once, it is to not halt.
        if used < CP.config(state)["soft"]:
            return 0
        return unattended_turn(payload, state, state_path, project, used, turn)

    if not state.get("needs_compact_offer"):
        return 0

    level = state.get("offer_level")
    if level not in ("soft", "hard"):
        # Statusline always sets a valid level when needs_compact_offer=True;
        # missing here means stale state from before the contract - skip.
        return 0

    bucket = int(state.get("offer_bucket") or state.get("current_bucket") or 0)

    # Mark offer as delivered (hysteresis)
    state["needs_compact_offer"] = False
    state["offer_level"] = None
    state["last_offered_bucket"] = bucket
    state["last_offer_at"] = CP.utc_now().isoformat()
    try:
        CP.write_json_atomic(state_path, state)
    except Exception as exc:
        # If state write fails, still deliver the offer this turn
        print(f"checkpoint-offer: state write failed: {exc}", file=sys.stderr)

    remaining = _remaining_percentage(state, used)

    # `state` is the copy read BEFORE the delivered-marker write above, and the
    # marker write preserves every key it does not name, so the operator's
    # `session_auto` is still in hand here.
    build = build_auto_reason if CP.config(state)["auto"] else build_reason
    reason = build(level, used, remaining)

    print(json.dumps({"decision": "block", "reason": reason}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
