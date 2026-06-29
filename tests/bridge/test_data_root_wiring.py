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
    """Records schedule() roots; no real filesystem watch."""

    def __init__(self):
        self.scheduled: list[str] = []

    def schedule(self, handler, path, recursive=True):
        self.scheduled.append(path)

    def start(self):
        pass


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
