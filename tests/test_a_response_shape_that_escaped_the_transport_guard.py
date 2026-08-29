"""A well-formed response with nothing in it must not kill the whole run.

Found by the 2026-08-29 audit of `scripts/census-submodel-bench.py`.

`TRANSPORT_FAILURES` was introduced to name "everything a model call can fail
with", and it carries `KeyError`. `KeyError` covers a MISSING `choices` key and
nothing else. `{"choices": []}` is a valid HTTP 200 with the key present, so
`data["choices"][0]` raised `IndexError` - a sibling of `KeyError` under
`LookupError`, and a subclass of nothing in the tuple. It escaped
`score_accuracy`'s `except TRANSPORT_FAILURES` when `pool.map` was consumed and
killed the run with a traceback and exit 1, a code the module docstring does not
define. Every measurement collected up to that point, and the JSON report, were
lost.

The Anthropic path carried the same hole one shape over: `block.get("text", "")`
over a `content` list holding anything that is not a dict raised
`AttributeError`, also outside the tuple.

The fix is not a wider tuple. `call_model` checks the shape and raises
`ValueError`, which the tuple already catches, so the NEXT malformed shape
cannot escape either. These tests assert the observable consequence: one runner
is skipped with a named cause, the others keep going, and nothing propagates.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _load():
    spec = importlib.util.spec_from_file_location(
        "census_submodel_bench_shape", ROOT / "scripts" / "census-submodel-bench.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["census_submodel_bench_shape"] = module
    spec.loader.exec_module(module)
    return module


bench = _load()


def _case() -> "bench.Case":
    return bench.Case(Path("doc.md"), "text of a document", 4_000, 4_000,
                      {"field": None, "checkboxes": 0, "mentions": False})


def _cases(n: int = 3) -> list:
    cases = [_case() for _ in range(n)]
    assert cases, "an empty case list would make every assertion below vacuous"
    return cases


PROXY = bench.Runner("proxy k3", "proxy", "k3", True, "test runner")
ANTHROPIC = bench.Runner("api haiku", "anthropic", "haiku", True, "test runner")


# ============================================================
# The proxy shape
# ============================================================

@pytest.mark.parametrize("payload", [
    {"choices": []},                                   # the reported crash
    {"choices": [{}]},                                 # no message object
    {"choices": [{"message": {}}]},                    # no content
    {"choices": [{"message": {"content": None}}]},     # content is not text
    {"choices": "not a list"},                         # not even a list
])
def test_an_empty_or_shapeless_proxy_response_is_a_named_transport_failure(
        monkeypatch, payload):
    """Whatever the response holds, the failure lands inside the guard."""
    monkeypatch.setattr(bench, "_post",
                        lambda url, body, headers, timeout=300: payload)
    monkeypatch.setattr(bench, "load_api_key", lambda *a, **k: None)

    with pytest.raises(bench.TRANSPORT_FAILURES):
        bench.call_model(PROXY, "prompt")


def test_score_accuracy_skips_the_runner_instead_of_killing_the_run(
        monkeypatch, capsys):
    """The whole point: the run survives and says which model was skipped.

    Before the fix this raised `IndexError` out of `list(pool.map(...))` and the
    process died with exit 1, discarding every cell already measured.
    """
    monkeypatch.setattr(bench, "_post",
                        lambda url, body, headers, timeout=300: {"choices": []})
    monkeypatch.setattr(bench, "load_api_key", lambda *a, **k: None)

    cases = _cases()
    assert bench.score_accuracy(PROXY, cases, "zzq-probe") is None
    assert "пропущена" in capsys.readouterr().out


def test_score_speed_skips_the_runner_instead_of_killing_the_run(
        monkeypatch, capsys):
    monkeypatch.setattr(bench, "_post",
                        lambda url, body, headers, timeout=300: {"choices": []})
    monkeypatch.setattr(bench, "load_api_key", lambda *a, **k: None)

    assert bench.score_speed(PROXY, _cases(), "zzq-probe") is None
    assert "пропущена" in capsys.readouterr().out


# ============================================================
# The Anthropic shape
# ============================================================

@pytest.mark.parametrize("payload", [
    {"content": ["a bare string, not a block"]},
    {"content": [None]},
    {"content": []},
    {"content": {"text": "an object where a list belongs"}},
    {},
])
def test_a_shapeless_anthropic_response_is_a_named_transport_failure(
        monkeypatch, payload):
    monkeypatch.setattr(bench, "_post",
                        lambda url, body, headers, timeout=300: payload)
    monkeypatch.setattr(bench, "load_api_key", lambda *a, **k: "test-key")
    monkeypatch.setattr(bench, "latest", lambda family: family)

    with pytest.raises(bench.TRANSPORT_FAILURES):
        bench.call_model(ANTHROPIC, "prompt")


# ============================================================
# The ollama shape
# ============================================================

@pytest.mark.parametrize("payload", [
    {"message": "a string where an object belongs"},
    {"message": {}},
    {"message": {"content": 42}},
    {},
])
def test_a_shapeless_ollama_response_is_a_named_transport_failure(
        monkeypatch, payload):
    runner = bench.Runner("ollama gemma3:4b", "ollama", "gemma3:4b", True, "test")
    monkeypatch.setattr(bench, "ollama_url", lambda: "http://ollama.invalid/api/chat")
    monkeypatch.setattr(bench, "_post",
                        lambda url, body, headers, timeout=300: payload)

    with pytest.raises(bench.TRANSPORT_FAILURES):
        bench.call_model(runner, "prompt")


# ============================================================
# The well-formed case still answers
# ============================================================

def test_a_well_formed_response_still_returns_its_text(monkeypatch):
    """The negative cases above prove nothing if everything now raises."""
    monkeypatch.setattr(bench, "load_api_key", lambda *a, **k: None)
    monkeypatch.setattr(
        bench, "_post",
        lambda url, body, headers, timeout=300: {
            "choices": [{"message": {"content": '{"field": null}'}}]})
    assert bench.call_model(PROXY, "prompt") == '{"field": null}'

    monkeypatch.setattr(bench, "load_api_key", lambda *a, **k: "test-key")
    monkeypatch.setattr(bench, "latest", lambda family: family)
    monkeypatch.setattr(
        bench, "_post",
        lambda url, body, headers, timeout=300: {
            "content": [{"text": "hello "}, {"text": "world"}]})
    assert bench.call_model(ANTHROPIC, "prompt") == "hello world"
