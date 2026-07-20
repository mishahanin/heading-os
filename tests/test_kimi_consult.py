"""Unit tests for scripts/kimi-consult.py — arg validation + config (no API calls)."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
pytest.importorskip("openai")  # F-7.1: skip on a core-only clone (needs the ai-extra extra)

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "kimi-consult.py"
_spec = importlib.util.spec_from_file_location("kimi_consult", SCRIPT)
kc = importlib.util.module_from_spec(_spec)
sys.modules["kimi_consult"] = kc
_spec.loader.exec_module(kc)


def test_default_model_from_council_config():
    from scripts.utils.council_models import get_model
    assert get_model("kimi") == kc.DEFAULT_MODEL
    assert kc.DEFAULT_MODEL == "kimi-for-coding"


def test_independent_requires_question():
    with pytest.raises(SystemExit):
        kc.parse_args(["--mode", "independent"])


def test_critique_requires_draft():
    with pytest.raises(SystemExit):
        kc.parse_args(["--mode", "critique"])


def test_independent_ok():
    args = kc.parse_args(["--mode", "independent", "--question", "Q?"])
    assert args.mode == "independent" and args.question == "Q?"


def test_critique_temperature_default_lower():
    args = kc.parse_args(["--mode", "critique", "--draft", "D"])
    assert args.temperature == kc.DEFAULT_CRITIQUE_TEMPERATURE


def test_main_returns_2_on_missing_key(monkeypatch):
    def _raise(*_a, **_k):
        raise RuntimeError("CLIPROXY_API_KEY is missing from .env.")
    monkeypatch.setattr(kc, "consult_kimi", _raise)
    assert kc.main(["--mode", "independent", "--question", "Q?"]) == 2


def test_main_returns_3_on_api_error(monkeypatch):
    def _raise(*_a, **_k):
        raise RuntimeError("Proxy connection failed for kimi-for-coding")
    monkeypatch.setattr(kc, "consult_kimi", _raise)
    assert kc.main(["--mode", "independent", "--question", "Q?"]) == 3


def test_main_returns_3_on_auth_failure(monkeypatch):
    # Auth failure mentions CLIPROXY_API_KEY but is NOT a missing key -> exit 3,
    # not 2. Guards the narrow "is missing from .env" sentinel.
    def _raise(*_a, **_k):
        raise RuntimeError("Proxy auth failed for kimi-for-coding: 401. "
                           "Check CLIPROXY_API_KEY in .env.")
    monkeypatch.setattr(kc, "consult_kimi", _raise)
    assert kc.main(["--mode", "independent", "--question", "Q?"]) == 3


def test_consult_kimi_forwards_kwargs_to_call_model(monkeypatch):
    # The thin delegate must pass model/temperature/max_tokens straight through.
    captured = {}
    def _fake(model, prompt, *, temperature, max_tokens, timeout=120.0, reasoning_effort=None):
        captured.update(model=model, prompt=prompt,
                        temperature=temperature, max_tokens=max_tokens)
        return "ok"
    monkeypatch.setattr(kc, "call_model", _fake)  # kc imported call_model by name
    out = kc.consult_kimi("the draft", model="kimi-for-coding",
                          temperature=0.4, max_tokens=1234)
    assert out == "ok"
    assert captured == {"model": "kimi-for-coding", "prompt": "the draft",
                        "temperature": 0.4, "max_tokens": 1234}


def test_consult_kimi_forwards_reasoning_effort(monkeypatch):
    captured = {}
    def _fake(model, prompt, *, temperature, max_tokens, timeout=120.0, reasoning_effort=None):
        captured["reasoning_effort"] = reasoning_effort
        return "ok"
    monkeypatch.setattr(kc, "call_model", _fake)
    kc.consult_kimi("draft", model="k3", temperature=0.4, max_tokens=1234, reasoning_effort="high")
    assert captured["reasoning_effort"] == "high"


def test_cli_reasoning_effort_passed_through(monkeypatch):
    captured = {}
    def _fake(prompt, model=kc.DEFAULT_MODEL, temperature=kc.DEFAULT_TEMPERATURE,
              max_tokens=kc.DEFAULT_MAX_TOKENS, reasoning_effort=None, timeout=None):
        captured["reasoning_effort"] = reasoning_effort
        captured["timeout"] = timeout
        return "answer"
    monkeypatch.setattr(kc, "consult_kimi", _fake)
    assert kc.main(["--mode", "independent", "--question", "Q?",
                    "--model", "k3", "--reasoning-effort", "max", "--timeout", "480"]) == 0
    assert captured["reasoning_effort"] == "max"
    assert captured["timeout"] == 480.0
