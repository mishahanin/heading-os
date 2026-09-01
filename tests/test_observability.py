"""Tests for the QW4 "loud degradation" behaviour in scripts.utils.observability.

A silent no-op when observability is *enabled* but cannot actually deliver
traces (langfuse unimportable, or credentials absent) is itself a reliability
defect. These assert the wrapper now emits exactly one WARNING per process in
that case, and stays quiet on intentional disables (sensitive session,
LANGFUSE_ENABLED=false) and when fully functional.

Observability is fail-closed (Plan 5): tracing is enabled only when sensitivity
is explicitly cleared (``SENSITIVE_MODE=off``) AND ``LANGFUSE_ENABLED`` is on.
"""

import logging

import pytest

from scripts.utils import observability as obs


@pytest.fixture(autouse=True)
def _reset_warned():
    obs._degraded_warned = False
    yield
    obs._degraded_warned = False


def test_warns_once_when_langfuse_unimportable(monkeypatch, caplog):
    monkeypatch.setenv("SENSITIVE_MODE", "off")
    monkeypatch.setenv("LANGFUSE_ENABLED", "true")
    monkeypatch.setattr(obs, "_get_real_observe", lambda: None)
    with caplog.at_level(logging.WARNING, logger="scripts.utils.observability"):

        @obs.observe()
        def f():
            return 1

        @obs.observe()
        def g():
            return 2

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1, "must warn exactly once per process, not per decoration"
    assert "degraded" in warnings[0].getMessage()
    # The decorator still degrades to a working no-op pass-through.
    assert f() == 1 and g() == 2


def test_warns_when_enabled_but_credentials_missing(monkeypatch, caplog):
    monkeypatch.setenv("SENSITIVE_MODE", "off")
    monkeypatch.setenv("LANGFUSE_ENABLED", "true")
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    monkeypatch.setattr(obs, "_get_real_observe", lambda: (lambda fn: fn))
    with caplog.at_level(logging.WARNING, logger="scripts.utils.observability"):

        @obs.observe()
        def f():
            return 1

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "not set" in warnings[0].getMessage()


def test_no_warn_when_explicitly_disabled(monkeypatch, caplog):
    monkeypatch.setenv("SENSITIVE_MODE", "off")
    monkeypatch.setenv("LANGFUSE_ENABLED", "false")
    with caplog.at_level(logging.WARNING, logger="scripts.utils.observability"):

        @obs.observe()
        def f():
            return 1

    assert [r for r in caplog.records if r.levelno == logging.WARNING] == []
    assert f() == 1


@pytest.mark.parametrize("value", ["false", "FALSE", "0", "no", "off", "", "  Off  "])
def test_every_documented_off_token_disables(monkeypatch, value):
    """The module docstring lists `false` / `0` / `no` (case-insensitive) and the
    code also refuses `off` and the empty string. Only `false` was ever asserted,
    so narrowing the tuple to `("false",)` alone left the whole file green while
    `LANGFUSE_ENABLED=off` silently started shipping traces.
    """
    monkeypatch.setenv("SENSITIVE_MODE", "off")
    monkeypatch.setenv("LANGFUSE_ENABLED", value)
    assert obs.is_enabled() is False, value


@pytest.mark.parametrize("value", ["true", "1", "yes", "on", "anything-else"])
def test_anything_else_leaves_it_enabled(monkeypatch, value):
    """The other direction, so the test above cannot be satisfied by an
    `is_enabled` that always returns False."""
    monkeypatch.setenv("SENSITIVE_MODE", "off")
    monkeypatch.setenv("LANGFUSE_ENABLED", value)
    assert obs.is_enabled() is True, value


def test_the_bare_decorator_form_degrades_to_the_function_itself(monkeypatch, caplog):
    """`@observe` written without parentheses.

    Every existing case uses `@obs.observe()`, which reaches `_noop_decorator`
    with an EMPTY `dargs` and so never executes its `return dargs[0]` branch.
    That branch could be replaced by `return lambda *a, **k: None` -- silently
    turning every bare-decorated function into a no-op that returns None -- with
    the whole file green.
    """
    monkeypatch.setenv("SENSITIVE_MODE", "off")
    monkeypatch.setenv("LANGFUSE_ENABLED", "false")

    @obs.observe
    def f(a, b=2):
        return a + b

    assert f(1) == 3
    assert f.__name__ == "f"


def test_no_warn_when_sensitive(monkeypatch, caplog):
    # Sensitive content must never traverse observability AND must not announce
    # its own state via a warning. Intentional (fail-closed) disable -> silent.
    # SENSITIVE_MODE unset => sensitive => suppressed.
    monkeypatch.delenv("SENSITIVE_MODE", raising=False)
    monkeypatch.setenv("LANGFUSE_ENABLED", "true")
    with caplog.at_level(logging.WARNING, logger="scripts.utils.observability"):

        @obs.observe()
        def f():
            return 1

    assert [r for r in caplog.records if r.levelno == logging.WARNING] == []
    assert not obs.is_enabled()


def test_no_warn_when_fully_functional(monkeypatch, caplog):
    monkeypatch.setenv("SENSITIVE_MODE", "off")
    monkeypatch.setenv("LANGFUSE_ENABLED", "true")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-test")
    monkeypatch.setattr(obs, "_get_real_observe", lambda: (lambda fn: fn))
    with caplog.at_level(logging.WARNING, logger="scripts.utils.observability"):

        @obs.observe()
        def f():
            return 1

    assert [r for r in caplog.records if r.levelno == logging.WARNING] == []
    assert f() == 1
