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
    """WIZARD_PING_MODEL env var overrides the resolved family in the POST body."""
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


def test_verify_anthropic_uses_the_resolved_family_when_env_unset(monkeypatch):
    """Without WIZARD_PING_MODEL, the ping resolves PING_FAMILY to a live model.

    Asserts the family, not a release: the whole point of the 2026-08-09 change
    is that this script keeps working when the model it used to name is retired.
    """
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
    sent = captured["body"]["model"]
    assert sent.startswith(f"claude-{mod.PING_FAMILY}-"), sent


def test_the_wizard_import_chain_stays_stdlib_only():
    """The setup wizard must run on a fresh clone, before `uv sync` installs anything.

    `wizard-verify-key.py` imports `scripts.utils.claude_models`, which reaches
    `scripts.utils.workspace` and `scripts.utils.paths`. Both keep their
    non-stdlib imports lazy today (`yaml` at workspace.py:435, a migrations
    import at paths.py:196). Either one promoted to module level would strand the
    wizard on an interpreter without the dependencies, silently. The comment in
    the script says so; this asserts it.
    """
    out = subprocess.run(
        [sys.executable, "-c",
         "import sys; sys.path.insert(0, '.');"
         "import scripts.utils.claude_models;"
         "print([n for n in sys.modules if n in ('yaml', 'requests', 'anthropic', 'numpy')])"],
        capture_output=True, text=True, cwd=str(REPO),
    )
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "[]", (
        f"a third-party module is now imported at module level in the wizard's "
        f"chain: {out.stdout.strip()}. The setup wizard runs before dependencies "
        f"are installed.")
