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

# Handoff archive is DATA -> resolves under the data root (sibling), not the engine.
# @-reference paths must therefore be data-root-relative (outputs/...), NOT
# engine-relative: archive_path lives under the data sibling, so relative_to(WORKSPACE)
# would raise ValueError. The data-path-redirect hook resolves the outputs/... ref.
HANDOFF_DIR = get_outputs_dir() / "operations" / "handoff-archive"
LATEST_DIR = HANDOFF_DIR / ".latest"
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

    summary_text = compact_summary or "_No compact summary provided._"

    archive_md = f"""# Handoff - post-compact ({trigger})

Generated: {now.isoformat()}
Trigger: compact / {trigger}
Session: {session_id}
Transcript: {transcript_path}

## Summary

{summary_text}

## Continuation prompt

Continue this Claude Code session from the saved handoff.

First read:

@{archive_path.relative_to(get_data_root()).as_posix()}

Then continue the latest unfinished task.

Rules:
1. Treat repository state as authoritative.
2. Do not redo broad discovery unless the summary is insufficient.
3. Before making changes, briefly restate the current objective, constraints, files involved, and next concrete action.
4. Continue implementation from the current repo state.

## Notes

This handoff was generated automatically after a {trigger} compact event.
Repository state is authoritative; this file is supporting context.
"""

    summary_pointer = f"""# Latest handoff summary

Source: {archive_path.relative_to(get_data_root()).as_posix()}
Generated: {now.isoformat()}
Trigger: compact / {trigger}

{summary_text}
"""

    prompt_pointer = f"""Continue this Claude Code session from the saved handoff.

First read:

@{archive_path.relative_to(get_data_root()).as_posix()}

Then continue the latest unfinished task.

Rules:
1. Treat repository state as authoritative.
2. Do not redo broad discovery unless the summary is insufficient.
3. Before making changes, briefly restate the current objective, constraints, files involved, and next concrete action.
4. Continue implementation from the current repo state.
"""

    try:
        write_text_atomic(archive_path, archive_md)
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
                "last_compact_summary_path": archive_path.relative_to(
                    get_data_root()
                ).as_posix(),
            }
        )
        write_json_atomic(STATE_PATH, cs)
    except Exception as exc:
        # State reset failure is non-fatal
        print(f"checkpoint-save: state reset failed: {exc}", file=sys.stderr)

    print(
        json.dumps(
            {
                "systemMessage": (
                    f"Saved handoff: {archive_path.relative_to(get_data_root()).as_posix()}"
                )
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
