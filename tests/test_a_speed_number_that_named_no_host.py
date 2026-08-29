"""A timing must carry the endpoint it was taken against.

Found by the 2026-08-29 audit of `scripts/census-submodel-bench.py`.

`ollama_url`'s docstring stated the invariant itself: "A speed number is only
comparable to another one taken on the same host, so a run that reports ollama
timings should say which host it reached." Nothing in the file did. The dict
`score_speed` returned carried `runner`, `model`, `cold_s`, `median_s`,
`parallel_wall_s`, `per_call_parallel_s` and `projected_200_s`, plus the fields
`main` stamped on afterwards. No host anywhere.

So two `speed` runs against different `HEADING_OS_OLLAMA_HOST` values wrote two
reports that could not be told apart, and an ollama timing landing in
`report["speed"]` was exactly the un-attributed number the docstring said must
not be produced. The docstring and the code could not both be right; the code
was the side that was wrong, and the docstring's own argument says recording the
host is the better half of the repair.

`score_speed` now stamps `endpoint`. These tests assert the number is
attributable, and that two hosts produce two distinguishable reports.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _load():
    spec = importlib.util.spec_from_file_location(
        "census_submodel_bench_host", ROOT / "scripts" / "census-submodel-bench.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["census_submodel_bench_host"] = module
    spec.loader.exec_module(module)
    return module


bench = _load()


def _cases(n: int = 3) -> list:
    cases = [bench.Case(Path(f"doc{i}.md"), "body", 4_000, 4_000,
                        {"field": None, "checkboxes": 0, "mentions": False})
             for i in range(n)]
    assert cases, "an empty case list makes score_speed refuse before measuring"
    return cases


def _no_network(monkeypatch) -> None:
    """Every call answers locally. This test makes no network call at all."""
    monkeypatch.setattr(bench, "call_model", lambda runner, prompt: "{}")
    monkeypatch.setattr(bench, "_post", _refuse_post)


def _refuse_post(*args, **kwargs):
    raise AssertionError("this test must not reach the network")


def test_an_ollama_timing_names_the_host_it_reached(monkeypatch):
    runner = bench.Runner("ollama gemma3:4b", "ollama", "gemma3:4b", True, "test")
    _no_network(monkeypatch)
    monkeypatch.setattr(bench, "ollama_url", lambda: "http://box-a.invalid/api/chat")

    result = bench.score_speed(runner, _cases(), "zzq-probe")
    assert result is not None
    assert result["endpoint"] == "http://box-a.invalid/api/chat"


def test_two_ollama_hosts_produce_two_distinguishable_reports(monkeypatch):
    """The defect stated as its consequence: telling the two runs apart.

    Before the fix the two dicts were identical except for timings, which are
    the very thing under comparison, so neither report could be attributed.
    """
    runner = bench.Runner("ollama gemma3:4b", "ollama", "gemma3:4b", True, "test")
    _no_network(monkeypatch)

    monkeypatch.setattr(bench, "ollama_url", lambda: "http://box-a.invalid/api/chat")
    first = bench.score_speed(runner, _cases(), "zzq-probe")
    monkeypatch.setattr(bench, "ollama_url", lambda: "http://box-b.invalid/api/chat")
    second = bench.score_speed(runner, _cases(), "zzq-probe")

    assert first is not None and second is not None
    assert first["endpoint"] != second["endpoint"]


def test_a_cloud_timing_names_its_endpoint_too(monkeypatch):
    """Attribution is not an ollama-only property; a proxy timing carries it."""
    _no_network(monkeypatch)
    proxy = bench.Runner("proxy k3", "proxy", "k3", True, "test")
    result = bench.score_speed(proxy, _cases(), "zzq-probe")
    assert result is not None
    assert result["endpoint"] == bench.PROXY_URL

    anthropic = bench.Runner("api haiku", "anthropic", "haiku", True, "test")
    result = bench.score_speed(anthropic, _cases(), "zzq-probe")
    assert result is not None
    assert result["endpoint"] == bench.ANTHROPIC_URL


def test_resolving_a_cloud_endpoint_never_probes_the_ollama_host(monkeypatch):
    """`ollama_url()` probes, so a cloud runner must not resolve it.

    `_post` already reasons this way for the destination assertion; `_endpoint`
    inherits the same obligation, and a pin to a machine that is off would
    otherwise raise inside a proxy run.
    """
    def explode() -> str:
        raise AssertionError("a cloud runner must not resolve the ollama host")

    monkeypatch.setattr(bench, "ollama_url", explode)
    assert bench._endpoint(bench.Runner("p", "proxy", "k3", True, "t")) == bench.PROXY_URL
    assert bench._endpoint(
        bench.Runner("a", "anthropic", "haiku", True, "t")) == bench.ANTHROPIC_URL
