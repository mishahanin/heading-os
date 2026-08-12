"""A failed mail fetch must say why, and must not claim the data is fresh.

Measured 2026-08-12: the bridge log carried two lines reading
`bridge.email: producer exited 2; stderr=` and nothing else. Exit 2 is the
producer's `exchange_unreachable` path, which writes a JSON diagnostic
carrying the detail and a pointer to the WSL/CGNAT thread -- to STDOUT. The
refresher logged only stderr, so the one useful sentence was captured and
discarded, leaving an operator with a bare exit code.

The same call then bumped `inbox` unconditionally, advancing the dashboard's
data_time, so an inbox that had not been fetched read as refreshed seconds ago.
Governed by .claude/rules/scope-claims.md.
"""
from __future__ import annotations

import json
import logging
import subprocess

import pytest

from scripts.bridge_daemon import state as state_mod
from scripts.bridge_daemon.refreshers import mail

UNREACHABLE = json.dumps({
    "error": "exchange_unreachable",
    "detail": "TransportError: connection timed out",
    "hint": "WSL->Exchange (mail.31c.io) on CGNAT not routed",
})


class _Result:
    def __init__(self, returncode, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


@pytest.fixture()
def st():
    return state_mod.State()


def _run(monkeypatch, tmp_path, st, result):
    monkeypatch.setattr(mail, "PRODUCER_SCRIPT", tmp_path / "producer.py")
    (tmp_path / "producer.py").write_text("", encoding="utf-8")
    monkeypatch.setattr(mail.subprocess, "run", lambda *a, **k: result)
    mail.refresh(tmp_path, st)


def test_the_producers_stdout_diagnostic_reaches_the_log(monkeypatch, tmp_path, st, caplog):
    with caplog.at_level(logging.WARNING):
        _run(monkeypatch, tmp_path, st, _Result(2, stdout=UNREACHABLE))
    text = caplog.text
    assert "exchange_unreachable" in text, f"reason discarded: {text}"
    assert "CGNAT" in text or "connection timed out" in text, text


def test_a_failed_fetch_does_not_advance_the_freshness_clock(monkeypatch, tmp_path, st):
    st.bump("inbox")
    fresh_before = st.data_time("inbox")
    version_before = st.version("inbox")

    _run(monkeypatch, tmp_path, st, _Result(2, stdout=UNREACHABLE))

    assert st.data_time("inbox") == fresh_before, "stale inbox reported as freshly fetched"
    assert st.version("inbox") > version_before, "ETag must still advance so the UI re-reads"


def test_a_successful_fetch_does_advance_the_freshness_clock(monkeypatch, tmp_path, st):
    st.bump("inbox")
    fresh_before = st.data_time("inbox")
    _run(monkeypatch, tmp_path, st, _Result(0, stdout="{}"))
    assert st.data_time("inbox") != fresh_before


def test_a_timeout_is_also_reported_as_not_fresh(monkeypatch, tmp_path, st):
    st.bump("inbox")
    fresh_before = st.data_time("inbox")

    def _timeout(*a, **k):
        raise subprocess.TimeoutExpired(cmd="producer", timeout=300)

    monkeypatch.setattr(mail, "PRODUCER_SCRIPT", tmp_path / "producer.py")
    (tmp_path / "producer.py").write_text("", encoding="utf-8")
    monkeypatch.setattr(mail.subprocess, "run", _timeout)
    mail.refresh(tmp_path, st)

    assert st.data_time("inbox") == fresh_before


def test_a_missing_producer_script_is_not_fresh_either(tmp_path, st, monkeypatch):
    st.bump("inbox")
    fresh_before = st.data_time("inbox")
    monkeypatch.setattr(mail, "PRODUCER_SCRIPT", tmp_path / "absent.py")
    mail.refresh(tmp_path, st)
    assert st.data_time("inbox") == fresh_before


def test_bump_defaults_to_fresh_for_every_existing_caller(st):
    """26 call sites pass no keyword; none of them may change behaviour."""
    st.bump("tasks")
    assert st.data_time("tasks") is not None
