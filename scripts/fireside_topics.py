#!/usr/bin/env python3
"""Fireside topic-collection logic — pure functions, no Telegram I/O, no globals.

Consumed by scripts/fireside-bot.py (thin cmd_* wrappers + the /idea DM branch
+ the cycle_invite callback) and scripts/fireside-bot-daemon.py (job dispatch).

Design: every function that touches disk takes an explicit `state_dir: Path`, so
the module holds no mutable global state and is trivially unit-testable. Message
renderers and cycle-detection helpers are pure (input -> string/bool).

State files (under the caller's STATE_DIR, the existing fireside-state dir):
  topic-ideas.jsonl            append-only, one idea per line
  topic-collection-state.json  {"last_digest_idea_id": str|None,
                                "pending_cycle_invite": dict|None}
"""
from __future__ import annotations

import json
import os
import tempfile
import uuid
from datetime import date
from pathlib import Path
from typing import Optional

TOPIC_IDEAS_FILE = "topic-ideas.jsonl"
TOPIC_STATE_FILE = "topic-collection-state.json"

MIN_IDEA_LEN = 3
MAX_IDEA_LEN = 1000

_SUNDAY = 6  # date.weekday(): Monday=0 .. Sunday=6


# ------------------------------------------------------------------
# Parsing
# ------------------------------------------------------------------

def _is_idea_command(text: str) -> bool:
    """True only for the /idea command at a token boundary (not /ideas, /ideabank)."""
    low = (text or "").strip().lower()
    return (low == "/idea" or low.startswith("/idea ")
            or low.startswith("/idea@") or low.startswith("/idea\n"))


def parse_idea_command(text: str) -> Optional[str]:
    """Extract the idea body from a '/idea ...' message.

    Strips the leading /idea (optionally /idea@botname), trims surrounding
    whitespace. Returns the cleaned body, or None when the message is not an
    /idea command, is empty, or is shorter than MIN_IDEA_LEN. Bodies longer
    than MAX_IDEA_LEN are truncated.
    """
    if not _is_idea_command(text):
        return None
    parts = text.strip().split(None, 1)
    body = parts[1].strip() if len(parts) == 2 else ""
    if len(body) < MIN_IDEA_LEN:
        return None
    return body[:MAX_IDEA_LEN]


# ------------------------------------------------------------------
# Rendering (pure)
# ------------------------------------------------------------------

def render_nudge() -> str:
    """Short, warm weekly invite posted to the Tribe group."""
    return (
        "💡 *Fireside topic box is open*\n\n"
        "Loving the sessions — and we want the next ones to be yours. "
        "Got something you'd like to hear, or present?\n\n"
        "DM me `/idea <your topic>` (e.g. `/idea a real DPI incident, start to finish`). "
        "Every idea is logged and goes into the pool we draw the next topics from."
    )


def render_cycle_end_invite() -> str:
    """The larger end-of-cycle message — drafted to the CEO for approval first."""
    return (
        "🔥 *That's a wrap on this fireside cycle*\n\n"
        "Thank you — the energy in these sessions has been real. We see how many of "
        "you show up, and how openly you share the things you care about.\n\n"
        "Now we're charting the next cycle, and we want it shaped by you. "
        "What do you want to hear? What would you step up to present?\n\n"
        "DM me `/idea <your topic>` — every single idea is written down and goes "
        "straight into the list we build the next cycle from. No idea is too small."
    )


def render_digest(ideas: list[dict]) -> str:
    """CEO weekly digest body. Empty string when there are no new ideas."""
    if not ideas:
        return ""
    lines = [f"📥 *New fireside topic ideas* ({len(ideas)})\n"]
    for i in ideas:
        who = i.get("name") or i.get("username") or "unknown"
        when = (i.get("ts") or "")[:10]
        lines.append(f"• {i.get('text','').strip()}\n   — {who}, {when}")
    return "\n".join(lines)


def render_backlog_summary(ideas: list[dict]) -> str:
    """Full current-cycle backlog, attached to the cycle-end draft for the CEO."""
    if not ideas:
        return "_No topic ideas were submitted this cycle._"
    lines = [f"*Full topic backlog this cycle* ({len(ideas)} ideas):\n"]
    for n, i in enumerate(ideas, 1):
        who = i.get("name") or i.get("username") or "unknown"
        lines.append(f"{n}. {i.get('text','').strip()}  — {who}")
    return "\n".join(lines)


# ------------------------------------------------------------------
# Storage (explicit state_dir; atomic JSON, append-only JSONL)
# ------------------------------------------------------------------

def _ideas_path(state_dir: Path) -> Path:
    return Path(state_dir) / TOPIC_IDEAS_FILE


def _state_path(state_dir: Path) -> Path:
    return Path(state_dir) / TOPIC_STATE_FILE


def append_idea(state_dir: Path, *, now_iso: str, user_id: int,
                username: str, name: str, text: str, cycle: int) -> str:
    """Append one idea to topic-ideas.jsonl. Returns the generated idea_id."""
    idea_id = uuid.uuid4().hex
    record = {
        "idea_id": idea_id,
        "ts": now_iso,
        "user_id": user_id,
        "username": username,
        "name": name,
        "text": text,
        "cycle": cycle,
    }
    path = _ideas_path(state_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    return idea_id


def load_ideas(state_dir: Path, cycle: Optional[int] = None,
               since_id: Optional[str] = None) -> list[dict]:
    """Load ideas in file order. Corrupt lines are skipped.

    cycle    -- if set, only ideas with that cycle number.
    since_id -- if set, only ideas appearing strictly AFTER the matching id
                (unknown id => all ideas, matching new_ideas_since semantics).
    """
    path = _ideas_path(state_dir)
    if not path.exists():
        return []
    out: list[dict] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if cycle is not None and rec.get("cycle") != cycle:
                continue
            out.append(rec)
    if since_id is not None:
        idx = next((n for n, r in enumerate(out) if r.get("idea_id") == since_id), None)
        if idx is not None:
            out = out[idx + 1:]
    return out


def load_topic_state(state_dir: Path) -> dict:
    """Load topic-collection-state.json, returning defaults when absent/corrupt."""
    path = _state_path(state_dir)
    default = {"last_digest_idea_id": None, "pending_cycle_invite": None}
    if not path.exists():
        return dict(default)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return dict(default)
    for k, v in default.items():
        data.setdefault(k, v)
    return data


def save_topic_state(state_dir: Path, state: dict) -> None:
    """Atomically write topic-collection-state.json (write-tmp + os.replace)."""
    path = _state_path(state_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2, ensure_ascii=False)
            f.write("\n")
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            try:
                os.unlink(tmp)
            except OSError:
                pass
        raise


def new_ideas_since(state_dir: Path, cursor: Optional[str]):
    """Return (new_ideas, new_cursor).

    new_ideas  -- all ideas after `cursor` (all of them when cursor is None or
                  not found). new_cursor -- the last idea's id, or the unchanged
                  cursor when there are no new ideas.
    """
    new = load_ideas(state_dir, since_id=cursor) if cursor else load_ideas(state_dir)
    if not new:
        return [], cursor
    return new, new[-1]["idea_id"]


# ------------------------------------------------------------------
# Cycle detection (pure; mirrors fireside-bot _current_or_upcoming_week)
# ------------------------------------------------------------------

def _upcoming_week(schedule: list, today: date) -> Optional[int]:
    """Week number of the current/next session on/after `today`, else None."""
    upcoming = [s for s in schedule
                if date.fromisoformat(s["session_date"]) >= today]
    if not upcoming:
        return None
    upcoming.sort(key=lambda s: s["session_date"])
    return upcoming[0]["week"]


def is_final_week(schedule: list, today: date) -> bool:
    """True when the current/upcoming week is the highest week in the schedule."""
    if not schedule:
        return False
    uw = _upcoming_week(schedule, today)
    if uw is None:
        return False
    return uw == max(s["week"] for s in schedule)


def cycle_end_trigger_today(schedule: list, today: date) -> bool:
    """True only on the Sunday whose next session is in the final week.

    One beat before the cycle's last Monday, giving the CEO time to approve the
    cycle-end invite before the cycle closes.
    """
    return today.weekday() == _SUNDAY and is_final_week(schedule, today)


def current_cycle(schedule: list, today: date) -> int:
    """Cycle number of the current/upcoming session, read from the schedule.

    Each schedule entry already carries a `cycle` field. Returns the cycle of
    the next session on/after today; once the schedule is exhausted, the highest
    cycle present; 1 for an empty schedule.
    """
    if not schedule:
        return 1
    upcoming = sorted(
        (s for s in schedule if date.fromisoformat(s["session_date"]) >= today),
        key=lambda s: s["session_date"],
    )
    if upcoming:
        return int(upcoming[0].get("cycle", 1))
    return int(max((s.get("cycle", 1) for s in schedule), default=1))
