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

A session may override the pair with `scripts/checkpoint-paths.py --compact-at N`;
`CP.config(state)` prefers `session_hard_threshold` and derives soft SOFT_OFFSET
below it, so the env-vars above are the workspace DEFAULT, not the mechanism.

Auto-compact is NOT disabled (last-resort policy). This hook only signals
the Stop hook to offer a checkpoint.

Stdlib only. Atomic JSON writes. Does not raise on a malformed payload: an
unparseable one prints "Claude Code", and a field of the wrong shape degrades to
`context: n/a` - never to a blank bar, which is what a dead hook looks like. That
sentence read "Never raises." until 2026-08-20 and was false: six of eight
malformed payloads exited 1 with an uncaught AttributeError and printed nothing.
Held by tests/test_checkpoint_session_scope.py.

The same promise covers the STATE file, and did not until 2026-08-31: a
hand-edited `last_offered_bucket`, and a `used_percentage` of `NaN` or `Infinity`
(which `json.loads` accepts by default), each printed that blank bar. Held by
tests/test_a_number_that_settled_nothing_it_was_added_to_settle.py.
"""

import json
import math
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

# Thresholds are deliberately NOT read at module scope. They are now a
# per-session decision the operator can set mid-work with `--compact-at`, so they
# are resolved in main() from the session's own state file - which has to be read
# BEFORE the level is computed. Same reason `auto` moved down on 2026-08-16.
#
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


def _finite(value) -> float | None:
    """A real, finite percentage, or None when the payload does not carry one.

    `float()` was the whole check until 2026-08-31 and it accepts three values
    that no arithmetic downstream survives. Measured that day against the shipped
    hook, driving it with the payload the harness sends: `used_percentage` of
    `NaN`, `Infinity` and `1e400` each reached `int(used // step)` and raised
    ValueError, "cannot convert float NaN to integer", uncaught, exit 1, NO
    status line printed. `inf // 5` is `nan`, so the two inputs meet on the same
    crash, and `progress_bar` raises identically one line later.

    Reachable through the same surface the 2026-08-20 hardening was written for:
    `json.loads` accepts bare `NaN` and `Infinity` literals BY DEFAULT, so a
    malformed payload carries them straight in. Rejecting them here lands the
    `context: n/a` degradation this module's docstring promises, instead of the
    blank bar that looks exactly like a hook which has stopped running.

    `bool` is rejected with them. `float(True)` is 1.0, so a payload reading
    `"used_percentage": true` rendered a confident 99% remaining off a value that
    says nothing about the window. A wrong number is worse than `n/a`, because
    only one of the two is believed.
    """
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def coerce_used(cw: dict) -> float | None:
    used = _finite(cw.get("used_percentage"))
    if used is not None:
        return used
    remaining = _finite(cw.get("remaining_percentage"))
    if remaining is not None:
        return 100.0 - remaining
    return None


# The three input-token classes of one API usage record. Their SUM is the context
# the request carried; `input_tokens` alone is only the part that missed the
# cache. Verified 2026-08-31 against 35,667 usage objects in this session's own
# transcript, whose newest read `input_tokens: 2`,
# `cache_creation_input_tokens: 21123`, `cache_read_input_tokens: 193725`.
_TOKEN_KEYS = ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")


def context_tokens(usage: dict) -> int | None:
    """The whole context in tokens, or None when the payload cannot say.

    `context_input_tokens` was `current_usage["input_tokens"]` until 2026-08-31,
    which is the LAST REQUEST's uncached input and not the context total. It sat
    under the comment below saying the absolute numbers exist because a
    percentage cannot settle an argument about where compaction fires, and it
    could not settle it: measured that day on the live state file, `used%` 22.0
    against a 1,000,000 window means 220,000 tokens and the recorded figure read
    2. The two are not proportional at any scale, and 2 is the number an operator
    would have checked a configured 750,000 threshold against. Summing the three
    classes gives 214,850 for the same reading, 21.485% of the window, which is
    the percentage back.

    Dropping the key was the other option. The sum wins because the argument it
    was added for is live: this session's `compact_history` shows compactions
    firing at 42% and 58% against a configured 75%, and a token count is what
    tells a threshold in force from a threshold ignored.

    A component that is absent counts as zero, because the API omits a class it
    has nothing to report in. A component of the wrong shape is skipped and says
    so on stderr, since a payload that changed shape is worth seeing rather than
    silently under-summing. None comes back only when not one class was readable,
    so a render that learned nothing about the window records nothing about it
    (.claude/rules/scope-claims.md § fail toward over-reporting).
    """
    total = 0
    counted = 0
    for key in _TOKEN_KEYS:
        value = usage.get(key)
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            print(
                f"checkpoint-statusline: current_usage.{key} is a "
                f"{type(value).__name__}, not a number; leaving it out of the sum",
                file=sys.stderr,
            )
            continue
        if not math.isfinite(value):
            print(
                f"checkpoint-statusline: current_usage.{key} is {value!r}; "
                "leaving it out of the sum",
                file=sys.stderr,
            )
            continue
        total += int(value)
        counted += 1
    return total if counted else None


def _last_offered(state: dict) -> int:
    """The highest bucket already offered, or 0 when the file does not say one.

    `int(state.get("last_offered_bucket") or 0)` was the whole read until
    2026-08-31. Measured that day by driving the real hook against a hand-written
    state file: `"high"` raised ValueError, a dict and a list each raised
    TypeError, and all three exited 1 with an EMPTY status line - the blank bar
    this module's docstring says it cannot produce, and the one shape that is
    indistinguishable from a hook no longer running.

    `_session_hard` in scripts/utils/checkpoint_paths.py already decided this
    class one file away, for the same file and the same reason: "a bad value
    arriving here means the file was hand-edited or written by an older build,
    and a status line that crashes on it is worse than one that falls back". So
    this is that rule, not a second one. It falls back to 0 rather than crashing,
    which re-offers a checkpoint at most once, and it names the value on stderr
    so the bad field gets fixed instead of silently absorbed.

    `not raw` first, so every value that already resolved to 0 keeps resolving to
    0 in silence: None, an absent key, 0, "" and False all did under `or 0`.
    """
    raw = state.get("last_offered_bucket")
    if not raw:
        return 0
    try:
        return int(raw)
    except (TypeError, ValueError):
        print(
            f"checkpoint-statusline: last_offered_bucket is a "
            f"{type(raw).__name__} {raw!r}; reading it as 0",
            file=sys.stderr,
        )
        return 0


def git_branch(cwd: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "branch", "--show-current"],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            # git stores a ref name as bytes and will print one back verbatim,
            # so a branch name that is not valid UTF-8 made strict decoding
            # raise `UnicodeDecodeError` from inside `subprocess.run`. That is a
            # `ValueError`, which none of the three handlers below catches, so
            # the statusline raised on every render instead of degrading to the
            # empty string it promises for every other failure.
            errors="replace",
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


def autonomy_segment(
    auto: bool, unattended: bool, ended: bool = False, hard: int | None = None
) -> str:
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

    `hard` is the threshold where the driven compaction fires, added 2026-08-21.
    It is rendered in every state that CAN fire it and in none that cannot. The
    gate is `_request_compaction`, which requires `auto_mode OR unattended_mode`:
    a paused stretch still qualifies, because the STRETCH ended and the SWITCH did
    not, and `manual` never does. The operator asked for the point at which
    compaction happens, not the point at which he is asked, so a number on
    `manual` would describe something that does not happen. It is shown whether it
    came from the session or from the environment - a number that appeared only
    once overridden would leave "not set" and "not working" looking the same,
    which is the ambiguity this segment exists to remove.
    """
    at = f" {hard}%" if hard is not None else ""
    if unattended:
        if ended:
            # Two spaces after the glyph, for the reason given on `manual` below.
            return f"{C_YELLOW}⏸  unattended paused{at}{C_RESET}"
        return f"{C_GREEN_B}⏵ unattended{at}{C_RESET}"
    if auto:
        return f"{C_YELLOW}⏵ auto{at}{C_RESET}"
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
    hard: int | None = None,
) -> str:
    parts: list[str] = []

    workspace = _mapping(payload.get("workspace"))
    # `isinstance`, not truthiness. The 2026-08-20 hardening shape-checked the
    # three top-level sub-objects and stopped there, so a non-string INSIDE one
    # of them - `{"cwd": 5}`, `{"workspace": {"current_dir": ["/tmp"]}}` - flowed
    # into `Path()` and raised TypeError, exit 1, no status line at all: the
    # blank bar the docstring says cannot happen. `CP.project_root` had already
    # rejected the same value and said so on stderr one call earlier, so the
    # process knew it was bad and used it anyway.
    raw_cwd = workspace.get("current_dir") or payload.get("cwd")
    cwd_str = raw_cwd if isinstance(raw_cwd, str) and raw_cwd else str(project)
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

    parts.append(autonomy_segment(auto, unattended, stretch_ended, hard))

    model = _mapping(payload.get("model")).get("display_name") or "Claude"
    parts.append(f"{C_DIM}{model}{C_RESET}")

    return " ".join(parts)


def apply_render(
    state: dict, payload: dict, cw: dict, used: float | None, now_iso: str
) -> dict:
    """Decide this render, stamp it into `state` in place, return what the bar needs.

    Split out of `main()` on 2026-08-31 so that every input to the offer decision
    is read from the same dict the decision is written into, inside ONE hold of
    `CP.locked_state`. Until then `main()` read the state file at the top of the
    render, decided from that copy, and merged the result under the lock at the
    bottom, so the lock covered the MERGE and not the DECISION. The exposed span
    is the one this file already measured on 2026-08-20: 0.814 ms median, 3.686 ms
    at worst.

    What that window cost, in the two fields read across it. The Stop hook
    stamping `last_offered_bucket` 30 inside it left this render still holding the
    25 it read before, so `30 > 25` queued a checkpoint offer for a bucket already
    consumed and the next Stop re-offered it. A `--compact-at N` landing inside it
    was echoed back stale into `soft_threshold` and `hard_threshold`, which is not
    only a display lag: that render also picks its LEVEL against the threshold it
    is about to overwrite.

    Nothing here prints, forks, sleeps or reaches the network, so the locked span
    is this arithmetic and two dict updates and nothing else. `main()` keeps the
    print outside the lock, where holding it would be worse than the race.

    Update THIS session's state file with hysteresis. A shared file here is what
    let one session's context usage block another session's turns.

    The state is read BEFORE the level is computed, which is the reverse of the
    order this function used until 2026-08-21. The thresholds come from the
    session's own file, and this hook is the SOLE producer of
    `needs_compact_offer`, the flag that eventually stamps `last_offer_at`, the
    floor `_request_compaction` hands to `_handoff_since`. Computing the level from
    the environment while the Stop hook read the session's number would queue no
    offer at that number: the threshold would look set and do nothing.
    """
    previous_last_offered = _last_offered(state)

    # Resolved from the session's own state, exactly as `auto` below is.
    cfg = CP.config(state)

    # Compute level + bucket
    if used is None:
        level = None
        bucket = 0
    else:
        if used >= cfg["hard"]:
            level = "hard"
        elif used >= cfg["soft"]:
            level = "soft"
        else:
            level = None
        bucket = int(used // cfg["step"]) * cfg["step"]

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

    state.update(
        {
            "session_id": CP.session_id(payload),
            "transcript_path": payload.get("transcript_path"),
            "soft_threshold": cfg["soft"],
            "hard_threshold": cfg["hard"],
            "remind_step": cfg["step"],
            "auto": auto,
            "updated_at": now_iso,
        }
    )
    # The MEASUREMENT keys, written only by a render that actually measured.
    #
    # They were in the block above until 2026-08-20, so a payload carrying no
    # readable `context_window` stamped `used_percentage: null` and
    # `current_bucket: 0` over the last good reading. That is not a display
    # detail: `checkpoint-offer.py::_used_percentage` reads this key, returns
    # None on a null, and every threshold decision on that Stop then runs with
    # no reading at all. Observed live in the compaction watch log the same day,
    # 51.0 -> null -> 52.0 inside three minutes.
    #
    # Same rule as the offer keys below: a render that measured nothing decides
    # nothing (.claude/rules/scope-claims.md § fail toward over-reporting).
    if used is not None:
        state.update(
            {
                "used_percentage": used,
                "remaining_percentage": cw.get("remaining_percentage"),
                # Absolute numbers, recorded because the percentage alone cannot
                # settle an argument about where compaction fires. The harness
                # sends these in the same payload; nothing here derives them.
                "context_window_size": cw.get("context_window_size"),
                "context_input_tokens": context_tokens(
                    _mapping(cw.get("current_usage"))
                ),
                "current_bucket": bucket,
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

    return {
        "level": level,
        "auto": auto,
        "unattended": unattended,
        "stretch_ended": stretch_ended,
        "hard": cfg["hard"],
    }


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

    project = CP.project_root(payload)
    state_path = CP.state_path(project, CP.session_slug(payload))
    now_iso = CP.utc_now().isoformat()

    # Read, decide and write inside one hold of the lock every writer of this
    # file shares (`CP.locked_state`). The dict it yields was read from disk under
    # that lock and is written back atomically when the block exits, so mutating
    # it in place IS the write, and nothing another process stamped in the
    # meantime can be overwritten by a value read before it existed.
    #
    # This replaced a read-decide-outside plus diff-and-merge-inside pair on
    # 2026-08-31. That pair closed half the hole: writing `state` wholesale had
    # lost a concurrent `--unattended on` in 1 of 60 forced-overlap trials on
    # 2026-08-20, and sending only the changed keys fixed exactly that. It could
    # not fix the decision, which still ran on the pre-lock copy. The computed
    # diff went with it: with the read and the write under one lock there is no
    # window left for it to protect against.
    render = None
    try:
        with CP.locked_state(state_path) as state:
            render = apply_render(state, payload, cw, used, now_iso)
    except Exception as exc:
        # State write failure should not break the status line
        print(f"checkpoint-statusline: state write failed: {exc}", file=sys.stderr)
    if render is None:
        # Nothing was recorded, and the bar still has to print rather than go
        # blank. So the display values are recomputed from an unlocked read whose
        # dict is thrown away: this pass decides for the screen and writes nothing.
        render = apply_render(CP.read_json(state_path), payload, cw, used, now_iso)

    print(
        build_status_line(
            payload,
            project,
            used,
            render["level"],
            render["auto"],
            render["unattended"],
            render["stretch_ended"],
            render["hard"],
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
