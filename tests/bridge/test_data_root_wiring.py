"""Regression tests for the HEADING OS engine/data two-root daemon wiring (Plan 4 T4).

The daemon serves data from ``data_root`` (CEO content overlay) while keeping
machine-local caches (``.daemon-state``) on the engine ``workspace_root``. On
transitional ceo-main the two roots are identical (no-op); a post-cutover engine
clone reads a separate data sibling. These tests pin the two distinct behaviours
without booting a real daemon or touching the live filesystem.
"""
from __future__ import annotations

from pathlib import Path

import scripts.bridge_daemon.watcher as watcher
import scripts.bridge_daemon.refreshers.pulse as pulse


class _FakeObserver:
    """Records the schedule() calls IN FULL, and whether start() was called.

    It used to keep `path` and throw `handler` away, and to answer `start()`
    with a bare `pass` that recorded nothing. Both omissions hid a mutation
    (see the two tests at the bottom of this file): the handler's `root` is
    what decides whether an event is classified or silently dropped, and
    `start()` is what makes the observer observe anything at all.
    """

    def __init__(self):
        self.scheduled: list[str] = []
        self.calls: list[tuple[Path, str, bool]] = []
        self.started = 0

    def schedule(self, handler, path, recursive=True):
        self.scheduled.append(path)
        self.calls.append((handler.root, path, recursive))

    def start(self):
        self.started += 1


def test_observer_single_handler_when_roots_identical(tmp_path, monkeypatch):
    """ceo-main case: data_root == workspace_root -> ONE handler (no double-fire)."""
    fake = _FakeObserver()
    monkeypatch.setattr(watcher, "Observer", lambda: fake)

    class _State:
        def bump(self, c):
            pass

    watcher.start_observer(tmp_path, _State(), interval=0.5, data_root=tmp_path)
    assert fake.scheduled == [str(tmp_path)]


def test_observer_two_handlers_when_roots_differ(tmp_path, monkeypatch):
    """Post-cutover case: distinct engine + data roots -> TWO handlers, one per tree."""
    engine = tmp_path / "engine"
    data = tmp_path / "data"
    engine.mkdir()
    data.mkdir()
    fake = _FakeObserver()
    monkeypatch.setattr(watcher, "Observer", lambda: fake)

    class _State:
        def bump(self, c):
            pass

    watcher.start_observer(engine, _State(), interval=0.5, data_root=data)
    assert sorted(fake.scheduled) == sorted([str(engine), str(data)])


def test_each_handler_is_rooted_at_the_tree_it_watches(tmp_path, monkeypatch):
    """A count of two says nothing about which handler got which tree.

    `_Handler._classify` computes `Path(abs_path).relative_to(self.root)` and
    returns `()` on ValueError, so a handler whose root is the OTHER tree
    rejects every event it is ever handed, in silence. Swapping the two
    pairings was measured on 2026-08-31:

        owner tests/bridge/test_data_root_wiring.py: 3 passed in 0.93s
        tests/bridge                              : 1312 passed, 1 skipped
        VERDICT: SURVIVED

    measured over the owning file and all of `tests/bridge`. The result is a
    daemon that starts
    cleanly, watches both trees, and updates no page ever, which is the
    hardest failure of the set to notice: the two-root layout is the
    operator's live one, and the symptom is "the dashboard is a bit stale".
    `test_a_moved_file_bumps_the_page_it_arrived_on.py` asserts `handler.root`
    only for the roots-identical case, where a swap cannot show.

    Asserted as a PAIRING rather than as two memberships, because
    `{engine, data} == {engine, data}` is true of the swap as well.
    """
    engine = tmp_path / "engine"
    data = tmp_path / "data"
    engine.mkdir()
    data.mkdir()
    fake = _FakeObserver()
    monkeypatch.setattr(watcher, "Observer", lambda: fake)

    class _State:
        def bump(self, c):
            pass

    watcher.start_observer(engine, _State(), interval=0.5, data_root=data)

    pairs = {(root, Path(path)) for root, path, _ in fake.calls}
    assert pairs == {(engine, engine), (data, data)}, (
        f"handler roots do not match the paths they watch: {fake.calls}")
    assert all(recursive for _, _, recursive in fake.calls), (
        f"a tree is watched non-recursively: {fake.calls}")


def test_a_real_event_reaches_the_bumper_through_each_handler(tmp_path,
                                                              monkeypatch):
    """The pairing above, asked of behaviour instead of arguments.

    Drives the handler the daemon actually built with a synthetic watchdog
    event under each root, and reads which component was bumped. This is the
    version that survives a refactor moving the root out of `_Handler`, and
    it is what proves the swap is a silent no-op rather than merely a
    different argument order.
    """
    engine = tmp_path / "engine"
    data = tmp_path / "data"
    (engine / ".claude" / "skills").mkdir(parents=True)
    (data / "threads").mkdir(parents=True)
    fake = _FakeObserver()
    monkeypatch.setattr(watcher, "Observer", lambda: fake)

    class _State:
        def bump(self, c):
            pass

    watcher.start_observer(engine, _State(), interval=0.5, data_root=data)
    by_root = {root: Path(path) for root, path, _ in fake.calls}
    assert set(by_root) == {engine, data}, by_root

    class _Ev:
        is_directory = False

        def __init__(self, src):
            self.src_path = str(src)

    class _RecordingBumper:
        """Records synchronously, so no debounce timer and no sleep."""

        def __init__(self):
            self.seen: list[str] = []

        def schedule(self, component):
            self.seen.append(component)

    # Rebuild each handler over the root start_observer paired with its path,
    # then feed it the event belonging to THAT path.
    events = {
        engine: engine / ".claude" / "skills" / "x" / "SKILL.md",
        data: data / "threads" / "t.md",
    }
    got = {}
    for root, path in by_root.items():
        bumper = _RecordingBumper()
        watcher._Handler(root, bumper).on_any_event(_Ev(events[path]))
        got[path.name] = set(bumper.seen)

    assert "capabilities" in got["engine"], f"engine tree never bumped: {got}"
    assert "threads" in got["data"], f"data tree never bumped: {got}"

    # And the cross pairing bumps nothing, which is exactly what the swap
    # produces: a silent drop in `_classify`, not a visible error.
    crossed = _RecordingBumper()
    watcher._Handler(data, crossed).on_any_event(_Ev(events[engine]))
    assert crossed.seen == [], (
        "a handler rooted at the wrong tree classified an event, so the swap "
        f"would not land in the silent-drop branch after all: {crossed.seen}")


def test_the_observer_is_actually_started(tmp_path, monkeypatch):
    """`start_observer` returns a watcher that WATCHES.

    The fake's `start()` was a bare `pass` and nothing asserted it had been
    called, so deleting `observer.start()` was measured on 2026-08-31:

        owner tests/bridge/test_data_root_wiring.py: 3 passed in 0.68s
        tests/bridge                              : 1312 passed, 1 skipped
        VERDICT: SURVIVED

    measured over the owning file and all of `tests/bridge`. Every other fake
    observer in this directory has
    the same no-op `start`, so no test anywhere held the line. A daemon in
    that state boots, schedules both handlers, logs nothing wrong, and never
    bumps a component for the rest of its life: the dashboard's own freshness
    display would keep showing the boot-time prime from
    `_prime_all_components`, so it would not even read as stale.

    Once per boot, not once per handler: `start()` on an already-running
    watchdog observer raises RuntimeError.
    """
    fake = _FakeObserver()
    monkeypatch.setattr(watcher, "Observer", lambda: fake)

    class _State:
        def bump(self, c):
            pass

    engine = tmp_path / "engine"
    data = tmp_path / "data"
    engine.mkdir()
    data.mkdir()

    returned = watcher.start_observer(engine, _State(), interval=0.5,
                                      data_root=data)
    assert fake.started == 1, (
        f"observer.start() was called {fake.started} times, not once")
    assert returned is fake, "the started observer is not the one returned"

    # Same for the single-root layout, which takes the other branch.
    fake2 = _FakeObserver()
    monkeypatch.setattr(watcher, "Observer", lambda: fake2)
    watcher.start_observer(tmp_path, _State(), interval=0.5, data_root=tmp_path)
    assert fake2.started == 1, fake2.started


def test_pulse_reads_data_root_writes_snapshot_to_engine_root(tmp_path, monkeypatch):
    """Pulse payload is computed from data_root; the snapshot cache is written
    under the (machine-local) engine workspace_root, not the data overlay."""
    engine = tmp_path / "engine"
    data = tmp_path / "data"
    (engine / ".daemon-state").mkdir(parents=True)
    data.mkdir()

    seen = {}

    def _fake_pulse_data(root, odin_5_target=None):
        seen["read_root"] = Path(root)
        return {"ok": True}

    written = {}

    def _fake_atomic_write_text(path, text, mode=0o600):
        written["path"] = Path(path)
        Path(path).write_text(text, encoding="utf-8")

    monkeypatch.setattr(pulse, "pulse_data", _fake_pulse_data)
    monkeypatch.setattr(pulse, "atomic_write_text", _fake_atomic_write_text)

    class _State:
        def bump(self, c):
            pass

    class _Cfg:
        config = {"kpi": {}}

    pulse.refresh(engine, _State(), _Cfg(), data_root=data)

    assert seen["read_root"] == data                      # payload read from data overlay
    assert written["path"] == engine / pulse.SNAPSHOT_FILENAME  # cache under engine root
    assert str(written["path"]).startswith(str(engine))
