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

Exit codes: 0 ok, 2 no session found, 3 session unreadable, 1 caller error
(an unparseable --since-utc, reported cleanly) or other parser crash. That last
line said only "other parser crash" until 2026-08-25, so an operator alerting on
exit 1 as an engine bug was paged by a typo'd timestamp.

CEO-EYES-ONLY USAGE: emitted envelope may contain session content. Do not pipe
to external services. Consumed only by the local /calibrate skill.

Tests: tests/test_a_comment_that_named_the_defect_as_the_model.py
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
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


def _mtime(path: Path) -> float:
    """Modification time, or 0.0 for a file that vanished mid-sort.

    Transcripts rotate. `sorted(..., key=lambda p: p.stat().st_mtime)` stats
    each candidate AFTER the glob listed it, so a file deleted in between
    raised an uncaught FileNotFoundError and crashed the whole run with exit 1
    instead of skipping the one candidate that no longer exists.
    """
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def locate_session(sessions_dir: Path) -> Path | None:
    """Return the newest .jsonl file in sessions_dir by mtime, or None."""
    candidates = sorted(sessions_dir.glob("*.jsonl"), key=_mtime, reverse=True)
    return candidates[0] if candidates else None


def parse_jsonl(path: Path) -> tuple[list, list]:
    """Return (events, skipped_line_numbers). Tolerate malformed lines.

    "Malformed" covers two shapes, and until 2026-08-24 it covered only the
    first. Unparseable JSON was skipped, but a line holding well-formed JSON
    that is not an OBJECT — `null`, `123`, `"text"`, `[]` — was appended as an
    event, and every consumer downstream calls `.get()` on it: `build_envelope`
    at the type switch, the first/last timestamp reads, and the `--since-utc`
    filter. One odd line killed the entire run with an AttributeError, which is
    the opposite of the tolerance this docstring promises.
    """
    events = []
    skipped = []
    # `errors="replace"`, since 2026-09-01. The decode runs inside the ITERATION
    # over the handle, so a byte that is not UTF-8 raised `UnicodeDecodeError`
    # from `for lineno, line in ...` - above the `json.JSONDecodeError` handler,
    # which could not have caught it anyway (the two are SIBLINGS under
    # `ValueError`, not parent and child), and above `main`'s
    # `except (PermissionError, FileNotFoundError)`, which is not on its
    # ancestry at all. MEASURED 2026-09-01 with one 0xff in a three-line
    # transcript: a traceback and exit 1, where the documented answer for a
    # session that cannot be read is exit 3, and where the right answer is
    # neither - a transcript is the entire input to /calibrate, and this
    # function's own docstring promises to carry on past a line it cannot use.
    #
    # Replacing rather than skipping keeps the cost bounded to the damaged line:
    # the bad byte becomes U+FFFD, that line either still parses as JSON or is
    # counted in `skipped`, and every other line arrives intact. Salvage by
    # partial read would not be bounded like that - what survives a raising
    # reader is a property of the buffer size, not of the file.
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for lineno, line in enumerate(fh, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                skipped.append(lineno)
                continue
            if not isinstance(obj, dict):
                skipped.append(lineno)
                continue
            events.append(obj)
    return events, skipped


def _turn_text(content) -> str:
    """The readable prose of one turn, whichever shape the harness wrote it in.

    Claude Code writes `message.content` as a plain string for a typed user
    message and as a LIST OF BLOCKS for everything else — every assistant turn,
    and every user turn that carries a tool result.

    This function exists because the caller used to accept only the string shape.
    Measured 2026-08-22 on a real 1.7 MB session: 27,405 characters of prose, of
    which 26,270 (96%) were dropped, including every single assistant turn. The
    Chronicle summarized what was left and its entries read as bare facts,
    because the reasoning is in the assistant turns and none arrived.

    Only `text` blocks are prose. `tool_use` and `tool_result` payloads are
    machine traffic that would drown it, and a `thinking` block is written with
    an empty `thinking` field and a signature only — nothing to read.
    """
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts = [
        block["text"]
        for block in content
        if isinstance(block, dict)
        and block.get("type") == "text"
        and isinstance(block.get("text"), str)
        and block["text"].strip()
    ]
    return "\n".join(parts)


def _message_content(ev: dict):
    """`ev["message"]["content"]`, or "" when `message` is not a mapping.

    The read used to be `(ev.get("message") or {}).get(...)`, and `or` only
    substitutes for a FALSY value: a line carrying `"message": "hello"` came
    through as the string, and the next `.get` raised AttributeError - out of
    `build_envelope`, out of `main`, and the run died on one odd line.
    `parse_jsonl`'s docstring calls that "the opposite of the tolerance this
    docstring promises", and the `tool_use` branch below carried a comment
    pointing AT those two lines as "the correct idiom". They were the same
    defect it was written to fix.
    """
    message = ev.get("message")
    return message.get("content", "") if isinstance(message, dict) else ""


def _result_text(content) -> str:
    """The readable payload of a tool_result block, whichever shape it carries.

    Usually a plain string. Some tools return a list of blocks instead, and a
    `str(...)` of that list would put Python repr punctuation into the envelope
    the detection prompts read.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [b.get("text", "") for b in content
                 if isinstance(b, dict) and isinstance(b.get("text"), str)]
        return "\n".join(p for p in parts if p)
    return ""


_EXIT_CODE_RE = re.compile(r"^(?:Error:\s*)?Exit code (\d+)\b")


def _exit_code_from(text: str):
    """The exit code a Bash failure spells out, or None when there is none.

    The transcript records no numeric exit code anywhere; for Bash the harness
    writes it into the first line of the error text ("Exit code 1"). For every
    other tool there is no code at all, and None means exactly that - not zero,
    which would read as success. The `is_error` flag is what decides the record
    exists; this is diagnostic detail on top.
    """
    m = _EXIT_CODE_RE.match(text.strip())
    return int(m.group(1)) if m else None


def build_envelope(session_path: Path, events: list) -> dict:
    """Filter and shape events into the envelope schema.

    Tool traffic is read from CONTENT BLOCKS inside messages, which is the only
    place Claude Code puts it. This function used to dispatch on TOP-LEVEL
    `{"type": "tool_use"}` / `{"type": "tool_result"}` events reading
    `exit_code` and `stderr` keys, a shape the harness has never written:
    measured 2026-08-27 across all 1115 transcripts under ~/.claude/projects,
    zero such events exist, while this session's own transcript alone carries
    13,503 tool_use blocks and 192 failed tool_result blocks. So `tool_errors`
    was ALWAYS empty on real data, and /calibrate's "Errors / friction" section
    - the one its own reference file calls "the highest-quality category,
    because the signal is structured" - silently had nothing to report. Only a
    fixture written in the invented shape kept the branches green.

    A failure is flagged by `is_error: true` on the tool_result block. The
    matching command comes from the tool_use block with the same id, which is
    the correct pairing: the old code keyed "last command per tool NAME", so
    with two Bash calls in one assistant turn the second command was attributed
    to the first result.
    """
    user_turns = []
    assistant_turns = []
    tool_errors = []
    system_reminders = []
    tool_uses: dict[str, dict] = {}  # tool_use_id -> {"tool", "cmd"}
    for ev in events:
        ev_type = ev.get("type")
        ts = ev.get("timestamp", "")
        if ev_type == "user":
            text = _turn_text(_message_content(ev))
            if text:
                user_turns.append({"ts": ts, "text": text})
        elif ev_type == "assistant":
            text = _turn_text(_message_content(ev))
            if text:
                assistant_turns.append({"ts": ts, "text": text})
        elif ev_type == "system":
            content = ev.get("content", "")
            if isinstance(content, str) and content:
                system_reminders.append({"ts": ts, "text": content})

        # Tool traffic rides inside user and assistant messages alike, so this
        # runs for every event rather than as another branch of the chain above.
        blocks = _message_content(ev)
        if not isinstance(blocks, list):
            continue
        for block in blocks:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "tool_use":
                use_id = block.get("id")
                if not use_id:
                    continue
                # `.get("input", {})` only defaults when the KEY IS ABSENT, so an
                # explicit `"input": null` (or a string) goes straight to
                # AttributeError without this isinstance check.
                raw_input = block.get("input")
                cmd = raw_input.get("command", "") if isinstance(raw_input, dict) else ""
                tool_uses[use_id] = {"tool": block.get("name", ""), "cmd": cmd}
            elif btype == "tool_result" and block.get("is_error"):
                use = tool_uses.get(block.get("tool_use_id")) or {}
                stderr = _result_text(block.get("content"))
                tool_errors.append({
                    "ts": ts,
                    "tool": use.get("tool", ""),
                    "cmd": use.get("cmd", ""),
                    "exit_code": _exit_code_from(stderr),
                    "stderr": stderr,
                })
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


def _instant(ts: str):
    """An ISO timestamp as an aware datetime, or None if it will not parse."""
    if not isinstance(ts, str) or not ts:
        return None
    try:
        parsed = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def filter_since(events: list, since_utc: str) -> list:
    """Keep events at or after `since_utc`, comparing instants, not strings.

    `ev["timestamp"] >= args.since_utc` was a LEXICOGRAPHIC comparison until
    2026-08-24, and a transcript mixes offset notations. `'+' < 'Z'`, so
    `2026-08-22T10:00:00+00:00` sorts before `2026-08-22T10:00:00Z` even though
    they are the same instant, and an event exactly at the threshold was
    dropped or kept depending on which form the harness happened to write.

    An unparseable `--since-utc` is a caller error and raises. An unparseable
    event timestamp is data, so it is KEPT: over-reporting beats silently
    discarding a turn the filter could not read.
    """
    floor = _instant(since_utc)
    if floor is None:
        raise ValueError(f"--since-utc is not an ISO timestamp: {since_utc!r}")
    out = []
    for ev in events:
        stamp = _instant(ev.get("timestamp", ""))
        if stamp is None or stamp >= floor:
            out.append(ev)
    return out


def envelope_bytes(envelope: dict) -> int:
    """Serialized size of the envelope, in the encoding `main` prints."""
    return len(json.dumps(envelope, ensure_ascii=False).encode("utf-8"))


def _entry_bytes(entry, siblings_left: int) -> int:
    """What the serialized envelope shrinks by when one list entry is dropped.

    An entry costs its own serialization plus the two-byte ", " separator that
    joined it to the next one; the last entry in a list carries no separator.
    The default json encoder serializes a nested value byte-for-byte the same
    inside the document as it does alone, so this delta is exact rather than an
    estimate, and `apply_truncation` can track the running size instead of
    re-serializing the whole envelope once per shed.
    """
    size = len(json.dumps(entry, ensure_ascii=False).encode("utf-8"))
    return size + 2 if siblings_left else size


def _drop_head(entries: list) -> int:
    """Pop `entries[0]` and return the bytes the envelope lost by it."""
    head = entries.pop(0)
    return _entry_bytes(head, len(entries))


def _pop_oldest(envelope: dict, keys: tuple[str, ...]) -> bool:
    """Drop the single oldest entry across `keys`. False when all are empty.

    Each list is already chronological, so the oldest overall is whichever
    list's head has the earliest `ts`. Shedding both sides together keeps the
    surviving tail a two-sided conversation; draining one list first would
    leave the questions without the answers.

    Compared as INSTANTS, not as strings. The heads were sorted raw until
    2026-08-24, which is the same defect `filter_since` documents twenty lines
    up: a transcript mixes offset notations and `'+' < 'Z'`, so
    `...T09:00:00Z` sorts before `...T10:00:00+05:00` while being four hours
    LATER. Under truncation that shed the newer turn and kept the older one.
    The module already had `_instant`; this was the one place that did not
    reach for it.
    """
    heads = [(envelope[k][0].get("ts", ""), k) for k in keys if envelope.get(k)]
    if not heads:
        return False

    def _age(head):
        moment = _instant(head[0])
        # An unplaceable stamp sheds FIRST. It cannot be compared against a
        # datetime without a TypeError, and falling back to string order is
        # the bug this function exists to stop.
        return (0, 0.0) if moment is None else (1, moment.timestamp())

    heads.sort(key=_age)
    envelope[heads[0][1]].pop(0)
    return True


def apply_truncation(envelope: dict, max_bytes: int) -> dict:
    """Shed oldest entries toward `max_bytes`, and never fake having reached it.

    This shed ONLY `user_turns` until 2026-08-24, and stopped when that list
    emptied — returning an envelope that could still be many times `max_bytes`,
    stamped `"truncated": True`, with no warning and exit 0. Doubly wrong
    because this module's own measurement (`_turn_text`) found assistant turns
    carry 96% of the prose: the loop shed the lighter side and never touched
    the heavy one.

    Order: harness boilerplate first, then the oldest prose from both sides
    together, then the small diagnostic list. `tool_errors` goes last because
    it is cheap and it is what the /calibrate skill reads for failure patterns.

    The size is a target, not a guarantee — a `max_bytes` smaller than the bare
    metadata cannot be met by shedding anything. Callers check
    `envelope_bytes()` against their own budget; `main` warns on stderr.

    The running size is tracked incrementally. Each of the three loops used to
    call `envelope_bytes()` per shed, which RE-SERIALIZES the whole envelope, so
    shedding cost one full serialization per dropped entry: O(n squared) in
    envelope size with nothing bounding the input. Measured 2026-08-31 on
    synthetic envelopes, 0.24 MB took 0.53 s, 0.49 MB took 1.94 s and 0.98 MB
    took 8.31 s, so doubling the input quadrupled the runtime. The reachable
    input is not synthetic: `/calibrate` with no `--session` takes the newest
    transcript by mtime, and on this workspace that file was 249,961,421 bytes
    on the day of the measurement, which puts a bare run past a slow one and
    into a hang. `_entry_bytes` gives the exact delta per shed, so the whole
    shed now costs two serializations regardless of how much it drops, and the
    shed order and the resulting envelope are unchanged.
    """
    size = envelope_bytes(envelope)
    if size <= max_bytes:
        return envelope
    envelope["truncated"] = True
    # Re-read rather than adjust by hand: `truncated` may be a flip from False
    # or a key this envelope did not carry at all, and callers pass both shapes.
    size = envelope_bytes(envelope)
    while envelope["system_reminders"] and size > max_bytes:
        size -= _drop_head(envelope["system_reminders"])
    prose = ("user_turns", "assistant_turns")
    while size > max_bytes:
        # `_pop_oldest` owns the choice of which side is older, so read the
        # delta off whichever list it shortened rather than duplicating that
        # comparison here, where a second copy would be the one left unfixed.
        heads = {k: envelope[k][0] for k in prose if envelope.get(k)}
        before = {k: len(envelope.get(k) or ()) for k in prose}
        if not _pop_oldest(envelope, prose):
            break
        shed = next(k for k in prose if len(envelope.get(k) or ()) < before[k])
        size -= _entry_bytes(heads[shed], len(envelope[shed]))
    while envelope["tool_errors"] and size > max_bytes:
        size -= _drop_head(envelope["tool_errors"])
    return envelope


def _ceo_only_paths() -> list[str]:
    """The path prefixes the routing map resolves to `private`.

    This field was a hardcoded `[]` until 2026-08-24 while both the module
    docstring and this function's own docstring promised it was enumerated, so
    a consumer could not tell "no ceo-only paths exist" from "this is a stub" —
    the coverage claim `.claude/rules/scope-claims.md` forbids. The routing map
    is the workspace's single classification input, so read it rather than
    restate it: a second list would be the copy that stops being updated.
    """
    try:
        from scripts.utils.workspace import load_routing_map
    except ImportError:
        return []
    try:
        rules = load_routing_map()["rules"]
    except Exception as exc:  # a broken map must not take the envelope down
        print(f"{YELLOW}[workspace warning]{RESET} routing map unreadable "
              f"({exc}); ceo_only_paths omitted.", file=sys.stderr)
        return []
    return sorted(k for k, dest in rules.items() if dest == "private")


def populate_workspace_block(repo_root: Path) -> dict:
    """Enumerate skills, rules, and ceo-only paths from the workspace."""
    skills_dir = repo_root / ".claude" / "skills"
    rules_dir = repo_root / ".claude" / "rules"
    skills = sorted(p.name for p in skills_dir.iterdir() if p.is_dir() and p.name != "archive") if skills_dir.exists() else []
    rules = sorted(p.name for p in rules_dir.glob("*.md")) if rules_dir.exists() else []
    return {"skills": skills, "rules": rules, "ceo_only_paths": _ceo_only_paths()}


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
    except OSError as e:
        # `(PermissionError, FileNotFoundError)` by name until 2026-09-01, which
        # named two of the ways a path fails to open and left the rest to a
        # traceback under an exit code that means nothing. `IsADirectoryError` is
        # the one a mistyped `--session` reaches first; exit 3 already means
        # "session unreadable", and a directory is exactly that.
        print(f"session unreadable: {e}", file=sys.stderr)
        return 3

    if skipped:
        print(f"{YELLOW}[parser warning]{RESET} skipped {len(skipped)} malformed line(s): {skipped}", file=sys.stderr)

    if args.since_utc:
        try:
            events = filter_since(events, args.since_utc)
        except ValueError as e:
            print(str(e), file=sys.stderr)
            return 1

    envelope = build_envelope(session_path, events)
    envelope = apply_truncation(envelope, args.max_bytes)
    actual = envelope_bytes(envelope)
    if actual > args.max_bytes:
        print(f"{YELLOW}[truncation warning]{RESET} envelope is {actual} bytes "
              f"after shedding every droppable list; --max-bytes "
              f"{args.max_bytes} could not be met.", file=sys.stderr)
    if not args.no_workspace:
        envelope["workspace"] = populate_workspace_block(get_workspace_root())

    print(json.dumps(envelope, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
