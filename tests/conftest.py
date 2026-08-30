"""Root test configuration.

Pin the per-instance timezone for the whole test session so tests that assert
local-time behaviour (calendar, scheduling, daemon heartbeats) validate the
real Etc/GMT-4 logic rather than the engine's UTC default. The production
value lives in the gitignored .env; here we set it deterministically.
See scripts.utils.workspace.get_default_tz().

This file also holds the re-exec guard for the whole suite; see below.
"""
import os
from pathlib import Path

import pytest

from scripts.utils import venv_guard as _venv

# Assignment, not setdefault, for the reason spelled out at WORKSPACE_LOG_DIR
# fifty lines down: a pin a stray shell variable can switch off is not a pin.
# It was a `setdefault` until 2026-08-30, so `HEADING_OS_TZ=America/New_York
# pytest tests/` quietly re-pointed every calendar, scheduling and heartbeat
# assertion at New York offsets while this docstring still promised Etc/GMT-4,
# and nothing in the run said the substitution had happened. A test that needs
# a different zone sets it per case with monkeypatch, which applies inside the
# test body and still wins.
# tests/test_a_timezone_pin_a_stray_variable_could_switch_off.py fails without
# this line being an assignment.
os.environ["HEADING_OS_TZ"] = "Etc/GMT-4"

# The suite's re-exec guard, set ONCE, here, because this file is collected
# before any test module.
#
# About twenty scripts call `ensure_venv()` at module scope and about twenty test
# modules load a script by path. When the running interpreter is not
# .venv/bin/python, that call `os.execv`s the WHOLE pytest process, which inherits
# pytest's capture as file descriptor 1: the relaunched run writes every byte
# into a temp file nobody reads, so `pytest tests/` prints ZERO bytes while
# exiting 0 on a passing set and 1 on a failing one. Measured on this repository,
# 2026-07-26. A run that prints nothing is indistinguishable from one that never
# happened.
#
# It lived as three per-module copies until wire 2.2, and that shape could not
# hold. The variable is process-global, so each copy satisfied the other two
# modules' guard tests: deleting the line from one module left its own test
# passing. Worse, the defect was self-erasing, because a NEW unguarded module
# re-execs at collection, `ensure_venv` sets this same sentinel before
# `os.execv`, and in the relaunched silent run every guard test passes.
#
# The constant is referenced rather than spelled out: a duplicated literal drifts
# silently the day venv.py renames it.
os.environ.setdefault(_venv._SENTINEL, "1")

_TESTS_ROOT = Path(__file__).resolve().parent
_ENGINE_ROOT = _TESTS_ROOT.parent

# Runtime logs written during a test run go to their own directory, never to the
# operator's. Measured the day the denial counter landed (2026-08-01): a single
# suite run appended 13 refusals to the production `.logs/denials/denials.jsonl`
# from tests that legitimately exercise leak-guard and the push walls with
# fixtures. Left alone, the instrument built to decide which guards earn their
# cost would have counted its own test suite as the workspace's main offender,
# which is the same defect class as an instrument with a silently wrong
# denominator — the thing this counter exists to end, reproduced inside it.
#
# Assignment, not setdefault: isolation that a stray shell variable can switch
# off is not isolation. Individual tests still redirect per-case with monkeypatch
# or an explicit subprocess env, and both continue to win over this.
_TEST_LOG_DIR = str(_ENGINE_ROOT / ".logs" / "_pytest")
os.environ["WORKSPACE_LOG_DIR"] = _TEST_LOG_DIR

# The same isolation, one guard along. `check_rate_limit` in the PreToolUse
# dispatcher counts Write and Edit calls per day and BLOCKS past 1000, and six
# test modules drive that hook in a subprocess exactly as production does.
# Measured 2026-08-07, before this line: the operator's counter stood at 1033
# and was blocking three tests, and the writes it had stored were fixtures —
# `threads/personal/foo.md`, a Windows path that cannot exist on this machine,
# a scratch probe file. One run of three of those modules added 12 more.
#
# UNLINKED at import, not merely redirected. The counter is keyed by date and
# resets itself tomorrow, but a run this hour would otherwise inherit every
# earlier run's fixtures today, and enough runs in one day would reproduce the
# same block in the new location. Starting from zero is what makes the
# redirection a fix rather than a move.
#
# Only the session that OWNS the variable resets it, and that distinction was
# measured rather than reasoned. This suite spawns pytest CHILDREN — the Canopus
# probe alone runs several per contract — and each child imports this file. With
# an unconditional unlink they wiped their parent's counter mid-run, so after a
# full run the file was simply gone and the reset the comment promised was not
# the reset the code performed. An inherited variable means a parent already did
# this; the child leaves it alone.
_TEST_RATE_STATE = _ENGINE_ROOT / ".logs" / "_pytest" / "dispatch-rate.json"
_OWNS_RATE_STATE = "WS_RATE_LIMIT_STATE" not in os.environ
os.environ["WS_RATE_LIMIT_STATE"] = str(_TEST_RATE_STATE)
if _OWNS_RATE_STATE:
    try:
        _TEST_RATE_STATE.unlink()
    except FileNotFoundError:
        pass
    except OSError as exc:  # pragma: no cover - reported, never fatal to the run
        print(f"[conftest] could not reset the test rate-limit state: {exc}")


# The same isolation, applied to the one instrument the suite could falsify from
# the outside. A Healthchecks deadman answers "is that daemon alive?", and this
# repository was answering for it. `_main_loop` in scripts/inbox_pulse/daemon.py
# pings STEWARD_HC_EMAIL_TRIAGE at the end of a clean cycle, eleven tests in
# tests/inbox_pulse/test_daemon.py drive that loop to completion, and any earlier
# test that calls the real load_env() puts the production ping URL in os.environ
# for the rest of the session. Measured 2026-08-18: one run of that one file sent
# 14 real success pings to the live check. The daemon behind it had been wedged
# since 2026-08-17 06:53 UTC and had not completed a single cycle in 33 hours,
# yet the operator saw the alert clear and re-fire nine times, because every push
# ran the suite and the suite pinged. The outage was found by reading a traceback,
# not by the monitor built to find it.
#
# Blanked, not deleted, and that distinction is the whole guard: load_env() uses
# os.environ.setdefault, so a name it finds already present is left alone, while a
# deleted one is restored the moment any test loads the environment. An empty
# value is also the documented no-op for ping(), which returns False on a falsy
# URL without opening a socket.
#
# Matched on the VALUE rather than the name, because the invariant is "this
# string is a live deadman", not "this key happens to be spelled _HC_". A ping URL
# added tomorrow under any name is contained on the day it is added.
# tests/test_deadman_ping_containment.py fails without these four lines.
_ENV_FILE = _ENGINE_ROOT / ".env"
if _ENV_FILE.exists():
    for _line in _ENV_FILE.read_text(encoding="utf-8").splitlines():
        _key, _, _value = _line.partition("=")
        if _ and "hc-ping.com" in _value:
            os.environ[_key.strip()] = ""

# The same containment, applied to the second instrument the suite could drive
# from the outside: the notifications bot. A deadman ping is believed; a Telegram
# message is READ, and the operator cannot tell one sent by a daemon from one
# sent by a test run.
#
# Measured 2026-08-19. `_notify_stall` in .claude/hooks/checkpoint-offer.py sends
# "HEADING OS: unattended run stopped. <reason>" when the no-progress fuse trips,
# and `test_the_pause_record_is_written_once` calls `_pause_unattended` directly.
# That test deleted HEADING_OS_TELEGRAM_CHAT_ID, a name `_notify_stall` never
# reads: the function walks CHECKPOINT_ / OPS_RADAR_ / ODIN_CADENCE_
# _TELEGRAM_TARGET, and any earlier test that called the real `load_env()` had
# already put ODIN_CADENCE_TELEGRAM_TARGET in os.environ for the rest of the
# session. Every run of the suite therefore sent the operator one real alert
# about an unattended run that had never started. He received nine of them in one
# afternoon and read them as a failing feature.
#
# Matched on the NAME here, not the value, because a chat id is a bare number
# with nothing distinctive to match on. Two rings: the targets, which the callers
# check first, and the bot token, without which `telegram_notify.notify()`
# returns False before it opens a socket whatever target survives.
#
# NOT contained here: TELEGRAM_API_ID / _API_HASH / _PHONE, the credentials of
# the operator's own Telegram ACCOUNT rather than the bot. No test in this suite
# drives that client, so blanking them would be a guard against nothing; a test
# that starts driving it needs its own containment, and it is named here so that
# day is not a discovery.
# tests/test_telegram_send_containment.py fails without these lines.
_MUTED_TELEGRAM = {"TELEGRAM_NOTIFY_BOT_TOKEN"}
_MUTED_TELEGRAM.update(k for k in os.environ if k.endswith("_TELEGRAM_TARGET"))
if _ENV_FILE.exists():
    for _line in _ENV_FILE.read_text(encoding="utf-8").splitlines():
        _key, _, _value = _line.partition("=")
        _key = _key.strip()
        if _ and _key.endswith("_TELEGRAM_TARGET"):
            _MUTED_TELEGRAM.add(_key)
_MUTED_TELEGRAM = tuple(sorted(_MUTED_TELEGRAM))
for _key in _MUTED_TELEGRAM:
    os.environ[_key] = ""


@pytest.fixture(autouse=True)
def _isolate_runtime_logs():
    """Re-arm the redirection before every test.

    The module-scope assignment above covers import time and nothing else. A
    test that sets the variable by hand and pops it in a `finally` leaves it
    UNSET for every test that follows, and the rest of the session then writes
    to the operator's real log. That is not hypothetical: the denial counter's
    own contract does exactly that, and it is how this fixture was found.
    Restoring per test costs nothing and does not fight monkeypatch, which
    applies inside the test body, after this.

    The Telegram names are re-armed here for a sharper version of the same
    reason. Several notifier tests `monkeypatch.delenv` a target to exercise the
    unconfigured path, and a DELETED name is not a blanked one: the next
    `load_env()` anywhere in the session restores the operator's real chat id,
    because load_env uses setdefault. Blanking again before each test is what
    keeps the containment from lasting only until the first test that clears it.
    """
    os.environ["WORKSPACE_LOG_DIR"] = _TEST_LOG_DIR
    os.environ["WS_RATE_LIMIT_STATE"] = str(_TEST_RATE_STATE)
    for _name in _MUTED_TELEGRAM:
        os.environ[_name] = ""
    yield


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


def _overlay_root():
    """The live overlay root, or None when this clone has no private overlay."""
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


def _overlay_dir(parts):
    """One directory inside the live overlay, or None. Kept for callers that
    want a single subtree rather than the whole walk."""
    root = _overlay_root()
    if root is None:
        return None
    directory = root.joinpath(*parts)
    return directory if directory.is_dir() else None


def _watch_snapshot():
    """{label: (directory, {relpath: size})} for the whole overlay.

    One label, because the unit being protected is the overlay, not a list of
    interesting places in it. Sizes are always taken: a truncation in place adds
    no file and removes none, which is how the memory index was lost in 2026-08.
    """
    root = _overlay_root()
    if root is None:
        return {}
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
    return {"operator overlay": (root, entries)}


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

_OVERLAY_PREFIX = None          # set in pytest_sessionstart
_WRITE_MODE_CHARS = frozenset("wxa+")


class OverlayWriteRefused(RuntimeError):
    """A test tried to write the operator's live data."""


def _refuse_overlay_path(target, verb):
    if _OVERLAY_PREFIX is None:
        return
    try:
        text = os.fspath(target)
    except TypeError:
        return
    if isinstance(text, bytes):
        text = os.fsdecode(text)
    if _OVERLAY_PREFIX not in text:
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

    # `io.open` and `builtins.open` are the same object, and pathlib reaches the
    # one on `io`. Both names are rebound or `Path.write_text` walks straight
    # past the guard.
    builtins.open = guarded_open
    io.open = guarded_open
    os.replace, os.rename = guarded_replace, guarded_rename
    os.remove, os.unlink = guarded_remove, guarded_unlink

    def restore():
        builtins.open = real_open
        io.open = real_open
        os.replace, os.rename = real_replace, real_rename
        os.remove, os.unlink = real_remove, real_unlink

    return restore


_RESTORE_WRITE_GUARD = None


def pytest_sessionstart(session):
    global _WATCH_BEFORE, _OVERLAY_PREFIX, _RESTORE_WRITE_GUARD
    _WATCH_BEFORE = _watch_snapshot()
    root = _overlay_root()
    if root is None:
        return
    _OVERLAY_PREFIX = f"{root}{os.sep}"
    _RESTORE_WRITE_GUARD = _install_overlay_write_guard()


def watch_complaints(before, after):
    """Pure diff of two `_watch_snapshot()` results. Public so a test can drive it.

    A directory present in `before` and absent from `after` is itself reported:
    a run that removed the whole archive must not read as a clean pass.
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
        for what, names in (("wrote", added), ("deleted", removed), ("rewrote", resized)):
            if names:
                complaints.append(
                    f"a test {what} {len(names)} file(s) in the operator's live "
                    f"{label} at {directory}: {names[:5]}"
                )
    return complaints


def pytest_sessionfinish(session, exitstatus):
    if not _WATCH_BEFORE:
        return
    complaints = watch_complaints(_WATCH_BEFORE, _watch_snapshot())
    if not complaints:
        return
    # Reported through the reporter rather than an exception: a raise here is
    # attributed to no test and reads as a harness crash.
    reporter = session.config.pluginmanager.get_plugin("terminalreporter")
    message = (
        "; ".join(complaints)
        + ". Pass HEADING_OS_DATA pointing at a tmp_path to anything that writes."
    )
    if reporter is not None:
        reporter.write_line("")
        reporter.write_line(f"ERROR: {message}", red=True)
    session.exitstatus = 1


def pytest_collection_modifyitems(config, items):
    """Auto-mark tests by top-level directory so the per-push CI filter holds.

    The per-push unit job runs `-m "not integration and not acceptance"`. That
    filter is only honest if every test under tests/integration/ actually carries
    the `integration` marker (and likewise tests/acceptance/ -> `acceptance`).
    Marking by path removes the requirement that each file remember to declare it,
    and closes the gap where an unmarked integration suite (e.g. the LFS-fixture
    convert-to-md tests) silently ran in the unit job and failed on a fresh clone.
    Both markers are registered in [tool.pytest.ini_options]; --strict-markers is on.
    """
    for item in items:
        try:
            rel = item.path.resolve().relative_to(_TESTS_ROOT)
        except (ValueError, AttributeError):
            continue
        top = rel.parts[0] if rel.parts else ""
        if top in ("integration", "acceptance"):
            item.add_marker(top)
