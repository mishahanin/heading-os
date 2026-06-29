#!/usr/bin/env python3
"""
checkpoint-statusline.py - Claude Code statusLine hook.

Reads context_window from the statusLine payload, computes a soft/hard
checkpoint level via hysteresis buckets, writes runtime state to
.claude/state/checkpoint-state.json (consumed by checkpoint-offer.py on
the Stop event), and prints a single-line status to stdout.

Thresholds (env-vars, optional):
  CLAUDE_HANDOFF_SOFT_THRESHOLD   default 25  (% used → soft offer)
  CLAUDE_HANDOFF_HARD_THRESHOLD   default 30  (% used → hard offer)
  CLAUDE_HANDOFF_REMIND_STEP      default 5   (bucket size for hysteresis)

Auto-compact is NOT disabled (last-resort policy). This hook only signals
the Stop hook to offer a checkpoint.

Stdlib only. Atomic JSON writes. Never raises.
"""

import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

# Force UTF-8 stdout/stderr (Windows defaults to CP1252 which breaks box chars + Cyrillic)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

WORKSPACE = Path(__file__).resolve().parent.parent.parent
STATE_DIR = WORKSPACE / ".claude" / "state"
STATE_PATH = STATE_DIR / "checkpoint-state.json"


def _env_int(name: str, default: int, *, minimum: int = 0, maximum: int = 100) -> int:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    if value < minimum or value > maximum:
        return default
    return value


SOFT_THRESHOLD = _env_int("CLAUDE_HANDOFF_SOFT_THRESHOLD", 25)
HARD_THRESHOLD = _env_int("CLAUDE_HANDOFF_HARD_THRESHOLD", 30)
REMIND_STEP = _env_int("CLAUDE_HANDOFF_REMIND_STEP", 5, minimum=1)
if SOFT_THRESHOLD >= HARD_THRESHOLD:
    SOFT_THRESHOLD, HARD_THRESHOLD = 25, 30


# ANSI colors for the status line. Stripped to plain text on terminals
# without VT100 support (classic cmd.exe sans WT_SESSION).
def _supports_ansi() -> bool:
    if os.name != "nt":
        return True
    # Modern Windows terminals set one of these env vars and handle VT100
    for var in ("WT_SESSION", "TERM_PROGRAM", "ANSICON", "ConEmuANSI"):
        if os.environ.get(var):
            return True
    # Claude Code TUI sets TERM to xterm-256color or similar
    term = os.environ.get("TERM", "")
    if term and term not in ("dumb", ""):
        return True
    return False


_USE_ANSI = _supports_ansi()


def c(code: str, text: str) -> str:
    if _USE_ANSI:
        return f"{code}{text}\033[0m"
    return text


C_RESET = "\033[0m" if _USE_ANSI else ""
C_DIM = "\033[2m" if _USE_ANSI else ""
C_CYAN_B = "\033[1;36m" if _USE_ANSI else ""
C_YELLOW_B = "\033[1;33m" if _USE_ANSI else ""
C_GREEN = "\033[32m" if _USE_ANSI else ""
C_YELLOW = "\033[33m" if _USE_ANSI else ""
C_RED = "\033[31m" if _USE_ANSI else ""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


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


def coerce_used(cw: dict) -> float | None:
    raw_used = cw.get("used_percentage")
    if raw_used is not None:
        try:
            return float(raw_used)
        except (TypeError, ValueError):
            pass
    raw_remaining = cw.get("remaining_percentage")
    if raw_remaining is not None:
        try:
            return 100.0 - float(raw_remaining)
        except (TypeError, ValueError):
            pass
    return None


def git_branch(cwd: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "branch", "--show-current"],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=2,
        )
        return result.stdout.strip()
    except (subprocess.SubprocessError, FileNotFoundError, OSError):
        return ""


def progress_bar(used: float) -> str:
    remaining = max(0, min(100, round(100 - used)))
    filled = round(remaining / 10)
    empty = 10 - filled
    return "[" + "█" * filled + "░" * empty + "]"


def build_status_line(payload: dict, used: float | None, level: str | None) -> str:
    parts: list[str] = []

    workspace = payload.get("workspace") or {}
    cwd_str = workspace.get("current_dir") or payload.get("cwd") or str(WORKSPACE)
    dir_name = Path(cwd_str).name or str(WORKSPACE)
    parts.append(f"{C_CYAN_B}{dir_name}{C_RESET}")

    branch = git_branch(Path(cwd_str))
    if branch:
        parts.append(f"{C_DIM}on{C_RESET} {C_YELLOW_B}{branch}{C_RESET}")

    if used is None:
        parts.append(f"{C_DIM}context: n/a{C_RESET}")
    else:
        if level == "hard":
            color = C_RED
            tail = f" {C_RED}⛔ checkpoint required{C_RESET}"
        elif level == "soft":
            color = C_YELLOW
            tail = f" {C_YELLOW}⚠ checkpoint suggested{C_RESET}"
        else:
            color = C_GREEN
            tail = ""
        bar = progress_bar(used)
        remaining = max(0, min(100, round(100 - used)))
        parts.append(f"{color}{bar} {remaining}%{C_RESET}{tail}")

    model = (payload.get("model") or {}).get("display_name") or "Claude"
    parts.append(f"{C_DIM}{model}{C_RESET}")

    return " ".join(parts)


def main() -> int:
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
    except Exception:
        # On any parse failure, print a minimal line and exit cleanly.
        # Never break the status line.
        print("Claude Code")
        return 0

    cw = payload.get("context_window") or {}
    used = coerce_used(cw)

    # Compute level + bucket
    if used is None:
        level = None
        bucket = 0
    else:
        if used >= HARD_THRESHOLD:
            level = "hard"
        elif used >= SOFT_THRESHOLD:
            level = "soft"
        else:
            level = None
        bucket = int(used // REMIND_STEP) * REMIND_STEP

    # Update state file with hysteresis
    state = read_json(STATE_PATH)
    previous_last_offered = int(state.get("last_offered_bucket") or 0)

    state.update(
        {
            "session_id": payload.get("session_id"),
            "transcript_path": payload.get("transcript_path"),
            "soft_threshold": SOFT_THRESHOLD,
            "hard_threshold": HARD_THRESHOLD,
            "remind_step": REMIND_STEP,
            "used_percentage": used,
            "remaining_percentage": cw.get("remaining_percentage"),
            "current_bucket": bucket,
            "updated_at": utc_now(),
        }
    )

    if level is not None:
        if bucket > previous_last_offered:
            state["needs_compact_offer"] = True
            state["offer_level"] = level
            state["offer_bucket"] = bucket
        else:
            state["needs_compact_offer"] = False
            state["offer_level"] = None
            state["offer_bucket"] = previous_last_offered
    else:
        # Below soft threshold - no offer queued. Preserve last_offered_bucket
        # so a transient dip + recovery does NOT re-fire the same offer.
        # last_offered_bucket only resets in checkpoint-save.py after a real
        # compact event (which actually frees context).
        state["needs_compact_offer"] = False
        state["offer_level"] = None
        state["offer_bucket"] = None

    try:
        write_json_atomic(STATE_PATH, state)
    except Exception as exc:
        # State write failure should not break the status line
        print(f"checkpoint-statusline: state write failed: {exc}", file=sys.stderr)

    print(build_status_line(payload, used, level))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
