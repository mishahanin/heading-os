#!/usr/bin/env python3
"""
checkpoint-statusline.py - Claude Code statusLine hook.

Reads context_window from the statusLine payload, computes a soft/hard
checkpoint level via hysteresis buckets, writes runtime state to
.claude/state/checkpoint-<session-slug>.json (consumed by checkpoint-offer.py on
the Stop event), and prints a single-line status to stdout.

The state file is keyed by SESSION. It was one shared file until 2026-08-16, and
this workspace runs several sessions on one tree: session A's context usage went
into the file session B's Stop hook read, so the idle session was offered the
checkpoint and the full one was not. Reproduced before the fix; held by
tests/test_checkpoint_session_scope.py.

Thresholds (env-vars, optional):
  CLAUDE_HANDOFF_SOFT_THRESHOLD   default 25  (% used → soft offer)
  CLAUDE_HANDOFF_HARD_THRESHOLD   default 30  (% used → hard offer)
  CLAUDE_HANDOFF_REMIND_STEP      default 5   (bucket size for hysteresis)
  CLAUDE_HANDOFF_AUTO             default off (hands-off save, no prompt)

Auto-compact is NOT disabled (last-resort policy). This hook only signals
the Stop hook to offer a checkpoint.

Stdlib only. Atomic JSON writes. Never raises.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

_BOOT = Path(__file__).resolve()
for _candidate in [_BOOT.parent, *_BOOT.parents]:
    if (_candidate / "scripts" / "utils" / "checkpoint_paths.py").is_file():
        sys.path.insert(0, str(_candidate))
        break
from scripts.utils import checkpoint_paths as CP  # noqa: E402

CP.force_utf8()

_CFG = CP.config()
SOFT_THRESHOLD = _CFG["soft"]
HARD_THRESHOLD = _CFG["hard"]
REMIND_STEP = _CFG["step"]
# Auto is deliberately NOT read at module scope. It is now a per-session
# decision the operator can flip mid-work, so it is resolved in main() from the
# session's own state file and passed down.


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


def build_status_line(
    payload: dict, project: Path, used: float | None, level: str | None, auto: bool
) -> str:
    parts: list[str] = []

    workspace = payload.get("workspace") or {}
    cwd_str = workspace.get("current_dir") or payload.get("cwd") or str(project)
    dir_name = Path(cwd_str).name or str(project)
    parts.append(f"{C_CYAN_B}{dir_name}{C_RESET}")

    branch = git_branch(Path(cwd_str))
    if branch:
        parts.append(f"{C_DIM}on{C_RESET} {C_YELLOW_B}{branch}{C_RESET}")

    if used is None:
        parts.append(f"{C_DIM}context: n/a{C_RESET}")
    else:
        # Auto mode is named in the bar, because a checkpoint that writes itself
        # with no prompt should never be a surprise to the operator watching.
        auto_tag = "auto-" if auto else ""
        if level == "hard":
            color = C_RED
            tail = f" {C_RED}⛔ {auto_tag}checkpoint required{C_RESET}"
        elif level == "soft":
            color = C_YELLOW
            tail = f" {C_YELLOW}⚠ {auto_tag}checkpoint suggested{C_RESET}"
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

    # Update THIS session's state file with hysteresis. A shared file here is
    # what let one session's context usage block another session's turns.
    project = CP.project_root(payload)
    state_path = CP.state_path(project, CP.session_slug(payload))
    state = CP.read_json(state_path)
    previous_last_offered = int(state.get("last_offered_bucket") or 0)

    # Resolved from the session's own switch when it has one, from the workspace
    # environment otherwise. `session_auto` is NOT in the update below and must
    # never be: this dict is written after every turn, so listing the operator's
    # choice here would erase it on the next render.
    auto = CP.auto_mode(state)

    state.update(
        {
            "session_id": CP.session_id(payload),
            "transcript_path": payload.get("transcript_path"),
            "soft_threshold": SOFT_THRESHOLD,
            "hard_threshold": HARD_THRESHOLD,
            "remind_step": REMIND_STEP,
            "auto": auto,
            "used_percentage": used,
            "remaining_percentage": cw.get("remaining_percentage"),
            "current_bucket": bucket,
            "updated_at": CP.utc_now().isoformat(),
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
        CP.write_json_atomic(state_path, state)
    except Exception as exc:
        # State write failure should not break the status line
        print(f"checkpoint-statusline: state write failed: {exc}", file=sys.stderr)

    print(build_status_line(payload, project, used, level, auto))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
