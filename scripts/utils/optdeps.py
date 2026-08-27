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


def _is_empty_namespace(mod_or_spec) -> bool:
    """True when this is a PEP-420 namespace package holding NOTHING.

    An empty directory anywhere on ``sys.path`` IS an importable module under
    PEP 420. It has no ``__file__``, no ``__init__.py`` and no attributes, and
    both checks below used to accept it: ``find_spec`` returns a real spec, and
    ``import_module`` returns a real module object. Measured 2026-08-27 - a bare
    directory named `fakedep` made `available()` answer True and `require()`
    hand its caller a module whose every attribute access raises AttributeError.
    That is the exact outcome this file's docstring exists to prevent: a stack
    trace from deep inside the caller instead of the actionable "run `uv sync
    --extra email`" line.

    EMPTINESS is the test, and refusing every namespace package was the first
    fix written here. The SUITE refuted it within one run: `google` is a
    legitimate namespace package - the whole point of the shape is that
    `google.auth`, `google.oauth2` and `google.protobuf` ship as separate
    distributions into one directory - and `gmail_auth.get_service()` calls
    `require("google", ...)`. Refusing it killed Gmail authentication outright.

    Measured in this venv the same day: `google` has 12 entries under its search
    location, and every other optional dependency (`exchangelib`, `telethon`,
    `playwright`, `langfuse`, `yaml`) is a regular package with an origin. The
    phantom directory has 0. That is the whole separation.

    The limit, stated rather than papered over: this distinguishes "a directory
    that happens to share the name" from "a namespace real distributions
    populate". It cannot tell a namespace populated by the WRONG distribution
    from the right one, and nothing at this layer could.
    """
    if getattr(mod_or_spec, "origin", None) is not None:
        return False
    locations = getattr(mod_or_spec, "submodule_search_locations", None)
    if locations is None:
        # No origin and no search locations: not a package at all. Erring toward
        # "available" here is deliberate - refusing something that might work is
        # the worse mistake for a probe whose answer gates a capability.
        return False
    import os

    for loc in list(locations):
        try:
            if os.listdir(loc):
                return False
        except OSError:
            # Unreadable is not evidence of emptiness.
            return False
    return True


def require(module: str, extra: str, json_error: bool = True):
    """Import ``module`` or exit 1 with a uniform, actionable message.

    Called from inside a function (never at module scope), so a missing extra
    surfaces only when the capability is actually exercised - import stays pure.

    An EMPTY PEP-420 namespace package counts as MISSING; see :func:`_is_empty_namespace`.
    """
    try:
        mod = importlib.import_module(module)
    except ImportError:
        mod = None
    if mod is None or _is_empty_namespace(getattr(mod, "__spec__", None) or mod):
        msg = f"{module} not installed; this capability needs: uv sync --extra {extra}"
        print(json.dumps({"error": msg}) if json_error else f"[ERROR] {msg}", file=sys.stderr)
        raise SystemExit(1) from None
    return mod


def available(module: str) -> bool:
    """True if ``module`` can be imported, without importing it (cheap probe).

    An EMPTY PEP-420 namespace package answers False; see :func:`_is_empty_namespace`.
    """
    try:
        spec = importlib.util.find_spec(module)
    except (ImportError, ValueError):
        # `find_spec` raises rather than returning None when a PARENT package is
        # missing or when a module was imported and then broken. A probe whose
        # whole purpose is "can I use this" must answer, not raise.
        return False
    return spec is not None and not _is_empty_namespace(spec)
