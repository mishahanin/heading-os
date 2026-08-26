#!/usr/bin/env python3
"""
checkpoint-save.py - Claude Code PostCompact hook (matcher: manual|auto).

Writes a combined handoff file (summary + continuation prompt) to
outputs/operations/handoff-archive/ after a compact event - manual OR auto.
Auto-compact remains enabled as last resort; this hook ensures a resume
artifact is captured either way.

Also updates TWO pointer surfaces, because they answer different questions:

  - .latest/<session-slug>/{summary,prompt}.md - THIS session's pointer, the
    only one checkpoint-inject.py will inject. Shared until 2026-08-16, which
    let a resumed session be handed another session's handoff.
  - .latest/{summary,prompt}.md - the workspace's newest handoff, read by
    scripts/next-signal.py for /next. Here last-writer-wins is the right
    answer, not a race, so this pair stays.

    Last-writer-wins holds PER FILE and not for the pair, and the difference was
    measured on 2026-08-20 rather than reasoned about: two hook processes were
    run concurrently with one of them delayed between its two shared writes, and
    the result was a shared summary.md naming session B's archive beside a shared
    prompt.md naming session A's - a mixed state neither writer produced. The
    per-session dirs stayed correct throughout, which is the 2026-08-16 fix
    doing its job. The pair IS written under one lock, `.latest/.pointers.lock`,
    which is bounded and degrades to an unlocked write on expiry (`CP.file_lock`),
    and `tests/test_checkpoint_state_lock.py::test_the_shared_pointer_pair_cannot_be_torn`
    holds it.

    This paragraph argued the opposite until 2026-08-25 - that the pair was "left
    unlocked deliberately" and that "a lock here would add a failure mode" - while
    the code three hundred lines down already took the lock. A maintainer reading
    the module docstring first, which is what anyone opening this file does, would
    have concluded the race was live and unfixed, or removed the lock as the
    unjustified failure mode this text warned against.

Resets hysteresis state in .claude/state/checkpoint-<session-slug>.json so the
post-compact session starts fresh, and prunes the per-session artifacts of
sessions that are long gone.
"""

import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# Walk to the tree that owns scripts/, rather than counting parents. This hook
# sits at .claude/hooks/ in the monorepo and at hooks/ inside a built plugin
# bundle, so a fixed parent count resolves wrongly in one of the two layouts -
# and here a failed import costs a handoff nobody can regenerate.
_BOOT = Path(__file__).resolve()
WORKSPACE = _BOOT.parent.parent.parent
for _candidate in [_BOOT.parent, *_BOOT.parents]:
    if (_candidate / "scripts" / "utils" / "checkpoint_paths.py").is_file():
        WORKSPACE = _candidate
        break
sys.path.insert(0, str(WORKSPACE))
from scripts.utils import checkpoint_paths as CP  # noqa: E402
from scripts.utils.workspace import (  # noqa: E402
    data_overlay_present,
    get_data_root,
)

# Guarded, and the guard is not defensive habit. This plan's own constraint says
# a lost handoff is worse than an unredacted one, because the hook runs after the
# session context is discarded and nobody can regenerate what it fails to write.
# An UNGUARDED import contradicts that constraint directly: if the module fails
# to import, checkpoint-save.py does not load at all and NO handoff is written.
# The try/except in main() would never run, because main() would never be
# reached. Caught at the pre-impl gate.
#
# Exception is caught broadly on purpose, matching _dispatch.py's reasoning for
# its own guarded import: a SyntaxError in a module this one imports is as fatal
# as an ImportError, and both cost the handoff. The failure is never silent.
try:
    from scripts.utils.secret_patterns import redact  # noqa: E402
except Exception as _exc:  # noqa: BLE001 - never lose the handoff
    print(f"checkpoint-save: redaction unavailable ({type(_exc).__name__}): {_exc}",
          file=sys.stderr)
    _REDACT_UNAVAILABLE = _exc

    def redact(_text):  # type: ignore[misc]
        # RAISES rather than returning the text unchanged, and the difference
        # matters. An identity fallback would let main() proceed as if redaction
        # had succeeded and write the raw summary into the TRACKED archive,
        # which is precisely the incident this slice removes. Raising routes the
        # handoff to the quarantine path instead: memory preserved, tracked tree
        # clean, backup unblocked.
        raise RuntimeError(f"redaction module unavailable: {_REDACT_UNAVAILABLE}")

# Handoff archive is DATA -> resolves under the data root (sibling), not the engine.
# @-reference paths must therefore be data-root-relative (outputs/...), NOT
# engine-relative: archive_path lives under the data sibling, so relative_to(WORKSPACE)
# would raise ValueError. The data-path-redirect hook resolves the outputs/... ref.
_ENGINE_TREE = CP.is_engine_tree(WORKSPACE)
# ONE call, not a second copy of the branch. This read
# `get_outputs_dir() / "operations" / "handoff-archive" if _ENGINE_TREE else
# CP.handoff_dir(...)` until 2026-08-27, which is `handoff_dir()`'s own engine
# branch written out again -- and that is exactly what made a fix to
# `handoff_dir()` invisible here. With no private overlay `get_outputs_dir()`
# falls to `<workspace_root>/examples`, so this line wrote whole session
# handoffs into the public engine clone. Measured on a worktree with no sibling
# overlay: five of them, after `handoff_dir()` had already been fixed.
HANDOFF_DIR = CP.handoff_dir(CP.project_root(), WORKSPACE)
LATEST_DIR = HANDOFF_DIR / ".latest"
QUARANTINE_DIR = HANDOFF_DIR / ".quarantine"


def state_dir_for(payload: dict) -> Path:
    """`.claude/state/` of the tree THIS session is working in.

    Derived from the payload rather than from this file's location, for the same
    reason the statusline does it: the two must agree on one directory, and in a
    plugin bundle the hook's own location is the plugin cache, not the operator's
    repository. Tests redirect it with CLAUDE_PROJECT_DIR.
    """
    return CP.project_root(payload) / ".claude" / "state"


def safe_slug(value: str, max_len: int = 32) -> str:
    cleaned = "".join(
        ch if ch.isalnum() or ch in ("-", "_") else "-" for ch in (value or "")
    )
    return cleaned[:max_len].strip("-") or "session"


def write_text_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=path.name + ".", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass
        raise


def write_json_atomic(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=path.name + ".", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass
        raise


def redacted_field(value: str) -> str:
    """redact() plus the check that a redactor which RETURNS something broken is
    a failure too.

    The guarded import above covers the raising case only. A redact() returning
    None never raises, and once wrote an archive whose entire Summary section
    was the literal string "None": the handoff destroyed, stderr silent, the
    systemMessage reporting success. Raising here routes that into the same
    quarantine as any other failure.

    Looks `redact` up as a module global on every call, so a test that swaps it
    is exercising the real path.
    """
    out = redact(value)
    if not isinstance(out, str):
        raise TypeError(f"redact() returned {type(out).__name__}, expected str")
    return out


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception as exc:
        # Generic systemMessage; full exception goes to stderr to avoid
        # leaking sensitive paths or tokens into Claude's surfaced output.
        print(f"checkpoint-save: payload parse error: {exc}", file=sys.stderr)
        print(
            json.dumps(
                {"systemMessage": "checkpoint-save: payload parse error (see stderr)"}
            )
        )
        return 0

    # A payload that is valid JSON but not an object still has `.get` called on
    # it. `[]`, `"x"` and `3` all parse, then raise an uncaught AttributeError.
    # Measured 2026-08-23 with `echo '[]' | python <hook>`: exit 1, traceback.
    # `.claude/hooks/checkpoint-inject.py` fixed this shape on 2026-08-20 and
    # these were missed. Degrade to the empty dict, which every path below
    # already handles, rather than dropping the hook's whole job.
    if not isinstance(payload, dict):
        print(f"checkpoint-save: payload was {type(payload).__name__}, not an "
              "object; continuing with defaults", file=sys.stderr)
        payload = {}

    # Coerce the FIELDS, the way the payload itself is coerced two lines above.
    # `.strip()` on `payload.get("compact_summary")` sits outside every try block
    # in this file, so `{"compact_summary": {"a": 1}}` exited 1 with an
    # AttributeError before anything was written: no archive, no quarantine, no
    # `.latest` pointer update (so the next session silently resumed from the
    # PREVIOUS compact's pointer), and no systemMessage - only a traceback on a
    # stream this file itself notes is read by no one. The hook's one rule is
    # that a handoff must never be lost.
    def _text(key: str, default: str = "") -> str:
        value = payload.get(key, default)
        if isinstance(value, str):
            return value
        if value is not None:
            print(f"checkpoint-save: {key} arrived as {type(value).__name__}, "
                  f"not a string; using {default!r}", file=sys.stderr)
        return default

    raw_session_id = _text("session_id", "session")
    raw_trigger = _text("trigger", "unknown")
    raw_transcript_path = _text("transcript_path", "")
    compact_summary = _text("compact_summary").strip()

    # Two clocks, on purpose, per the workspace's datetime split.
    #
    # `now` is SERIALIZED: it goes into the `Generated:` header (an ISO string
    # that carries its own offset, parsed by scripts/next-signal.py and
    # documented as UTC in the skill's handoff-template.md) and into
    # `last_compact_at` in the state JSON. UTC is right for all of those.
    #
    # `stamp` is DISPLAY: it becomes the archive FILENAME, which is a calendar
    # day the operator reads and browses. It used `now` until 2026-08-20, and on
    # an operator at UTC+4 that filed a handoff written at 02:56 local as
    # `2026-08-19-2256..` — under the previous day. Midnight to 04:00 local is
    # when this operator works, so it was most nights. Nothing reads the stamp
    # back (every consumer orders by st_mtime), so the change is forward-only
    # and cannot reorder the archive.
    now = datetime.now(timezone.utc)
    stamp = CP.local_now().strftime("%Y-%m-%d-%H%M%S")

    # Redact BEFORE the text reaches any file. The archive is tracked, and a
    # credential-shaped string reaching it blocks push-all's content scan and
    # therefore the whole backup. Measured on 2026-07-31, when this hook wrote
    # the two files that refused the push.
    #
    # ALL FOUR payload fields, not just the summary. trigger, session_id and
    # transcript_path go into the same tracked files verbatim, and each one was
    # measured driving the real content_scan to a refusal on its own: a poisoned
    # trigger refused with findings in three files, a poisoned session_id in
    # four (it survives safe_slug into the FILENAME, which the body, both
    # pointers and the state entry then quote), a poisoned transcript_path in
    # one. Redacting the summary alone left three doors open into the same
    # incident.
    #
    # Best-effort on purpose. This hook runs after the session's context has
    # been discarded, so a handoff it fails to write is gone for good, and that
    # is worse than an unredacted one: the push-time scan still refuses to let
    # a real secret off the machine.
    summary_text = compact_summary or "_No compact summary provided._"
    quarantine_kind = None
    try:
        summary_text = redacted_field(summary_text)
        trigger = redacted_field(raw_trigger)
        session_id = redacted_field(raw_session_id)
        transcript_path = redacted_field(raw_transcript_path)
    except Exception as exc:  # noqa: BLE001 - never lose the handoff
        # Nothing was redacted, so the raw values are used for the QUARANTINED
        # body only, and never for anything a tracked artifact reproduces.
        trigger = raw_trigger
        session_id = raw_session_id
        transcript_path = raw_transcript_path
        # The TYPE goes to the tracked pointer; the MESSAGE goes to stderr only,
        # and the split is the whole point. An exception message is a channel
        # that can carry the summary text - `raise ValueError("failed on input: "
        # + text)` is an ordinary shape - and the premise of the quarantine is
        # that nothing outside it reproduces text that could not be redacted.
        # stderr is not tracked, so the full message is safe there and nowhere
        # else.
        quarantine_kind = type(exc).__name__
        print(f"checkpoint-save: redaction failed ({quarantine_kind}: {exc}); "
              f"QUARANTINING the handoff", file=sys.stderr)

    # The filename, and with it every artifact that quotes the filename.
    #
    # safe_slug keeps alphanumerics, "-" and "_" verbatim, which is the whole
    # of an API key, so a poisoned session_id rode the name into four tracked
    # artifacts. On the success branch the slug is taken from the REDACTED value
    # (a marker slugs down to something like REDACTED--Anthropic-API-key, still
    # a sane name). On the quarantine branch nothing was redacted, so a fixed
    # literal is used instead: the quarantine file's own name is reproduced in
    # the tracked pointer, and an unredacted slug there would poison the very
    # file the quarantine exists to keep clean.
    if quarantine_kind is None:
        session_slug = safe_slug(session_id)
        # `safe_slug("")` returns "session", never "", so the `or "unknown"` this
        # line carried until 2026-08-20 could not fire: measured by calling
        # safe_slug("") directly, and by a payload carrying `"trigger": ""`,
        # which named the file `..._handoff_compact-session_...`. A trigger slug
        # reading "session" says nothing about the trigger and reads like the
        # session field one column over, so the fallback is applied to the VALUE,
        # where it is reachable, instead of to a return that is never falsy.
        trigger_slug = safe_slug(trigger or "unknown", max_len=12)
        pointer_trigger = trigger
    else:
        session_slug = "unredacted"
        trigger_slug = "quarantine"
        pointer_trigger = "(withheld: unredacted)"

    archive_name = f"{stamp}_handoff_compact-{trigger_slug}_{session_slug}.md"
    # Where this bundle actually writes. In the monorepo the module-level values
    # are already right and the sandboxed tests read them. Inside a built plugin
    # bundle the archive belongs to the CONSUMER's repository, which only the
    # payload names: resolving it from this file's location would write the
    # stranger's handoff into the plugin cache, or worse, into whatever data
    # root the environment happened to carry. Measured on 2026-08-16 against the
    # first built bundle, which wrote into the operator's own live archive.
    # Unconditional since 2026-08-27. With a real overlay `handoff_dir()` ignores
    # the project and answers the data seam, so recomputing costs nothing there;
    # without one the project IS the base and the payload names it.
    hdir = CP.handoff_dir(CP.project_root(payload), WORKSPACE)
    latest_dir = hdir / ".latest"
    quarantine_dir = hdir / ".quarantine"

    archive_path = hdir / archive_name
    quarantine_path = quarantine_dir / archive_name

    # Refs. Every path any artifact NAMES is one of these three, so a channel
    # can only name something that was actually written.
    #
    # One surface names the same file twice, and the comment used to deny it.
    # The quarantine pointer gives the ref AND, on a labelled line of its own,
    # the ABSOLUTE form of that ref: the ref is what every other surface uses,
    # and the absolute path is what a human recovering the file can paste into a
    # shell that knows nothing about the data root. Two spellings of one written
    # file, stated as such rather than left to be inferred.
    # The refs are relative to whatever root the archive ACTUALLY landed under.
    # `get_data_root()` is right only when the archive followed the data seam; on
    # an engine clone with no overlay the archive is project-local and this would
    # send every ref down `_ref`'s absolute fallback.
    data_root = (get_data_root() if _ENGINE_TREE and data_overlay_present()
                 else CP.project_root(payload))

    def _ref(path: Path) -> str:
        """Data-root-relative when it can be, absolute when it cannot. Total.

        HANDOFF_DIR resolves through get_outputs_dir(), which on a non-CEO
        workspace prefers the exec's slug-named data sibling; the refs are
        computed against get_data_root(), which never reads the workspace
        identity and stops at the generic one. The two diverge on any exec
        workspace that has not pinned HEADING_OS_DATA, and provisioning does not
        pin it, so nothing warns.

        A bare relative_to() raised there, uncaught, BEFORE a single byte was
        written: no archive, no quarantine, no pointer and no systemMessage.
        This hook runs after the session's context has been discarded, so that
        is the one loss nobody can undo. An absolute ref is an uglier string and
        an incomparably better outcome.
        """
        try:
            return path.relative_to(data_root).as_posix()
        except ValueError:
            return path.as_posix()

    archive_ref = _ref(archive_path)
    quarantine_ref = _ref(quarantine_path)
    summary_ref = _ref(latest_dir / "summary.md")

    # The body names where IT actually landed. archive_md is built once and
    # written down one of two branches, so an unconditional archive_ref here
    # tells a human recovering the QUARANTINED file to open a dated archive
    # that was never written.
    if quarantine_kind is None:
        body_lead = f"""First read:

@{archive_ref}

Then continue the latest unfinished task."""
    else:
        body_lead = f"""You are reading the quarantined handoff itself, at:

{quarantine_ref}

Redaction failed ({quarantine_kind}), so this file is UNREDACTED and sits
outside the backup. No dated archive file was written. Never copy this text
into a tracked file. Then continue the latest unfinished task."""

    archive_md = f"""# Handoff - post-compact ({trigger})

Generated: {now.isoformat()}
Trigger: compact / {trigger}
Session: {session_id}
Transcript: {transcript_path}

## Summary

{summary_text}

## Continuation prompt

Continue this Claude Code session from the saved handoff.

{body_lead}

Rules:
1. Treat repository state as authoritative.
2. Do not redo broad discovery unless the summary is insufficient.
3. Before making changes, briefly restate the current objective, constraints, files involved, and next concrete action.
4. Continue implementation from the current repo state.

## Notes

This handoff was generated automatically after a {trigger} compact event.
Repository state is authoritative; this file is supporting context.
"""

    # The body is written FIRST, and its outcome decides what the pointers say.
    #
    # Until this slice both pointer writes sat inside the same try as the body,
    # so a body that could not be written suppressed them as collateral: the
    # PREVIOUS compact's .latest/summary.md survived untouched and the next
    # session resumed from a stale handoff with nothing marking it stale.
    # Measured by making .quarantine a regular file so its mkdir fails - return
    # code 0, a systemMessage reading "write failed", and the handoff gone.
    lost_kind = None
    try:
        if quarantine_kind is None:
            write_text_atomic(archive_path, archive_md)
        else:
            # QUARANTINE, not a raw write into the tracked archive.
            #
            # The obvious fallback, writing the unredacted summary where it
            # normally goes, RESURRECTS the incident this slice exists to
            # remove: the wall refuses, the backup of the irreplaceable half of
            # the workspace is blocked, and nobody finds out because this hook's
            # stderr is read by no one. Rarer than before and undiagnosed is a
            # worse failure than the original, not a better one.
            #
            # So the memory is preserved OUTSIDE the tracked tree and the wall
            # is left unarmed. What lands at the normal pointer path is a
            # POINTER carrying no summary text at all, and the tracked tree
            # stays clean. This is an alarm state, not the permanent hiding that
            # gitignoring the whole archive would have been.
            #
            # The SessionStart inject does NOT reach the next session on this
            # branch, and the sentence above claimed it did until 2026-08-25.
            # The artifact slug here is the literal "unredacted", so the
            # per-session pointer pair lands at `.latest/unredacted/`, while
            # `checkpoint-inject.py` reads only `.latest/<session_slug(payload)>/`
            # - the slug of the RAW id. The two can never match.
            #
            # That is left as it is, deliberately. Pointing the pair at the raw
            # slug would build a TRACKED directory name out of an id that failed
            # redaction, which is the one thing this branch exists to prevent;
            # the state file at line ~636 can use the raw slug precisely because
            # `.claude/state/` is gitignored, and `.latest/` is not. So the alarm
            # reaches the operator through the systemMessage and the shared
            # `.latest/summary.md`, which is what the note 165 lines below has
            # said all along, and through nothing else.
            write_text_atomic(quarantine_path, archive_md)
    except Exception as exc:  # noqa: BLE001 - the pointers must still be told
        # The MESSAGE stays on stderr for the same reason it does above: it can
        # carry the summary text, and on this path there is not even a
        # quarantine file to hold it.
        lost_kind = type(exc).__name__
        print(f"checkpoint-save: the handoff body could not be written "
              f"({lost_kind}: {exc}); the handoff is LOST", file=sys.stderr)

    if lost_kind is not None:
        # The worst outcome the frozen contract names, and the one the previous
        # shape reported as an ordinary write failure. There is no archive, no
        # quarantine, and no way to regenerate the text, because this hook runs
        # after the session's context has been discarded. The pointer's only job
        # now is to be TRUE and not to be last week's handoff.
        summary_pointer = f"""# Latest handoff summary

Source: (none - the handoff body was never written)
Generated: {now.isoformat()}
Trigger: compact / {pointer_trigger}

## Objective

THE HANDOFF WAS LOST ({lost_kind}): the post-compact body could not be written, so there is no archive file and no quarantine file. The summary is unrecoverable.

## Next steps

- Do not resume from this pointer. There is no handoff text anywhere on disk.
- Treat the repository state and the most recent commits as the only record of where the session was.
- Read stderr from the failed compact for the underlying error, then fix it before the next one.

## Notes

The summary text is deliberately not reproduced here. It may never have been redacted, this file is tracked, and there is no quarantine on this path to hold it instead.
"""

        prompt_pointer = f"""Continue this Claude Code session with NO saved handoff.

THE LAST HANDOFF WAS LOST ({lost_kind}). The post-compact write failed, so no
archive file and no quarantine file was written and the summary is
unrecoverable. There is nothing to read.

Reconstruct the objective from the repository state and the most recent commits,
restate it, and confirm it before making any change.

Rules:
1. Treat repository state as authoritative.
2. Do not assume any earlier plan survived; nothing of it was saved.
3. Before making changes, briefly restate the current objective, constraints, files involved, and next concrete action.
"""
    elif quarantine_kind is None:
        # The two headings are not decoration: scripts/next-signal.py's
        # read_handoff() renders /next's strongest-signal block out of
        # `## Objective` and `## Next steps`, and this pointer carried neither,
        # so /next printed its handoff header over nothing after EVERY
        # successful compact, for the whole life of the archive. Measured
        # against the live archive on 2026-07-31: 20 of the 20 newest handoffs
        # parsed to an empty objective and zero steps.
        #
        # The hook cannot know the session's objective, so it does not invent
        # one. It states the one thing it does know, and points at the text
        # that carries the rest. The summary keeps its own heading and stays
        # last, so a body that happens to contain its own `## ` heading cannot
        # displace the fields above it.
        summary_pointer = f"""# Latest handoff summary

Source: {archive_ref}
Generated: {now.isoformat()}
Trigger: compact / {pointer_trigger}

## Objective

Resume the work this session was doing when it compacted. The full summary is under Summary below, and in the archive file named above.

## Next steps

- Read the archived handoff at {archive_ref} for the full summary.
- Treat the repository state and the most recent commits as authoritative.

## Summary

{CP.bound_summary(summary_text, archive_ref)}
"""

        prompt_pointer = f"""Continue this Claude Code session from the saved handoff.

First read:

@{archive_ref}

Then continue the latest unfinished task.

Rules:
1. Treat repository state as authoritative.
2. Do not redo broad discovery unless the summary is insufficient.
3. Before making changes, briefly restate the current objective, constraints, files involved, and next concrete action.
4. Continue implementation from the current repo state.
"""
    else:
        # The alarm state, written in the shape the readers actually parse.
        #
        # Source / Generated / "## Objective" / "## Next steps" are what
        # scripts/next-signal.py read_handoff() looks for, and render_text()
        # prints only the objective and the steps. A pointer carrying none of
        # them made /next print its "Handoff (strongest signal)" header with
        # nothing under it, so the loudest surface the operator has rendered the
        # alarm as blank. Measured.
        #
        # Only the exception TYPE appears here. The message stays on stderr.
        summary_pointer = f"""# Latest handoff summary

Source: {quarantine_ref}
Generated: {now.isoformat()}
Trigger: compact / {pointer_trigger}

## Objective

REDACTION FAILED ({quarantine_kind}), so this handoff was QUARANTINED: it is NOT in the archive and NOT in the backup.

## Next steps

- Read the quarantined handoff at: {quarantine_ref}
- Absolute path, for a shell: {quarantine_path}
- Treat it as UNREDACTED - it may carry live credentials, so never copy it into a tracked file.
- Fix the redactor (scripts/utils/secret_patterns.py), then re-file the handoff into the archive once it redacts clean.

## Notes

The summary text is deliberately not reproduced here: this file is tracked, and copying an unredacted summary into it is the exact failure that made the quarantine necessary. The exception message is on stderr only, because a message can itself carry the summary text.
"""

        prompt_pointer = f"""Continue this Claude Code session from the QUARANTINED handoff.

Redaction failed on the last compact, so no dated archive file was written. The
full handoff text is UNREDACTED and quarantined outside the tracked tree at:

{quarantine_ref}

First read:

@{summary_ref}

Then continue the latest unfinished task.

Rules:
1. Treat repository state as authoritative.
2. Do not redo broad discovery unless the summary is insufficient.
3. Before making changes, briefly restate the current objective, constraints, files involved, and next concrete action.
4. Continue implementation from the current repo state.
5. Never copy the quarantined text into a tracked file.
"""

    # TWO surfaces, one text. The shared pair is what /next reads ("the newest
    # handoff in this workspace"); the per-session dir is what the inject hook
    # reads ("the handoff of THIS session"), and only the second one is safe to
    # push into a resumed session's context.
    #
    # The dir is named with the SAME slug as the archive file, which on the
    # success path is derived from the REDACTED session id. In the pathological
    # case where the id itself carried a credential the two differ, the inject
    # hook finds nothing, and that is the intended direction: a poisoned string
    # must not become a tracked path just to make an injection convenient. The
    # quarantine branch is louder still - it slugs to the fixed literal, so the
    # alarm reaches the operator through the systemMessage and the shared
    # pointer rather than through a path built from unredacted text.
    pointers_kind = None
    try:
        # The SHARED pair goes under one lock. Each file is written atomically on
        # its own, which is not the same as the two of them landing together:
        # they are two `os.replace` calls back to back, and two sessions
        # compacting at once can interleave between them. The result is
        # `summary.md` naming one session's archive while `prompt.md` names
        # another's - a state neither session ever held, and the one a resumed
        # session reads. Reproduced in tests/test_checkpoint_state_lock.py.
        #
        # The per-session pair below needs no lock: one session writes it.
        with CP.file_lock(latest_dir / ".pointers.lock"):
            write_text_atomic(latest_dir / "summary.md", summary_pointer)
            write_text_atomic(latest_dir / "prompt.md", prompt_pointer)
        write_text_atomic(latest_dir / session_slug / "summary.md", summary_pointer)
        write_text_atomic(latest_dir / session_slug / "prompt.md", prompt_pointer)
    except Exception as exc:  # noqa: BLE001 - the systemMessage must still report
        # Generic systemMessage; full exception goes to stderr to avoid
        # leaking sensitive paths in Claude's surfaced output.
        pointers_kind = type(exc).__name__
        print(f"checkpoint-save: the .latest pointers could not be written "
              f"({pointers_kind}: {exc}); they are STALE", file=sys.stderr)

    # Reset THIS session's hysteresis so the post-compact session starts clean.
    # A shared state file here reset a sibling session's bucket too, which is
    # half of why the offer landed in the wrong session.
    #
    # `CP.session_slug(payload)`, NOT the artifact slug above. The two answer
    # different questions and were the same variable until 2026-08-20.
    #
    # The artifact slug is deliberately not the raw one: on the success branch it
    # comes from the REDACTED id, and on the quarantine branch it is the literal
    # "unredacted". That is right for a TRACKED path, where a poisoned string
    # would ride the filename into the data repo and the push scan.
    #
    # It is wrong here, and the cost was measured: a save with a raising redactor
    # wrote `.claude/state/checkpoint-unredacted.json` and left the real session's
    # file untouched, so its hysteresis was never reset and its compaction never
    # recorded. The success branch has the same shape whenever redaction alters
    # the id.
    #
    # The argument for keeping the literal was that a raw-derived name puts an
    # unredacted string into a filename. It does not close that door: the
    # statusline, the Stop hook and `scripts/checkpoint-paths.py` ALL key off
    # `safe_slug(raw session id)` unconditionally, so that name is already on
    # disk and rewritten on every render. This hook writing somewhere else
    # prevents nothing; it only means the reset lands on a file nobody reads. And
    # `.claude/state/` is gitignored (`.gitignore:224`), so nothing here reaches
    # a repository at all - which is the door the archive name closed, and a
    # different one.
    state_dir = state_dir_for(payload)
    state_path = state_dir / f"checkpoint-{CP.session_slug(payload)}.json"
    try:
        # The whole read-modify-write sits inside the lock, and the mutation
        # happens on the locked object rather than on a copy merged in
        # afterwards. Two reasons, and the second is the one a merge cannot
        # satisfy: the statusline writes this file on every render, so a copy
        # read outside the lock is already stale; and `record_compaction`
        # DELETES the statusline's running peak, which `update` has no way to
        # express.
        with CP.locked_state(state_path) as cs:
            # Close the running peak into a dated record BEFORE the reset below.
            # This is the only moment the level compaction fired at still exists
            # anywhere: the next statusline render writes the post-compact
            # reading and the pre-compact one is gone for good.
            CP.record_compaction(cs, now.isoformat(), pointer_trigger)
            cs.update(
                {
                    "needs_compact_offer": False,
                    "offer_level": None,
                    "offer_bucket": None,
                    "last_offered_bucket": 0,
                    "last_compact_at": now.isoformat(),
                    "last_compact_trigger": pointer_trigger,
                    # The path that EXISTS. Recording the dated archive on the
                    # quarantine branch left a dangling pointer in state, naming
                    # a file no branch had written. On the lost path no body file
                    # exists at all, so the entry is cleared rather than pointed
                    # at either candidate.
                    "last_compact_summary_path": (
                        None if lost_kind is not None
                        else archive_ref if quarantine_kind is None
                        else quarantine_ref
                    ),
                }
            )
    except Exception as exc:
        # State reset failure is non-fatal
        print(f"checkpoint-save: state reset failed: {exc}", file=sys.stderr)

    # One pointer dir and one state file per session, never revisited once that
    # session ends. Pruning runs last and reports rather than raises: it is
    # housekeeping, and nothing about it is worth costing a handoff. Only the
    # disposable surfaces are touched - the dated archives are the record.
    try:
        CP.prune_pointer_dirs(hdir, session_slug)
        CP.prune_state_dir(state_dir, state_path.name)
    except Exception as exc:  # noqa: BLE001 - housekeeping never breaks the save
        print(f"checkpoint-save: prune failed: {exc}", file=sys.stderr)

    # The one channel the operator and the assistant actually see. On the alarm
    # path it used to report a save and name a file that was never written,
    # which made the quarantine silent - and loudness is the entire reason to
    # quarantine rather than write the summary raw. On the worst path it said
    # "write failed", which reads like a retryable hiccup rather than what it
    # was: the handoff gone, unrecoverably.
    if lost_kind is not None:
        message = (
            f"HANDOFF LOST ({lost_kind}): the post-compact body could not be "
            "written, so no archive file and no quarantine file exists and the "
            "summary is unrecoverable. See stderr."
        )
    elif quarantine_kind is None:
        message = f"Saved handoff: {archive_ref}"
    else:
        message = (
            f"REDACTION FAILED ({quarantine_kind}): handoff QUARANTINED at "
            f"{quarantine_ref}, unredacted and outside the backup. "
            "No archive file was written. See stderr."
        )
    if pointers_kind is not None:
        message += (
            f" The .latest pointers could not be written either "
            f"({pointers_kind}), so they are STALE and describe an earlier compact."
        )
    print(json.dumps({"systemMessage": message}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
