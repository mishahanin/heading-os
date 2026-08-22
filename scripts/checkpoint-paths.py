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
  python scripts/checkpoint-paths.py --compact-at 35      # compact here; raises unattended
  python scripts/checkpoint-paths.py --compact-at off     # back to the environment pair
  python scripts/checkpoint-paths.py --compact-at status  # report, change nothing
  python scripts/checkpoint-paths.py --done "plan X: 7 of 7"  # the plan is finished
  python scripts/checkpoint-paths.py --compact-history    # where compaction fired

Archive paths are DATA-root-relative (`outputs/...`), which is the form the
@-reference and the inject hook resolve. The state path is project-relative.
Write them as printed; do not rebuild them by hand.

Fifteen test files exercise this module and every one of them is named after the
behaviour it pins, so `scripts/turn-check.py`'s stem rule - which looks for
`test_checkpoint_paths*.py` - matched NONE of them, and editing this file ran
zero tests at the end of a turn while the lane printed `clean`. The declaration
below is that lane's fast contract, measured 2026-08-22: 97 tests in 2.91s. It is
deliberately NOT the full fifteen; those cost 60.6s because the rest of the
checkpoint suite sleeps through real countdowns, and `scripts/run-tests.py` still
runs all of them. Add a file here when it pins behaviour of THIS module and is
cheap; leave the sleepers out.

Tests: tests/test_checkpoint_state_lock.py, tests/test_unattended_state_machine.py
Tests: tests/test_session_compaction_threshold.py, tests/test_checkpoint_write_path.py
Tests: tests/test_checkpoint_operator_surface.py, tests/test_checkpoint_stamp_timezone.py
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils import checkpoint_paths as CP  # noqa: E402


def collect(kind: str = "manual") -> dict:
    """Every path the checkpoint skill needs, for one save.

    `kind` becomes the archive's kind segment. It exists because a hook-driven
    save and an operator-typed /checkpoint were indistinguishable on disk before
    2026-08-19: both landed as `_handoff_manual_`, so nothing downstream could
    tell "the system saved because it had to" from "the operator chose to save".
    The compaction probe's handoff invariant needs exactly that distinction, and
    the archive holds only four kinds - compact-manual, compact-auto, manual,
    session-close - none of which carried it.
    """
    project = CP.project_root()
    handoff = CP.handoff_dir(project, CP.engine_root())
    sid = CP.session_id()
    slug = CP.safe_slug(sid)
    # local_now, not utc_now: this stamp is the archive FILENAME, a calendar day
    # the operator reads. See CP.local_now for the measurement that changed it.
    # Stored timestamps in this file stay on utc_now and must.
    stamp = CP.local_now().strftime("%Y-%m-%d-%H%M%S")

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
        "archive": ref(handoff / f"{stamp}_handoff_{kind}_{slug}.md"),
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

    try:
        # The mutation happens INSIDE the lock, not merged into it
        # afterwards. `update` cannot delete, and these paths delete:
        # `lower_unattended` pops the keys it restored from, and a
        # merge would leave them behind for the next lower to read.
        with CP.locked_state(path) as state:
            state["session_auto"] = value == "on"
            state["session_auto_at"] = CP.utc_now().isoformat()
    except OSError as exc:
        print(f"checkpoint-paths: could not write the switch: {exc}", file=sys.stderr)
        return 1

    if value == "on":
        # The three lines below said the opposite until 2026-08-20: "no hook can
        # trigger it, so Claude Code's own auto-compact still decides". True when
        # written, false since the driven block landed in checkpoint-offer.py on
        # 2026-08-19 - `_request_compaction` gates on `auto_mode OR
        # unattended_mode`, so auto alone reaches the HERDR submit. A switch that
        # under-reports what it turns on is the defect this workspace calls a
        # scope claim (.claude/rules/scope-claims.md), and the operator was
        # reading it every time he flipped the switch.
        print(f"auto=on for this session ({slug}).")
        print("Checkpoints now save silently at each threshold and you stop being asked.")
        print("At or above the hard threshold, once that handoff is on disk, the Stop")
        print("hook also submits /compact to this session's terminal through HERDR.")
        print("Without HERDR hosting it, Claude Code's own auto-compact frees the")
        print("context instead.")
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
                f"continuations {int(state.get('unattended_continuations') or 0)}"
            )
        if state.get("unattended_done_at"):
            # Measured 2026-08-20 and deliberately left alone: `--unattended off`
            # does NOT clear the window keys, so this line still reports last
            # night's DONE while the mode reads off. Confusing, never wrong - the
            # marker did happen. The fix belongs in `CP.lower_unattended`, which
            # would have to call `clear_unattended_window`, and that function is
            # the hook's fuse path too. Not changed from this CLI alone.
            note = state.get("unattended_done_note") or "no note given"
            print(f"DONE: {note}")
            print(f"declared at: {state['unattended_done_at']}")
        if state.get("unattended_paused_at"):
            # The recorded reason, not a hardcoded one. The done marker and the
            # ceiling both pause a stretch and the hook writes which; one
            # hardcoded wording made them indistinguishable to the operator.
            why = state.get("unattended_stop_reason") or "no reason recorded"
            print(f"PAUSED: {why}")
            print(f"paused at: {state['unattended_paused_at']}")
            print("The switch is still on. Your next instruction resumes it.")
        return 0

    # Both bodies moved into scripts/utils/checkpoint_paths.py on 2026-08-19, so
    # this CLI and the hook's fuse stop cannot drift apart. They already had.
    try:
        # The mutation happens INSIDE the lock, not merged into it
        # afterwards. `update` cannot delete, and this path deletes:
        # `lower_unattended` pops the keys it restored from, and a
        # merge would leave them behind for the next lower to read.
        with CP.locked_state(path) as state:
            if value == "on":
                CP.raise_unattended(state)
            else:
                CP.lower_unattended(state)
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


def done_marker(note: str) -> int:
    """Declare the plan finished, so the unattended stretch stops at the pause.

    The assistant's half of the mode. The Stop hook reads state and never prose,
    so an assistant that writes "the work is finished" and stops has told the
    mechanism nothing, and the next pause continues it again. This is the sentence
    the mechanism can hear.

    It does NOT turn the mode off: `session_unattended` is the operator's, and he
    reads the status line in the morning and decides. The operator's next
    instruction clears this marker on its own, so a resumed plan needs no command.
    """
    project = CP.project_root()
    slug = CP.safe_slug(CP.session_id())
    path = CP.state_path(project, slug)
    state = CP.read_json(path)

    if not CP.unattended_mode(state):
        # Not an error. The marker is harmless and correct to record either way,
        # and refusing it would make the assistant's instruction conditional on a
        # switch it is told never to read.
        print(f"note: unattended is off for this session ({slug}); marker recorded anyway.")

    try:
        # Marked INSIDE the lock, on the current file. The statusline writes this
        # same file on every render, so marking a copy read a moment earlier and
        # then writing it back is how a marker goes missing.
        with CP.locked_state(path) as state:
            CP.mark_unattended_done(state, note)
    except OSError as exc:
        print(f"checkpoint-paths: could not write the marker: {exc}", file=sys.stderr)
        return 1

    # ONE line, and it was four until 2026-08-22. It still names BOTH halves of
    # the state, because they are not the same thing and the operator asked for
    # the distinction: the STRETCH is over and the bar says so, while the SWITCH
    # stays up. What went is the padding around that - the slug, and an echo of
    # the note the assistant had just typed on the line above. He reads this
    # output at the end of every stretch, so a word that tells him nothing new
    # is a word he re-reads forever.
    print("done recorded. Stretch ended, bar reads `unattended paused`; "
          "the switch stays on and your next instruction resumes it.")
    return 0


def compact_at_switch(value: str) -> int:
    """Set the threshold where THIS session compacts, or report it.

    The operator says "делаем compact на пороге 35%" at the start of important
    work, and the number has to take effect in the RUNNING window - a restart is
    the thing being avoided. It does: the Stop hook and the status line are both
    fresh processes per event and both re-read this file, so the value is in
    force at the next pause.

    Stored under `session_hard_threshold`, never under `hard_threshold`. The
    status line rewrites that key on every render as its echo of the resolved
    config, so a choice recorded there would last about one turn. Same reason
    `--auto` writes `session_auto`.

    The soft threshold is NOT stored. It is always `CP.SOFT_OFFSET` below the
    hard one, which is the relationship the operator fixed.

    No cleanup path is needed. The state file is keyed by session and pruned with
    it, so the number dies with the window - which is the right lifetime for a
    choice made about one piece of work.
    """
    project = CP.project_root()
    slug = CP.safe_slug(CP.session_id())
    path = CP.state_path(project, slug)
    state = CP.read_json(path)

    if value == "status":
        cfg = CP.config(state)
        chosen = state.get("session_hard_threshold")
        source = "this session" if chosen is not None else "the environment"
        print(f"compact-at={cfg['hard']}% hard - {cfg['soft']}% soft (set by {source})")
        when = state.get("session_hard_threshold_at")
        if when:
            print(f"set at: {when}")
        used = state.get("used_percentage")
        print(f"last status-line render read {used}% used" if used is not None
              else "this session has not reported its context usage yet")
        print(f"session_slug={slug}")
        return 0

    if value == "off":
        try:
            # Inside the lock, and `pop` rather than a merged update: `update`
            # cannot delete, so a merge would leave the key behind.
            with CP.locked_state(path) as fresh:
                fresh.pop("session_hard_threshold", None)
                fresh.pop("session_hard_threshold_at", None)
        except OSError as exc:
            print(f"checkpoint-paths: could not clear the threshold: {exc}", file=sys.stderr)
            return 1
        cfg = CP.config(CP.read_json(path))
        print(f"compact-at cleared for this session ({slug}).")
        print(f"Back to the workspace default: {cfg['hard']}% hard, {cfg['soft']}% soft.")
        return 0

    try:
        hard = int(value)
    except (TypeError, ValueError):
        print(f"checkpoint-paths: --compact-at takes a whole number, `status` or `off`, "
              f"not {value!r}", file=sys.stderr)
        return 2

    if hard < CP.HARD_THRESHOLD_MIN or hard > CP.HARD_THRESHOLD_MAX:
        print(f"checkpoint-paths: refused. {hard} is outside {CP.HARD_THRESHOLD_MIN}-"
              f"{CP.HARD_THRESHOLD_MAX}. Under {CP.HARD_THRESHOLD_MIN} the soft reminder "
              f"lands below 10% and the trigger cascades against the context floor; over "
              f"{CP.HARD_THRESHOLD_MAX} there is no window left to write the handoff.",
              file=sys.stderr)
        return 2

    # Guarded for the same reason `_session_hard` is: this file is hand-editable,
    # and a CLI that tracebacks on a bad sample is worse than one that says it
    # could not check (.claude/rules/scope-claims.md).
    raw_used = state.get("used_percentage")
    try:
        used = None if raw_used is None else float(raw_used)
    except (TypeError, ValueError):
        used = None
    if used is not None and hard <= used:
        # The reading is named as one render old, not as the present fill. Only
        # `checkpoint-statusline.py` writes it, and only on a render that
        # measured, so inside one long turn the true fill has already outrun it.
        print(f"checkpoint-paths: refused. This session read {used}% used at its last "
              f"status-line render, so a hard threshold of {hard} would fire at the very "
              f"next pause. The reading is one render old and the window only grows. "
              f"Pick a number above {used}, or run --compact-at off.", file=sys.stderr)
        return 2

    # Setting a threshold RAISES unattended, inside the same lock. Operator
    # directive, 2026-08-22: he typed the two commands together every time, and
    # a threshold with both switches down moves the QUESTION and compacts
    # nothing - which is a value set and never acted on, the failure this
    # replaces. `raise_unattended` raises `session_auto` too, and that is what
    # makes the compaction driven rather than merely offered.
    #
    # Only on an accepted NUMBER. `status` and `off` return above, and both
    # refusals return before this block, so the switch rides on the write and
    # cannot outlive a rejected value.
    #
    # Skipped when the mode is already on, because `raise_unattended` clears the
    # window - it pops the continuation counter and every stretch key - so
    # re-raising mid-run would hand a live stretch a fresh ceiling the operator
    # never asked for. A paused stretch needs no help from here either: any
    # instruction he types clears the pause through `unattended-resume.py`.
    raised = False
    try:
        with CP.locked_state(path) as fresh:
            fresh["session_hard_threshold"] = hard
            fresh["session_hard_threshold_at"] = CP.utc_now().isoformat()
            if not CP.unattended_mode(fresh):
                CP.raise_unattended(fresh)
                raised = True
    except OSError as exc:
        print(f"checkpoint-paths: could not write the threshold: {exc}", file=sys.stderr)
        return 1

    print(f"compact-at={hard}% for this session ({slug}). "
          f"Soft reminder at {hard - CP.SOFT_OFFSET}%.")
    if used is None:
        print("This session has not reported a usable context reading, so the value was "
              "not checked against the current fill.")
    if raised:
        print(f"unattended is now on as well, so the hook compacts at {hard}% instead "
              "of asking. Only you lower it: --unattended off.")
    else:
        print("unattended was already on, and the running stretch was left untouched.")
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
        "--done",
        metavar="NOTE",
        help="declare the plan finished: the unattended stretch stops at the next "
             "pause, and the switch is left alone for the operator to read",
    )
    ap.add_argument(
        "--compact-at",
        metavar="N|status|off",
        help="hard threshold where THIS session offers, and compacts when auto or "
             "unattended is on; 15-90, soft is always 5 below; `off` returns it to "
             "CLAUDE_HANDOFF_HARD_THRESHOLD",
    )
    ap.add_argument(
        "--kind",
        choices=("manual", "auto"),
        default="manual",
        help="archive kind segment: manual for an operator-typed /checkpoint, "
             "auto for a save the Stop hook asked for",
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

    if args.done is not None:
        return done_marker(args.done)

    if args.compact_at is not None:
        return compact_at_switch(args.compact_at)

    paths = collect(args.kind)
    if args.json:
        print(json.dumps(paths, indent=2))
    else:
        for key, value in paths.items():
            print(f"{key}={value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
