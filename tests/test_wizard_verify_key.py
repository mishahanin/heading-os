"""Unit tests for scripts/wizard-verify-key.py.

This is the one module in the wizard that talks to a provider, and every test
below keeps the network out by monkeypatching `urlopen` in its own body. That is
per-test discipline, and per-test discipline is what a future test forgets.

Two things sit behind that forgetting, which is why the ban below is a fixture
rather than a convention. `verify_anthropic` resolves its model through
`claude_models.latest`, which reads `ANTHROPIC_API_KEY` out of the operator's
`.env` when the on-disk model cache is stale - and MEASURED 2026-09-01 that
cache WAS stale by 33 hours on the operator's machine, so `fetch_from_api()` is
reached on the ordinary path. A test that patched `urlopen` for the ping but not
for the resolver would put the operator's live key on the wire.

The whole file was measured that day for outbound connections, with a probe
installed through `sitecustomize.py` on `PYTHONPATH` so child processes inherit
it too (an in-process probe cannot see the two subprocess tests): zero connects,
in this process and in both children. The fixture pins that result.
"""
import socket
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).parent.parent


@pytest.fixture(autouse=True)
def _no_sockets(monkeypatch):
    """Any outbound connect from this file's own process is a test defect."""

    def refuse(self, address):
        raise AssertionError(
            f"a unit test opened a socket to {address!r}. Nothing here may "
            f"reach a provider: patch urlopen, or stub claude_models.latest.")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)


def test_the_socket_ban_is_armed():
    """The control. Without it the ban is a fixture nobody has seen fire, and a
    typo in the patch target would leave every test below unprotected."""
    with pytest.raises(AssertionError, match="opened a socket"):
        socket.create_connection(("127.0.0.1", 9), timeout=0.1)


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
    """Without WIZARD_PING_MODEL, the ping resolves PING_FAMILY and sends it.

    Asserts the family, not a release: the whole point of the 2026-08-09 change
    is that this script keeps working when the model it used to name is retired.

    `claude_models.latest` is stubbed rather than left to run, and that is the
    fix for a real flake. `wizard-verify-key` and `claude_models` share one
    `urllib.request` module object, so patching urlopen here also patches the
    model-list fetch. Whether that fetch happens at all depends on a memo and an
    on-disk cache TTL: warm, `latest()` returns immediately and the test passes;
    expired, it issues a GET into this stub, whose `req.data` is None, and the
    test dies on an AttributeError that says nothing about the wizard. It blocked
    a push on 2026-08-12 for exactly that reason, under `-n auto` where each
    worker starts with a cold memo. A test must not resolve on a cache clock.
    """
    import json as _json
    mod = _load_verify()
    captured = {}
    asked = {}

    class FakeResp:
        status = 200
        def read(self): return b'{"id":"test"}'
        def __enter__(self): return self
        def __exit__(self, *a): pass

    def fake_urlopen(req, timeout):
        captured["body"] = _json.loads(req.data.decode("utf-8"))
        return FakeResp()

    def fake_latest(family, **kw):
        asked["family"] = family
        return f"claude-{family}-9-9-test-fixture"

    monkeypatch.setattr(mod.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(mod.claude_models, "latest", fake_latest)
    monkeypatch.delenv("WIZARD_PING_MODEL", raising=False)

    mod.verify_anthropic("TEST-FIXTURE-KEY")

    assert asked["family"] == mod.PING_FAMILY, "the ping did not resolve PING_FAMILY"
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


# ============================================================
# Exit code 3 says what it means, and it is not "the network failed"
# ============================================================
# Audit campaign 2026-08-24, shard `scripts-15-p4` finding 6, verified still
# present and fixed 2026-09-02. The module docstring's only caller-facing
# contract said `3 = network/timeout`, while `main` returns 3 for every
# `unknown` status - including an HTTP status the script does not classify and a
# key the request header refuses. The wizard therefore told the operator his
# connection had failed on runs where the server had demonstrably answered, and
# the docstring itself stresses that the caller "reads the code and proceeds".


def _codes_module():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "wizard_verify_key_codes", REPO / "scripts" / "wizard-verify-key.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _exit_code_paragraph(mod) -> str:
    """The docstring's exit-code block, up to the blank line after code 4."""
    doc = mod.__doc__ or ""
    assert "Exit codes:" in doc, "the exit-code contract left the docstring"
    return doc.split("Exit codes:", 1)[1]


def test_a_server_answer_the_script_cannot_classify_is_not_called_a_network_failure(
        monkeypatch):
    """A 500 from the API is a completed exchange, and it exits 3.

    So 3 cannot be documented as "network/timeout": that reading is false on
    exactly this run, and it is the reading the wizard shows the operator.
    """
    import urllib.error
    mod = _codes_module()
    monkeypatch.setattr(mod.claude_models, "latest", lambda family: "test-model")

    def five_hundred(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 500, "Server Error", {}, None)

    monkeypatch.setattr(mod.urllib.request, "urlopen", five_hundred)

    status, message = mod.verify_anthropic("TEST-FIXTURE-KEY")
    assert status == "unknown"
    assert "HTTP 500" in message

    monkeypatch.setenv("WIZARD_VERIFY_KEY", "sk-fixture-value")
    assert mod.main(["--provider", "anthropic"]) == 3, (
        "the server answered and the run still exits 3, which is why 3 cannot "
        "be documented as a network failure")

    # The DEFINITION line only. The paragraph goes on to record what the line
    # used to say, and a substring search over the whole block would match that
    # history and fail a correct docstring.
    definition = _exit_code_paragraph(mod).split("3 =", 1)[1].split("\n")[0]
    assert "network/timeout" not in definition, (
        f"code 3 is still defined as network/timeout: {definition!r}")


def test_the_documented_meaning_of_three_covers_every_path_that_returns_it():
    """The three unknown paths are each named where the operator reads them.

    An unclassified HTTP status, a header the key cannot go into, and the actual
    network failure all land on 3. Naming only the third is what made the
    message wrong; naming all three is what keeps it honest when a fourth path
    is added.
    """
    mod = _codes_module()
    paragraph = _exit_code_paragraph(mod).split("4 =")[0].lower()
    for phrase in ("http", "header", "network"):
        assert phrase in paragraph, (
            f"the docstring for exit code 3 never mentions {phrase!r}, and a "
            f"path that returns 3 goes through it: {paragraph!r}")


def test_the_docstring_admits_that_main_both_returns_and_exits():
    """Two refusal paths, two mechanisms, one number.

    `main` returns 4 for a key holding a control character and `_Parser.error`
    calls `sys.exit(4)` for a usage error. The suite asserts both shapes, which
    is correct and was read as a contradiction by an audit that could not see
    the script. An in-process caller has to be ready for `SystemExit`, so the
    docstring says so.
    """
    mod = _codes_module()
    doc = mod.__doc__ or ""
    assert "SystemExit" in doc, (
        "nothing warns an in-process caller of main() that a usage error "
        "raises rather than returns")


def test_a_usage_error_raises_while_a_control_character_returns(monkeypatch):
    """The behaviour the docstring now describes, measured on both paths."""
    mod = _codes_module()
    monkeypatch.setenv("WIZARD_VERIFY_KEY", "sk-fixture\x01value")
    assert mod.main(["--provider", "anthropic"]) == 4

    monkeypatch.delenv("WIZARD_VERIFY_KEY", raising=False)
    with pytest.raises(SystemExit) as exc:
        mod.main(["--provider", "anthropic"])
    assert exc.value.code == 4
