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
    if not data_overlay_present():
        return None
    try:
        root = get_data_root().resolve()
    except OSError:
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


def _refuse_overlay_path(target, verb):
    if not _OVERLAY_PREFIXES:
        return
    try:
        text = os.fspath(target)
    except TypeError:
        return
    if isinstance(text, bytes):
        text = os.fsdecode(text)
    if not any(prefix in text for prefix in _OVERLAY_PREFIXES):
        return
    raise OverlayWriteRefused(
        f"a test tried to {verb} the operator's live data at {text}. "
        f"Point HEADING_OS_DATA at a tmp_path before anything that writes, and "
        f"pass it to any child process too."
    )


def _install_overlay_write_guard():
    """Wrap the write primitives. Returns a callable that puts them back."""
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
    # reported ALLOWED. Two of the 35 modules that resolve the data root at
    # import time reach it (`scripts/sentinel.py`, read-write, and
    # `.claude/hooks/memory-inject.py`, read-only).
    import sqlite3 as _sqlite3

    real_sqlite_connect = _sqlite3.connect

    import subprocess as _subprocess

    real_sp_popen = _subprocess.Popen

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

    def guarded_remove(path, *args, **kwargs):
        _refuse_overlay_path(path, "delete")
        return real_remove(path, *args, **kwargs)

    def guarded_unlink(path, *args, **kwargs):
        _refuse_overlay_path(path, "delete")
        return real_unlink(path, *args, **kwargs)

    def guarded_mkdir(path, *args, **kwargs):
        # Only refuse a call that would actually CREATE something. `Path.mkdir(
        # exist_ok=True)` still reaches `os.mkdir` and lets the resulting
        # FileExistsError through its own handler, so refusing unconditionally
        # rejected five tests that were creating nothing at all. Over-friction
        # is how a guard gets switched off, after which nothing guards the real
        # thing - so the test is "would this bring a new path into existence?",
        # not "does this call look like a write?".
        if not os.path.exists(path):
            _refuse_overlay_path(path, "create a directory in")
        return real_mkdir(path, *args, **kwargs)

    def guarded_makedirs(name, *args, **kwargs):
        if not os.path.exists(name):
            _refuse_overlay_path(name, "create a directory tree in")
        return real_makedirs(name, *args, **kwargs)

    def guarded_rmdir(path, *args, **kwargs):
        _refuse_overlay_path(path, "remove a directory from")
        return real_rmdir(path, *args, **kwargs)

    _CREATING_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_TRUNC

    def guarded_os_open(path, flags, *args, **kwargs):
        if flags & _CREATING_FLAGS:
            _refuse_overlay_path(path, "open for writing")
        return real_os_open(path, flags, *args, **kwargs)

    def guarded_sqlite_connect(database, *args, **kwargs):
        # A READ-ONLY connection is allowed, and that is not a softening. It
        # creates nothing and writes nothing, and `.claude/hooks/memory-inject.py`
        # opens the operator's memory index exactly that way on purpose
        # (`?mode=ro`, `uri=True`). Refusing it would be the over-friction that
        # gets a guard switched off. Everything else can create or write, so it
        # is refused.
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

    def restore():
        builtins.open = real_open
        io.open = real_open
        os.replace, os.rename = real_replace, real_rename
        os.remove, os.unlink = real_remove, real_unlink
        os.mkdir, os.makedirs, os.rmdir = real_mkdir, real_makedirs, real_rmdir
        os.open = real_os_open
        _sqlite3.connect = real_sqlite_connect
        _subprocess.Popen = real_sp_popen

    return restore


_RESTORE_WRITE_GUARD = None


def arm():
    """Take the snapshot and install the wrappers. Idempotent per process.

    Exactly the sequence `pytest_sessionstart` ran before this module existed:
    snapshot FIRST, so a write made while arming is still visible as a change,
    then the prefixes, then the wrappers. A clone with no overlay on disk gets
    an empty root set and arms nothing, which is the public-clone and CI case.
    """
    global _WATCH_BEFORE, _WATCH_BEFORE_AT, _OVERLAY_PREFIXES, _RESTORE_WRITE_GUARD
    _WATCH_BEFORE_AT = time.time()
    _WATCH_BEFORE = _watch_snapshot()
    roots = _watched_roots()
    if not roots:
        return
    _OVERLAY_PREFIXES = tuple(f"{root}{os.sep}" for root in roots.values())
    _RESTORE_WRITE_GUARD = _install_overlay_write_guard()


def disarm():
    """Put the primitives back. A no-op when `arm()` installed nothing."""
    global _RESTORE_WRITE_GUARD, _OVERLAY_PREFIXES
    if _RESTORE_WRITE_GUARD is not None:
        _RESTORE_WRITE_GUARD()
        _RESTORE_WRITE_GUARD = None
    _OVERLAY_PREFIXES = ()

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
