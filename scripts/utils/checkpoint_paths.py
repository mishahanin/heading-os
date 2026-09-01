#!/usr/bin/env python3
"""Path and config seam shared by the four checkpoint hooks and /checkpoint.

Every path the checkpoint system writes is keyed by SESSION, because this
workspace routinely has several sessions open on one tree and anything shared
between them is last-writer-wins. Measured on 2026-08-16 against the pre-fix
hooks: session A at 46% wrote the state file that session B's Stop hook read, so
the idle session was told to checkpoint and the full one was not.

Two roots, deliberately not the same thing:

  - the PROJECT root comes from the hook payload and is where `.claude/state/`
    lives. It follows the session's own tree, so a plugin installed in someone
    else's repository writes there and not into the plugin cache.
  - the ENGINE root is the third parent of this file, which is the tree owning
    the `scripts/utils/` it sits in. In the monorepo that is the engine; inside
    a built plugin bundle it is the bundle. It decides which layout applies.
    This said "found by walking up from this file until the tree that contains
    `scripts/utils/`" until 2026-08-30, describing a search `engine_root()` does
    not perform - and could not usefully perform, since three parents up from
    any `<X>/scripts/utils/checkpoint_paths.py` IS `<X>`, so the two agree
    wherever the walk has an answer at all. What the fixed climb really rests on
    is the file's installed LOCATION, and a caller that cannot vouch for it
    passes its own root instead (see `handoff_dir`).

The archive follows the engine's data seam when this is a HEADING OS tree AND a
private overlay actually backs it. Without the overlay it goes to gitignored
`.claude/state/handoff/`, and outside a HEADING OS tree to project-local
`.claude/handoff/`, which is the case for any repository that installs the
plugin bundle. See `handoff_dir()` for why the two questions are separate.

Stdlib only, and it stays that way: `checkpoint-save.py` runs after the session
context has been discarded, so an import this module cannot satisfy costs a
handoff nobody can regenerate.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import subprocess  # nosec B404 - fixed argv, never shell=True
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

# Pointer dirs and state files are per session and never revisited once that
# session ends, so without a bound they grow forever. The nexi plugin pruned the
# pointer dirs and left the state files: 30 sessions, 26 dirs, 30 state files.
KEEP_DAYS = 14
KEEP_MAX = 25

# What a pointer may carry. The live shared pointer reached 32261 bytes against
# an 8000-character inject cap, so three quarters of it never reached a session
# and the quarter that did was cut mid-sentence. Bounding it at the WRITE keeps
# the file on disk equal to the text that gets injected.
MAX_POINTER_SUMMARY = 6000


def force_utf8() -> None:
    """Windows defaults to CP1252, which breaks the status bar's box characters."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception as exc:  # noqa: BLE001 - never break a hook over stream setup
                print(f"checkpoint: stream reconfigure failed: {exc}", file=sys.stderr)


def engine_root() -> Path:
    """The tree that owns `scripts/utils/` — engine monorepo or plugin bundle.

    Three fixed parents, no search and no verification: correct exactly while
    this file sits at `<root>/scripts/utils/checkpoint_paths.py`, and silently
    an ancestor of the wrong tree if a packager ever moves it. Measured
    2026-08-30 from `/tmp/bundle/utils/checkpoint_paths.py`, one level short:
    it answered `/tmp`. There is nothing better to do here - a walk looking for
    `scripts/utils/` finds the same answer whenever it finds one at all - so the
    guard is on the CALLER side: `handoff_dir` takes an explicit `root`.
    """
    return Path(__file__).resolve().parent.parent.parent


# `bootstrap_root(start)` lived here from 04e6707 (2026-08-16) until 2026-08-20.
# It walked up from a hook's path looking for `scripts/utils/checkpoint_paths.py`
# and returned the tree that owned it — to answer, in its own words, "find the
# tree a HOOK can import this module from".
#
# It could never be called. Reaching it requires importing THIS module, which
# requires already knowing the answer it computes. The five hooks each inline the
# identical walk in their own preamble, before their import line, because that is
# the only place it can run. Removed after checking both halves: `grep` across
# every file type finds no reference outside the definition, and `git show`
# on the commit that introduced it shows the name appearing exactly once, as the
# `def`. It was never wired, not even at birth.
#
# The five inline copies are real duplication and are NOT removable the same way,
# for the same chicken-and-egg reason. Left in place deliberately.


def is_engine_tree(root: Path) -> bool:
    """True for the HEADING OS monorepo, false for a built plugin bundle.

    `config/routing-map.yaml` is the engine's routing input and is not bundled,
    so its presence separates "this is a HEADING OS tree" from "this is somebody
    else's repository with our plugin installed" without guessing.

    It says NOTHING about whether a private data overlay is mounted. This
    docstring claimed it did until 2026-08-26, and `handoff_dir()` believed it:
    a public clone answers True here and has no overlay, so handoffs were written
    into the engine repository. Ask `data_overlay_present()` for that question.
    """
    return (root / "config" / "routing-map.yaml").is_file()


def project_root(payload: dict | None = None) -> Path:
    """Where `.claude/state/` belongs: the tree this session is working in."""
    payload = payload or {}
    candidates: list[str] = []
    workspace = payload.get("workspace") or {}
    if isinstance(workspace, dict):
        for key in ("project_dir", "current_dir"):
            if workspace.get(key):
                candidates.append(workspace[key])
    if payload.get("cwd"):
        candidates.append(payload["cwd"])
    for var in ("CLAUDE_PROJECT_DIR", "WORKSPACE_ROOT"):
        if os.environ.get(var):
            candidates.append(os.environ[var])
    candidates.append(os.getcwd())
    for candidate in candidates:
        try:
            path = Path(candidate).expanduser()
            if path.is_dir():
                return path.resolve()
        except Exception as exc:  # noqa: BLE001 - a bad candidate is skipped, not fatal
            print(f"checkpoint: unusable project candidate {candidate!r}: {exc}",
                  file=sys.stderr)
    return Path(os.getcwd()).resolve()


def handoff_dir(project: Path, root: Path | None = None) -> Path:
    """The archive root: the operator's data overlay, or project-local.

    `root` is the tree the CALLER resolved, and passing it is not ceremony. This
    function used to ask `engine_root()`, which reads THIS module's `__file__` -
    and the module a hook ends up importing is not always the copy beside it. In
    a venv where the engine is installed as a package, an editable-install
    finder runs ahead of `sys.path`, so a hook inside a plugin bundle imported
    the ENGINE's copy of this file and was told it was in an engine tree. It
    then wrote a stranger's handoff into the operator's data root. Measured
    2026-08-16. The hook already knows which tree it is in; it says so here.

    TWO questions, not one, and conflating them leaked. `is_engine_tree()` was
    documented as separating "this operator has a data overlay" from "somebody
    else's repository with our plugin installed", and that belief was refuted on
    2026-08-26: a PUBLIC clone of the engine carries `config/routing-map.yaml`
    and no overlay at all. It took the engine branch, `get_outputs_dir()` fell to
    its documented last resort `<workspace_root>/examples`, and whole session
    handoffs landed in the repository that gets pushed. Measured in a worktree
    with no sibling overlay: one suite run wrote six handoff files into
    `examples/outputs/operations/handoff-archive/`.

    So the layout question is asked first and the overlay question second. With
    no overlay the archive goes under `.claude/state/`, which is gitignored, so a
    handoff can never be committed by accident. `data_overlay_present()` rather
    than `not data_root_is_demo()` on purpose: the in-tree transitional layout
    answers the data root as the workspace root itself, which is still inside the
    clone, and only the wider predicate refuses both.

    A hook must not raise here. `checkpoint-save.py` runs after the session
    context is gone, so a refusal that propagates costs a handoff nobody can
    regenerate. This one redirects instead.

    And until 2026-08-30 it did not: the engine branch reached two in-tree
    modules with nothing around them, so anything they raised left this function
    and the handoff with it. Not hypothetical, and it needs no sabotage to
    reproduce - `HEADING_OS_DATA` is pinned per host, and pointing it at a
    directory that has been moved or deleted makes `data_overlay_present()`
    raise `DataRootError` by design ("Refusing to fall back"). Measured that day
    against this very tree. That refusal is right for a tool about to WRITE the
    operator's data and wrong for a hook about to write a handoff, so it is
    caught here and the archive redirects to the gitignored project-local
    directory - never into the engine tree. `local_now`, in this same file,
    has guarded its sibling import this way all along.
    """
    root = root or engine_root()
    if is_engine_tree(root):
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
        try:
            from scripts.utils.paths import data_overlay_present
            from scripts.utils.workspace import get_outputs_dir

            if data_overlay_present():
                return get_outputs_dir() / "operations" / "handoff-archive"
        except Exception as exc:  # noqa: BLE001 - losing the handoff is the worse outcome
            print(f"checkpoint: cannot resolve the data overlay ({exc}); "
                  "filing the handoff under .claude/state/handoff",
                  file=sys.stderr)
        # Through `state_root` and not spelled out, so a pinned state directory
        # carries the handoff archive with it. Without that, an isolated test
        # that redirects state still files handoffs into the operator's live
        # tree, which is the same leak one level down.
        return state_root(project) / "handoff"
    return project / ".claude" / "handoff"


def latest_root(handoff: Path) -> Path:
    return handoff / ".latest"


def latest_dir(handoff: Path, slug: str) -> Path:
    """This session's pointer dir — the only one safe to inject."""
    return latest_root(handoff) / slug


STATE_DIR_ENV = "HEADING_OS_STATE_DIR"


def state_root(project: Path) -> Path:
    """Where `.claude/state/` writes actually land. The test-isolation seam.

    ## The defect this closes

    `.claude/state/` sits under the ENGINE root, and the engine root is not the
    data overlay. So `HEADING_OS_DATA`, which every isolated test in this suite
    pins, does not redirect it and never did. Tests drove hooks that wrote into
    the OPERATOR'S LIVE state directory, and the evidence sat on disk until
    2026-09-01: `checkpoint-True.json` and `checkpoint-3.json` from the
    malformed-payload sweep's `BAD_FIELD_VALUES`, `checkpoint-sweep-session.json`
    from its own base payload, and `checkpoint-session.json` from the `[]` and
    `{}` cases collapsing onto a shared bucket.

    ## Why it has to sit ABOVE the payload, and not be another candidate

    `project_root()` already consults `CLAUDE_PROJECT_DIR`, and that is exactly
    why a fifth entry in its candidate list would not have worked. The payload
    wins there by design: `payload["cwd"]` is checked at candidate 3 and the
    environment only at candidate 4. `tests/test_every_hook_survives_a_malformed_
    payload.py` sends `"cwd": <the engine root>` in its base payload, so it beats
    `CLAUDE_PROJECT_DIR` before that variable is ever read, and every test in it
    wrote to the live tree while looking perfectly well isolated.

    So this is a SEPARATE question from the one `project_root()` answers.
    `project_root()` asks "which tree is this session working in", which the
    payload is genuinely the authority on. This asks "where does state go", which
    the payload has no business deciding. The same split fixed
    `overlay_write_guard._structural_overlay_root()` on 2026-08-31, for the same
    reason: it had asked the environment where the operator's data was, and the
    environment is the one thing a test session can change.

    ## Why a relative value is refused rather than joined

    A relative path resolves against the process CWD, which for a hook is
    wherever the harness happened to launch. Two consequences, both bad. It could
    land anywhere on disk; and if it lands inside the engine clone anywhere other
    than `.claude/state/`, those writes are untracked-and-not-ignored, so the
    pre-commit wall and the push wall both start refusing (`.gitignore` covers
    `.claude/state/` whole, and nothing else). `private_cache_dir` in
    `scripts/utils/paths.py` records the same failure.

    ## Why it warns and falls back rather than raising

    `HEADING_OS_DATA` raises when it is set to a missing directory, and that is
    right for a tool about to write the operator's data. It is wrong here. A
    checkpoint hook runs AFTER the session context is discarded, so a refusal
    that propagates costs a handoff nobody can regenerate - the rule stated at
    length in `handoff_dir()` above. A relative value is a developer's typo, it
    is announced on stderr, and `tests/test_a_state_directory_the_data_pin_never_
    redirected.py` pins both the announcement and the fallback. Losing a handoff
    to be strict about a typo is the worse trade.

    Unset is not an error and never will be: it means the structural default,
    which is the production path and must stay untouched.

    ## The pin redirects THIS clone's state and nothing else

    A blanket redirect was the first version and it was wrong, measured within
    the hour: 14 tests failed, and every one of them was a test that had already
    isolated itself properly. `test_state_lands_in_the_consumers_repository`
    builds a fake "someone else's repository", drives the hook against it, and
    asserts the state landed THERE - which is the entire plugin-bundle contract.
    A global pin overrode the destination those tests exist to verify.

    That is the same shape as the mistake this suite already made once with
    `HEADING_OS_DATA`: a global scratch pin does not create a sandbox, it MOVES
    the thing being guarded onto the sandbox.

    So the question is narrowed to the one this seam actually needs to answer.
    The harm is a write into the OPERATOR'S OWN `.claude/state/`. A project root
    that already points somewhere else is already safe, and re-aiming it can
    only break a caller that chose that destination deliberately. The pin
    therefore applies only when the default would land in this clone.

    Each case, stated so the condition can be checked rather than trusted:

      production, no pin              -> default, unchanged
      production, pin set             -> redirected (nothing sets it in production)
      test, payload cwd = this clone  -> redirected; THE DEFECT, now closed
      test, project root elsewhere    -> default; the 14 tests keep working
      plugin bundle in a consumer repo-> default; `engine_root()` is the bundle,
                                         the consumer's tree is not it
    """
    default = project / ".claude" / "state"

    # Already pointing outside this clone: nothing to protect, and overriding a
    # deliberate destination is a behaviour change, not isolation.
    try:
        if project.resolve() != engine_root():
            return default
    except OSError as exc:  # pragma: no cover - an unresolvable root is not fatal
        print(f"checkpoint: cannot resolve {project} ({exc}); "
              f"leaving the state directory at its default", file=sys.stderr)
        return default

    env = os.environ.get(STATE_DIR_ENV)
    if env:
        candidate = Path(env).expanduser()
        if candidate.is_absolute():
            return candidate
        print(
            f"checkpoint: {STATE_DIR_ENV}={env!r} is relative and is being "
            f"ignored; state falls back to {default}. "
            f"A relative value resolves against whatever directory the harness "
            f"launched the hook in, and if that lands inside the engine clone "
            f"the push wall refuses the untracked writes.",
            file=sys.stderr)
    return default


def state_path(project: Path, slug: str) -> Path:
    return state_root(project) / f"checkpoint-{slug}.json"


def session_id(payload: dict | None = None) -> str:
    """The session's own id, from the hook payload or from the environment.

    `/checkpoint` is model-driven and gets no payload, so it reads
    CLAUDE_CODE_SESSION_ID, which Claude Code exports to child processes
    (verified on 2.1.228). That is what puts the skill and the hooks in the
    same directory instead of two.

    **A non-string id is not an id.** This used to read `str(candidate).strip()`
    over whatever the payload held, so `{"session_id": true}` became the id
    `"True"` and `{"session_id": 3}` became `"3"`. Neither is a session, and
    both are WORSE than the fallback: every session sending that value shares
    one state file, so one session's compaction threshold and unattended switch
    silently become another's.

    Not hypothetical. On 2026-09-01 an audit probe drove the hooks with
    malformed payloads and `.claude/state/` was left holding a real
    `checkpoint-True.json` and `checkpoint-3.json` beside the operator's own
    state, each with a live `last_compact_at`. That is the shape this workspace
    has hit repeatedly: a `.get(key, default)` is not a type check, because the
    default fires only on an ABSENT key and a present-but-wrong value passes
    straight through.

    A non-string falls back to `FALLBACK_SESSION_ID`, which the callers already
    handle: `session_id_is_known` returns False for it, so anything printing a
    sentence about whose handoff it found already says it does not know.
    """
    payload = payload or {}
    for candidate in (payload.get("session_id"), os.environ.get("CLAUDE_CODE_SESSION_ID")):
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return FALLBACK_SESSION_ID


# The value `session_id` returns when neither the payload nor the environment
# names one. Every id-less session shares it, so `.latest/session/` is a
# CROSS-SESSION bucket, not one session's. Anything printing a sentence about
# whose handoff it found has to ask `session_id_is_known` first.
FALLBACK_SESSION_ID = "session"


def session_id_is_known(payload: dict | None = None) -> bool:
    """False when `session_id` fell back to the shared sentinel.

    The test must be the SAME test `session_id` applies, or the pair disagrees
    on exactly the inputs that matter. Before 2026-09-01 this accepted anything
    truthy while `session_id` accepted anything at all, and both str()-ed it; if
    only one had been narrowed to `isinstance(str)`, a boolean payload would
    make `session_id` fall back to the shared sentinel while this function
    reported the id KNOWN. A caller would then print a sentence naming one
    session's handoff over a bucket every id-less session shares.
    """
    payload = payload or {}
    return any(
        isinstance(candidate, str) and candidate.strip()
        for candidate in (payload.get("session_id"),
                          os.environ.get("CLAUDE_CODE_SESSION_ID"))
    )


def safe_slug(value: str, max_len: int = 32) -> str:
    cleaned = "".join(
        ch if ch.isalnum() or ch in ("-", "_") else "-" for ch in (value or "")
    )
    return cleaned[:max_len].strip("-") or "session"


def session_slug(payload: dict | None = None) -> str:
    return safe_slug(session_id(payload))


def raise_unattended(state: dict) -> dict:
    """Turn the mode ON in `state`, remembering exactly what it changed.

    `unattended_raised_auto` says whether WE are the reason `session_auto` is on.
    `unattended_prior_auto` says what `session_auto` HELD, including the case of
    holding nothing - and that second key is why `lower_unattended` can claim to
    restore rather than merely to unset. Added 2026-08-19.

    Mutates and returns `state`; the caller writes it.
    """
    already = "unattended_raised_auto" in state
    state["session_unattended"] = True
    state["session_unattended_at"] = utc_now().isoformat()
    if not already:
        # Only the FIRST raise records the prior. A second raise on an
        # already-raised session read the `session_auto = True` that the first
        # raise had just written and recorded it as an operator-held prior, so
        # the following `lower_unattended` restored True: `--unattended off`
        # printed "A pause waits for you again" while auto stayed on for the
        # rest of the session, and the docstring's claim to undo "exactly what
        # raise_unattended did" stopped holding. `scripts/checkpoint-paths.py`
        # calls raise on every `--unattended on` with no already-on guard, and
        # an accepted `--compact-at N` raises it too, so two raises is the
        # ordinary path, not an edge case.
        state["unattended_raised_auto"] = state.get("session_auto") is not True
        # `None` is a real, distinct prior value: an absent `session_auto`
        # defers to CLAUDE_HANDOFF_AUTO, which is not the same as a
        # `session_auto` of False.
        state["unattended_prior_auto"] = state.get("session_auto")
    state["session_auto"] = True
    # A fresh run starts its counters from zero, or last night's numbers would
    # stop tonight's work before it began.
    state.pop("unattended_turn_id", None)
    clear_unattended_window(state)
    return state


# The keys that describe ONE unattended stretch, as opposed to the switch itself.
# Cleared together, always: a stretch that has ended and a stretch that never
# started must look identical, or `--unattended status` reports this morning's
# run with last night's reason. It did, on 2026-08-19, for the whole of one
# session: `unattended_stop_reason` survived a `--unattended on` because only
# some of these were popped.
_WINDOW_KEYS = (
    "unattended_continuations",
    "unattended_done_at",
    "unattended_done_note",
    "unattended_paused_at",
    "unattended_stop_reason",
    # Written by the fingerprint fuse this file carried until 2026-08-19. Popped
    # so a state file written before that date does not keep reporting a stall
    # nothing can now clear.
    "unattended_stall",
    "unattended_fingerprint",
    "unattended_stalled_at",
)


def clear_unattended_window(state: dict) -> dict:
    """Start a new stretch: forget how the previous one ended, keep the switch.

    Called when the mode is raised, and when the operator speaks during the grace
    period. Operator input IS a new window: the ceiling bounds one uninterrupted
    stretch, so a count left over from last night would cut tonight short, and a
    done marker describes a plan he has just replaced with a new instruction.

    Deliberately does NOT touch `session_unattended`. The switch is the
    operator's; only he lowers it.

    Mutates and returns `state`; the caller writes it.
    """
    for key in _WINDOW_KEYS:
        state.pop(key, None)
    return state


def mark_unattended_done(state: dict, note: str) -> dict:
    """Record that the plan is finished, so the Stop hook stops continuing.

    The EXPLICIT end-of-work signal, written by the assistant through
    `scripts/checkpoint-paths.py --done`. It replaced a heuristic on 2026-08-19
    that inferred the same thing from whether any file had changed across three
    continuations. The heuristic could not tell a finished plan from a night of
    reading, research and thinking, and it stopped all three unattended runs that
    had ever been attempted, at three and five continuations each.

    Like everything else in `_WINDOW_KEYS`, this describes one stretch and not
    the switch. It does not turn the mode off, and the operator's next
    instruction clears it.

    Mutates and returns `state`; the caller writes it.
    """
    state["unattended_done_at"] = utc_now().isoformat()
    state["unattended_done_note"] = (note or "").strip() or "no note given"
    return state


def lower_unattended(state: dict) -> dict:
    """Turn the mode OFF in `state`, undoing exactly what `raise_unattended` did.

    ONE implementation, called by both the `--unattended off` CLI and the hook's
    fuse stop. They diverged before this existed: the CLI cleared the switch and
    the fuse did not, so a fuse-stopped run reported a mode that was on and
    inert, and `--unattended status` said `on` about a run that had already
    stopped hours earlier.

    The `session_auto` half is a RESTORE, not an unset. The CLI used to write
    `session_auto = False` whenever it had raised the flag, which pins False over
    a workspace `CLAUDE_HANDOFF_AUTO=1` the operator set deliberately - a
    behaviour change wearing the word "restore". Popping the key returns the
    session to deferring, which is what an absent prior value meant.

    Mutates and returns `state`; the caller writes it.
    """
    state["session_unattended"] = False
    state["session_unattended_at"] = utc_now().isoformat()
    if state.pop("unattended_raised_auto", False):
        prior = state.pop("unattended_prior_auto", None)
        if prior is None:
            state.pop("session_auto", None)
        else:
            state["session_auto"] = prior
    else:
        state.pop("unattended_prior_auto", None)
    return state


def transcript_dir(project: Path | str) -> Path | None:
    """Where Claude Code keeps this workspace's session transcripts.

    The harness mangles the project path into a single directory name by
    replacing every `/` and every `.` with `-`, so
    `/home/x/ai/.heading-os` becomes `-home-x-ai--heading-os` - the doubled dash
    is the dot following a slash, not a typo. Derived rather than hardcoded, so a
    clone at a different path resolves its own transcripts.

    It lives here rather than in a caller because the second copy of a
    path-mangling rule is the one that stops being fixed. That prediction came
    true inside this repository: `scripts/archive-transcripts.py` carried a
    third copy until 2026-08-23, pointing at `scripts/calibrate.py` as a fourth
    authority. Callers are `compact-now.py`, `compaction-probe.py` and
    `archive-transcripts.py`.

    **Returns None off POSIX, rather than guessing.** The two replacements do
    not touch a backslash or a drive colon, so on Windows `C:\\Users\\...`
    mangles to a name no directory can carry, and every caller then reads an
    absent directory as an empty one. `archive-transcripts.py --status` printed
    `live 0 file(s)` on every run and exited 0 - silent transcript loss, which is
    the thing it exists to prevent. The correct Windows slug is not something
    this repository can verify, and an unverifiable guess is worse than a
    refusal a caller can report.
    """
    if os.name != "posix":
        return None                # before any Path(): off POSIX it is what raises
    mangled = str(Path(project).resolve()).replace("/", "-").replace(".", "-")
    return Path.home() / ".claude" / "projects" / mangled


def newest_session_id(project: Path) -> str | None:
    """The id of the most recently written transcript for this workspace.

    Used by CLI entry points that have no hook payload and no exported session
    id. Returns None when the directory is absent or holds no transcript; the
    caller decides whether that is an error, because it is one for
    `compact-now.py` and merely an empty window for the probe.
    """
    directory = transcript_dir(project)
    if directory is None or not directory.is_dir():
        return None
    newest = None
    newest_mtime = -1.0
    for path in directory.glob("*.jsonl"):
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        if mtime > newest_mtime:
            newest, newest_mtime = path, mtime
    return newest.stem if newest else None


def utc_now() -> datetime:
    """A SERIALIZED timestamp. Use for anything stored: JSON state, audit lines.

    Not for a filename or a header the operator reads — see `local_now`.
    """
    return datetime.now(timezone.utc)


def local_now() -> datetime:
    """A DISPLAY / calendar-day timestamp, in the operator's own zone.

    The workspace convention (`.lint-baseline.json` DTZ ruleset, at zero since
    F-3.1) splits the two cases and names this one explicitly: "Display /
    calendar-day (formatting, 'today' for a filename/header, building a calendar
    range): datetime.now(get_default_tz())". A handoff filename is exactly that.

    The mechanism stamped filenames with `utc_now()` until 2026-08-20, and the
    cost was visible on the night it was found: the operator is on Asia/Dubai
    (UTC+4), so a handoff written at 02:56 local was filed as
    `2026-08-19-225625` — under the PREVIOUS calendar day. Every artifact
    written between midnight and 04:00 local landed on yesterday's date, which
    is exactly when this operator works.

    Nothing reads the stamp back: every consumer of the archive orders by
    `st_mtime` (`scripts/next-signal.py`, `newest_transcript`,
    `prune_pointer_dirs`). So the stamp is a human label, the change is
    forward-only, and it cannot reorder anything. The shift is also monotonic
    (+4h here, never negative), so a new name still sorts after every older one.

    The import is deferred rather than module-scope on purpose. Five hooks
    import this module on every turn, `get_default_tz` reads the gitignored
    `.env` through `load_env()`, and that costs 50 ms in a cold process. Only
    the two callers that build a filename should pay it.
    """
    try:
        from scripts.utils.workspace import get_default_tz
    except Exception:
        # A hook running outside a resolvable engine tree still needs a stamp.
        # UTC is the wrong calendar day, not a wrong instant, so degrade rather
        # than fail the save — losing the handoff is the worse outcome.
        return datetime.now(timezone.utc)
    return datetime.now(get_default_tz())


def env_int(name: str, default: int, *, minimum: int = 0, maximum: int = 100) -> int:
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


def env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on", "auto")


def auto_mode(state: dict | None = None) -> bool:
    """Is the hands-off mode on for THIS session?

    Two inputs, and the session's own flag wins in BOTH directions.
    CLAUDE_HANDOFF_AUTO is a launch-time decision for the whole workspace;
    `session_auto` is the running one, taken mid-work in a single window ("this
    is going to be long, stop asking"). Symmetric on purpose: a window may also
    want the question back while the workspace default is silence.

    Read from `session_auto` and never from `auto`. The statusline rewrites
    `auto` after every turn as its echo of the RESOLVED mode, so an operator
    choice stored under that key would survive roughly one turn.
    """
    if state:
        flag = state.get("session_auto")
        if flag is not None:
            return bool(flag)
    return env_bool("CLAUDE_HANDOFF_AUTO", False)


def unattended_mode(state: dict | None = None) -> bool:
    """Is this session allowed to continue on its own at a pause?

    A SEPARATE switch from `auto_mode`, never a third value inside it, because
    the two answer different questions: `auto` decides whether a checkpoint saves
    without asking, this decides whether a pause hands the turn back to the
    operator at all. Folding them together would put `auto_mode` in the path of
    every later change to either, and `auto_mode` is what the statusline and both
    older modes read.

    Read from `session_unattended`. Same reason `auto_mode` reads
    `session_auto`: the statusline rewrites its own echo keys every turn, so an
    operator choice stored in one of those would survive about one turn.
    """
    if state:
        flag = state.get("session_unattended")
        if flag is not None:
            return bool(flag)
    return env_bool("CLAUDE_HANDOFF_UNATTENDED", False)


# Claude Code DISCARDS the output of a hook that outruns its registered timeout.
# A grace period at or above that timeout therefore loses the continuation in
# silence: no block decision, no state write, no stall notice - and the operator
# was told in writing that the session would carry on. The knob used to clamp at
# 600, so `CLAUDE_HANDOFF_UNATTENDED_WAIT=120` was accepted and reported back as
# "wait 120s, continue on silence" while the hook was being killed at 90. The
# shipped Stop registration allows 90 seconds; 75 left room for the progress
# fingerprint's git call and the state write that follow the wait.
#
# Lowered 75 -> 60 on 2026-08-19, for a second reason that stacks on the first.
# The wait now renders a countdown through `herdr agent rename`, and three of
# those calls are ADDITIVE to the wait rather than absorbed by it: the
# `resolve_pane` lookup before the loop (10s ceiling), the `clear_label` in the
# `finally` (2s), and the final iteration's overrun (2s). The alternative,
# dropping the countdown above 60, would remove it precisely when the wait is
# longest and a still terminal is most likely to be read as a hang.
#
# **The "near 79 seconds, fits under 90" this comment claimed until 2026-08-20
# was wrong by 13 seconds.** It counted the three calls above and omitted the two
# `_request_compaction` makes EARLIER on the same Stop (`resolve_pane` and
# `submit_compact`, 10s each). Measured with a HERDR answering just inside each
# of its own timeouts and the wait at this ceiling: 92.0 seconds end to end,
# past the 90 the hook is registered with, so the continuation was discarded in
# exactly the way the ceiling exists to prevent.
#
# The number below is therefore an upper limit on what may be CONFIGURED, and no
# longer a proof that the hook fits. The proof now lives in
# `.claude/hooks/checkpoint-offer.py::_effective_wait`, which measures the
# remaining budget from the hook's own process start and shortens the grace
# period so every upstream cost is charged automatically. Do not re-derive a
# worst case from this comment; measure the hook.
UNATTENDED_WAIT_MAX = 60


# A task record carrying one of these is history, not work in flight.
TERMINAL_TASK_STATES = frozenset({
    "completed", "complete", "done", "finished", "failed", "error",
    "cancelled", "canceled", "stopped", "killed", "timeout", "timed_out",
})


def wait_seconds() -> int:
    """The grace period, clamped against the registered hook timeout."""
    return env_int(
        "CLAUDE_HANDOFF_UNATTENDED_WAIT", 60, minimum=1, maximum=UNATTENDED_WAIT_MAX
    )


# The soft reminder is always this far below the hard threshold, never a second
# setting. The operator fixed the relationship: "софт напоминание (порог) было на
# 30% (всегда на 5% меньше чем жёсткий порог)".
SOFT_OFFSET = 5

# Below 15 the derived soft threshold lands under 10%, where the trigger sits at
# or under the always-loaded context floor and cascades - the confirmed cause of
# the 2026-08-19 incident. Above 90 there is no window left to write the handoff
# that has to precede the compaction.
HARD_THRESHOLD_MIN = 15
HARD_THRESHOLD_MAX = 90


def _session_hard(state: dict | None) -> int | None:
    """This session's own hard threshold, or None to use the environment.

    An unparseable or out-of-range value returns None rather than raising. The
    CLI refuses both at the door, so a bad value arriving here means the file was
    hand-edited or written by an older build - and a status line that crashes on
    it is worse than one that falls back to the workspace default. This mirrors
    `env_int`, which already treats an invalid value as absent.

    Read from `session_hard_threshold` and NEVER from `hard_threshold`: the
    status line rewrites that key on every render as its echo of what `config()`
    resolved, so an operator choice stored there would survive about one turn.
    """
    if not state:
        return None
    raw = state.get("session_hard_threshold")
    if raw is None:
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    if value < HARD_THRESHOLD_MIN or value > HARD_THRESHOLD_MAX:
        return None
    return value


def config(state: dict | None = None) -> dict:
    """Thresholds and mode.

    `auto` is OFF unless the operator turns it on. The capability ships; the
    activation is a switch they flip, never a default they discover. Pass the
    session's state file to let its own switch answer instead of the workspace
    default.

    The same applies to the thresholds since 2026-08-21. A session that carries
    `session_hard_threshold` uses it, with the soft reminder derived SOFT_OFFSET
    below; a session that carries nothing keeps the environment pair untouched.
    The environment branch below is unchanged, including its `soft >= hard`
    fallback.
    """
    hard = _session_hard(state)
    if hard is not None:
        soft = hard - SOFT_OFFSET
    else:
        soft = env_int("CLAUDE_HANDOFF_SOFT_THRESHOLD", 25)
        hard = env_int("CLAUDE_HANDOFF_HARD_THRESHOLD", 30)
        if soft >= hard:
            soft, hard = 25, 30
    return {
        "soft": soft,
        "hard": hard,
        "step": env_int("CLAUDE_HANDOFF_REMIND_STEP", 5, minimum=1),
        "auto": auto_mode(state),
    }


def _ralph_owner(project: Path) -> str | None:
    """The session id written in the ralph-loop plugin's state file.

    None means nothing claims the loop here. An EMPTY string means the file
    exists without a `session_id:` line, and that is not the same answer: the
    plugin's own hook falls through in that case and drives the loop for
    whichever session reaches the Stop event, so a missing id claims every
    session on the tree rather than none of them.

    Presence of the file is taken as a LIVE loop rather than as evidence one ran
    once, because the plugin removes it on every terminal path it has: the
    completion promise detected, the iteration ceiling reached, and a state file
    it cannot parse. Read off `hooks/stop-hook.sh` in the installed plugin on
    2026-08-17 rather than assumed.
    """
    path = project / ".claude" / "ralph-loop.local.md"
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    lines = raw.splitlines()
    if not lines or lines[0].strip() != "---":
        # Not the plugin's shape. Its own hook cannot read an iteration out of
        # this either, and deletes the file when it tries, so claim nothing.
        return None
    for line in lines[1:]:
        stripped = line.strip()
        if stripped == "---":
            break
        if stripped.startswith("session_id:"):
            return stripped.split(":", 1)[1].strip()
    return ""


def continuation_claimant(
    payload: dict | None = None, project: Path | None = None
) -> str:
    """The name of whatever already drives this Stop event, or "" if nothing does.

    A Stop event can carry more than one hook, and a second voice on an event
    something else is already driving costs that driver a turn: the offer's
    question is answered by a model instead of by the operator who is not there
    to read it. So the offer stays quiet whenever any of three signals fires.

    Two of the three are Claude Code's own payload fields, not heuristics.
    `session_crons` carries the tasks that will wake the session later, which is
    what `/loop`, `CronCreate` and `ScheduleWakeup` register. `background_tasks`
    carries in-flight work, and its own description draws exactly the
    distinction that matters here, between a finished session and one paused
    waiting to be woken. The third is the ralph-loop plugin's state file.

    **`/goal` is NOT among them, and cannot be.** It is itself a Stop hook of
    type `prompt` registered into the session, and its state lives in the
    harness's memory: it reaches neither the disk nor this payload, so no hook
    written in Python can see it. What limits the damage is the harness rather
    than anything here. Once the goal's own hook blocks a turn,
    `stop_hook_active` is true for the remainder of that turn and the caller
    bails on it, so the offer can reach a goal run at most once per operator
    turn, and a blocked offer does not end the run.
    """
    payload = payload or {}
    for field in ("session_crons", "background_tasks"):
        value = payload.get(field)
        if not isinstance(value, list):
            continue
        for item in value:
            # A record that says it is finished does not claim anything. Reading
            # mere list non-emptiness meant one completed background task could
            # silence the checkpoint system for the rest of the session - every
            # threshold offer suppressed, unattended mode never engaging, and the
            # only trace a key in a state file nobody reads. An entry with no
            # status, or a status this does not recognise, still claims: unknown
            # resolves toward staying out of the way.
            if isinstance(item, dict):
                status = str(item.get("status") or "").strip().lower()
                if status in TERMINAL_TASK_STATES:
                    continue
            return field
    if project is not None:
        owner = _ralph_owner(project)
        if owner is not None and owner in ("", session_id(payload)):
            return "ralph-loop"
    return ""


def compact_point() -> tuple[str, str] | None:
    """Where native auto-compact is configured to fire, or None if nothing says.

    A hook knows only what the environment tells it. Saying "compaction frees
    context near 55%" when nothing set a window is an assertion the method never
    established, so this returns None and the caller says so instead of guessing.

    The percentage form is preferred over the token form on purpose: a token
    count is measured against `min(setting, the model's own window)`, so it
    silently means something else the moment the operator changes model.
    """
    percent = os.environ.get("CLAUDE_AUTOCOMPACT_PCT_OVERRIDE", "").strip()
    if percent.isdigit():
        return ("percent", percent)
    window = os.environ.get("CLAUDE_CODE_AUTO_COMPACT_WINDOW", "").strip()
    if window.isdigit():
        return ("tokens", window)
    return None


COMPACT_HISTORY_MAX = 20


def record_peak(state: dict, used: float | None, when_iso: str) -> None:
    """Carry the HIGHEST context reading seen since the last compaction.

    The statusline rewrites the whole state dict after every turn, so
    `used_percentage` is the LAST reading and never the largest. Measured on
    2026-08-19: auto-compact fired at 38% remaining, and seven minutes later the
    state file read 11% used with nothing left of the level it fired at - the
    one number that decides whether the configured threshold is in force.

    Monotone by construction: a lower reading is ignored, so only a compaction
    (record_compaction below) resets it.
    """
    if used is None:
        return
    previous = state.get("peak_used_percentage")
    if (
        isinstance(previous, (int, float))
        and not isinstance(previous, bool)
        and float(previous) >= float(used)
    ):
        return
    state["peak_used_percentage"] = float(used)
    state["peak_used_at"] = when_iso


def record_compaction(state: dict, when_iso: str, trigger: str) -> dict | None:
    """Close the current peak into a compaction record and start a fresh one.

    Returns the appended entry, or None when no reading was ever observed.

    `used_pct_at_or_above` is a LOWER BOUND, not the firing point. The statusline
    renders once per turn, and the harness can compact between two renders, so
    the true level is at or above the last reading this hook saw. Naming it as a
    bound is the whole difference between a measurement and an assertion the
    method never established (.claude/rules/scope-claims.md).

    `configured` is what the environment asks for, so the record answers the
    operator's actual question - configured versus observed - in one line.
    """
    peak = state.pop("peak_used_percentage", None)
    peak_at = state.pop("peak_used_at", None)
    if not isinstance(peak, (int, float)) or isinstance(peak, bool):
        return None

    point = compact_point()
    window = state.get("context_window_size")
    entry = {
        "at": when_iso,
        "trigger": trigger,
        "used_pct_at_or_above": float(peak),
        "last_seen_at": peak_at,
        "configured": f"{point[0]}:{point[1]}" if point else None,
        "context_window_size": window if isinstance(window, int) else None,
    }
    history = state.get("compact_history")
    if not isinstance(history, list):
        history = []
    history.append(entry)
    state["compact_history"] = history[-COMPACT_HISTORY_MAX:]
    return entry


def read_json(path: Path) -> dict:
    """This session's state, or `{}`. Always a dict, whatever the file holds.

    The isinstance check is the second half of the same promise the `except`
    below makes, and it was missing until 2026-08-30. `json.loads` succeeds on
    any well-formed JSON, not only on an object, so `null`, `"oops"`, `[]` and
    `3` were returned as-is under a `-> dict` annotation. Measured that day:
    every one of them crashed the first consumer to touch it - `locked_state`
    raised TypeError on `state["session_auto"] = True`, `auto_mode` raised
    AttributeError on `.get`. A statusline runs on every render and the Stop
    hook on every turn, so that is the whole session broken by the exact input
    the `except` comment says must not stop a turn. `_session_hard` already
    documents hand-edited state files as an anticipated reality.
    """
    if not path.exists():
        return {}
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - a corrupt state file must not stop a turn
        print(f"checkpoint: unreadable state at {path.name}: {exc}", file=sys.stderr)
        return {}
    if not isinstance(parsed, dict):
        print(f"checkpoint: state at {path.name} is a "
              f"{type(parsed).__name__}, not an object; ignoring it",
              file=sys.stderr)
        return {}
    return parsed


def write_json_atomic(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp_name, path)
    except BaseException:
        # BaseException, not Exception. `KeyboardInterrupt` and `SystemExit` do
        # not derive from `Exception`, so a Ctrl-C landing between the mkstemp
        # and the replace walked straight past this clause and left the scratch
        # file beside the state file. This is the writer `locked_state` builds
        # on, called from the Stop and PreCompact hooks, so the interrupt and
        # the write happen at the same moment by construction. MEASURED
        # 2026-09-01: a KeyboardInterrupt raised inside mkstemp's caller left
        # `state.json.li4tm3qc.tmp` behind. Guard:
        # `tests/test_twenty_one_atomic_writers_and_three_that_survive_a_ctrl_c.py`.
        #
        # The temp file is cleanup, not the failure. `raise` below re-raises the
        # real error, so suppressing an already-absent temp file hides nothing.
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp_name)
        raise


LOCK_WAIT_SECONDS = 2.0
LOCK_POLL_SECONDS = 0.01


@contextlib.contextmanager
def file_lock(lock_path: Path, *, wait: float = LOCK_WAIT_SECONDS, label: str = "checkpoint"):
    """Hold an exclusive lock on `lock_path` for the duration of the block.

    Yields True when the lock is held and False when it is not, so a caller that
    wants to say something different in the degraded case can. Most callers
    ignore the value: the block runs either way.

    `label` prefixes the two stderr lines. It exists because this primitive is
    no longer checkpoint-only: `scripts/email-intelligence.py` serialises its
    state file with it, and a message reading "checkpoint: ... busy" from an
    email run points its reader at the wrong file. One implementation, correct
    attribution — a second copy is the one that stops being fixed.

    **Bounded, never blocking.** A hook that waits forever is worse than a hook
    that races - the Stop hook has a 90-second budget and the statusline runs on
    every turn - so the wait expires, the block proceeds unlocked, and a line
    goes to stderr saying which happened. Where `fcntl` is unavailable (Windows)
    the lock is skipped entirely and behaviour is what it was before this
    existed.

    Separate from `locked_state` because not everything that needs serialising
    is a JSON dict. The `.latest` pointer PAIR is two text files written back to
    back, and two sessions interleaving there leave `summary.md` naming one
    session's archive while `prompt.md` names another's - a state neither
    session ever held.
    """
    try:
        import fcntl
    except ImportError:  # pragma: no cover - non-POSIX
        fcntl = None

    if fcntl is None:
        yield False
        return

    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = None
    held = False
    deadline = time.monotonic() + wait
    try:
        handle = open(lock_path, "a+")  # noqa: SIM115 - released in the finally
        while True:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                held = True
                break
            except OSError:
                if time.monotonic() >= deadline:
                    print(
                        f"{label}: {lock_path.name} busy for {wait:.0f}s; "
                        "writing unlocked",
                        file=sys.stderr,
                    )
                    break
                time.sleep(LOCK_POLL_SECONDS)
    except OSError as exc:
        print(f"{label}: could not open {lock_path.name}: {exc}", file=sys.stderr)

    try:
        yield held
    finally:
        if handle is not None:
            if held:
                with contextlib.suppress(OSError):
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            with contextlib.suppress(OSError):
                handle.close()


@contextlib.contextmanager
def locked_state(path: Path, *, wait: float = LOCK_WAIT_SECONDS):
    """Read-modify-write one state file under an exclusive lock.

    Yields the dict. Whatever the block leaves in it is written atomically when
    the block exits without raising; on an exception nothing is written and the
    file keeps its previous contents.

    **Why a lock and not just an atomic write.** `write_json_atomic` makes each
    WRITE indivisible, which is a different guarantee from making a read and its
    following write indivisible. Four processes write this file - the statusline
    on every render, the Stop hook, the PostCompact hook, and the CLI - and the
    statusline writes back the WHOLE dict it read at the top of its run.
    Measured 2026-08-20: the exposed span between that read and that write is
    0.814 ms median and 3.686 ms at worst, and in 1 of 60 forced-overlap trials
    a concurrent `checkpoint-paths.py --unattended on` was lost, silently, while
    the CLI printed `unattended=on`. The operator's switch went nowhere and
    nothing said so.

    **It degrades rather than hangs, and says which.** A hook that blocks is
    worse than a hook that races: the Stop hook has a 90-second budget and the
    statusline runs on every turn. So the wait is bounded, and on expiry the
    block proceeds UNLOCKED with a line on stderr rather than raising. The same
    applies where `fcntl` is unavailable (Windows): the lock is skipped and the
    behaviour is exactly what it was before this existed.

    The lock lives in a sidecar `<name>.lock`, never in the state file itself -
    locking the file a writer is about to `os.replace` would lock an inode that
    stops being the file.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with file_lock(path.with_name(path.name + ".lock"), wait=wait):
        state = read_json(path)
        yield state
        write_json_atomic(path, state)


def write_text_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp_name, path)
    except BaseException:
        # See `write_json_atomic` above for why this is BaseException. Same
        # reasoning, same hooks, same moment: an interrupt during a Stop.
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp_name)
        raise


def bound_summary(text: str, archive_ref: str, limit: int = MAX_POINTER_SUMMARY) -> str:
    """Cut the pointer's copy at a stated place, naming where the rest lives."""
    if len(text) <= limit:
        return text
    return (
        text[:limit].rstrip()
        + f"\n\n[Cut at {limit} characters. The full summary is in {archive_ref}.]\n"
    )


def _prune(entries: list[tuple[float, Path]], remove) -> None:
    entries.sort(key=lambda item: item[0], reverse=True)
    cutoff = utc_now().timestamp() - KEEP_DAYS * 86400
    doomed = [p for mtime, p in entries if mtime < cutoff]
    doomed.extend(p for _, p in entries[KEEP_MAX:] if p not in doomed)
    for path in doomed:
        try:
            remove(path)
        except OSError as exc:
            print(f"checkpoint: could not prune {path.name}: {exc}", file=sys.stderr)


def prune_pointer_dirs(handoff: Path, keep_slug: str) -> None:
    """Drop pointer dirs of sessions that are long gone.

    Only the per-session DIRS. The shared `.latest/summary.md` and
    `.latest/prompt.md` are files, not dirs, and `scripts/next-signal.py` reads
    them; the archives the pointers name are the actual record and are never
    touched by anything here.
    """
    base = latest_root(handoff)
    if not base.is_dir():
        return
    entries: list[tuple[float, Path]] = []
    for child in base.iterdir():
        if not child.is_dir() or child.name == keep_slug:
            continue
        try:
            mtime = max(
                (f.stat().st_mtime for f in child.iterdir()),
                default=child.stat().st_mtime,
            )
        except OSError:
            continue
        entries.append((mtime, child))

    def remove(path: Path) -> None:
        for f in path.iterdir():
            if f.is_file():
                f.unlink()
        path.rmdir()

    _prune(entries, remove)


def prune_state_dir(state_dir: Path, keep_name: str) -> None:
    """The half the nexi plugin left growing: one JSON per session, forever.

    Takes the directory rather than the project root, because checkpoint-save.py
    exposes `STATE_DIR` as the seam its sandboxed tests redirect.
    """
    base = state_dir
    if not base.is_dir():
        return
    keep = keep_name
    entries: list[tuple[float, Path]] = []
    for child in base.glob("checkpoint-*.json"):
        if child.name == keep or not child.is_file():
            continue
        try:
            entries.append((child.stat().st_mtime, child))
        except OSError:
            continue

    _prune(entries, lambda path: path.unlink())

    # Then every `.lock` whose state file is gone.
    #
    # `locked_state` creates `<name>.json.lock` beside each state file, and the
    # glob above cannot see it: fnmatch wants a whole-name match, and
    # `checkpoint-x.json.lock` does not end in `.json`. So the JSON half pruned
    # at KEEP_MAX while the lock half grew forever, in the very directory this
    # function exists to bound. Measured here: 25 state files, the cap, beside
    # 22 orphan locks.
    #
    # Keying on "its state file is gone" rather than deleting each lock beside
    # its file does BOTH jobs with one rule: the sessions pruned a line above,
    # and every session pruned before this existed. It also cannot touch the
    # live session's lock, since the live session's state file is right there -
    # and that lock is the one `locked_state` is holding.
    for lock in base.glob("checkpoint-*.json.lock"):
        if not lock.is_file():
            continue
        if not lock.with_name(lock.name[:-len(".lock")]).exists():
            try:
                lock.unlink()
            except OSError:
                continue
