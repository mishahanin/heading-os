#!/usr/bin/env python3
"""Print this session's checkpoint paths, ready to write.

The hooks get a payload and know which session they are. `/checkpoint` is
model-driven and gets nothing, so before this script it CONSTRUCTED its own
paths and its SKILL.md told it to fall back to the literal slug "session" when
it could not derive one. That is how a manual checkpoint lands in the wrong
place: every path below the workspace root is keyed by session id now, and
guessing the key defeats the keying.

The session id comes from CLAUDE_CODE_SESSION_ID, which Claude Code exports to
child processes (verified on 2.1.228).

Usage:
  python scripts/checkpoint-paths.py                # key=value lines
  python scripts/checkpoint-paths.py --json         # the same, as JSON
  python scripts/checkpoint-paths.py --auto on      # stop asking, this session
  python scripts/checkpoint-paths.py --auto off     # ask again, this session
  python scripts/checkpoint-paths.py --auto status  # report, change nothing
  python scripts/checkpoint-paths.py --unattended on      # continue at a pause
  python scripts/checkpoint-paths.py --unattended off     # halt at a pause again
  python scripts/checkpoint-paths.py --unattended status  # report, change nothing
  python scripts/checkpoint-paths.py --compact-history    # where compaction fired

Archive paths are DATA-root-relative (`outputs/...`), which is the form the
@-reference and the inject hook resolve. The state path is project-relative.
Write them as printed; do not rebuild them by hand.
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils import checkpoint_paths as CP  # noqa: E402


def collect() -> dict:
    project = CP.project_root()
    handoff = CP.handoff_dir(project, CP.engine_root())
    sid = CP.session_id()
    slug = CP.safe_slug(sid)
    stamp = CP.utc_now().strftime("%Y-%m-%d-%H%M%S")

    root = CP.engine_root()
    if CP.is_engine_tree(root):
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
        from scripts.utils.workspace import get_data_root

        base = get_data_root()
    else:
        base = project

    def ref(path: Path) -> str:
        """Relative when it can be, absolute when it cannot. Total on purpose:
        a path the skill cannot resolve is worse than an ugly one."""
        try:
            return path.relative_to(base).as_posix()
        except ValueError:
            return path.as_posix()

    latest = CP.latest_dir(handoff, slug)
    shared = CP.latest_root(handoff)
    state = CP.state_path(project, slug)
    return {
        "session_id": sid,
        "session_slug": slug,
        "stamp": stamp,
        "project_root": str(project),
        "data_root": str(base),
        "archive": ref(handoff / f"{stamp}_handoff_manual_{slug}.md"),
        "summary_pointer": ref(latest / "summary.md"),
        "prompt_pointer": ref(latest / "prompt.md"),
        "shared_summary_pointer": ref(shared / "summary.md"),
        "shared_prompt_pointer": ref(shared / "prompt.md"),
        "state": (
            state.relative_to(project).as_posix()
            if project in state.parents
            else state.as_posix()
        ),
    }


def compact_history() -> int:
    """Print where compaction actually fired, per session, newest last.

    Console-first (.claude/rules/console-first.md): the record is written by two
    hooks, and without this the only way to read it is to cat a JSON file whose
    name contains a session id nobody has memorised.

    Every state file in the directory is read, not just this session's, because
    the question is usually asked in a FRESH session about the previous one -
    and by then this session's own history is empty.
    """
    state_dir = CP.state_path(CP.project_root(), "x").parent
    files = sorted(state_dir.glob("checkpoint-*.json")) if state_dir.is_dir() else []

    point = CP.compact_point()
    print(f"configured: {point[0]}={point[1]}" if point
          else "configured: nothing in the environment sets a compaction point")

    rows = 0
    for path in files:
        history = CP.read_json(path).get("compact_history")
        if not isinstance(history, list) or not history:
            continue
        print(f"\n{path.stem.removeprefix('checkpoint-')}")
        for entry in history:
            if not isinstance(entry, dict):
                continue
            pct = entry.get("used_pct_at_or_above")
            print(
                f"  {entry.get('at')}  trigger={entry.get('trigger')}  "
                f"fired at >= {pct}% used  (configured {entry.get('configured')})"
            )
            rows += 1

    if rows == 0:
        # Stated, not left as an empty page. A silent exit here reads as "it
        # never fires", which is a different claim from "nothing has been
        # recorded yet" - and after this change the second one is true until the
        # next compaction.
        print("\nNo compaction has been recorded yet on this tree.")
        print("The record is written at the next compact, by checkpoint-save.py.")
    return 0


def auto_switch(value: str) -> int:
    """Turn the hands-off mode on or off for THIS session, or report it.

    The switch lives in the session's own state file rather than in the
    environment, because the decision it records is a running one: the operator
    is twenty minutes into a piece of work when they conclude it is going to be
    long, and the conclusion is about one window, not about the workspace. Three
    sessions on this tree routinely do three different sizes of work.

    It is written under `session_auto`, never under `auto`. The statusline
    rewrites `auto` after every turn as its echo of the resolved mode, so a
    choice recorded there would last about one turn.

    No cleanup is needed to end it. The state file is keyed by session and
    pruned with the session, so the flag dies when the window does.
    """
    project = CP.project_root()
    slug = CP.safe_slug(CP.session_id())
    path = CP.state_path(project, slug)
    state = CP.read_json(path)

    if value == "status":
        chosen = state.get("session_auto")
        source = "this session" if chosen is not None else "the environment"
        print(f"auto={'on' if CP.auto_mode(state) else 'off'} (set by {source})")
        print(f"session_slug={slug}")
        return 0

    state["session_auto"] = value == "on"
    state["session_auto_at"] = CP.utc_now().isoformat()
    try:
        CP.write_json_atomic(path, state)
    except OSError as exc:
        print(f"checkpoint-paths: could not write the switch: {exc}", file=sys.stderr)
        return 1

    if value == "on":
        print(f"auto=on for this session ({slug}).")
        print("Checkpoints now save silently at each threshold and you stop being asked.")
        print("Compaction is unchanged: no hook can trigger it, so Claude Code's own")
        print("auto-compact still decides when the context is freed.")
    else:
        print(f"auto=off for this session ({slug}). The threshold offer comes back.")
    return 0


def unattended_switch(value: str) -> int:
    """Decide whether a pause hands the turn back, for THIS session.

    A second switch beside `--auto`, never a third value inside it. Two
    independent decisions live here: whether a checkpoint saves silently, and
    whether the session halts when it pauses and nobody answers. One field
    holding both would put `auto_mode()` in the path of every later change to
    either, and `auto_mode()` is what the statusline and both older modes read.

    Turning it ON also turns `--auto` on, because a run nobody is watching wants
    its handoff on disk. Turning it OFF undoes that, and ONLY that: it restores
    `--auto` to off when this switch is what turned it on, and leaves a
    separately chosen `--auto on` alone. The asymmetry is deliberate, and the
    first version got it wrong in a way a live run exposed at once: clearing only
    its own key left the operator with an `auto` he never asked for, silently,
    after typing `--unattended on` and then `--unattended off`.
    """
    project = CP.project_root()
    slug = CP.safe_slug(CP.session_id())
    path = CP.state_path(project, slug)
    state = CP.read_json(path)

    if value == "status":
        chosen = state.get("session_unattended")
        source = "this session" if chosen is not None else "the environment"
        on = CP.unattended_mode(state)
        cfg = CP.config(state)
        print(f"unattended={'on' if on else 'off'} (set by {source})")
        print(f"auto={'on' if cfg['auto'] else 'off'} · threshold {cfg['soft']}%")
        if on:
            wait = CP.wait_seconds()
            print(
                f"at the threshold: wait {wait}s, continue on silence · "
                f"continuations {int(state.get('unattended_continuations') or 0)}, "
                f"stall {int(state.get('unattended_stall') or 0)}"
            )
        if state.get("unattended_stalled_at"):
            # The recorded reason, not a hardcoded one. Two different fuses can
            # stop the mode and the hook writes which; printing the stall wording
            # for both made them indistinguishable to the operator.
            why = state.get("unattended_stop_reason") or "no reason recorded"
            print(f"STOPPED: {why}")
            print(f"stopped at: {state['unattended_stalled_at']}")
        return 0

    state["session_unattended"] = value == "on"
    state["session_unattended_at"] = CP.utc_now().isoformat()
    if value == "on":
        # Record whether WE are the reason auto is on, so `off` can undo exactly
        # what `on` did and nothing more.
        state["unattended_raised_auto"] = state.get("session_auto") is not True
        state["session_auto"] = True
        # A fresh run starts its counters from zero, or yesterday's stall would
        # stop tonight's work before it began.
        for key in (
            "unattended_continuations",
            "unattended_stall",
            "unattended_fingerprint",
            "unattended_stalled_at",
            "unattended_turn_id",
        ):
            state.pop(key, None)
    elif state.pop("unattended_raised_auto", False):
        state["session_auto"] = False
    try:
        CP.write_json_atomic(path, state)
    except OSError as exc:
        print(f"checkpoint-paths: could not write the switch: {exc}", file=sys.stderr)
        return 1

    if value == "on":
        wait = CP.wait_seconds()
        soft = CP.config(state)["soft"]
        print(f"unattended=on for this session ({slug}).")
        print(f"At {soft}% used: wait {wait}s for you, then continue without asking.")
        print("Type anything inside that window and the turn goes back to you.")
        print("Checkpoints also save silently now (auto=on).")
        print("Turn it off: python scripts/checkpoint-paths.py --unattended off")
    else:
        print(f"unattended=off for this session ({slug}). A pause waits for you again.")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Print this session's checkpoint paths.")
    ap.add_argument("--json", action="store_true", help="emit JSON instead of key=value")
    ap.add_argument(
        "--auto",
        choices=("on", "off", "status"),
        help="hands-off mode for THIS session only (overrides CLAUDE_HANDOFF_AUTO)",
    )
    ap.add_argument(
        "--unattended",
        choices=("on", "off", "status"),
        help="continue at a pause after a silent grace period, THIS session only "
             "(overrides CLAUDE_HANDOFF_UNATTENDED); `on` implies --auto on",
    )
    ap.add_argument(
        "--compact-history",
        action="store_true",
        help="print where compaction fired on this tree, per session",
    )
    args = ap.parse_args(argv)

    if args.compact_history:
        return compact_history()

    if args.auto:
        return auto_switch(args.auto)

    if args.unattended:
        return unattended_switch(args.unattended)

    paths = collect()
    if args.json:
        print(json.dumps(paths, indent=2))
    else:
        for key, value in paths.items():
            print(f"{key}={value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
