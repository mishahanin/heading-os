"""Unit tests for scripts/wizard-verify-key.py."""
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).parent.parent


def _load_verify():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "verify_mod", REPO / "scripts" / "wizard-verify-key.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_verify_key_script_help_works():
    result = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "wizard-verify-key.py"), "--help"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0
    assert "--provider" in result.stdout
    assert "--key" in result.stdout


def test_verify_key_anthropic_success(monkeypatch):
    mod = _load_verify()

    class FakeResp:
        status = 200
        def read(self): return b'{"id":"test"}'
        def __enter__(self): return self
        def __exit__(self, *a): pass

    monkeypatch.setattr(mod.urllib.request, "urlopen", lambda *a, **kw: FakeResp())
    status, msg = mod.verify_anthropic("TEST-FIXTURE-OK")
    assert status == "ok"


def test_verify_key_anthropic_unauthorized(monkeypatch):
    import urllib.error
    mod = _load_verify()

    def raise_401(*a, **kw):
        raise urllib.error.HTTPError("url", 401, "Unauthorized", {}, None)

    monkeypatch.setattr(mod.urllib.request, "urlopen", raise_401)
    status, msg = mod.verify_anthropic("TEST-FIXTURE-BAD")
    assert status == "invalid"


def test_verify_key_anthropic_timeout(monkeypatch):
    import socket
    mod = _load_verify()

    def raise_timeout(*a, **kw):
        raise socket.timeout("timed out")

    monkeypatch.setattr(mod.urllib.request, "urlopen", raise_timeout)
    status, msg = mod.verify_anthropic("TEST-FIXTURE-ANY")
    assert status == "unknown"


def test_verify_anthropic_uses_env_model_override(monkeypatch):
    """WIZARD_PING_MODEL env var overrides DEFAULT_PING_MODEL in the POST body."""
    import json as _json
    mod = _load_verify()
    captured = {}

    class FakeResp:
        status = 200
        def read(self): return b'{"id":"test"}'
        def __enter__(self): return self
        def __exit__(self, *a): pass

    def fake_urlopen(req, timeout):
        captured["body"] = _json.loads(req.data.decode("utf-8"))
        return FakeResp()

    monkeypatch.setattr(mod.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setenv("WIZARD_PING_MODEL", "claude-sonnet-X-Y-test-fixture")
    mod.verify_anthropic("TEST-FIXTURE-KEY")
    assert captured["body"]["model"] == "claude-sonnet-X-Y-test-fixture"


def test_verify_anthropic_uses_default_model_when_env_unset(monkeypatch):
    """Without WIZARD_PING_MODEL, requests use DEFAULT_PING_MODEL."""
    import json as _json
    mod = _load_verify()
    captured = {}

    class FakeResp:
        status = 200
        def read(self): return b'{"id":"test"}'
        def __enter__(self): return self
        def __exit__(self, *a): pass

    def fake_urlopen(req, timeout):
        captured["body"] = _json.loads(req.data.decode("utf-8"))
        return FakeResp()

    monkeypatch.setattr(mod.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.delenv("WIZARD_PING_MODEL", raising=False)
    mod.verify_anthropic("TEST-FIXTURE-KEY")
    assert captured["body"]["model"] == mod.DEFAULT_PING_MODEL
