"""Tests for the bridge daemon error tracker (Phase J / spec 3.7)."""
import logging
import threading
import types

import pytest

from scripts.bridge_daemon.error_tracker import (
    ErrorTracker,
    _TrackerHandler,
    _reset_for_tests,
    get_tracker,
    install_handler,
)


@pytest.fixture(autouse=True)
def _reset():
    """Make every test start with an empty tracker + no installed handler."""
    _reset_for_tests()
    yield
    _reset_for_tests()


def test_empty_tracker_returns_none_and_zero():
    t = ErrorTracker()
    assert t.last_error() is None
    assert t.recent_count() == 0


def test_record_advances_count_and_last_error():
    t = ErrorTracker()
    t.record("first warning")
    t.record("second warning")
    assert t.recent_count() == 2
    assert t.last_error() == "second warning"


def test_window_prunes_old_events(monkeypatch):
    """Events older than window_s drop off on the next read.

    Uses a controllable fake clock instead of a real ``time.sleep`` so the
    window boundary is exact and deterministic. The previous real-sleep
    version was timing-coupled: under CPU load the wall clock could advance
    between ``record("new")`` and the read, aging "new" out (count 0) or, on
    an NTP step-back, keeping "old" alive (count 2). Patching the module's
    time source removes that race entirely.
    """
    clock = {"t": 1000.0}
    fake_time = types.SimpleNamespace(time=lambda: clock["t"])
    monkeypatch.setattr(
        "scripts.bridge_daemon.error_tracker.time", fake_time
    )
    t = ErrorTracker(window_s=1)
    t.record("old")          # stamped at t=1000.0
    clock["t"] += 1.05       # advance past the 1s window
    t.record("new")          # stamped at t=1001.05
    assert t.recent_count() == 1   # "old" pruned (1.05s old > 1s window)
    assert t.last_error() == "new"


def test_max_events_caps_memory():
    """Cap kicks in even if all events are inside the window."""
    t = ErrorTracker(window_s=3600, max_events=5)
    for i in range(20):
        t.record(f"e{i}")
    assert t.recent_count() == 5
    # Should keep the newest 5: e15..e19
    assert t.last_error() == "e19"


def test_clear_empties_the_buffer():
    t = ErrorTracker()
    t.record("a")
    t.record("b")
    t.clear()
    assert t.recent_count() == 0
    assert t.last_error() is None


def test_thread_safety_under_concurrent_writes():
    """100 threads x 100 records each = 10000 expected, no exceptions."""
    t = ErrorTracker(window_s=3600, max_events=20000)

    def worker(n):
        for i in range(100):
            t.record(f"thread{n}-event{i}")

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(100)]
    for thr in threads:
        thr.start()
    for thr in threads:
        thr.join()
    assert t.recent_count() == 10_000


# logging.Handler integration tests


def test_handler_forwards_warning_to_tracker():
    t = ErrorTracker()
    handler = _TrackerHandler(t)
    logger = logging.getLogger("test_handler_warning")
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    logger.warning("uh oh")
    assert t.last_error() == "uh oh"
    assert t.recent_count() == 1


def test_handler_forwards_error_and_critical():
    t = ErrorTracker()
    handler = _TrackerHandler(t)
    logger = logging.getLogger("test_handler_error")
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    logger.error("bad")
    logger.critical("worse")
    assert t.recent_count() == 2
    assert t.last_error() == "worse"


def test_handler_ignores_info_and_debug():
    """The handler's level is WARNING; INFO/DEBUG must not reach the tracker."""
    t = ErrorTracker()
    handler = _TrackerHandler(t)
    logger = logging.getLogger("test_handler_info")
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    logger.info("just info")
    logger.debug("just debug")
    assert t.recent_count() == 0


def test_handler_swallows_internal_exceptions():
    """A broken tracker must not propagate exceptions through emit()."""
    class BrokenTracker:
        def record(self, msg):
            raise RuntimeError("boom")

    handler = _TrackerHandler(BrokenTracker())  # type: ignore[arg-type]
    logger = logging.getLogger("test_handler_broken")
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    # Must not raise:
    logger.warning("hello")


def test_install_handler_is_idempotent():
    """Re-calling install_handler() must not double-attach."""
    logger = logging.getLogger("test_install_idempotent")
    assert install_handler(logger) is True
    assert install_handler(logger) is False
    # Exactly one handler attached.
    attached = [h for h in logger.handlers if isinstance(h, _TrackerHandler)]
    assert len(attached) == 1


def test_install_handler_uses_root_logger_by_default():
    """install_handler() with no arg attaches to root."""
    install_handler()
    attached = [h for h in logging.getLogger().handlers if isinstance(h, _TrackerHandler)]
    assert len(attached) == 1


def test_get_tracker_returns_singleton():
    """All callers share the same instance."""
    a = get_tracker()
    b = get_tracker()
    assert a is b
    a.record("x")
    assert b.recent_count() == 1


def test_singleton_feeds_from_logging_through_install_handler():
    """End-to-end: install handler -> emit warning -> singleton sees it."""
    logger = logging.getLogger("test_singleton_e2e")
    install_handler(logger)
    logger.warning("integration test")
    assert get_tracker().last_error() == "integration test"
