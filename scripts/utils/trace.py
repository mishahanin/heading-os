"""X31C trace-ID minting and access (R12).

A single correlation ID per process tree, carried in ``os.environ`` so it
propagates automatically to every subprocess a daemon or script spawns
(children inherit the parent environment; none of the workspace daemons pass
an ``env=`` override to ``subprocess.run``). Scope is per-process-tree: one
daemon boot plus the subprocesses that boot spawns share one ID. It is NOT a
per-business-flow ID spanning multiple daemons; cross-daemon flow-threading is
deferred (see plans/2026-06-03-now-phase-spine.md, Design Decision 1).

Usage::

    from scripts.utils import trace
    trace.mint()                 # at daemon / CLI-script entry, before logging
    tid = trace.get()            # current ID, or None
    trace.set("known-id")        # adopt an externally-supplied ID
    trace.clear()                # drop it (tests)

Pair with ``scripts.utils.trace_filter.install_log_factory()`` so every log
record carries the ID, then add ``[%(trace_id)s]`` to the log formatter.
"""
from __future__ import annotations

import os
import uuid

ENV_KEY = "X31C_TRACE_ID"


def mint() -> str:
    """Mint a fresh UUID4 trace ID, store it in os.environ, and return it.

    Called once at each daemon boot and CLI-script entry. A fresh ID per boot
    means no cross-restart contamination.
    """
    tid = uuid.uuid4().hex
    os.environ[ENV_KEY] = tid
    return tid


def get() -> str | None:
    """Return the current trace ID, or None if none is set."""
    return os.environ.get(ENV_KEY) or None


def set(tid: str) -> None:  # noqa: A001 - deliberate ergonomic name in this tiny module
    """Adopt an externally-supplied trace ID (e.g. inherited from a parent)."""
    os.environ[ENV_KEY] = tid


def ensure() -> str:
    """Return the current trace ID, minting one if absent. Idempotent."""
    return get() or mint()


def clear() -> None:
    """Remove the trace ID from the environment (used by tests)."""
    os.environ.pop(ENV_KEY, None)
