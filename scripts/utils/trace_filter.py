"""Logging integration for the X31C trace ID (R12).

Two mechanisms, use either or both:

1. ``install_log_factory()`` - the primary, defensive path. Installs a
   ``logging.setLogRecordFactory`` wrapper (idempotent) so EVERY ``LogRecord``
   gains a ``trace_id`` attribute, read from the environment at record-creation
   time, defaulting to ``"-"``. Once installed, a formatter can reference
   ``%(trace_id)s`` without a per-handler filter and never raise ``KeyError``
   on records emitted by third-party loggers.

2. ``TraceFilter`` - a ``logging.Filter`` for callers that prefer the explicit
   per-logger form. Sets ``record.trace_id`` on records passing through.

``attach(logger, fmt=...)`` is a convenience that installs the factory, adds a
``TraceFilter`` to ``logger``, and applies a formatter to its handlers.

See ``scripts.utils.trace`` for minting and ``.claude/rules/trace-id.md`` for
the convention.
"""
from __future__ import annotations

import logging

from scripts.utils import trace

# Standard log line format with the trace ID bracketed before the message.
DEFAULT_FORMAT = "%(asctime)s %(levelname)s [%(trace_id)s] %(message)s"

_factory_installed = False


class TraceFilter(logging.Filter):
    """Inject the current trace ID onto each record as ``trace_id``."""

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003
        record.trace_id = trace.get() or "-"
        return True


def install_log_factory() -> None:
    """Install a record factory that defaults ``trace_id`` on every record.

    Idempotent: safe to call from every daemon/script entry. Wraps whatever
    factory is currently installed so we compose with other custom factories.
    The ID is read at record-creation time, so it reflects the value set by
    ``trace.mint()`` for the life of the process.
    """
    global _factory_installed
    if _factory_installed:
        return
    existing = logging.getLogRecordFactory()

    def _factory(*args, **kwargs):
        record = existing(*args, **kwargs)
        # Only set when absent so an explicit TraceFilter or adapter can win.
        if not hasattr(record, "trace_id"):
            record.trace_id = trace.get() or "-"
        return record

    logging.setLogRecordFactory(_factory)
    _factory_installed = True


def attach(logger: logging.Logger, fmt: str = DEFAULT_FORMAT) -> logging.Logger:
    """Install the factory, add a TraceFilter to ``logger``, and set ``fmt`` on
    its handlers. Convenience for new CLI scripts that build a single logger.
    """
    install_log_factory()
    logger.addFilter(TraceFilter())
    formatter = logging.Formatter(fmt)
    for handler in logger.handlers:
        handler.setFormatter(formatter)
    return logger
