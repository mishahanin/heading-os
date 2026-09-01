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
    _write(tmp_config, {"grok": "grok-9.9", "gemini": "gemini-x", "kimi": "kimi-z"})
    assert cm.get_model("grok") == "grok-9.9"
    assert cm.get_model("gemini") == "gemini-x"
    assert cm.get_model("kimi") == "kimi-z"


def test_missing_config_falls_back(tmp_config):
    assert not tmp_config.exists()
    for provider in cm.PROVIDERS:
        assert cm.get_model(provider) == cm.FALLBACKS[provider]


def test_empty_or_blank_value_falls_back(tmp_config):
    _write(tmp_config, {"grok": "   ", "kimi": ""})
    assert cm.get_model("grok") == cm.FALLBACKS["grok"]
    assert cm.get_model("kimi") == cm.FALLBACKS["kimi"]


def test_a_padded_value_resolves_stripped(tmp_config):
    """The pin is handed to the proxy as a model id, so surrounding whitespace
    from a hand-edited config is not a cosmetic difference: the id stops
    matching the catalog and `council_freshness` reports the pin as broken.
    `get_model` strips, and nothing measured that it still does: removing the
    `.strip()` from the return left the whole repository green (2026-09-01),
    because the only blank-value case in this file uses `"   "`, which falls
    back before it can reach the return."""
    _write(tmp_config, {"grok": "  grok-9.9\n", "gemini": "\tgemini-x "})
    assert cm.get_model("grok") == "grok-9.9"
    assert cm.get_model("gemini") == "gemini-x"


def test_set_model_stores_the_stripped_value(tmp_config):
    """The other end of the same contract: a padded id must not be persisted."""
    cm.set_model("kimi", "  kimi-fresh  ")
    assert json.loads(tmp_config.read_text(encoding="utf-8"))["kimi"] == "kimi-fresh"


def test_set_model_writes_atomically(tmp_config, monkeypatch):
    """`config/council-models.json` is persistent state, so the workspace rule
    is write-to-tmp then `os.replace`. A direct `open(path, "w")` truncates the
    real file first, and a crash between truncate and write leaves every council
    pin gone rather than stale. The docstring says "atomic write"; nothing
    checked it, and replacing the tmp+replace pair with a plain write left the
    whole repository green (measured 2026-09-01)."""
    _write(tmp_config, {"grok": "grok-old", "gemini": "gemini-keep"})
    replaced = []
    real_replace = cm.os.replace

    def tracking_replace(src, dst):
        replaced.append((str(src), str(dst)))
        return real_replace(src, dst)

    monkeypatch.setattr(cm.os, "replace", tracking_replace)
    cm.set_model("grok", "grok-new")

    assert replaced, "set_model did not go through os.replace"
    src, dst = replaced[0]
    assert src.endswith(".tmp") and dst == str(tmp_config)
    # And no scratch file is left behind next to the config.
    assert [p.name for p in tmp_config.parent.glob("*.tmp")] == []
    assert cm.get_model("gemini") == "gemini-keep"


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
    cm.set_model("kimi", "kimi-fresh")
    assert tmp_config.exists()
    assert cm.get_model("kimi") == "kimi-fresh"


def test_set_model_rejects_unknown_provider(tmp_config):
    with pytest.raises(ValueError):
        cm.set_model("bogus", "x")


def test_set_model_rejects_empty_model(tmp_config):
    with pytest.raises(ValueError):
        cm.set_model("grok", "   ")


def test_load_all_covers_every_provider(tmp_config):
    result = cm.load_all()
    assert set(result.keys()) == set(cm.PROVIDERS)


def test_providers_are_the_three_proxy_voices():
    """The council roster is three voices, and stays three when a caller-specific
    pin joins the table. `kimi_reasoning` (the /scrutinize judge tier, added
    2026-08-09) is a second pin for an existing voice, not a fourth voice."""
    from scripts.utils.council_models import COUNCIL_PROVIDERS, PROVIDERS
    assert set(COUNCIL_PROVIDERS) == {"gemini", "grok", "kimi"}
    assert set(COUNCIL_PROVIDERS) <= set(PROVIDERS)


def test_glm_and_kimi_code_are_unknown_providers():
    import pytest
    from scripts.utils.council_models import get_model
    for gone in ("glm", "kimi-code"):
        with pytest.raises(ValueError):
            get_model(gone)


def test_proxy_pins_resolve():
    from scripts.utils.council_models import get_model
    assert get_model("gemini") == "gemini-3-flash"
    assert get_model("grok") == "grok-4.5"
    assert get_model("kimi") == "kimi-for-coding"
