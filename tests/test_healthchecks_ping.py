"""Unit tests for scripts/utils/healthchecks.py."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils import healthchecks  # noqa: E402


def test_ping_success_returns_true(monkeypatch):
    monkeypatch.setenv("FIRESIDE_HC_POLL", "https://hc-ping.com/abc")
    with patch("scripts.utils.healthchecks.requests.get") as mock_get:
        mock_get.return_value.status_code = 200
        assert healthchecks.ping("FIRESIDE_HC_POLL") is True
    mock_get.assert_called_once_with("https://hc-ping.com/abc", timeout=10)


def test_ping_missing_env_var_returns_false(monkeypatch):
    monkeypatch.delenv("FIRESIDE_HC_POLL", raising=False)
    assert healthchecks.ping("FIRESIDE_HC_POLL") is False


def test_ping_timeout_returns_false_does_not_raise(monkeypatch):
    monkeypatch.setenv("FIRESIDE_HC_POLL", "https://hc-ping.com/abc")
    with patch(
        "scripts.utils.healthchecks.requests.get",
        side_effect=requests.exceptions.Timeout("timed out"),
    ):
        assert healthchecks.ping("FIRESIDE_HC_POLL") is False


def test_ping_connection_error_returns_false(monkeypatch):
    monkeypatch.setenv("FIRESIDE_HC_POLL", "https://hc-ping.com/abc")
    with patch(
        "scripts.utils.healthchecks.requests.get",
        side_effect=requests.exceptions.ConnectionError("conn refused"),
    ):
        assert healthchecks.ping("FIRESIDE_HC_POLL") is False


def test_ping_http_500_returns_false(monkeypatch):
    """Non-200 (e.g. HC.io 5xx) must not be reported as a successful ping."""
    monkeypatch.setenv("FIRESIDE_HC_POLL", "https://hc-ping.com/abc")
    with patch("scripts.utils.healthchecks.requests.get") as mock_get:
        mock_get.return_value.status_code = 500
        assert healthchecks.ping("FIRESIDE_HC_POLL") is False


def test_ping_unexpected_exception_returns_false(monkeypatch):
    """Broad except Exception path: ValueError etc. must not propagate."""
    monkeypatch.setenv("FIRESIDE_HC_POLL", "https://hc-ping.com/abc")
    with patch(
        "scripts.utils.healthchecks.requests.get",
        side_effect=ValueError("unexpected"),
    ):
        assert healthchecks.ping("FIRESIDE_HC_POLL") is False
