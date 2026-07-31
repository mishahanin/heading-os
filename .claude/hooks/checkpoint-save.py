#!/usr/bin/env python3
"""
checkpoint-save.py - Claude Code PostCompact hook (matcher: manual|auto).

Writes a combined handoff file (summary + continuation prompt) to
outputs/operations/handoff-archive/ after a compact event - manual OR auto.
Auto-compact remains enabled as last resort; this hook ensures a resume
artifact is captured either way.

Also updates pointer files at outputs/operations/handoff-archive/.latest/
that the SessionStart inject hook reads on the next session.

Resets hysteresis state in .claude/state/checkpoint-state.json so the
post-compact session starts fresh.
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

WORKSPACE = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(WORKSPACE))
from scripts.utils.workspace import get_data_root, get_outputs_dir  # noqa: E402

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
HANDOFF_DIR = get_outputs_dir() / "operations" / "handoff-archive"
LATEST_DIR = HANDOFF_DIR / ".latest"
QUARANTINE_DIR = HANDOFF_DIR / ".quarantine"
STATE_PATH = WORKSPACE / ".claude" / "state" / "checkpoint-state.json"


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

    session_id = payload.get("session_id", "session")
    session_slug = safe_slug(session_id)
    trigger = payload.get("trigger", "unknown")
    trigger_slug = safe_slug(trigger, max_len=12) or "unknown"
    compact_summary = (payload.get("compact_summary") or "").strip()
    transcript_path = payload.get("transcript_path", "")

    now = datetime.now(timezone.utc)
    stamp = now.strftime("%Y-%m-%d-%H%M%S")
    archive_name = f"{stamp}_handoff_compact-{trigger_slug}_{session_slug}.md"
    archive_path = HANDOFF_DIR / archive_name
    quarantine_path = QUARANTINE_DIR / archive_name

    # Data-root-relative refs. Every path any artifact NAMES is one of these
    # three, so a channel can only name something that was actually written.
    data_root = get_data_root()
    archive_ref = archive_path.relative_to(data_root).as_posix()
    quarantine_ref = quarantine_path.relative_to(data_root).as_posix()
    summary_ref = (LATEST_DIR / "summary.md").relative_to(data_root).as_posix()

    # Redact BEFORE the text reaches any file. The archive is tracked, and a
    # credential-shaped string reaching it blocks push-all's content scan and
    # therefore the whole backup. Measured on 2026-07-31, when this hook wrote
    # the two files that refused the push.
    #
    # Best-effort on purpose. This hook runs after the session's context has
    # been discarded, so a handoff it fails to write is gone for good, and that
    # is worse than an unredacted one: the push-time scan still refuses to let
    # a real secret off the machine.
    summary_text = compact_summary or "_No compact summary provided._"
    quarantine_kind = None
    try:
        redacted = redact(summary_text)
        # A redactor that RETURNS something broken never raises, so the guarded
        # import above does not cover it. `redact` returning None wrote an
        # archive whose entire Summary section was the literal string "None":
        # the handoff destroyed, stderr silent, systemMessage reporting success.
        # Raising routes that failure into the same quarantine as any other.
        if not isinstance(redacted, str):
            raise TypeError(
                f"redact() returned {type(redacted).__name__}, expected str")
        summary_text = redacted
    except Exception as exc:  # noqa: BLE001 - never lose the handoff
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

    if quarantine_kind is None:
        summary_pointer = f"""# Latest handoff summary

Source: {archive_ref}
Generated: {now.isoformat()}
Trigger: compact / {trigger}

{summary_text}
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
Trigger: compact / {trigger}

## Objective

REDACTION FAILED ({quarantine_kind}), so this handoff was QUARANTINED: it is NOT in the archive and NOT in the backup.

## Next steps

- Read the quarantined handoff at: {quarantine_path}
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
            # POINTER carrying no summary text at all, so the SessionStart
            # inject still tells the next session where to look and the tracked
            # tree stays clean. This is an alarm state, not the permanent hiding
            # that gitignoring the whole archive would have been.
            write_text_atomic(quarantine_path, archive_md)
        write_text_atomic(LATEST_DIR / "summary.md", summary_pointer)
        write_text_atomic(LATEST_DIR / "prompt.md", prompt_pointer)
    except Exception as exc:
        # Generic systemMessage; full exception goes to stderr to avoid
        # leaking sensitive paths in Claude's surfaced output.
        print(f"checkpoint-save: write failed: {exc}", file=sys.stderr)
        print(
            json.dumps(
                {"systemMessage": "checkpoint-save: write failed (see stderr)"}
            )
        )
        return 0

    # Reset hysteresis state so the post-compact session starts clean
    try:
        if STATE_PATH.exists():
            try:
                cs = json.loads(STATE_PATH.read_text(encoding="utf-8"))
            except Exception:
                cs = {}
        else:
            cs = {}
        cs.update(
            {
                "needs_compact_offer": False,
                "offer_level": None,
                "offer_bucket": None,
                "last_offered_bucket": 0,
                "last_compact_at": now.isoformat(),
                "last_compact_trigger": trigger,
                # The path that EXISTS. Recording the dated archive on the
                # quarantine branch left a dangling pointer in state, naming a
                # file no branch had written.
                "last_compact_summary_path": (
                    archive_ref if quarantine_kind is None else quarantine_ref
                ),
            }
        )
        write_json_atomic(STATE_PATH, cs)
    except Exception as exc:
        # State reset failure is non-fatal
        print(f"checkpoint-save: state reset failed: {exc}", file=sys.stderr)

    # The one channel the operator and the assistant actually see. On the alarm
    # path it used to report a save and name a file that was never written,
    # which made the quarantine silent - and loudness is the entire reason to
    # quarantine rather than write the summary raw.
    if quarantine_kind is None:
        message = f"Saved handoff: {archive_ref}"
    else:
        message = (
            f"REDACTION FAILED ({quarantine_kind}): handoff QUARANTINED at "
            f"{quarantine_ref}, unredacted and outside the backup. "
            "No archive file was written. See stderr."
        )
    print(json.dumps({"systemMessage": message}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
