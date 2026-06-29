#!/usr/bin/env python3
"""PostToolUse hook: warn when context window usage approaches capacity.

Reads remaining_percentage from the hook event input, normalizes for
Claude Code's 16.5% autocompact buffer, and emits advisory warnings
at two thresholds (AMBER at 35% usable remaining, RED at 25%).

Debounces repeated warnings via a temp file to prevent fatigue.
"""
import sys
import json
import os
import time
import tempfile


# Thresholds (percentage of usable context remaining)
AMBER_THRESHOLD = 35
RED_THRESHOLD = 25

# Claude Code reserves 16.5% for autocompact - normalize around this
AUTO_COMPACT_BUFFER_PCT = 16.5

# Suppress repeated same-level warnings for this many tool calls
DEBOUNCE_CALLS = 5

# Ignore state files older than this (seconds)
STALE_SECONDS = 60

AMBER_MSG = (
    "CONTEXT MONITOR [AMBER]: Context window approaching capacity "
    "(~{used}% used). Consider wrapping current task before starting "
    "new complex work."
)

RED_MSG = (
    "CONTEXT MONITOR [RED]: Context critically low (~{used}% used). "
    "Complete or pause current work. Would you like me to save a "
    "handoff file before we wrap up?"
)


def get_state_path(session_id):
    """Return path to the debounce state file for this session."""
    return os.path.join(
        tempfile.gettempdir(),
        f"claude-ctx-{session_id}-warned.json"
    )


def read_state(path):
    """Read debounce state, returning defaults if missing/corrupt."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            state = json.loads(f.read())
        # Check staleness
        if time.time() - state.get("timestamp", 0) > STALE_SECONDS:
            return {"calls_since_warn": 0, "last_level": None, "timestamp": 0}
        return state
    except (OSError, json.JSONDecodeError, ValueError):
        return {"calls_since_warn": 0, "last_level": None, "timestamp": 0}


def write_state(path, state):
    """Write debounce state to temp file."""
    state["timestamp"] = time.time()
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(json.dumps(state))
    except OSError as e:
        print(f"[context-monitor] write_state failed: {e}", file=sys.stderr)


def should_warn(state, level):
    """Determine if a warning should fire based on debounce rules."""
    last = state.get("last_level")
    calls = state.get("calls_since_warn", 0)

    # Severity escalation always fires
    if last == "amber" and level == "red":
        return True

    # First warning at this level fires immediately
    if last != level:
        return True

    # Same level: debounce
    return calls >= DEBOUNCE_CALLS


def main():
    try:
        input_data = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, ValueError):
        sys.exit(0)

    # Extract remaining_percentage from the event
    # Claude Code provides this in the conversation context
    remaining = input_data.get("remaining_percentage")
    if remaining is None:
        # Not available (subagent, older Claude Code version)
        sys.exit(0)

    # Normalize for autocompact buffer
    usable_remaining = max(
        0,
        (remaining - AUTO_COMPACT_BUFFER_PCT)
        / (100 - AUTO_COMPACT_BUFFER_PCT)
        * 100
    )
    used_pct = round(100 - usable_remaining)

    # Determine warning level
    if usable_remaining <= RED_THRESHOLD:
        level = "red"
        msg = RED_MSG.format(used=used_pct)
    elif usable_remaining <= AMBER_THRESHOLD:
        level = "amber"
        msg = AMBER_MSG.format(used=used_pct)
    else:
        sys.exit(0)

    # Debounce check
    session_id = input_data.get("session_id", "unknown")
    state_path = get_state_path(session_id)
    state = read_state(state_path)

    if not should_warn(state, level):
        # Increment call counter and save
        state["calls_since_warn"] = state.get("calls_since_warn", 0) + 1
        write_state(state_path, state)
        sys.exit(0)

    # Emit warning
    json.dump({"additionalContext": msg}, sys.stdout)

    # Reset debounce state
    write_state(state_path, {
        "calls_since_warn": 0,
        "last_level": level,
    })

    sys.exit(0)


if __name__ == "__main__":
    main()
