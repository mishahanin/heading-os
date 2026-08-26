"""Sovereignty-safe Langfuse tracing wrapper for the Inbox Pulse daemon.

Exports ``observe_metadata_only`` - the ONLY sanctioned tracing path for any
daemon that processes email content.  Principle 5 (Data Sovereignty Always)
forbids email bodies, subjects, sender identifiers, CRM excerpts, and any LLM
annotation text from flowing to third-party services.

The standard ``langfuse.observe`` decorator ships argument values and return
values by default.  Applying it directly to email-handling functions would leak
sovereign data.  This module uses ``capture_input=False, capture_output=False``
and injects a strictly-controlled whitelist of metadata instead.

Whitelisted metadata fields
---------------------------
- latency_ms        (float)   Wall-clock duration of the decorated call in ms
- input_tokens      (int|None) From anthropic Message response, if returned
- output_tokens     (int|None) From anthropic Message response, if returned
- model             (str|None) From anthropic Message.model, if returned
- tier              (str|None) From return value ``tier`` field, if present
- confidence        (float|None) From return value ``confidence`` field, if present
- sender_domain     (str|None) Domain part of ``email_addr`` kwarg only
- subject_length    (int|None) len() of ``subject`` kwarg - NOT the text
- language          (str|None) "ru" if subject starts with Cyrillic, else "en"

NEVER captured
--------------
- Email body text
- Subject text content
- Full sender address or name
- CRM excerpts
- Recipient list
- LLM annotation / recommended_action text
- Raw function arguments
- Raw function return values

Disable triggers (same as ``observability.py``)
------------------------------------------------
1. Sensitivity not explicitly cleared (``is_sensitive()`` True) - fail-closed
2. ``LANGFUSE_ENABLED`` env var set to ``false`` / ``0`` / ``no`` / ``off``
3. ``langfuse`` package not installed

Debug mode
----------
Set ``INBOX_PULSE_DEBUG_TRACE=true`` to write the FULL payload (input args
+ return value) to ``<state_dir>/debug-trace.jsonl``.  State dir is
``INBOX_PULSE_STATE_DIR`` if set, else the DATA overlay's
``state/email-triage/``.  With no overlay the resolver answers a path inside the
public engine clone, so the trace is refused and diverted to a private temp
file instead; see ``_debug_trace_path``.
OFF by default.  Never enable in production - it will capture sovereign data.

Usage::

    from scripts.utils.observability_safe import observe_metadata_only

    @observe_metadata_only("classify_email")
    def classify(email_addr: str, subject: str, body: str) -> dict:
        ...
"""

from __future__ import annotations

import functools
import json
import logging
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

from scripts.utils.sensitive import is_sensitive

__all__ = ["observe_metadata_only"]

# ---------------------------------------------------------------------------
# Sensitivity + enabled gate (same fail-closed logic as observability.py)
# ---------------------------------------------------------------------------

def _is_enabled() -> bool:
    if is_sensitive():
        return False
    flag = os.environ.get("LANGFUSE_ENABLED", "true").strip().lower()
    return flag not in ("false", "0", "no", "off", "")


# ---------------------------------------------------------------------------
# Lazy import of langfuse (same pattern as observability.py)
# ---------------------------------------------------------------------------

_langfuse_observe_cache: "Callable[..., Any] | bool | None" = None


def _get_langfuse_observe() -> "Callable[..., Any] | None":
    global _langfuse_observe_cache
    if _langfuse_observe_cache is None:
        try:
            from langfuse import observe as _real  # type: ignore[import-not-found]
            _langfuse_observe_cache = _real
        except Exception:
            _langfuse_observe_cache = False
    return _langfuse_observe_cache if _langfuse_observe_cache is not False else None


def _get_langfuse_client() -> "Any | None":
    """Return a langfuse 4.x client for metadata injection, or None if unavailable.

    Uses ``langfuse.get_client()`` (the correct 4.x API).  The old
    ``langfuse.decorators.langfuse_context`` module does not exist in 4.x and
    raises ImportError.  This function replaces the old ``_get_langfuse_context``
    helper that was silently broken.
    """
    try:
        from langfuse import get_client  # type: ignore[import-not-found]
        return get_client()
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Metadata extraction - whitelist only
# ---------------------------------------------------------------------------

def _is_cyrillic(ch: str) -> bool:
    return "Ѐ" <= ch <= "ӿ"


def _extract_metadata(
    kwargs: dict[str, Any],
    result: Any,
    latency_ms: float,
) -> dict[str, Any]:
    """Return ONLY whitelisted metadata.  Never include sovereign data."""
    meta: dict[str, Any] = {"latency_ms": latency_ms}

    # token counts + model from anthropic Message
    input_tokens: "int | None" = None
    output_tokens: "int | None" = None
    model: "str | None" = None
    if hasattr(result, "usage") and result.usage is not None:
        usage = result.usage
        input_tokens = getattr(usage, "input_tokens", None)
        output_tokens = getattr(usage, "output_tokens", None)
    if hasattr(result, "model"):
        model = result.model

    meta["input_tokens"] = input_tokens
    meta["output_tokens"] = output_tokens
    meta["model"] = model

    # tier + confidence from dict return value
    tier: "str | None" = None
    confidence: "float | None" = None
    if isinstance(result, dict):
        tier = result.get("tier")
        confidence = result.get("confidence")
    meta["tier"] = tier
    meta["confidence"] = confidence

    # sender_domain - domain portion only, never full address
    sender_domain: "str | None" = None
    email_addr = kwargs.get("email_addr")
    if isinstance(email_addr, str) and "@" in email_addr:
        sender_domain = email_addr.split("@", 1)[1]
    meta["sender_domain"] = sender_domain

    # subject_length - length only, never the text
    subject_length: "int | None" = None
    language: "str | None" = None
    subject = kwargs.get("subject")
    if isinstance(subject, str):
        subject_length = len(subject)
        if subject:
            language = "ru" if _is_cyrillic(subject[0]) else "en"
    meta["subject_length"] = subject_length
    meta["language"] = language

    return meta


# ---------------------------------------------------------------------------
# Debug trace writer
# ---------------------------------------------------------------------------

# `_workspace_root()` lived here and had exactly one caller, `_debug_trace_path`,
# which used it to put raw e-mail bodies in the ENGINE tree. Removed with that
# call rather than left as a convenience the next writer would reach for.


def _private_temp_trace_path(reason: str) -> Path:
    """The refusal path: a private temp file, plus one line saying why.

    A reporting helper must not raise. `require_writable_data_root()` is the
    right shape for an archiver, whose only honest answer is to stop; here the
    caller is a decorator wrapped around live e-mail handling, so raising would
    take the traced call down with the trace. Refusing the LOCATION and saying
    so on stderr keeps both properties: nothing lands in the engine, and the
    daemon keeps running.
    """
    print(f"[observability_safe] {reason}; the debug trace would land in the "
          f"engine tree, so it is written to a private temp file instead.",
          file=sys.stderr)
    return Path(tempfile.gettempdir()) / "heading-os-debug-trace.jsonl"


def _debug_trace_path() -> Path:
    """Where the full debug payload lands. The DATA overlay, never the engine.

    This file holds RAW args, kwargs and return values - e-mail bodies, subjects
    and sender addresses. The fallback used to be the ENGINE workspace root, and
    `state/` is neither gitignored nor routed private: `get_routing_destination
    ("state/email-triage/debug-trace.jsonl")` answers `engine`, the PUBLIC repo.
    Measured 2026-08-25 with `INBOX_PULSE_STATE_DIR` unset.

    This is the same defect already found and fixed in
    `scripts/inbox_pulse/cost.py`, whose own note records the spend ledger
    landing in the code repository for exactly this reason. So the resolver is
    ASKED FOR rather than reimplemented a third time; the module falls back to
    its own root only when the seam cannot be reached at all, and says so.

    Asking the resolver is necessary and was not sufficient. `get_state_dir()`
    builds on `get_data_root()`, whose documented last resort is
    `<workspace_root>/examples` - INSIDE the engine clone. So on any clone with
    no private overlay (CI, and every public checkout) the canonical resolver
    answered `<engine>/examples/state/email-triage/debug-trace.jsonl`, and
    `get_state_dir()` created that directory on the way. Measured 2026-08-26 in
    a sibling-less worktree; it is why this file's own guard has been red since
    2026-08-22.

    `data_overlay_present()`, not `data_root_is_demo()`, is the question asked:
    the demo root is one of two roots that sit inside the clone, the other being
    the transitional in-tree layout where the data root IS the workspace root.
    Both must refuse, and only the wider predicate answers False for both.
    """
    state_dir = os.environ.get("INBOX_PULSE_STATE_DIR")
    if state_dir:
        return Path(state_dir) / "debug-trace.jsonl"
    try:
        from scripts.inbox_pulse.paths import get_state_dir
        from scripts.utils.paths import data_overlay_present
    except Exception as exc:  # noqa: BLE001 - tracing must not break the caller
        return _private_temp_trace_path(
            f"state dir unresolved ({type(exc).__name__}: {exc})")
    try:
        overlay = data_overlay_present()
    except Exception as exc:  # noqa: BLE001 - a set-but-missing HEADING_OS_DATA
        return _private_temp_trace_path(
            f"the data root could not be resolved ({type(exc).__name__}: {exc})")
    if not overlay:
        return _private_temp_trace_path(
            "no private data overlay backs this workspace, so the canonical "
            "state dir resolves inside the engine clone")
    return get_state_dir() / "debug-trace.jsonl"


def _write_debug_trace(
    func_name: str,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
    result: Any,
    latency_ms: float,
) -> None:
    trace_path = _debug_trace_path()
    trace_path.parent.mkdir(parents=True, exist_ok=True)

    def _safe_serialize(v: Any) -> Any:
        try:
            json.dumps(v)
            return v
        except (TypeError, ValueError):
            return repr(v)

    payload = {
        "func": func_name,
        "latency_ms": latency_ms,
        "args": [_safe_serialize(a) for a in args],
        "kwargs": {k: _safe_serialize(v) for k, v in kwargs.items()},
        "result": _safe_serialize(result),
    }
    with trace_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload) + "\n")


# ---------------------------------------------------------------------------
# Main export
# ---------------------------------------------------------------------------

def observe_metadata_only(name: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorator factory.  Wraps a function with sovereignty-safe Langfuse tracing.

    Args:
        name: Logical trace name sent to Langfuse (never includes sovereign data).

    Returns a decorator that, when applied to a function:
    - Calls the function normally and returns its value unchanged.
    - Records a Langfuse trace with whitelisted metadata only.
    - Never passes function arguments or return values to Langfuse.
    - Writes a full debug trace to disk when INBOX_PULSE_DEBUG_TRACE=true.
    """

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            debug_mode = os.environ.get("INBOX_PULSE_DEBUG_TRACE", "").strip().lower() == "true"
            enabled = _is_enabled()

            if not enabled:
                # Tracing disabled - just call through
                t0 = time.monotonic()
                result = func(*args, **kwargs)
                latency_ms = (time.monotonic() - t0) * 1000.0
                if debug_mode:
                    _write_debug_trace(name, args, kwargs, result, latency_ms)
                return result

            real_observe = _get_langfuse_observe()

            if real_observe is None:
                # langfuse unavailable - call through
                t0 = time.monotonic()
                result = func(*args, **kwargs)
                latency_ms = (time.monotonic() - t0) * 1000.0
                if debug_mode:
                    _write_debug_trace(name, args, kwargs, result, latency_ms)
                return result

            # Wrap the inner call with langfuse observe (no arg/output capture).
            # Metadata injection happens INSIDE _traced_call so the span is
            # still active when update_current_span is called.
            @real_observe(name=name, capture_input=False, capture_output=False)
            def _traced_call(*a: Any, **kw: Any) -> Any:
                t_inner = time.monotonic()
                r = func(*a, **kw)
                elapsed_ms = (time.monotonic() - t_inner) * 1000.0
                meta = _extract_metadata(kw, r, elapsed_ms)
                try:
                    client = _get_langfuse_client()
                    if client is not None:
                        client.update_current_span(metadata=meta)
                except Exception as exc:
                    # Best-effort: never crash the caller because tracing stuttered
                    logging.getLogger("scripts.utils.observability_safe").debug(
                        "span metadata update failed: %s", exc)
                return r

            t0 = time.monotonic()
            result = _traced_call(*args, **kwargs)
            latency_ms = (time.monotonic() - t0) * 1000.0

            if debug_mode:
                _write_debug_trace(name, args, kwargs, result, latency_ms)

            return result

        return wrapper

    return decorator
