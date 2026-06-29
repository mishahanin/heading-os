#!/usr/bin/env python3
"""calibrate.py - Parse the active Claude Code session JSONL transcript into a clean envelope.

Used by the /calibrate skill to surface user corrections, preferences, repeated
patterns, errors, success signals, and voice violations from a finished session.

Usage:
    python scripts/calibrate.py [--session PATH] [--sessions-dir PATH]
                                [--since-utc TS] [--max-bytes N]
                                [--no-workspace]

Output: JSON envelope to stdout with session_id, session_path, started_at_utc,
ended_at_utc, event_count, truncated, user_turns, assistant_turns, tool_errors,
system_reminders, and (unless --no-workspace) workspace block with skills/rules/ceo_only_paths.

Exit codes: 0 ok, 2 no session found, 3 session unreadable, 1 other parser crash.

CEO-EYES-ONLY USAGE: emitted envelope may contain session content. Do not pipe
to external services. Consumed only by the local /calibrate skill.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.utils.colors import YELLOW, RESET  # noqa: E402
from scripts.utils.workspace import get_workspace_root  # noqa: E402


def _derive_sessions_dir() -> Path:
    """Derive the Claude Code session-transcript directory for this workspace.

    Claude Code stores JSONL transcripts under ``~/.claude/projects/{slug}`` where
    ``slug`` is the absolute workspace path with every non-alphanumeric character
    replaced by ``-``. Deriving it programmatically keeps /calibrate portable across
    Windows accounts, macOS, and Linux without hardcoding a single user's path.

    Override via the ``CLAUDE_SESSIONS_DIR`` env var or the ``--sessions-dir`` flag.
    """
    override = os.environ.get("CLAUDE_SESSIONS_DIR")
    if override:
        return Path(override)
    slug = re.sub(r"[^a-zA-Z0-9]", "-", str(get_workspace_root().resolve()))
    return Path.home() / ".claude" / "projects" / slug


DEFAULT_SESSIONS_DIR = _derive_sessions_dir()
DEFAULT_MAX_BYTES = 800_000


def locate_session(sessions_dir: Path) -> Path | None:
    """Return the newest .jsonl file in sessions_dir by mtime, or None."""
    candidates = sorted(sessions_dir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0] if candidates else None


def parse_jsonl(path: Path) -> tuple[list, list]:
    """Return (events, skipped_line_numbers). Tolerate malformed lines."""
    events = []
    skipped = []
    with path.open("r", encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                skipped.append(lineno)
    return events, skipped


def build_envelope(session_path: Path, events: list) -> dict:
    """Filter and shape events into the envelope schema."""
    user_turns = []
    assistant_turns = []
    tool_errors = []
    system_reminders = []
    last_tool_use_cmd: dict[str, str] = {}  # tool name -> last command string
    for ev in events:
        ev_type = ev.get("type")
        ts = ev.get("timestamp", "")
        if ev_type == "user":
            content = ev.get("message", {}).get("content", "")
            if isinstance(content, str) and content:
                user_turns.append({"ts": ts, "text": content})
        elif ev_type == "assistant":
            content = ev.get("message", {}).get("content", "")
            if isinstance(content, str) and content:
                assistant_turns.append({"ts": ts, "text": content})
        elif ev_type == "tool_use":
            tool = ev.get("tool", "")
            cmd = ev.get("input", {}).get("command", "")
            if tool:
                last_tool_use_cmd[tool] = cmd
        elif ev_type == "tool_result":
            exit_code = ev.get("exit_code", 0)
            stderr = ev.get("stderr", "")
            if exit_code != 0 or stderr:
                tool = ev.get("tool", "")
                tool_errors.append({
                    "ts": ts,
                    "tool": tool,
                    "cmd": last_tool_use_cmd.get(tool, ""),
                    "exit_code": exit_code,
                    "stderr": stderr,
                })
        elif ev_type == "system":
            content = ev.get("content", "")
            if isinstance(content, str) and content:
                system_reminders.append({"ts": ts, "text": content})
    started = events[0].get("timestamp", "") if events else ""
    ended = events[-1].get("timestamp", "") if events else ""
    return {
        "session_id": session_path.stem,
        "session_path": str(session_path),
        "started_at_utc": started,
        "ended_at_utc": ended,
        "event_count": len(events),
        "truncated": False,
        "user_turns": user_turns,
        "assistant_turns": assistant_turns,
        "tool_errors": tool_errors,
        "system_reminders": system_reminders,
    }


def apply_truncation(envelope: dict, max_bytes: int) -> dict:
    """Drop oldest user_turns until serialized envelope fits within max_bytes."""
    serialized = json.dumps(envelope, ensure_ascii=False)
    if len(serialized.encode("utf-8")) <= max_bytes:
        return envelope
    envelope["truncated"] = True
    # Drop oldest user_turns first - they're typically the heaviest
    while envelope["user_turns"] and len(json.dumps(envelope, ensure_ascii=False).encode("utf-8")) > max_bytes:
        envelope["user_turns"].pop(0)
    return envelope


def populate_workspace_block(repo_root: Path) -> dict:
    """Enumerate skills, rules, and ceo-only paths from the workspace."""
    skills_dir = repo_root / ".claude" / "skills"
    rules_dir = repo_root / ".claude" / "rules"
    skills = sorted(p.name for p in skills_dir.iterdir() if p.is_dir() and p.name != "archive") if skills_dir.exists() else []
    rules = sorted(p.name for p in rules_dir.glob("*.md")) if rules_dir.exists() else []
    return {"skills": skills, "rules": rules, "ceo_only_paths": []}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session", type=Path, help="explicit session JSONL path")
    parser.add_argument("--sessions-dir", type=Path, default=DEFAULT_SESSIONS_DIR)
    parser.add_argument("--since-utc", type=str, help="filter events after this ISO timestamp")
    parser.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES)
    parser.add_argument("--no-workspace", action="store_true", help="skip workspace block (testing)")
    args = parser.parse_args(argv)

    if args.session:
        session_path = args.session
    else:
        session_path = locate_session(args.sessions_dir)
        if session_path is None:
            print(f"no session JSONL found in {args.sessions_dir}", file=sys.stderr)
            return 2

    try:
        events, skipped = parse_jsonl(session_path)
    except (PermissionError, FileNotFoundError) as e:
        print(f"session unreadable: {e}", file=sys.stderr)
        return 3

    if skipped:
        print(f"{YELLOW}[parser warning]{RESET} skipped {len(skipped)} malformed line(s): {skipped}", file=sys.stderr)

    if args.since_utc:
        events = [ev for ev in events if ev.get("timestamp", "") >= args.since_utc]

    envelope = build_envelope(session_path, events)
    envelope = apply_truncation(envelope, args.max_bytes)
    if not args.no_workspace:
        envelope["workspace"] = populate_workspace_block(get_workspace_root())

    print(json.dumps(envelope, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
