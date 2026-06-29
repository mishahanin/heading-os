"""Workspace-aware Langfuse observability wrapper.

Provides ``@observe`` that automatically becomes a no-op pass-through when
observability is disabled. Disabled triggers:

1. Sensitivity not explicitly cleared (``is_sensitive()`` True) - tracing is
   **fail-closed**: telemetry flows only when ``SENSITIVE_MODE`` is deliberately
   set to a cleared token (off/0/false/no/cleared). Unset/garbage → suppressed.
   This is the fail-closed successor to the removed ``_secure/`` vault air-gap.
2. ``LANGFUSE_ENABLED`` env var set to ``false`` / ``0`` / ``no`` (case-insensitive)
3. ``langfuse`` package not installed (defensive - keeps the workspace runnable
   if a fresh clone hasn't ``pip install``-ed yet)

When all checks pass, ``@observe`` delegates to the real ``langfuse.observe``
decorator and traces flow to the configured Langfuse instance (Cloud EU by
default per ``feedback_langfuse_observability.md``).

Usage::

    from scripts.utils.observability import observe

    @observe()
    def call_claude(...):
        client = anthropic.Anthropic()
        return client.messages.create(...)

Closes P2.1 from the 2026-05-14 workspace deep audit (replaced original
Helicone recommendation - Helicone discontinued service; CEO rejected Docker
self-host of Langfuse; cloud chosen).
"""

from __future__ import annotations

import functools
import inspect
import logging
import os
from typing import Any, Callable

from scripts.utils.sensitive import is_sensitive

__all__ = ["observe", "is_enabled", "flush"]


def is_enabled() -> bool:
    """Return True iff observability should record traces in this session.

    Fail-closed: tracing is suppressed whenever sensitivity is not explicitly
    cleared (``is_sensitive()`` True), so an unset/garbage ``SENSITIVE_MODE``
    degrades to "no telemetry". Only after sensitivity is deliberately cleared
    does ``LANGFUSE_ENABLED`` (default on) decide.
    """
    if is_sensitive():
        return False
    flag = os.environ.get("LANGFUSE_ENABLED", "true").strip().lower()
    return flag not in ("false", "0", "no", "off", "")


# QW4: a silent no-op is itself a reliability defect. When observability is
# *enabled* (the session intends to record) but cannot actually deliver traces -
# the langfuse package is unimportable, or its credentials are absent - emit one
# loud WARNING per process instead of degrading silently. Intentional disables
# (sensitive session, LANGFUSE_ENABLED=false) never reach here: is_enabled()
# returns False first, so they stay quiet by design.
_degraded_warned = False


def _warn_if_degraded(real: "Callable[..., Any] | None") -> None:
    """Emit one visible WARNING per process if enabled-but-non-functional."""
    global _degraded_warned
    if _degraded_warned:
        return
    if real is None:
        reason = "langfuse package is not importable"
    elif not (os.environ.get("LANGFUSE_PUBLIC_KEY") and os.environ.get("LANGFUSE_SECRET_KEY")):
        reason = "LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY are not set"
    else:
        return
    _degraded_warned = True
    logging.getLogger("scripts.utils.observability").warning(
        "Langfuse observability is enabled but degraded (%s); traces will NOT be "
        "recorded this session. Set LANGFUSE_ENABLED=false to disable intentionally "
        "and silence this warning.",
        reason,
    )


def _noop_decorator(*dargs, **dkwargs):
    """A drop-in replacement for ``@observe`` when observability is disabled."""
    # Support both @observe and @observe(...) call forms
    if len(dargs) == 1 and callable(dargs[0]) and not dkwargs:
        return dargs[0]

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        return func

    return decorator


# Lazy-import langfuse only when @observe is actually applied, not at module load.
# Module load happens during pytest collection too; importing langfuse there crashes
# on Python 3.14 Windows (numpy -> os.uname() missing). Deferring to decoration time
# keeps `from scripts.utils.observability import observe` cheap and crash-free in
# environments that haven't `pip install langfuse`-ed yet.

_langfuse_observe_cache: "Callable[..., Any] | bool | None" = None  # None=not-tried, False=failed


def _get_real_observe():
    global _langfuse_observe_cache
    if _langfuse_observe_cache is None:
        try:
            from langfuse import observe as _real_observe  # type: ignore[import-not-found]

            _langfuse_observe_cache = _real_observe
        except Exception:
            # Catch broadly - transitive imports can fail (numpy on Python 3.14 Windows,
            # for example). Treat any failure as "langfuse unavailable; degrade to no-op."
            _langfuse_observe_cache = False
    return _langfuse_observe_cache or None


def _stamp_trace_id() -> None:
    """Best-effort: attach the X31C trace ID to the current Langfuse trace's
    metadata so Langfuse traces correlate with our ``[trace_id]`` log lines.

    Runs from inside the observed function's span (a trace is active by then).
    Never raises - observability must never break the traced call, and the
    ``update_current_trace`` API may be absent on some SDK versions.
    """
    tid = os.environ.get("X31C_TRACE_ID")
    if not tid:
        return
    try:
        from langfuse import get_client  # type: ignore[import-not-found]

        get_client().update_current_trace(metadata={"x31c_trace_id": tid})
    except Exception as exc:  # noqa: BLE001 - best-effort trace stamp; observability must never break the traced call
        logging.getLogger("scripts.utils.observability").debug("trace stamp failed: %s", exc)


def _apply(real, func, *dargs, **dkwargs):
    """Wrap ``func`` so it stamps the X31C trace ID from within its span, then
    apply the real Langfuse ``observe`` decorator to the wrapper. Preserves
    async-ness so coroutine functions stay coroutines under tracing."""
    if inspect.iscoroutinefunction(func):
        @functools.wraps(func)
        async def inner(*args, **kwargs):
            _stamp_trace_id()
            return await func(*args, **kwargs)
    else:
        @functools.wraps(func)
        def inner(*args, **kwargs):
            _stamp_trace_id()
            return func(*args, **kwargs)
    if dargs or dkwargs:
        return real(*dargs, **dkwargs)(inner)
    return real(inner)


def observe(*dargs, **dkwargs):
    """Lazy ``@observe`` - applies real Langfuse decorator on first decoration; no-op fallback.

    R12: when active, the decorated function is wrapped so the X31C trace ID is
    stamped onto the current Langfuse trace metadata at call time.
    """
    if not is_enabled():
        return _noop_decorator(*dargs, **dkwargs)
    real = _get_real_observe()
    _warn_if_degraded(real)
    if real is None:
        return _noop_decorator(*dargs, **dkwargs)
    # @observe (bare) form: dargs[0] is the function.
    if len(dargs) == 1 and callable(dargs[0]) and not dkwargs:
        return _apply(real, dargs[0])
    # @observe(...) form: return a decorator that wraps the eventual function.

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        return _apply(real, func, *dargs, **dkwargs)

    return decorator


def flush() -> None:
    """Flush pending traces to Langfuse. No-op when disabled or langfuse missing.

    Daemons should call this on graceful shutdown to avoid losing the last batch
    of events. CLI scripts can usually skip it - the SDK flushes atexit by default.
    """
    if not is_enabled():
        return
    try:
        from langfuse import get_client  # type: ignore[import-not-found]

        get_client().flush()
    except Exception as exc:
        # Best-effort: never crash the caller because observability stuttered.
        logging.getLogger("scripts.utils.observability").debug("flush failed: %s", exc)
