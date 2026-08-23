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


# ============================================================
# The documented exit contract is 0 / 2 / 3 -- nothing else
# ============================================================
def test_an_unwrapped_error_still_exits_three(monkeypatch):
    """`call_model` wraps the API errors it knows into RuntimeError, but not
    every one. An unlisted APIStatusError subclass, or a KeyError on an
    unexpected response shape, used to escape as a traceback and exit 1 -- a
    code the module docstring does not define, so a caller following the
    contract mis-handled it.
    """
    def _boom(*a, **k):
        raise KeyError("choices")

    monkeypatch.setattr(kc, "call_model", _boom)
    rc = kc.main(["--mode", "independent", "--question", "why"])
    assert rc == 3, f"expected the documented API-failure code 3, got {rc}"


def test_a_missing_key_still_exits_two(monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("KIMI_API_KEY is missing from .env")

    monkeypatch.setattr(kc, "call_model", _boom)
    assert kc.main(["--mode", "independent", "--question", "why"]) == 2


# --------------------------------------------------------- the length cap CLI
#
# `--length-hint` landed 2026-08-23. The 2026-08-23 engine audit ran its
# per-file shards through this wrapper, and every shard silently inherited
# "Aim for 200-400 words." from `council_prompts` while its own question said
# "list EVERY defect". The cap belongs to a council consult, not to enumeration.

def test_the_council_word_cap_is_still_the_default(monkeypatch):
    captured = {}
    monkeypatch.setattr(kc, "consult_kimi",
                        lambda prompt, **k: captured.setdefault("p", prompt) or "answer")
    assert kc.main(["--mode", "independent", "--question", "Q?"]) == 0
    assert kc.DEFAULT_LENGTH_HINT in captured["p"]


def test_an_empty_length_hint_removes_the_cap_from_the_prompt(monkeypatch):
    captured = {}
    monkeypatch.setattr(kc, "consult_kimi",
                        lambda prompt, **k: captured.setdefault("p", prompt) or "answer")
    assert kc.main(["--mode", "independent", "--question", "List every defect.",
                    "--length-hint", ""]) == 0
    assert "200-400" not in captured["p"]
    assert "List every defect." in captured["p"], "the question itself was lost"


def test_the_flag_reaches_critique_mode_too(monkeypatch):
    captured = {}
    monkeypatch.setattr(kc, "consult_kimi",
                        lambda prompt, **k: captured.setdefault("p", prompt) or "answer")
    assert kc.main(["--mode", "critique", "--draft", "D", "--length-hint", ""]) == 0
    assert "200-400" not in captured["p"]
