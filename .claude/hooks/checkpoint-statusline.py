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

Stdlib only. Atomic JSON writes. Does not raise on a malformed payload: an
unparseable one prints "Claude Code", and a field of the wrong shape degrades to
`context: n/a` - never to a blank bar, which is what a dead hook looks like. That
sentence read "Never raises." until 2026-08-20 and was false: six of eight
malformed payloads exited 1 with an uncaught AttributeError and printed nothing.
Held by tests/test_checkpoint_session_scope.py.
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
    # UNUSED. Measured 2026-08-20: no call site in this file, none anywhere under
    # scripts/ .claude/ or tests/ - every colour in this module is applied by the
    # C_* constants directly. Left in place rather than deleted, because it is
    # pre-existing dead code and not something this change orphaned
    # (.claude/rules/development-standards.md, Surgical changes). Surfaced here so
    # the next reader does not take it for a live seam.
    if _USE_ANSI:
        return f"{code}{text}\033[0m"
    return text


C_RESET = "\033[0m" if _USE_ANSI else ""
C_DIM = "\033[2m" if _USE_ANSI else ""
C_CYAN_B = "\033[1;36m" if _USE_ANSI else ""
C_YELLOW_B = "\033[1;33m" if _USE_ANSI else ""
C_GREEN = "\033[32m" if _USE_ANSI else ""
C_GREEN_B = "\033[1;32m" if _USE_ANSI else ""
C_YELLOW = "\033[33m" if _USE_ANSI else ""
C_RED = "\033[31m" if _USE_ANSI else ""


def _mapping(value) -> dict:
    """A payload sub-object, or an empty dict when it is not one.

    "Never raises" was in this module's docstring and was false. Measured on
    2026-08-20 against the shipped hook: a payload whose `context_window`,
    `workspace` or `model` is a string or a list, and a payload that is itself a
    JSON list or string, each ended in an uncaught AttributeError - six of eight
    malformed inputs exited 1 and printed NO status line at all. A blank bar is
    the worst answer this hook has, because it is exactly what a hook that has
    stopped running looks like, and telling those two apart is why the autonomy
    segment exists. So the shape is checked rather than assumed, and a payload
    this hook cannot read degrades to `context: n/a` instead of to nothing.
    """
    return value if isinstance(value, dict) else {}


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


def autonomy_segment(auto: bool, unattended: bool, ended: bool = False) -> str:
    """One always-present segment naming which autonomy switches are live.

    Always rendered, in all four states, on purpose. Until 2026-08-19 the only
    hint was an `auto-` prefix inside the checkpoint tag, which appeared solely
    when a checkpoint was already due. So `off` and `on` looked identical for
    most of a session, and the operator could not tell a switch that was off
    from a mechanism that had failed. Showing `off` costs nine characters and
    removes that ambiguity, which is the whole point of putting it here.

    `ended` is the fourth state and it was missing until 2026-08-20. The done
    marker and the ceiling fuse both END a stretch WITHOUT lowering the switch -
    deliberately, because the switch is the operator's - so `session_unattended`
    stays true while the next pause hands the turn back instead of continuing.
    Measured that morning: a stretch stopped by `--done` rendered
    `⏵ unattended` byte-identically to one still running, while
    `--unattended status` reported `DONE:` and `PAUSED:` for the same state. The
    bar was the surface the operator reads first and it was the one that could
    not tell a working night from a finished one, which is the same ambiguity
    the segment was added to remove.
    """
    if unattended:
        if ended:
            # Two spaces after the glyph, for the reason given on `manual` below.
            return f"{C_YELLOW}⏸  unattended paused{C_RESET}"
        return f"{C_GREEN_B}⏵ unattended{C_RESET}"
    if auto:
        return f"{C_YELLOW}⏵ auto{C_RESET}"
    # Two spaces after the glyph, not one. U+23F8 renders narrower than U+23F5 in
    # the operator's terminal, so a single space left `manual` visually crowding
    # the pause mark while `unattended` sat clear of the play mark.
    return f"{C_DIM}⏸  manual{C_RESET}"


def build_status_line(
    payload: dict,
    project: Path,
    used: float | None,
    level: str | None,
    auto: bool,
    unattended: bool = False,
    stretch_ended: bool = False,
) -> str:
    parts: list[str] = []

    workspace = _mapping(payload.get("workspace"))
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

    parts.append(autonomy_segment(auto, unattended, stretch_ended))

    model = _mapping(payload.get("model")).get("display_name") or "Claude"
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

    payload = _mapping(payload)
    cw = _mapping(payload.get("context_window"))
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
    # Read for display only. Like `session_auto`, it is deliberately absent from
    # the update below: this dict is written every turn, so listing the
    # operator's choice here would erase it on the next render.
    unattended = CP.unattended_mode(state)
    # The two window keys that say the STRETCH ended while the SWITCH stayed up.
    # Read here rather than in `build_status_line`, which is handed values and
    # never the state file. Both are cleared together by
    # `CP.clear_unattended_window`, so a resumed run drops the marker on its own
    # and the bar goes back to green without anything else to run.
    stretch_ended = bool(
        state.get("unattended_done_at") or state.get("unattended_paused_at")
    )

    now_iso = CP.utc_now().isoformat()
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
            # Absolute numbers, recorded because the percentage alone cannot
            # settle an argument about where compaction fires. The harness
            # sends these in the same payload; nothing here derives them.
            "context_window_size": cw.get("context_window_size"),
            "context_input_tokens": _mapping(cw.get("current_usage")).get("input_tokens"),
            "current_bucket": bucket,
            "updated_at": now_iso,
        }
    )

    # AFTER the update, never inside it. This dict is written every turn, so a
    # peak listed among the fields above would be overwritten by the current
    # reading on the next render - which is the defect being fixed, not the fix.
    CP.record_peak(state, used, now_iso)

    if used is None:
        # The payload carried no readable context reading, so this render
        # measured NOTHING about the window. Leave the three offer keys exactly
        # as they are on disk.
        #
        # This branch exists because of the malformed-payload hardening added on
        # 2026-08-20. Before it, such a payload raised and the hook wrote no
        # state at all, so a pending hard-threshold offer survived. Afterwards
        # the same payload fell through to the below-threshold branch below and
        # CLEARED it: `needs_compact_offer` went False and the queued save was
        # dropped in silence, one render before the Stop hook would have taken
        # it. "I could not measure" is not "below the threshold"
        # (.claude/rules/scope-claims.md § fail toward over-reporting).
        pass
    elif level is not None:
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

    # NOT converted to the read-modify-write of `checkpoint-offer.py::_persist`,
    # which applies only its own keys to what is on disk NOW. Measured
    # 2026-08-20: the exposed span between the read above and this write is 0.814
    # ms median (3.686 ms max), of which 0.239 ms is the read and the atomic write
    # themselves, so re-reading here would shrink the window about 3x and close
    # nothing. A concurrent `scripts/checkpoint-paths.py --unattended on` was lost
    # in 1 of 60 forced-overlap trials, silently, while the CLI printed
    # "unattended=on". Closing it needs one exclusive lock around read-modify-write
    # shared by every writer of this file, which belongs in
    # scripts/utils/checkpoint_paths.py - outside this hook. Reported rather than
    # half-fixed here, because a 0.24 ms window that looks handled is worse than a
    # 0.81 ms one that is known.
    try:
        CP.write_json_atomic(state_path, state)
    except Exception as exc:
        # State write failure should not break the status line
        print(f"checkpoint-statusline: state write failed: {exc}", file=sys.stderr)

    print(
        build_status_line(
            payload, project, used, level, auto, unattended, stretch_ended
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
