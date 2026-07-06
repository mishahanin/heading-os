"""Loud, attributed optional-dependency loading (F-2.1 + F-7.1).

Absence of a heavy, capability-scoped dependency is a user-actionable message,
never a stack trace, and never an import-time ``SystemExit`` that would kill
pytest collection on a fresh clone. Every module that needs one of the optional
integration packages (exchangelib, Telethon, playwright, weasyprint, yt-dlp, ...)
imports it lazily through :func:`require`, so the module itself imports pure.

    from scripts.utils.optdeps import require

    def main() -> int:
        exchangelib = require("exchangelib", extra="email")
        ...

The ``extra`` argument names the pyproject optional-dependencies group that
supplies the package (F-7.1), so the error tells the operator exactly what to
install: ``uv sync --extra email``.
"""
from __future__ import annotations

import importlib
import importlib.util
import json
import sys


def require(module: str, extra: str, json_error: bool = True):
    """Import ``module`` or exit 1 with a uniform, actionable message.

    Called from inside a function (never at module scope), so a missing extra
    surfaces only when the capability is actually exercised - import stays pure.
    """
    try:
        return importlib.import_module(module)
    except ImportError:
        msg = f"{module} not installed; this capability needs: uv sync --extra {extra}"
        print(json.dumps({"error": msg}) if json_error else f"[ERROR] {msg}", file=sys.stderr)
        raise SystemExit(1) from None


def available(module: str) -> bool:
    """True if ``module`` can be imported, without importing it (cheap probe)."""
    return importlib.util.find_spec(module) is not None
