"""F-9.2: DNS-rebinding / localhost-CSRF guard on the bridge daemon.

The daemon binds 127.0.0.1 and authed routes require a bearer token, but the
unauthenticated surface (/_bootstrap, /health) needs belt-and-suspenders: a
rebound external hostname resolving to loopback must be rejected (421), and a
hostile cross-origin page must be rejected (403). These tests exercise the
middleware through /health (unauthenticated) so the guard, not the token check,
is what they observe.
"""
import pytest
pytest.importorskip("fastapi")  # F-7.1: skip on a core-only clone (needs the dashboard extra)

from fastapi.testclient import TestClient

from scripts.bridge_daemon.app import build_app
from scripts.bridge_daemon.state import State


def _client(workspace_root, token="testtoken"):  # noqa: S107  test fixture default, not a real secret
    app = build_app(workspace_root=workspace_root, state=State(), token=token,
                    user_slug="misha", data_root=workspace_root)
    # Loopback base_url so the default Host header passes the guard; individual
    # tests override Host/Origin per request to exercise the reject paths.
    return TestClient(app, base_url="http://127.0.0.1")


def test_health_allows_loopback(workspace_root):
    r = _client(workspace_root).get("/health")
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_rejects_spoofed_host(workspace_root):
    # Guard against a false pass (L3): confirm the explicit Host header actually
    # reaches the middleware. If TestClient ignored it, this would 200, not 421.
    r = _client(workspace_root).get("/health", headers={"Host": "evil.example.com"})
    assert r.status_code == 421
    assert r.json()["detail"] == "host not allowed"


def test_rejects_cross_origin(workspace_root):
    r = _client(workspace_root).get("/health", headers={"Origin": "http://evil.example.com"})
    assert r.status_code == 403
    assert r.json()["detail"] == "cross-origin blocked"


def test_allows_same_origin(workspace_root):
    r = _client(workspace_root).get("/health", headers={"Origin": "http://127.0.0.1:8765"})
    assert r.status_code == 200


def test_allows_localhost_host(workspace_root):
    r = _client(workspace_root).get("/health", headers={"Host": "localhost:8765"})
    assert r.status_code == 200


def test_allows_ipv6_loopback_host(workspace_root):
    # L1: [::1]:port must normalize to ::1 and pass, not be rejected.
    r = _client(workspace_root).get("/health", headers={"Host": "[::1]:8765"})
    assert r.status_code == 200
