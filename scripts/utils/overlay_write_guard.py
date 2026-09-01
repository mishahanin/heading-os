"""The operator's private overlay is not scratch space.

Two halves of one guard, and neither replaces the other.

* A whole-tree snapshot at arm time and again at the end, which catches a write
  made by a CHILD process that no wrapper in this interpreter could see, and
  cannot say which caller made it.
* Wrappers over the write primitives, which refuse an in-process write at the
  moment it is attempted, so the traceback names the caller.

MOVED here from `tests/conftest.py` on 2026-08-31, with no change to what it
refuses. It lived in a conftest, so it armed under pytest and nowhere else, and
that bound was measured the hard way: a scratch probe run as a plain
`.venv/bin/python` called an entry point blind, `openpyxl` saved over a real
18,857-byte operator workbook, and the guard that would have refused the write
was never imported. See `auto-memory/a-probe-that-called-an-entry-point-blind.md`.

Extraction only. Arming this module outside pytest is a separate change, and it
needs the legitimate writers measured before anything refuses them: `thread.py`,
`crm.py`, `send-email.py`, `action-queue.py`, the sync daemons and every artifact
writer all write the overlay on purpose.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
import contextlib

# ============================================================
# The operator's overlay is not scratch space
# ============================================================
#
# Anything that resolves a path through `get_data_root()` reads HEADING_OS_DATA
# from the environment. A child process launched with `cwd=tmp_path` and no env
# override therefore writes into the operator's REAL overlay.
#
# Measured 2026-08-27, twice in one day:
#
#   * 114 archives named `..._handoff_compact-manual_probe-session.md` had
#     accumulated in `outputs/operations/handoff-archive/`, and the shared
#     `.latest/summary.md` and `.latest/prompt.md` - the pair `/next` reads as
#     "the newest handoff in this workspace" - were pointing at one of them.
#   * A mutation-testing run put a `MEMORY.md` writer back into `thread.py open`,
#     to prove the guard against it. The CLI tests that call `open` did not pin
#     HEADING_OS_DATA, so the mutant truncated the operator's live 20 KB memory
#     index to a 20-byte stub. It was restored, and the lesson is that the first
#     guard was scoped to one directory while the hazard is the whole overlay.
#
# So this watches both, and watches CONTENT as well as membership: a truncation
# in place adds no file and removes none. Names and sizes only, at session start
# and session finish, and nothing at all when there is no overlay on disk.

# The comment above ends "the hazard is the whole overlay", and then the fix
# watched two directories. On 2026-08-29 the third one was found the same way as
# the first two: a mutation harness reverted `StateManager.__init__` in
# scripts/email-intelligence.py to its import-time default, ran the email-intel
# tests in the main tree, and four runs of `main()` rewrote
# `outputs/operations/email-intelligence/state.json` in the live overlay. The
# guard said nothing, because that path was not one of the two. Measured the same
# day: of four writes into a fake overlay, three drew no complaint.
#
# So the snapshot is now the WHOLE overlay, minus a named few that change on
# their own. Walking it costs about 50 ms for roughly 11,000 files, which is
# nothing beside a 100-second suite.
#
# Each exclusion needs a reason, and "it was noisy" is not one. A rebuildable
# index or a credential file that a daemon refreshes is genuinely not the
# operator's data; an output, a CRM record or a thread is.
_UNWATCHED = {
    ".git": "git's own object store, rewritten by any git command",
    ".memory-index": "a rebuildable search index with a live file watcher",
    ".memory-index-code": "the same, for code",
    ".codegraph": "the CodeGraph index, rebuilt by its own watcher",
    ".sessions": "runtime credentials refreshed by the daemons",
}

_WATCH_BEFORE = None

# Children that could reach the operator's live overlay, as (test node id,
# command). RECORDED, never refused: forcing a scratch root into every child
# would change what a run is permitted to do and could hide a real defect, while
# recording costs nothing and turns "the overlay changed and nothing can say who"
# into a short suspect list. Capped, because a 19,000-test suite spawns a lot.
_CHILD_SPAWNS: list[tuple[str, str]] = []
_CHILD_SPAWN_CAP = 200

# Wall-clock moment `_WATCH_BEFORE` was taken. Recorded because the overlay is a
# LIVE tree: a concurrent agent, a daemon or the operator can create a file in it
# while the suite runs, and a reader comparing the snapshot against a later walk
# of the disk would call that file "missed by the snapshot". It was not missed;
# it did not exist yet. Anything wanting to audit the snapshot's coverage must
# ignore files younger than this.
_WATCH_BEFORE_AT = None


_LIVE_OVERLAY_LABEL = "operator overlay"
_ENV_ROOT_LABEL = "data root in use"


def _structural_overlay_root():
    """The operator's real overlay, derived from THIS FILE's location alone.

    NOT `get_data_root()`, and that is the entire point of this function.
    `get_data_root()` honours `HEADING_OS_DATA` (scripts/utils/paths.py), so
    until 2026-08-31 the guard asked the environment where the operator's data
    was — and the environment is the one thing a test session can change.

    Measured that day, with nothing written: launched plainly, the snapshot
    watched 10,919 real files and `_OVERLAY_PREFIXES` named the real overlay.
    Launched as `HEADING_OS_DATA=<scratch> pytest`, which is the remedy every
    isolation fix in this repository recommends and what a careful operator and
    CI both do, the snapshot watched 0 files and the prefix named the scratch
    directory. Both halves of the protection moved off the operator's data at
    once, for the whole session, silently. A guard has to ask about the WRITE,
    not about the environment.

    Structural, like `_FALLBACK_ROOT` in scripts/utils/paths.py: this file is
    `<engine>/scripts/utils/overlay_write_guard.py`, so the engine root is three
    parents up and the sibling data repo is beside it. No environment variable
    reaches it. A clone with no sibling overlay (a fresh public clone, CI) gets
    None and the guard stays off, exactly as before.

    The depth is asserted by a test, because it is the one number the move from
    `tests/conftest.py` changed. Get it wrong by one and this returns None or a
    stranger, the whole guard arms over nothing, and every test of it still
    passes: they drive it against a pretend overlay.

    Only `.heading-os-data` is returned. The four `.heading-os-data-<slug>` exec
    overlays alongside it are equally real private data and are equally
    unwatched; that is a known gap, reported rather than silently widened here,
    because bringing them in changes which writes a run is allowed to make.
    """
    engine = Path(__file__).resolve().parents[2]
    sibling = engine.parent / ".heading-os-data"
    try:
        return sibling.resolve() if sibling.is_dir() else None
    except OSError:
        return None


def _overlay_root():
    """The overlay THIS SESSION's data root points at, or None.

    Still environment-sensitive on purpose: it answers "where is this run
    writing", which is a different question from "where is the operator's data".
    The guard unions both — see `_watched_roots()`.
    """
    try:
        from scripts.utils.paths import data_overlay_present
        from scripts.utils.workspace import get_data_root
    except ImportError:
        return None
    # EVERY exception, not `OSError` and not `ImportError`. MEASURED 2026-08-31,
    # the day the `.pth` landed: `HEADING_OS_DATA` pointing at a directory that
    # does not exist makes `paths.env_data_root()` raise `DataRootError`, which
    # is neither. It escaped this function, escaped `_watch_snapshot()`, escaped
    # `arm()`, and was swallowed by the `except Exception: pass` inside the .pth
    # line, so the guard did not arm and said nothing. A mistyped variable
    # switched the whole guard off in silence, which is the exact failure this
    # guard was rewritten to stop the environment from causing.
    #
    # Returning None is the safe direction and not a shrug: the STRUCTURAL root
    # is resolved separately from `__file__` and no variable reaches it, so the
    # operator's real overlay stays watched. What is lost is only the second,
    # session-scoped root, which by definition cannot be resolved when the
    # variable naming it is broken.
    try:
        if not data_overlay_present():
            return None
        root = get_data_root().resolve()
    except Exception:  # noqa: BLE001 - see above; a broken variable must not disarm
        return None
    return root if root.is_dir() else None


def _watched_roots():
    """{label: root} — every root this run must not write without saying so.

    The union of the two questions above. In the ordinary case they are the same
    directory and there is one label, which is what every earlier run saw.

    When they differ, both are watched. The structural one because it is the
    operator's data and no `HEADING_OS_DATA` may move the guard off it. The
    session one because a run pointed at a scratch root still must not scatter
    private records into it unnoticed: those are exactly the writes that would
    have hit the real overlay had the variable been absent, and reporting them
    is how the scratch remedy stays honest instead of merely quiet.

    One function, so a test can drive the real snapshot over a fake overlay by
    replacing this and nothing else.
    """
    roots = {}
    structural = _structural_overlay_root()
    if structural is not None:
        roots[_LIVE_OVERLAY_LABEL] = structural
    session = _overlay_root()
    if session is not None and session != structural:
        roots[_ENV_ROOT_LABEL] = session
    return roots


def _overlay_dir(parts):
    """One directory inside the live overlay, or None. Kept for callers that
    want a single subtree rather than the whole walk."""
    root = _overlay_root()
    if root is None:
        return None
    directory = root.joinpath(*parts)
    return directory if directory.is_dir() else None


def _snapshot_one(root):
    """{relpath: size} for one root, minus the _UNWATCHED subtrees."""
    entries = {}
    for path in root.rglob("*"):
        try:
            rel = path.relative_to(root)
        except ValueError:
            continue
        if rel.parts and rel.parts[0] in _UNWATCHED:
            continue
        try:
            if not path.is_file():
                continue
            entries[rel.as_posix()] = path.stat().st_size
        except OSError:
            continue
    return entries


def _watch_snapshot():
    """{label: (directory, {relpath: size})} for every watched root.

    One label per root, because the unit being protected is a whole overlay, not
    a list of interesting places in it. Sizes are always taken: a truncation in
    place adds no file and removes none, which is how the memory index was lost
    in 2026-08.
    """
    return {
        label: (root, _snapshot_one(root))
        for label, root in _watched_roots().items()
    }


# The snapshot above is a post-mortem: it says the overlay changed, after the
# run, and it cannot say WHICH test did it. It also catches a child process,
# which nothing in this interpreter can. The guard below is its opposite half:
# it refuses an in-process write at the moment it is attempted, so the traceback
# names the test. Neither replaces the other.
#
# The check is a substring test on the path, done before any resolve, because it
# runs on every `open()` in a 15,000-test suite. A relative path, a symlink or a
# `..` walk therefore slips past it. That is honest and deliberate: this guards
# against an accident, and the accidents all look like an absolute path built
# from `get_data_root()`. The snapshot is what covers the rest.

# A TUPLE, and renamed from the singular `_OVERLAY_PREFIX` it replaced on
# 2026-08-31. Renamed rather than widened in place so that a caller still setting
# the old name arms nothing and fails, instead of handing a bare string to code
# that iterates it and silently guarding twenty-six single characters.
_OVERLAY_PREFIXES = ()          # set in pytest_sessionstart
_WRITE_MODE_CHARS = frozenset("wxa+")


class OverlayWriteRefused(RuntimeError):
    """A test tried to write the operator's live data."""


# ------------------------------------------------------------
# Two modes, and only one of them refuses
# ------------------------------------------------------------
#
# REFUSE is what a test run wants and it is the default: a write to the overlay
# raises, and the traceback names the caller.
#
# RECORD exists because arming this guard in EVERY process cannot start by
# refusing. The overlay is where the operator's work legitimately lands, and the
# list of writers that belong there is long: `thread.py`, `crm.py`,
# `send-email.py`, `action-queue.py`, the sync daemons, every artifact writer.
# A hand-written allowlist of them is the defect this workspace keeps finding,
# because a hand-written list falls behind the tree. So the refusal rule gets
# DERIVED from a measurement, and RECORD is the measurement: it logs who wrote
# what, from which entry point, and allows the write.
#
# Nothing reads the log automatically and nothing refuses on it. It is data for
# a decision that has not been taken yet.
MODE_REFUSE = "refuse"
MODE_RECORD = "record"

# GUARD is the third mode and the one meant to be always on. It refuses a write
# whose innermost workspace frame is a file GIT DOES NOT TRACK, and allows (and
# records) one from a tracked file.
#
# DERIVED, not chosen. `scripts/overlay-writer-census.py` swept 1360 Python files
# under scripts/, .claude/hooks/, .claude/skills/ and tests/ on 2026-08-31 and
# found 184 that both reach a data-root resolver and call a write primitive.
# ALL 184 are git-tracked; none is not. The write that destroyed a real operator
# workbook came from `.tmp/frozen/behaviour.py`, which `.gitignore` covers and
# which therefore cannot ever be tracked. So the boundary separates every
# legitimate writer measured from the one destructive writer observed, and it
# needs no hand-maintained allowlist of tools, which is the thing that falls
# behind the tree.
#
# What this mode does NOT establish, because a refusal has to be honest about
# its own reach:
#
# * A TRACKED file that `exec`s untracked code passes. The caller frame names the
#   tracked file, and the guard cannot see through it. Reported by the census.
# * A write with NO workspace frame in the stack window is ALLOWED and recorded,
#   never refused. That happens when workspace code is further up than the window
#   reaches, and refusing on an unidentified caller would break writes nobody has
#   diagnosed. Failing toward allow-and-log is the deliberate choice here; the
#   log is what makes it reviewable.
# * It says nothing about a child process. Only `_CHILD_SPAWNS` sees those, and
#   only as a suspect list.
MODE_GUARD = "guard"

# OFF is the escape hatch, and it exists because GUARD is armed by default. An
# always-on control needs a way out that is not "edit a file inside
# site-packages": an operator whose work is being refused wrongly must be able to
# get past it in one command, or they will remove the arming file and the guard
# is gone for good rather than for one command.
MODE_OFF = "off"

_MODES = (MODE_REFUSE, MODE_RECORD, MODE_GUARD, MODE_OFF)
_MODE = MODE_REFUSE

# Set by `_install_overlay_write_guard()` to a writer that closes over the
# UNWRAPPED `open`. It must not be the wrapped one: a sink that logs through the
# primitive it is logging about is a recursion waiting for its first write.
_RECORD_SINK = None

# A POSIX-separated relative string, joined by pathlib below rather than by
# `os.path.join`, which `tests/test_no_os_path_join.py` forbids in live scripts.
_RECORD_RELPATH = ".logs/overlay-write-record.jsonl"

# Frames inside this file explain nothing about who called it.
_THIS_FILE = os.path.abspath(__file__)


def record_log_path():
    """Where RECORD mode appends. Inside the ENGINE, never inside the overlay.

    A sink in the overlay would be a write this guard is watching for, and every
    entry would create the next one.
    """
    return Path(__file__).resolve().parents[2] / _RECORD_RELPATH


def _caller_frames(limit=8):
    """Frames outside this file, plus the innermost one inside the workspace.

    Returns `(frames, caller)`. `frames` is the raw stack, innermost first, as
    'file:line:function'. `caller` is the first of them that is workspace code.

    The second field is the one worth having. Measured while building this:
    `Path.write_text` and `Path.mkdir` both put `pathlib.py` innermost, so the
    raw top frame names the standard library on most writes and says nothing
    about who is responsible. `sys.argv[0]` names the entry point but not the
    code inside it. `caller` is the field a derived rule can group by.

    A `.venv` path counts as outside the workspace even though it sits under the
    engine root; a frame in a third-party package is no more actionable than one
    in the stdlib.
    """
    import sys as _sys

    engine = str(Path(__file__).resolve().parents[2])
    venv = str(Path(engine) / ".venv")

    frames, caller = [], None
    frame = _sys._getframe(1)
    while frame is not None and len(frames) < limit:
        name = frame.f_code.co_filename
        absolute = os.path.abspath(name)
        if absolute != _THIS_FILE:
            entry = f"{name}:{frame.f_lineno}:{frame.f_code.co_name}"
            frames.append(entry)
            if caller is None and absolute.startswith(engine) \
                    and not absolute.startswith(venv):
                caller = entry
        frame = frame.f_back
    return frames, caller


def _record_overlay_write(text, verb):
    """Append one line about an allowed write. Never raises, never blocks."""
    if _RECORD_SINK is None:
        return
    import json
    import sys as _sys

    try:
        frames, caller = _caller_frames()
        line = json.dumps({
            "ts": time.time(),
            "pid": os.getpid(),
            "verb": verb,
            "path": text,
            "argv0": _sys.argv[0] if _sys.argv else "",
            "argv": [str(a)[:80] for a in _sys.argv[1:6]],
            "pytest": os.environ.get("PYTEST_CURRENT_TEST", "").split(" (")[0],
            "caller": caller,
            "frames": frames,
        }, ensure_ascii=True)
        _RECORD_SINK(line + "\n")
    except Exception:  # noqa: BLE001 - a measurement must never break the tool
        return


# A relative name plus a `dir_fd` names a file that no prefix test can see, and
# `shutil.rmtree` is built entirely out of that pair.
#
# MEASURED 2026-08-31, on the pretend overlay `/tmp/f2probe/pretend`: `rmtree` of
# a directory holding two files raised `OverlayWriteRefused` and left
# `children left: []`. BOTH files were already deleted. Only the final
# full-path `os.rmdir(path)` was ever refused, and the traceback it produced is
# indistinguishable from a write this guard actually prevented, so the guard
# reported that it had saved data it had just destroyed.
#
# The cause is the capability-set registration below, which is correct and stays:
# it keeps `shutil._use_fd_functions` True, which routes `_rmtree_safe_fd`
# through `os.unlink(entry.name, dir_fd=topfd)` with a RELATIVE name. The prefix
# test is a substring test, and `"a.txt"` contains no prefix.
_DIR_FD_UNRESOLVED = object()

# Set at install time. `None` means "not probed"; False means this platform could
# not resolve a descriptor, in which case the capability sets are deliberately
# NOT re-registered so `shutil` falls back to its full-path walk, which every
# wrapper here can see. Either the fds are readable or the fd algorithm is off:
# both are safe, and neither refuses a delete outside the overlay.
_DIR_FD_READABLE = None


def _resolve_dir_fd_path(target, dir_fd):
    """The absolute path a (relative name, `dir_fd`) pair actually names.

    Returns `target` unchanged when there is no descriptor or the name is
    already absolute, an absolute path when the descriptor resolved, and
    `_DIR_FD_UNRESOLVED` when it did not.
    """
    if dir_fd is None:
        return target
    try:
        text = os.fspath(target)
    except TypeError:
        return target
    if isinstance(text, bytes):
        text = os.fsdecode(text)
    if os.path.isabs(text):
        return text
    try:
        base = os.readlink(f"/proc/self/fd/{int(dir_fd)}")
    except (OSError, ValueError, TypeError, OverflowError):
        return _DIR_FD_UNRESOLVED
    # `pathlib`, not the `os.path` join helper: `tests/test_no_os_path_join.py`
    # refuses that helper anywhere under `scripts/`, and it caught this line.
    return str(Path(base) / text)


def _refuse_overlay_path(target, verb, dir_fd=None):
    if _MODE == MODE_OFF:
        # An `arm(MODE_OFF)` on an already-armed process leaves the wrappers in
        # place, and every branch below this one either records or raises, so OFF
        # behaved as REFUSE: the documented escape hatch inverted into a total
        # block. MEASURED 2026-08-31. `arm()` now disarms on OFF as well, and
        # this line is the belt for a mode set by hand.
        return
    if not _OVERLAY_PREFIXES:
        return
    if dir_fd is not None:
        resolved = _resolve_dir_fd_path(target, dir_fd)
        if resolved is _DIR_FD_UNRESOLVED:
            # Anomalous: the install-time probe passed, so a descriptor should
            # resolve. Refusing is right when the alternative is deleting the
            # operator's data without being able to say whether it was theirs.
            raise OverlayWriteRefused(
                f"cannot resolve dir_fd {dir_fd!r} to a directory, so a "
                f"request to {verb} {target!r} cannot be checked against the "
                "operator's live data. Refusing rather than guessing.")
        target = resolved
    try:
        text = os.fspath(target)
    except TypeError:
        return
    if isinstance(text, bytes):
        text = os.fsdecode(text)
    if not any(prefix in text for prefix in _OVERLAY_PREFIXES):
        return
    if _MODE == MODE_RECORD:
        _record_overlay_write(text, verb)
        return
    if _MODE == MODE_GUARD:
        _, caller = _caller_frames()
        if caller is None or _caller_is_tracked(caller):
            # Allowed, and still logged. The log is the only thing that makes an
            # always-on allow decision reviewable afterwards.
            _record_overlay_write(text, verb)
            return
        raise OverlayWriteRefused(_refusal_message(text, verb, caller=caller))
    raise OverlayWriteRefused(_refusal_message(text, verb))


# The tracked set, loaded ONCE and only when first needed. `None` means "not
# asked yet"; an empty frozenset would be indistinguishable from "git said
# nothing", which must not read as "nothing is tracked, refuse everything".
_TRACKED = None


def _tracked_in(repo):
    """`git ls-files` for one repo, absolute paths, or None when it cannot be asked."""
    import subprocess as _sp

    try:
        result = _sp.run(
            ["git", "ls-files", "-z"],
            capture_output=True, cwd=str(repo), timeout=30, check=True,
        )
    except Exception:  # noqa: BLE001 - no git, not a repo, timeout: all mean "unknown"
        return None
    return frozenset(
        str(repo / part.decode("utf-8", "surrogateescape"))
        for part in result.stdout.split(b"\0") if part
    )


def tracked_workspace_files(refresh=False):
    """Every path git tracks in BOTH repos, absolute. Cached per process.

    BOTH, and the second one is not a nicety. The engine's `git ls-files` covers
    2126 files; the private DATA overlay is a SEPARATE repository tracking 525
    Python files of its own, and some of them legitimately write the overlay:
    `admin/provision/provision_exec.py`, `admin/provision/identity_check.py`, and
    the per-letter renderers under `outputs/documents/`. Measured 2026-08-31,
    before this function asked the second repo: every one of them would have been
    refused as "untracked", which is the operator's own committed tooling being
    blocked by a guard that simply asked the wrong repository. A guard that
    refuses legitimate work is a guard that gets switched off, after which
    nothing guards anything.

    Loaded LAZILY, on the first attempted write into the overlay, not at
    `arm()`. Measured the same day: `git ls-files` over 2126 files costs under
    10 ms, but most processes never write the overlay at all, so most should
    never pay even that.

    Returns None when NEITHER repo could be read, and a None tracked set makes
    GUARD mode ALLOW and record rather than refuse. That direction is deliberate:
    a guard that cannot tell tracked from untracked must not refuse the
    operator's own tools because git was unavailable. A partial answer (one repo
    readable, the other not) is returned as the partial union rather than None,
    because refusing to answer would disarm the half that did work.
    """
    global _TRACKED
    if _TRACKED is not None and not refresh:
        return _TRACKED

    repos = [Path(__file__).resolve().parents[2]]
    overlay = _structural_overlay_root()
    if overlay is not None and (overlay / ".git").exists():
        repos.append(overlay)

    union, any_answer = set(), False
    for repo in repos:
        found = _tracked_in(repo)
        if found is None:
            continue
        any_answer = True
        union |= found
    if not any_answer:
        return None
    _TRACKED = frozenset(union)
    return _TRACKED


def _caller_is_tracked(caller):
    """Is the file in a 'path:line:function' frame string one git tracks?

    Splits from the RIGHT twice, because a filename can contain a colon while
    the line number and function name cannot.
    """
    tracked = tracked_workspace_files()
    if tracked is None:
        return True          # unknown, so do not refuse; see the docstring above
    path = caller.rsplit(":", 2)[0]
    return os.path.abspath(path) in tracked


def _refusal_message(text, verb, caller=None):
    """What the refusal says, and it does not say who.

    Until the guard could arm outside pytest this message opened "a test tried
    to write", which was true of every caller that could reach it. It is not any
    more: a `.pth` arms plain scripts too, and the guard knows only that THIS
    process attempted the write. Naming a test would be the shape
    `.claude/rules/scope-claims.md` exists to stop, and it has already cost one
    investigation in this repository: a complaint reading "a test wrote" sent an
    agent hunting a test that had written nothing.

    So it reports the entry point and the innermost workspace frame, both of
    which the interpreter actually knows, and offers the remedy that fits which
    of the two kinds of caller this is.
    """
    import sys as _sys

    entry = _sys.argv[0] if _sys.argv else "<unknown entry point>"
    under_pytest = bool(os.environ.get("PYTEST_CURRENT_TEST"))
    if caller is None:
        _, caller = _caller_frames()

    # WHO, then WHY, then the REMEDY, and the three are decided separately.
    #
    # They used to be one if-chain with the pytest branch first, and that chain
    # was wrong: under pytest a GUARD refusal reported the generic
    # HEADING_OS_DATA remedy and never said the actual reason, which is that the
    # calling file is not tracked. Found by this rule's own test, 2026-08-31.
    # Mode decides the REASON; being under pytest decides only the prefix and
    # which remedy is useful.
    who = ""
    if under_pytest:
        who = os.environ.get("PYTEST_CURRENT_TEST", "").split(" (")[0] + ": "

    what = f"{entry} tried to {verb} the operator's live data at {text}."
    if caller:
        what += f" Innermost workspace frame: {caller}."

    if _MODE == MODE_GUARD:
        why = (
            " That file is not tracked by git, so this guard treats it as a probe,"
            " a harness or a scratch experiment rather than operator tooling."
            " Every one of the 300 files across both repositories that can write"
            " the overlay is tracked."
        )
        remedy = (
            " If this code should be able to write the operator's data, commit it;"
            " if it is an experiment, point HEADING_OS_DATA at a scratch directory."
        )
    elif under_pytest:
        why = ""
        remedy = (
            " Point HEADING_OS_DATA at a tmp_path before anything that writes,"
            " and pass it to any child process too."
        )
    else:
        why = " This is not a test run."
        remedy = (
            f" If the write is legitimate operator work, set {ENV_MODE}=off for"
            f" this command; if it is a probe, a harness or an experiment, point"
            f" HEADING_OS_DATA at a scratch directory."
        )
    return who + what + why + remedy


def _install_overlay_write_guard():
    """Wrap the write primitives. Returns a callable that puts them back."""
    global _RECORD_SINK
    import builtins
    import io

    real_open = builtins.open
    real_replace, real_rename = os.replace, os.rename
    real_remove, real_unlink = os.remove, os.unlink
    # MEASURED 2026-08-31: the guard wrapped the file primitives and NOT the
    # directory ones, so a test reaching `write_text` failed loudly while one
    # reaching `mkdir` or `touch` planted a stray directory in the operator's
    # real private data in total silence. `git status` does not show an empty
    # directory either, so nothing downstream would have shown it. An audit of
    # the 31 test-reachable modules that resolve the data root at import time
    # found 17 of them bite through exactly this gap.
    real_mkdir, real_makedirs, real_rmdir = os.mkdir, os.makedirs, os.rmdir
    # `os.open`, separately from `builtins.open`. MEASURED the same day, by
    # driving the guard by hand: with the three directory calls wrapped and this
    # one not, `Path.touch()` was still ALLOWED and left a real file in the
    # operator's overlay. `Path.touch` does not go through `builtins.open` at
    # all - it calls `os.open` with O_CREAT directly. Wrapping the pretty name
    # and missing the primitive under it is how a guard reads complete and is
    # not. Only creating flags are refused, so an ordinary read still works.
    real_os_open = os.open
    # `sqlite3.connect` opens its file in C and never reaches `os.open`, so it
    # walked straight past every wrapper above. MEASURED 2026-08-31 by driving
    # the guard by hand: it created a real database in the operator's overlay and
    # reported ALLOWED. Two of the 35 modules that resolved the data root at
    # import time reached it when this was measured (`scripts/sentinel.py`,
    # read-write, and `.claude/hooks/memory-inject.py`, read-only; the second was
    # retired on 2026-09-01, which narrows the caller set and not the defect).
    import sqlite3 as _sqlite3

    real_sqlite_connect = _sqlite3.connect

    import subprocess as _subprocess

    real_sp_popen = _subprocess.Popen

    # The RECORD sink, closing over the UNWRAPPED `open` and the UNWRAPPED
    # `os.makedirs`. Both matter: the sink writes into `.logs/`, which is not in
    # the overlay so the wrapped versions would allow it, but a sink that runs
    # through the primitives it reports on is one refactor away from recursing.
    # Line-buffered append, opened per write: a long-lived handle would have to
    # be closed by something, and there is no shutdown hook in a plain script.
    def _record_sink(line):
        target = record_log_path()
        if not target.parent.is_dir():
            real_makedirs(target.parent, exist_ok=True)
        with real_open(target, "a", encoding="utf-8") as fh:
            fh.write(line)

    _RECORD_SINK = _record_sink

    def guarded_open(file, mode="r", *args, **kwargs):
        if _WRITE_MODE_CHARS & set(mode):
            _refuse_overlay_path(file, "write")
        return real_open(file, mode, *args, **kwargs)

    def guarded_replace(src, dst, *args, **kwargs):
        _refuse_overlay_path(dst, "replace")
        return real_replace(src, dst, *args, **kwargs)

    def guarded_rename(src, dst, *args, **kwargs):
        _refuse_overlay_path(dst, "rename onto")
        return real_rename(src, dst, *args, **kwargs)

    # `dir_fd` is keyword-only on all five primitives that take it, so reading it
    # out of `kwargs` sees every call. Passing it on is what makes a relative
    # name checkable; without it `shutil.rmtree` empties a directory and only the
    # closing `rmdir` is ever refused.
    def guarded_remove(path, *args, **kwargs):
        _refuse_overlay_path(path, "delete", dir_fd=kwargs.get("dir_fd"))
        return real_remove(path, *args, **kwargs)

    def guarded_unlink(path, *args, **kwargs):
        _refuse_overlay_path(path, "delete", dir_fd=kwargs.get("dir_fd"))
        return real_unlink(path, *args, **kwargs)

    def guarded_mkdir(path, *args, **kwargs):
        # Only refuse a call that would actually CREATE something. `Path.mkdir(
        # exist_ok=True)` still reaches `os.mkdir` and lets the resulting
        # FileExistsError through its own handler, so refusing unconditionally
        # rejected five tests that were creating nothing at all. Over-friction
        # is how a guard gets switched off, after which nothing guards the real
        # thing - so the test is "would this bring a new path into existence?",
        # not "does this call look like a write?".
        # Resolve the descriptor BEFORE the existence test. `os.path.exists`
        # takes no `dir_fd`, so a relative name would be tested against the
        # process cwd, which is a different directory entirely.
        probe = _resolve_dir_fd_path(path, kwargs.get("dir_fd"))
        if probe is _DIR_FD_UNRESOLVED or not os.path.exists(probe):
            _refuse_overlay_path(path, "create a directory in",
                                 dir_fd=kwargs.get("dir_fd"))
        return real_mkdir(path, *args, **kwargs)

    def guarded_makedirs(name, *args, **kwargs):
        if not os.path.exists(name):
            _refuse_overlay_path(name, "create a directory tree in")
        return real_makedirs(name, *args, **kwargs)

    def guarded_rmdir(path, *args, **kwargs):
        _refuse_overlay_path(path, "remove a directory from",
                             dir_fd=kwargs.get("dir_fd"))
        return real_rmdir(path, *args, **kwargs)

    _CREATING_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_TRUNC

    def guarded_os_open(path, flags, *args, **kwargs):
        if flags & _CREATING_FLAGS:
            _refuse_overlay_path(path, "open for writing",
                                 dir_fd=kwargs.get("dir_fd"))
        return real_os_open(path, flags, *args, **kwargs)

    def guarded_sqlite_connect(database, *args, **kwargs):
        # A READ-ONLY connection is allowed, and that is not a softening. It
        # creates nothing and writes nothing, and this workspace opens databases
        # exactly that way on purpose: `scripts/utils/sqlite_uri.read_only_uri()`
        # is the one sanctioned spelling (`?mode=ro`, `uri=True`), used by the
        # cookie readers and the CodeGraph symbol source, and it was how the
        # retired `.claude/hooks/memory-inject.py` read the operator's memory
        # index. Refusing it would be the over-friction that gets a guard
        # switched off. Everything else can create or write, so it is refused.
        target = database
        read_only = False
        if kwargs.get("uri") and isinstance(database, str):
            from urllib.parse import parse_qs, urlparse
            parsed = urlparse(database)
            target = parsed.path or database
            read_only = parse_qs(parsed.query).get("mode", [""])[0] == "ro"
        if not read_only and target not in (":memory:", ""):
            _refuse_overlay_path(target, "open a database for writing in")
        return real_sqlite_connect(database, *args, **kwargs)

    # `io.open` and `builtins.open` are the same object, and pathlib reaches the
    # one on `io`. Both names are rebound or `Path.write_text` walks straight
    # past the guard.
    builtins.open = guarded_open
    io.open = guarded_open
    os.replace, os.rename = guarded_replace, guarded_rename
    os.remove, os.unlink = guarded_remove, guarded_unlink
    # `Path.mkdir` and `Path.touch` reach `os.mkdir` and `os.open`; the latter is
    # already covered through `builtins.open`/`io.open` above, so wrapping the
    # three directory calls closes the pair.
    os.mkdir, os.makedirs, os.rmdir = guarded_mkdir, guarded_makedirs, guarded_rmdir
    def _child_reaches_live_overlay(kwargs) -> bool:
        """True when the child could resolve the operator's real overlay.

        The env it will actually get is `os.environ` unless `env=` overrides it.
        A child with `HEADING_OS_DATA` pointing somewhere else is safe; one with
        it absent, or pointing inside the live tree, is a suspect.
        """
        env = kwargs.get("env")
        pinned = (os.environ if env is None else env).get("HEADING_OS_DATA")
        if not pinned:
            return True
        return any(prefix.rstrip(os.sep) in pinned for prefix in _OVERLAY_PREFIXES)

    def _record_spawn(cmd, kwargs):
        if len(_CHILD_SPAWNS) >= _CHILD_SPAWN_CAP:
            return
        try:
            if not _child_reaches_live_overlay(kwargs):
                return
            head = " ".join(str(a) for a in cmd)[:120] if isinstance(cmd, (list, tuple)) \
                else str(cmd)[:120]
            nodeid = os.environ.get("PYTEST_CURRENT_TEST", "<unknown test>").split(" (")[0]
            _CHILD_SPAWNS.append((nodeid, head))
        except Exception:  # noqa: BLE001 - a diagnostic must never break a run
            return

    # ONLY `Popen` is wrapped. `subprocess.run`, `call`, `check_call` and
    # `check_output` all construct a `Popen`, so wrapping `run` as well recorded
    # every `run` TWICE: measured, four spawns produced five records. Wrap the
    # primitive, not the convenience wrapper over it. This is the same shape as
    # `Path.touch` reaching `os.open` rather than `builtins.open`.
    class _GuardedPopen(real_sp_popen):
        def __init__(self, *args, **kwargs):
            if args:
                _record_spawn(args[0], kwargs)
            super().__init__(*args, **kwargs)

    os.open = guarded_os_open
    _sqlite3.connect = guarded_sqlite_connect
    _subprocess.Popen = _GuardedPopen

    # ------------------------------------------------------------
    # Keep the stdlib's capability detection true
    # ------------------------------------------------------------
    #
    # `os.supports_dir_fd`, `os.supports_fd` and `os.supports_follow_symlinks`
    # are sets of FUNCTION OBJECTS, and the standard library tests membership by
    # identity to decide which algorithm to use. `shutil` does it at import:
    #
    #     _use_fd_functions = ({os.open, os.stat, os.unlink, os.rmdir}
    #                          <= os.supports_dir_fd and ...)
    #
    # A wrapper is a different object, so replacing `os.open` silently removes it
    # from every one of those sets and any module importing AFTER the wrap sees a
    # platform that has lost a capability it actually has.
    #
    # MEASURED 2026-08-31, the day this armed by default:
    # `shutil._use_fd_functions` was True unguarded and False guarded, so
    # `shutil.rmtree` took its legacy walk instead of the file-descriptor one.
    # `tests/test_a_radar_that_watched_three_of_fourteen_layers.py::
    # test_an_unreadable_directory_is_removed_not_raised_over` failed with
    # `OSError: Directory not empty`, and passed with the guard off. A guard that
    # changes which algorithm the standard library runs is doing far more than it
    # was asked to.
    #
    # The wrappers forward `*args, **kwargs` verbatim, so each one genuinely
    # supports whatever its original did. Registering them says something true.
    _CAPABILITY_SETS = (
        os.supports_dir_fd, os.supports_fd,
        os.supports_follow_symlinks, os.supports_effective_ids,
    )
    _WRAPPED_PAIRS = (
        (real_open, guarded_open), (real_replace, guarded_replace),
        (real_rename, guarded_rename), (real_remove, guarded_remove),
        (real_unlink, guarded_unlink), (real_mkdir, guarded_mkdir),
        (real_makedirs, guarded_makedirs), (real_rmdir, guarded_rmdir),
        (real_os_open, guarded_os_open),
    )
    # Registering the wrappers keeps `shutil._use_fd_functions` True, which is
    # the whole reason `rmtree` reaches the guard through relative names plus a
    # `dir_fd`. That is only safe while a descriptor can be resolved back to a
    # directory, so probe it ONCE, here, rather than trusting the platform.
    #
    # If the probe fails, do not register: `shutil` then falls back to its
    # full-path walk, which every wrapper above sees directly. Either the
    # descriptors are readable or the descriptor algorithm is off. Both are safe,
    # and neither refuses a delete outside the overlay, which is what an
    # unconditional refusal would have done to every `rmtree` in the suite.
    global _DIR_FD_READABLE
    _probe_fd = None
    try:
        _probe_fd = real_os_open(os.getcwd(), os.O_RDONLY)
        _DIR_FD_READABLE = os.path.isdir(os.readlink(f"/proc/self/fd/{_probe_fd}"))
    except OSError:
        _DIR_FD_READABLE = False
    finally:
        if _probe_fd is not None:
            with contextlib.suppress(OSError):
                os.close(_probe_fd)

    _registered = []
    if _DIR_FD_READABLE:
        for capability in _CAPABILITY_SETS:
            for original, wrapper in _WRAPPED_PAIRS:
                if original in capability:
                    capability.add(wrapper)
                    _registered.append((capability, wrapper))

    def restore():
        builtins.open = real_open
        io.open = real_open
        os.replace, os.rename = real_replace, real_rename
        os.remove, os.unlink = real_remove, real_unlink
        os.mkdir, os.makedirs, os.rmdir = real_mkdir, real_makedirs, real_rmdir
        os.open = real_os_open
        _sqlite3.connect = real_sqlite_connect
        _subprocess.Popen = real_sp_popen
        # Leaving the wrappers registered would leave dead objects advertising a
        # capability, and a later module could pick one out of the set.
        for capability, wrapper in _registered:
            capability.discard(wrapper)
        _registered.clear()

    return restore


_RESTORE_WRITE_GUARD = None


ENV_MODE = "HEADING_OS_OVERLAY_GUARD"


def resolve_mode(explicit=None, default=MODE_REFUSE):
    """Which mode `arm()` will use, and why. Pure, so a test can ask it.

    An explicit argument wins, then `HEADING_OS_OVERLAY_GUARD`, then `default`.

    An UNRECOGNISED value falls back to `default` rather than being honoured or
    silently ignored. A typo in the variable must never be the thing that softens
    a guard: `HEADING_OS_OVERLAY_GUARD=recrod` reading as RECORD would allow every
    write and log none, and reading as OFF would allow every write and say
    nothing at all. Both are worse than either real mode.

    `default` is REFUSE for a direct caller, and the `.pth` that arms every
    process passes GUARD. Two different defaults on purpose: an explicit
    `arm()` in code is almost always a test harness, and the process-wide arming
    is the always-on control.
    """
    if explicit is not None:
        return explicit if explicit in _MODES else default
    asked = os.environ.get(ENV_MODE)
    return asked if asked in _MODES else default


def arm_process_wide():
    """The entry point the `.pth` calls at interpreter startup.

    GUARD by default, so the control is on without anyone remembering to turn it
    on, and `HEADING_OS_OVERLAY_GUARD=off` is the one-command way past it. No
    snapshot, because walking 11,000 files costs half a second and an arbitrary
    process has no end-of-run moment to diff it against.

    STRUCTURAL ROOT ONLY, and that is not a simplification. MEASURED 2026-08-31:
    62 tests went red the first time this was armed by default. `_overlay_root()`
    does `from scripts.utils.paths import ...`, which binds the name `scripts` in
    `sys.modules` to the ENGINE's package during `site.py`, before any user code
    runs. After that, `python -m scripts.anything` from a directory with its own
    `scripts/` package resolves against the engine instead, and the skill-creator
    tests that do exactly that failed with `No module named
    scripts.improve_description`.

    An always-on startup hook must not decide what `scripts` means for the rest
    of the interpreter. `_structural_overlay_root()` needs no import at all: it
    walks up from `__file__`. The session-scoped second root is what
    `_overlay_root()` is for, and only a bounded run has any use for it, so
    `tests/conftest.py` still gets it by calling `arm()` directly.
    """
    arm(resolve_mode(default=MODE_GUARD), structural_only=True)


# Written onto the installed `builtins.open` wrapper, so the marker travels with
# the PROCESS state rather than with any one module copy. An attribute on the
# live primitive is the only place every copy of this module can agree to look.
_OWNER_ATTR = "_heading_os_overlay_guard_owner"

# The namespace whose wrappers this copy displaced when it took over, so
# `disarm()` can hand back instead of leaving the process unguarded.
_DISPLACED_OWNER = None


def _primitive_slots():
    """Every (module, attribute) pair the wrappers rebind.

    Derived from one place so a snapshot and `restore()` cannot drift apart. A
    name added to the wrappers and forgotten here would make a hand-back
    incomplete, which is why the test asserting object identity after
    `disarm()` is the one that has to stay.
    """
    import builtins
    import io
    import sqlite3
    import subprocess
    return (
        (builtins, "open"), (io, "open"),
        (os, "replace"), (os, "rename"), (os, "remove"), (os, "unlink"),
        (os, "mkdir"), (os, "makedirs"), (os, "rmdir"), (os, "open"),
        (sqlite3, "connect"), (subprocess, "Popen"),
    )


def _snapshot_primitives():
    return tuple((mod, attr, getattr(mod, attr)) for mod, attr in _primitive_slots())


def _apply_primitives(snapshot):
    for mod, attr, value in snapshot:
        setattr(mod, attr, value)


def _mark_owner(namespace):
    """Record which module copy's wrappers are the live ones."""
    import builtins
    try:
        builtins.open.__dict__[_OWNER_ATTR] = namespace
    except AttributeError:  # a builtin with no __dict__: nothing installed
        return


def _installed_owner():
    """The globals of the copy whose wrappers are live, or None if unwrapped."""
    import builtins
    return getattr(builtins.open, _OWNER_ATTR, None)


def arm(mode=None, snapshot=None, structural_only=False):
    """Install the wrappers, and optionally take the before-snapshot.

    Snapshot FIRST when it is taken at all, so a write made while arming is
    still visible as a change; then the prefixes, then the wrappers. A clone
    with no overlay on disk gets an empty root set and arms nothing, which is
    the public-clone and CI case.

    `mode` defaults to whatever `resolve_mode()` decides, which is REFUSE unless
    the environment asks otherwise. Callers that must not be softened by an
    environment variable pass a mode explicitly; `tests/conftest.py` does.

    `snapshot` defaults to True for REFUSE and RECORD and False for GUARD, and
    the reason is a measurement. Walking the overlay costs about half a second
    for roughly 11,000 files: 0.53 s against 0.01 s for a bare interpreter,
    measured 2026-08-31. That is nothing beside a four-minute test suite, and it
    is unacceptable on every `python` invocation in the workspace, which is what
    GUARD mode is for. The snapshot also has no consumer there: it exists to be
    diffed at the END of a bounded run, and an arbitrary script has no such
    moment. The wrappers, which are the half that refuses, cost nothing to
    install.
    """
    global _WATCH_BEFORE, _WATCH_BEFORE_AT, _OVERLAY_PREFIXES, _RESTORE_WRITE_GUARD
    global _MODE, _DISPLACED_OWNER
    _MODE = resolve_mode(mode)
    if _MODE == MODE_OFF:
        # Install NOTHING. Not "install and allow": a wrapped primitive that
        # always returns is still a wrapper on every `open()` in the process, and
        # the escape hatch has to cost nothing or it is not an escape.
        #
        # `disarm()` rather than a bare return. MEASURED 2026-08-31: returning
        # here left `_OVERLAY_PREFIXES` and the wrappers from an EARLIER `arm()`
        # in place, and `_refuse_overlay_path` raised unconditionally once past
        # the RECORD and GUARD branches. So OFF behaved as REFUSE, and the one
        # documented escape an operator has when the guard refuses their work
        # wrongly became a total block instead. Reachable whenever the `.pth`
        # arms at startup and anything later exports the variable and re-arms.
        disarm()
        _MODE = MODE_OFF        # `disarm()` resets the mode; OFF is the answer
        # BOUND, stated rather than papered over: this turns off the layer THIS
        # copy installed. It does not reach a layer owned by a different copy of
        # this module, and deliberately so. Reaching across was tried and it
        # broke `test_off_mode_installs_nothing_at_all`, which arms a throwaway
        # copy OFF and must not thereby disarm the live session.
        #
        # The operator's escape hatch does not need it. MEASURED 2026-08-31:
        # `HEADING_OS_OVERLAY_GUARD=off .venv/bin/python` gives 0 wrapper layers
        # and loads no copy of this module at all, because the `.pth` resolves
        # the same variable before it arms. The escape works at its source; only
        # a mid-process change of the variable reaches this line, and that is not
        # the escape-hatch path.
        return
    if snapshot is None:
        snapshot = _MODE != MODE_GUARD
    if snapshot:
        _WATCH_BEFORE_AT = time.time()
        _WATCH_BEFORE = _watch_snapshot()
    if structural_only:
        # No `_overlay_root()`, so no `scripts` import. See `arm_process_wide()`
        # for the 62 tests that measured why.
        structural = _structural_overlay_root()
        roots = {} if structural is None else {_LIVE_OVERLAY_LABEL: structural}
    else:
        roots = _watched_roots()
    if not roots:
        return
    _OVERLAY_PREFIXES = tuple(f"{root}{os.sep}" for root in roots.values())
    # Install once per PROCESS, not once per module copy.
    #
    # MEASURED 2026-08-31: `builtins.open layers: 2` and a `Popen` MRO carrying
    # `_GuardedPopen` twice, in a plain `.venv/bin/python`. CPython's `site.py`
    # processes a venv's site-packages TWICE (`site.venv()` then `site.main()`),
    # and `known_paths` de-duplicates only sys.path ENTRIES, not `.pth`
    # execution. So the `.pth` body ran twice, built two module objects, and
    # `sys.modules` kept only the second. The install-once check below is a
    # module global, so copy #2 never saw copy #1's install, and copy #1 is
    # unreachable through `sys.modules` for the life of the interpreter: its
    # `restore()` can never be called by anything. Under pytest a third layer
    # arrived from `tests/conftest.py`.
    #
    # HANDOVER, not delegation, and not a bare skip. Each of the three is a
    # different bug and only one is right:
    #
    # * A bare skip leaves the process on the `.pth`'s GUARD mode while the
    #   caller believes it armed REFUSE. That is a silent SOFTENING.
    # * Delegating (pushing this copy's mode and prefixes into the owner's
    #   globals, leaving the owner's wrappers live) was tried and FAILED LOUDLY:
    #   4 tests went red because two module copies have two distinct
    #   `OverlayWriteRefused` CLASSES, so the wrappers raised the owner's class
    #   and `pytest.raises(cf.OverlayWriteRefused)` matched nothing. Every other
    #   name shared across a copy boundary carries the same hazard.
    # * Handover keeps ONE layer AND puts it in the copy that arms last, which is
    #   the copy reachable through `sys.modules` and therefore the one whose
    #   `restore()` and whose exception class the rest of the tree can see.
    #
    # And the handover HANDS BACK on `disarm()`. Without that, a test that arms a
    # throwaway copy takes the live session guard away and its own `disarm()`
    # leaves the process with NO guard at all for every later test. MEASURED:
    # the 4 tests that went red were all green in isolation and red in a combined
    # run, which is the signature of exactly this. `disarm()` must return the
    # process to the state before `arm()`, not to bare primitives.
    owner = _installed_owner()
    if owner is not None and owner is not globals():
        # Snapshot EVERYTHING FIRST, while the owner's wrappers are still bound
        # and its globals still hold its real settings. Taking the snapshot after
        # `restore()` captures the bare primitives instead, and then the
        # hand-back returns a process with no guard at all: MEASURED, 9 tests in
        # `test_a_guard_that_armed_under_pytest_and_nowhere_else.py` each left
        # the live session unguarded for every test that followed.
        #
        # Identity is why this is a snapshot and not a re-install. Handing back
        # freshly built wrappers reads to
        # `test_arming_twice_installs_one_layer_of_wrappers` as a leftover layer,
        # because it compares `builtins.open` against the object it captured.
        _DISPLACED_OWNER = (owner, owner.get("_OVERLAY_PREFIXES", ()),
                            owner.get("_MODE", MODE_REFUSE),
                            _snapshot_primitives(),
                            owner.get("_RESTORE_WRITE_GUARD"),
                            _installed_owner())
        restore = owner.get("_RESTORE_WRITE_GUARD")
        if restore is not None:
            restore()
            owner["_RESTORE_WRITE_GUARD"] = None
        owner["_OVERLAY_PREFIXES"] = ()
        # Spawns recorded before the handover are still suspects, so carry them.
        _CHILD_SPAWNS.extend(owner.get("_CHILD_SPAWNS") or ())
    # Install ONCE per process. A second `arm()` refreshes the mode, the prefixes
    # and the snapshot, and deliberately does NOT wrap again. Two layers of
    # wrappers is not a cosmetic problem: `restore()` unwinds exactly one, so the
    # inner layer would survive `disarm()` and keep refusing writes for the rest
    # of the process, after the code that installed it believed it was gone.
    # This is reachable now that a `.pth` can arm at interpreter startup and
    # `tests/conftest.py` then arms again at `pytest_sessionstart`.
    #
    # The `_installed_owner()` handover above is what actually enforces this
    # across module copies; this check alone could not, because it is a module
    # global and copy #2 cannot see copy #1's.
    if _RESTORE_WRITE_GUARD is None:
        _RESTORE_WRITE_GUARD = _install_overlay_write_guard()
    _mark_owner(globals())


def disarm():
    """Put the primitives back. A no-op when `arm()` installed nothing."""
    global _RESTORE_WRITE_GUARD, _OVERLAY_PREFIXES, _RECORD_SINK, _MODE
    global _DISPLACED_OWNER
    if _RESTORE_WRITE_GUARD is not None:
        _RESTORE_WRITE_GUARD()
        _RESTORE_WRITE_GUARD = None
        if _DISPLACED_OWNER is not None:
            # Hand back. This copy took the wrappers from another copy, so
            # "put the primitives back" means back to THAT copy's guard, not to
            # bare primitives. Otherwise one throwaway `arm()`/`disarm()` pair
            # leaves the whole process unguarded, silently, for good.
            prior, prefixes, mode, snapshot, restore, marker = _DISPLACED_OWNER
            _DISPLACED_OWNER = None
            prior["_OVERLAY_PREFIXES"] = prefixes
            prior["_MODE"] = mode
            prior["_RESTORE_WRITE_GUARD"] = restore
            _apply_primitives(snapshot)
            # The marker lives on the wrapper object, and the snapshot restored
            # that object, so re-stamp it or the next `arm()` sees an unowned
            # process and stacks a second layer.
            if marker is not None:
                _mark_owner(marker)
    # NOTHING here reaches into another module copy. A copy that installed
    # nothing disarms nothing. MEASURED: an earlier draft did reach out, and
    # `test_off_mode_installs_nothing_at_all` (which calls `arm(MODE_OFF)` on a
    # FRESH copy that had never armed) silently tore down the live session
    # guard, taking 4 later tests with it in a combined run while every one of
    # them passed alone. Turning the process-wide guard off is `arm(MODE_OFF)`'s
    # job, where the operator actually asked for it.
    _OVERLAY_PREFIXES = ()
    # The sink closes over the primitives `restore()` just put back, so leaving
    # it bound would let a later `_refuse_overlay_path` log through a guard that
    # is no longer installed.
    _RECORD_SINK = None
    _MODE = MODE_REFUSE


def watch_complaints(before, after):
    """Pure diff of two `_watch_snapshot()` results. Public so a test can drive it.

    A directory present in `before` and absent from `after` is itself reported:
    a run that removed the whole archive must not read as a clean pass.

    The wording says the overlay CHANGED, never that "a test wrote" it, and the
    difference is not pedantry. This is a whole-session before/after diff: it
    knows the tree moved between two instants and it knows nothing whatever
    about who moved it. On 2026-08-31 a run of this suite reported four rewritten
    files under docs/ and templates/; the cause was a second agent editing those
    documents and running the doc regenerator at 05:00:47, inside the window.
    The earlier wording had already cost one investigation that day: a complaint
    reading "a test wrote" sent an agent hunting a test that had written nothing,
    and it took an audit hook to establish the negative.

    So the message reports the observation, and the reader draws the inference.
    The in-process guard above is the half that CAN name a culprit, because it
    refuses at the moment of the write and the traceback carries the test.
    """
    complaints = []
    for label, (directory, snapshot) in before.items():
        if label not in after:
            complaints.append(f"{label} at {directory} disappeared during the run")
            continue
        now = after[label][1]
        added = sorted(set(now) - set(snapshot))
        removed = sorted(set(snapshot) - set(now))
        resized = sorted(
            n for n in set(snapshot) & set(now)
            if snapshot[n] is not None and snapshot[n] != now[n]
        )
        for what, names in (("appeared", added), ("vanished", removed), ("rewrote", resized)):
            if names:
                complaints.append(
                    f"{len(names)} file(s) {what} in the operator's live {label} "
                    f"at {directory} during the run: {names[:5]}"
                )
    return complaints
