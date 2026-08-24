"""The watcher: a timer that cannot be cancelled, a move that bumps the wrong
page, and a filename matched as a prefix.

Found by the 2026-08-23 engine audit, shard `scripts-02-p3`. All three were
reproduced against this tree on 2026-08-24, and one reported case was refuted
by the same measurement.

**The timer.** `threading.Timer.cancel()` cannot stop a timer whose function
has already begun. Measured interleaving: T1 for `inbox` expires and enters
`_fire`; before it takes the lock, `schedule("inbox")` cancels it (a no-op) and
stores T2; T1 then takes the lock and pops T2. The observed run left `_timers`
empty, T2 alive and untracked - so no later `schedule()` could cancel it - and
called `bump_fn` twice for one burst, the first 0.051 s into a 0.5 s debounce.
A generation token fixes it: a superseded timer returns instead of firing.

**The move.** `on_any_event` read only `event.src_path`. A `FileMovedEvent`
carries the destination in `dest_path`, so `knowledge/x.md` -> `threads/x.md`
bumped `library` and left the Threads page stale. The audit's own reproduction
was a move in from OUTSIDE the tree, and measuring it refuted that case:
watchdog's inotify emitter turns an unpaired IN_MOVED_TO into a
`FileCreatedEvent` whose `src_path` is already the destination, so that path
always worked. The defect is real; the stated trigger was not.

**The filename.** `classify_path` prefix-matched every key, and one key is a
file rather than a directory. `context/pipeline.md.bak`, `.mdx` and `.tmp` all
resolved to `pipeline`, so an atomic write bumped the page twice - once for the
temp file, once for the rename. Directory keys were never affected; their
trailing slash already excludes `knowledge-old/`.
"""
from __future__ import annotations

import shutil
import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from scripts.bridge_daemon.watcher import (  # noqa: E402
    DebouncedBumper,
    PATH_TO_COMPONENTS,
    _Handler,
    classify_path,
    start_observer,
)


# --- a file key is a file, not a prefix --------------------------------------

@pytest.mark.parametrize("path", [
    "context/pipeline.md.bak",
    "context/pipeline.mdx",
    "context/pipeline.md.tmp",
    "context/pipeline.md~",
])
def test_a_neighbour_of_the_pipeline_file_does_not_bump_the_pipeline(path):
    assert classify_path(path) == (), f"{path} still matched as a prefix"


def test_the_pipeline_file_itself_still_bumps():
    """Anchor: exact-matching must not stop matching the real file."""
    assert classify_path("context/pipeline.md") == ("pipeline",)


def test_directory_keys_still_match_everything_below_them():
    assert "library" in classify_path("knowledge/odin-brain/note.md")
    assert "threads" in classify_path("threads/business/x.md")


def test_a_directory_key_does_not_match_a_lookalike_sibling():
    assert classify_path("knowledge-archive/x.md") == ()


def test_every_file_key_is_exercised_by_the_test_above():
    """A rule for file keys is dead the day the last file key is removed, and
    the tests above would keep passing while covering nothing."""
    file_keys = [k for k in PATH_TO_COMPONENTS if not k.endswith("/")]
    assert file_keys == ["context/pipeline.md"], file_keys


# --- a superseded timer does not fire ----------------------------------------

def test_a_timer_that_was_superseded_while_waking_does_not_bump():
    calls: list[str] = []
    b = DebouncedBumper(calls.append, interval=0.05)

    # Hold T1 exactly where the race lives: past expiry, before the lock.
    entered, release = threading.Event(), threading.Event()
    real_fire = b._fire

    def slow_fire(component, generation):
        entered.set()
        release.wait(2)
        real_fire(component, generation)

    b._fire = slow_fire

    b.schedule("inbox")
    assert entered.wait(2), "T1 never reached _fire"
    b.schedule("inbox")                      # cancel() is a no-op on a running timer
    t2 = b._timers.get("inbox")
    release.set()
    time.sleep(0.4)

    assert calls == ["inbox"], f"one burst produced {len(calls)} bumps: {calls}"
    assert b._timers.get("inbox") is not t2 or t2 is None, (
        "the superseded timer popped its successor out of the registry, "
        "leaving a timer alive that no later schedule() can cancel"
    )


def test_an_ordinary_burst_still_coalesces_to_one_bump():
    """Anchor: the guard must not have turned every timer into a no-op."""
    calls: list[str] = []
    b = DebouncedBumper(calls.append, interval=0.08)
    for _ in range(5):
        b.schedule("inbox")
        time.sleep(0.01)
    time.sleep(0.3)
    assert calls == ["inbox"]


def test_two_components_do_not_share_a_generation():
    calls: list[str] = []
    b = DebouncedBumper(calls.append, interval=0.05)
    b.schedule("inbox")
    b.schedule("tribe")
    time.sleep(0.3)
    assert sorted(calls) == ["inbox", "tribe"]


def test_a_later_burst_bumps_again():
    """The generation counter must not latch after the first fire."""
    calls: list[str] = []
    b = DebouncedBumper(calls.append, interval=0.05)
    b.schedule("inbox")
    time.sleep(0.25)
    b.schedule("inbox")
    time.sleep(0.25)
    assert calls == ["inbox", "inbox"]


# --- a move bumps where the file went ----------------------------------------

class _Moved:
    is_directory = False

    def __init__(self, src, dest):
        self.src_path, self.dest_path = str(src), str(dest)


class _Created:
    is_directory = False

    def __init__(self, src):
        self.src_path = str(src)


def _handler(root: Path):
    seen: list[str] = []
    return _Handler(root, type("B", (), {"schedule": staticmethod(seen.append)})()), seen


def test_a_move_across_components_bumps_both_pages(tmp_path):
    h, seen = _handler(tmp_path)
    h.on_any_event(_Moved(tmp_path / "knowledge" / "x.md",
                          tmp_path / "threads" / "x.md"))
    assert set(seen) == {"library", "threads"}, (
        f"only the page the file LEFT was bumped: {seen}"
    )


def test_a_move_out_of_the_tree_still_bumps_the_page_it_left(tmp_path):
    h, seen = _handler(tmp_path)
    h.on_any_event(_Moved(tmp_path / "threads" / "x.md", "/elsewhere/x.md"))
    assert seen == ["threads"]


def test_a_move_within_one_component_bumps_it_once(tmp_path):
    """`os.replace(tmp, target)` in the same directory is the atomic-write
    shape and must not double-bump."""
    h, seen = _handler(tmp_path)
    h.on_any_event(_Moved(tmp_path / "threads" / "x.tmp",
                          tmp_path / "threads" / "x.md"))
    assert seen == ["threads"]


def test_a_plain_create_is_unaffected(tmp_path):
    h, seen = _handler(tmp_path)
    h.on_any_event(_Created(tmp_path / "knowledge" / "x.md"))
    assert seen == ["library"]


def test_a_path_outside_the_root_is_still_ignored(tmp_path):
    h, seen = _handler(tmp_path)
    h.on_any_event(_Created("/somewhere/else/knowledge/x.md"))
    assert seen == []


def test_watchdog_really_reports_a_move_in_from_outside_as_a_create(tmp_path):
    """The refutation, pinned. The audit's reproduction was a move in from
    OUTSIDE the tree, and it does not reproduce: watchdog turns an unpaired
    IN_MOVED_TO into a create whose `src_path` is already the destination. If a
    watchdog upgrade ever emits a FileMovedEvent there instead, the destination
    is handled anyway - but the docstring above would be wrong, and this says so.

    Bounded poll rather than fixed sleeps, so it is fast on inotify and still
    correct on the polling emitter a container without inotify falls back to.
    """
    pytest.importorskip("watchdog")
    from watchdog.events import FileSystemEventHandler
    from watchdog.observers import Observer

    root, outside = tmp_path / "root", tmp_path / "outside"
    (root / "threads").mkdir(parents=True)
    outside.mkdir()
    seen: list[tuple[str, str]] = []

    class H(FileSystemEventHandler):
        def on_any_event(self, e):
            if not e.is_directory:
                seen.append((type(e).__name__, e.src_path))

    obs = Observer()
    obs.schedule(H(), str(root), recursive=True)
    obs.start()
    try:
        (outside / "a.md").write_text("x", encoding="utf-8")
        shutil.move(str(outside / "a.md"), str(root / "threads" / "a.md"))
        deadline = time.monotonic() + 15
        while not seen and time.monotonic() < deadline:
            time.sleep(0.05)
        time.sleep(0.2)   # let a second event for the same move arrive, if any
    finally:
        obs.stop()
        obs.join()

    assert seen, "the observer saw nothing in 15 s; the test proves nothing"
    kinds = {k for k, _ in seen}
    assert "FileMovedEvent" not in kinds, (
        f"watchdog now reports a move-in as a move: {seen}"
    )
    assert any(str(root) in p for _, p in seen), seen


# --- both roots are resolved before anything is matched against them ---------

def test_both_roots_are_resolved_before_scheduling(tmp_path, monkeypatch):
    """The equality test always resolved; the paths actually watched did not.
    A relative root reaches `relative_to` unresolved, and a backend reporting
    absolute paths then swallows every event into the silent branch."""
    real = (tmp_path / "ws").resolve()
    (real / "outputs").mkdir(parents=True)
    scheduled: list[tuple[Path, str]] = []

    class _Obs:
        def schedule(self, handler, path, recursive=False):
            scheduled.append((handler.root, path))

        def start(self):
            pass

    monkeypatch.setattr("scripts.bridge_daemon.watcher.Observer", _Obs)
    monkeypatch.chdir(tmp_path)
    start_observer(Path("ws"), state=type("S", (), {"bump": staticmethod(lambda c: None)})(),
                   data_root=Path("ws"))

    assert scheduled, "nothing was scheduled"
    for root, path in scheduled:
        assert root.is_absolute(), f"handler root left relative: {root}"
        assert Path(path).is_absolute(), f"watch path left relative: {path}"
        assert root == real


def test_identical_roots_still_schedule_only_one_handler(tmp_path, monkeypatch):
    """Resolving must not make an engine-equals-data workspace double-watch."""
    (tmp_path / "outputs").mkdir()
    scheduled = []

    class _Obs:
        def schedule(self, handler, path, recursive=False):
            scheduled.append(path)

        def start(self):
            pass

    monkeypatch.setattr("scripts.bridge_daemon.watcher.Observer", _Obs)
    start_observer(tmp_path, state=type("S", (), {"bump": staticmethod(lambda c: None)})(),
                   data_root=tmp_path)
    assert len(scheduled) == 1, scheduled


def test_differing_roots_schedule_two_handlers(tmp_path, monkeypatch):
    engine, data = tmp_path / "engine", tmp_path / "data"
    engine.mkdir()
    data.mkdir()
    scheduled = []

    class _Obs:
        def schedule(self, handler, path, recursive=False):
            scheduled.append(path)

        def start(self):
            pass

    monkeypatch.setattr("scripts.bridge_daemon.watcher.Observer", _Obs)
    start_observer(engine, state=type("S", (), {"bump": staticmethod(lambda c: None)})(),
                   data_root=data)
    assert len(scheduled) == 2, scheduled
