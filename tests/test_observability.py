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
