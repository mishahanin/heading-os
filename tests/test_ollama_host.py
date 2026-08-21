"""Tests for scripts/utils/ollama_host.py.

Locks the two properties the resolver exists for: `auto:<port>` follows the
CURRENT gateway (the address that changes on every WSL restart), and anything
unreachable degrades to the local daemon instead of failing the caller.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils import ollama_host  # noqa: E402
from scripts.utils.ollama_host import (  # noqa: E402
    LOCAL_HOST,
    candidate_url,
    read_default_gateway,
    resolve_ollama_host,
)

# /proc/net/route as the kernel writes it: gateway is little-endian hex.
# 01301EAC -> 172.30.48.1
ROUTE_TABLE = (
    "Iface\tDestination\tGateway\tFlags\tRefCnt\tUse\tMetric\tMask\tMTU\tWindow\tIRTT\n"
    "eth0\t00003CAC\t00000000\t0001\t0\t0\t0\t0000F0FF\t0\t0\t0\n"
    "eth0\t00000000\t01301EAC\t0003\t0\t0\t0\t00000000\t0\t0\t0\n"
)


def _route_file(tmp_path, content=ROUTE_TABLE):
    path = tmp_path / "route"
    path.write_text(content, encoding="utf-8")
    return str(path)


# --- gateway parsing ------------------------------------------------------

def test_reads_gateway_from_route_table(tmp_path):
    assert read_default_gateway(_route_file(tmp_path)) == "172.30.48.1"


def test_no_default_route_yields_none(tmp_path):
    only_link_local = (
        "Iface\tDestination\tGateway\tFlags\n"
        "eth0\t00003CAC\t00000000\t0001\n"
    )
    assert read_default_gateway(_route_file(tmp_path, only_link_local)) is None


def test_missing_route_file_yields_none(tmp_path):
    assert read_default_gateway(str(tmp_path / "absent")) is None


def test_malformed_gateway_is_skipped(tmp_path):
    garbage = (
        "Iface\tDestination\tGateway\tFlags\n"
        "eth0\t00000000\tZZZZZZZZ\t0003\n"
    )
    assert read_default_gateway(_route_file(tmp_path, garbage)) is None


# --- candidate resolution (address only, never probed) --------------------

def test_candidate_of_no_preference_is_none(monkeypatch):
    monkeypatch.setattr(ollama_host, "probe", lambda *a, **k: pytest.fail("must not probe"))
    assert candidate_url("") is None
    assert candidate_url(None) is None
    assert candidate_url("   ") is None


def test_candidate_of_auto_uses_the_current_gateway(monkeypatch):
    monkeypatch.setattr(ollama_host, "read_default_gateway", lambda *a, **k: "172.30.48.1")
    assert candidate_url("auto:11436") == "http://172.30.48.1:11436"


def test_candidate_of_auto_without_port_defaults_to_11434(monkeypatch):
    monkeypatch.setattr(ollama_host, "read_default_gateway", lambda *a, **k: "172.30.48.1")
    assert candidate_url("auto") == "http://172.30.48.1:11434"


def test_candidate_is_none_when_the_gateway_is_unreadable(monkeypatch):
    monkeypatch.setattr(ollama_host, "read_default_gateway", lambda *a, **k: None)
    assert candidate_url("auto:11436") is None


def test_candidate_keeps_a_literal_url_without_probing(monkeypatch):
    # The address is returned whether or not anything answers there. Probing is
    # the CALLER's job - a signal that needs to report "configured but down"
    # cannot tell those apart if the resolver folds them together.
    monkeypatch.setattr(ollama_host, "probe", lambda *a, **k: pytest.fail("must not probe"))
    assert candidate_url("http://10.0.0.5:11434/") == "http://10.0.0.5:11434"


def test_candidate_refuses_a_non_http_scheme():
    assert candidate_url("file:///etc/passwd") is None
    assert candidate_url("ftp://10.0.0.5:11434") is None


# --- resolution -----------------------------------------------------------

def test_empty_preference_returns_local_without_probing(monkeypatch):
    monkeypatch.delenv("HEADING_OS_OLLAMA_HOST", raising=False)
    monkeypatch.setattr(ollama_host, "probe", lambda *a, **k: pytest.fail("must not probe"))
    assert resolve_ollama_host() == LOCAL_HOST


def test_auto_builds_url_from_current_gateway(monkeypatch):
    monkeypatch.setattr(ollama_host, "read_default_gateway", lambda *a, **k: "172.30.48.1")
    monkeypatch.setattr(ollama_host, "probe", lambda host, **k: True)
    assert resolve_ollama_host("auto:11436") == "http://172.30.48.1:11436"


def test_auto_follows_a_changed_gateway(monkeypatch):
    # The whole point: after a WSL restart the address differs, and `auto`
    # must track it rather than return yesterday's literal.
    monkeypatch.setattr(ollama_host, "read_default_gateway", lambda *a, **k: "172.19.64.1")
    monkeypatch.setattr(ollama_host, "probe", lambda host, **k: True)
    assert resolve_ollama_host("auto:11436") == "http://172.19.64.1:11436"


def test_auto_without_port_defaults_to_11434(monkeypatch):
    monkeypatch.setattr(ollama_host, "read_default_gateway", lambda *a, **k: "172.30.48.1")
    monkeypatch.setattr(ollama_host, "probe", lambda host, **k: True)
    assert resolve_ollama_host("auto") == "http://172.30.48.1:11434"


def test_unreachable_auto_falls_back_to_local(monkeypatch, capsys):
    monkeypatch.setattr(ollama_host, "read_default_gateway", lambda *a, **k: "172.30.48.1")
    monkeypatch.setattr(ollama_host, "probe", lambda host, **k: False)
    assert resolve_ollama_host("auto:11436") == LOCAL_HOST
    assert "falling back" in capsys.readouterr().err


def test_unreadable_gateway_falls_back_to_local(monkeypatch, capsys):
    monkeypatch.setattr(ollama_host, "read_default_gateway", lambda *a, **k: None)
    assert resolve_ollama_host("auto:11436") == LOCAL_HOST
    assert "gateway" in capsys.readouterr().err


def test_literal_url_is_probed_and_kept(monkeypatch):
    monkeypatch.setattr(ollama_host, "probe", lambda host, **k: host == "http://10.0.0.5:11434")
    assert resolve_ollama_host("http://10.0.0.5:11434/") == "http://10.0.0.5:11434"


def test_literal_url_unreachable_falls_back(monkeypatch):
    monkeypatch.setattr(ollama_host, "probe", lambda host, **k: False)
    assert resolve_ollama_host("http://10.0.0.5:11434") == LOCAL_HOST


def test_environment_variable_is_honoured(monkeypatch):
    monkeypatch.setenv("HEADING_OS_OLLAMA_HOST", "auto:11436")
    monkeypatch.setattr(ollama_host, "read_default_gateway", lambda *a, **k: "172.30.48.1")
    monkeypatch.setattr(ollama_host, "probe", lambda host, **k: True)
    assert resolve_ollama_host() == "http://172.30.48.1:11436"


def test_separate_env_var_can_be_named(monkeypatch):
    # chronicle keeps generation and embeddings on independent variables.
    monkeypatch.setenv("HEADING_OS_OLLAMA_EMBED_HOST", "http://10.0.0.9:11434")
    monkeypatch.setattr(ollama_host, "probe", lambda host, **k: True)
    resolved = resolve_ollama_host(env_var="HEADING_OS_OLLAMA_EMBED_HOST")
    assert resolved == "http://10.0.0.9:11434"


def test_fallback_is_silent_when_verbose_is_off(monkeypatch, capsys):
    monkeypatch.setattr(ollama_host, "probe", lambda host, **k: False)
    assert resolve_ollama_host("http://10.0.0.5:11434", verbose=False) == LOCAL_HOST
    assert capsys.readouterr().err == ""


# --- probe ----------------------------------------------------------------

def test_probe_rejects_a_non_ollama_answer(monkeypatch):
    class FakeResponse:
        def read(self):
            return b'{"something_else": 1}'

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(ollama_host.urllib.request, "urlopen", lambda *a, **k: FakeResponse())
    assert ollama_host.probe("http://localhost:11434") is False


def test_probe_survives_a_connection_error(monkeypatch):
    def boom(*a, **k):
        raise OSError("connection refused")

    monkeypatch.setattr(ollama_host.urllib.request, "urlopen", boom)
    assert ollama_host.probe("http://localhost:11434") is False


# --- scheme guard ---------------------------------------------------------

def test_non_http_scheme_is_refused_before_it_reaches_the_opener(monkeypatch, capsys):
    # The candidate comes from an env var or a config file, and urlopen would
    # happily read file:// URLs. Nothing but http(s) may reach the opener.
    monkeypatch.setattr(
        ollama_host.urllib.request, "urlopen",
        lambda *a, **k: pytest.fail("opener must not be reached"),
    )
    assert resolve_ollama_host("file:///etc/passwd") == LOCAL_HOST
    assert "not an http(s) URL" in capsys.readouterr().err


def test_probe_refuses_a_non_http_scheme(monkeypatch):
    monkeypatch.setattr(
        ollama_host.urllib.request, "urlopen",
        lambda *a, **k: pytest.fail("opener must not be reached"),
    )
    assert ollama_host.probe("file:///etc/passwd") is False
    assert ollama_host.probe("ftp://example.invalid") is False


def test_is_http_url_needs_a_host_part():
    assert ollama_host.is_http_url("http://localhost:11434")
    assert ollama_host.is_http_url("https://box.local:11434")
    assert not ollama_host.is_http_url("http://")
    assert not ollama_host.is_http_url("localhost:11434")   # no scheme
