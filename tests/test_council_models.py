"""Unit tests for scripts/utils/council_models.py — the /council model pin resolver."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.utils import council_models as cm


@pytest.fixture
def tmp_config(tmp_path, monkeypatch):
    """Point the resolver at an isolated config file so the real one is untouched."""
    path = tmp_path / "council-models.json"
    monkeypatch.setattr(cm, "config_path", lambda: path)
    return path


def _write(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data), encoding="utf-8")


def test_get_model_reads_config(tmp_config):
    _write(tmp_config, {"grok": "grok-9.9", "gemini": "gemini-x", "kimi": "kimi-z:cloud"})
    assert cm.get_model("grok") == "grok-9.9"
    assert cm.get_model("gemini") == "gemini-x"
    assert cm.get_model("kimi") == "kimi-z:cloud"


def test_missing_config_falls_back(tmp_config):
    assert not tmp_config.exists()
    for provider in cm.PROVIDERS:
        assert cm.get_model(provider) == cm.FALLBACKS[provider]


def test_empty_or_blank_value_falls_back(tmp_config):
    _write(tmp_config, {"grok": "   ", "kimi": ""})
    assert cm.get_model("grok") == cm.FALLBACKS["grok"]
    assert cm.get_model("kimi") == cm.FALLBACKS["kimi"]


def test_malformed_config_falls_back(tmp_config, capsys):
    tmp_config.write_text("not json{", encoding="utf-8")
    assert cm.get_model("grok") == cm.FALLBACKS["grok"]
    assert "fallback" in capsys.readouterr().err.lower()


def test_non_object_config_falls_back(tmp_config):
    tmp_config.write_text("[1, 2, 3]", encoding="utf-8")
    assert cm.get_model("kimi") == cm.FALLBACKS["kimi"]


def test_unknown_provider_raises(tmp_config):
    with pytest.raises(ValueError):
        cm.get_model("bogus")


def test_set_model_round_trip_preserves_other_keys(tmp_config):
    _write(tmp_config, {"grok": "grok-old", "gemini": "gemini-keep"})
    cm.set_model("grok", "grok-new")
    assert cm.get_model("grok") == "grok-new"
    # Untouched key survives the atomic rewrite.
    assert cm.get_model("gemini") == "gemini-keep"
    on_disk = json.loads(tmp_config.read_text(encoding="utf-8"))
    assert on_disk["grok"] == "grok-new"
    assert on_disk["gemini"] == "gemini-keep"


def test_set_model_creates_config_when_absent(tmp_config):
    assert not tmp_config.exists()
    cm.set_model("kimi", "kimi-fresh:cloud")
    assert tmp_config.exists()
    assert cm.get_model("kimi") == "kimi-fresh:cloud"


def test_set_model_rejects_unknown_provider(tmp_config):
    with pytest.raises(ValueError):
        cm.set_model("bogus", "x")


def test_set_model_rejects_empty_model(tmp_config):
    with pytest.raises(ValueError):
        cm.set_model("grok", "   ")


def test_load_all_covers_every_provider(tmp_config):
    result = cm.load_all()
    assert set(result.keys()) == set(cm.PROVIDERS)
