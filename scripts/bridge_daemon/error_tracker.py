"""Rolling-window error tracker for bridge daemon heartbeat.

Spec section 3.7 (Phase 3 deferral note): "recent_error_count: errors
logged in the last hour (best-effort, currently always 0; Phase 3
wires a logging filter to update it)."

This module is that filter. A `logging.Handler` subclass captures every
WARNING+ record in a thread-safe deque keyed by timestamp; the
heartbeat writer reads `recent_count()` + `last_error()` on each
60-second tick.

Singleton pattern: one tracker per process. install_handler() is
idempotent - safe to call from start_daemon() on every reload.
"""
from __future__ import annotations

import logging
import threading
import time
from collections import deque

_DEFAULT_WINDOW_S = 3600  # 1 hour, spec section 3.7
_MAX_EVENTS = 1000  # cap memory: a runaway crash-loop can't OOM the daemon


class ErrorTracker:
    """Thread-safe rolling-window error counter.

    Stores (timestamp, message) tuples. Prunes on every read. Capped at
    `max_events` entries so a flood of WARNINGs cannot exhaust memory.
    """

    def __init__(self, window_s: int = _DEFAULT_WINDOW_S, max_events: int = _MAX_EVENTS):
        self._window_s = window_s
        self._max_events = max_events
        self._events: deque[tuple[float, str]] = deque()
        self._lock = threading.Lock()

    def record(self, msg: str) -> None:
        """Add one event at the current time. Hard-capped at max_events."""
        with self._lock:
            self._events.append((time.time(), msg))
            self._prune()

    def _prune(self) -> None:
        """Drop entries older than window_s. Drop the oldest if over cap.
        Caller must hold the lock.
        """
        cutoff = time.time() - self._window_s
        while self._events and self._events[0][0] < cutoff:
            self._events.popleft()
        while len(self._events) > self._max_events:
            self._events.popleft()

    def last_error(self) -> str | None:
        """Return the most recent error message in the window, or None."""
        with self._lock:
            self._prune()
            return self._events[-1][1] if self._events else None

    def recent_count(self) -> int:
        """Return how many events sit inside the rolling window right now."""
        with self._lock:
            self._prune()
            return len(self._events)

    def clear(self) -> None:
        """Empty the buffer. Used by tests; production code never calls this."""
        with self._lock:
            self._events.clear()


class _TrackerHandler(logging.Handler):
    """logging.Handler that forwards every WARNING+ record into a tracker."""

    def __init__(self, tracker: ErrorTracker):
        super().__init__(level=logging.WARNING)
        self.tracker = tracker

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.tracker.record(record.getMessage())
        except Exception:  # noqa: S110 - a logging.Handler.emit() must never raise, and logging here would recurse into this same handler; swallowing is the only safe option.
            # logging handlers must never raise; swallow and move on.
            pass


# Module-level singleton. The daemon attaches one handler at boot; the
# heartbeat writer reads from this instance on every 60s tick.
_GLOBAL_TRACKER = ErrorTracker()
_INSTALLED = False
_INSTALL_LOCK = threading.Lock()


def get_tracker() -> ErrorTracker:
    """Return the process-wide tracker singleton."""
    return _GLOBAL_TRACKER


def install_handler(logger: logging.Logger | None = None) -> bool:
    """Attach the tracker handler to the given logger (root by default).

    Idempotent: re-calling has no effect. Returns True iff the handler
    was newly installed (False on subsequent calls). Tests use the
    return value to assert single-attachment semantics.
    """
    global _INSTALLED
    with _INSTALL_LOCK:
        if _INSTALLED:
            return False
        target = logger if logger is not None else logging.getLogger()
        target.addHandler(_TrackerHandler(_GLOBAL_TRACKER))
        _INSTALLED = True
        return True


def _reset_for_tests() -> None:
    """Test-only reset hook. Clears events + un-marks the install flag so a
    fresh handler can be attached in the next test."""
    global _INSTALLED
    with _INSTALL_LOCK:
        _GLOBAL_TRACKER.clear()
        _INSTALLED = False
