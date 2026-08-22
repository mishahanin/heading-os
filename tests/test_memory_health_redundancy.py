import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import scripts.utils.embeddings as embeddings_mod
import scripts.utils.ollama_host as ollama_host_mod
from scripts.utils.embeddings import index_embed_target
from scripts.utils.memory_health import scan_redundancy


def test_flags_near_duplicates_not_distinct(tmp_path):
    (tmp_path / "a.md").write_text("Misha prefers no em-dash in prose.", encoding="utf-8")
    (tmp_path / "b.md").write_text("No em-dash in Misha's authored prose.", encoding="utf-8")
    (tmp_path / "c.md").write_text("The bridge daemon binds to loopback.", encoding="utf-8")
    (tmp_path / "MEMORY.md").write_text("- index line", encoding="utf-8")

    table = {
        "Misha prefers no em-dash in prose.": [1.0, 0.0, 0.0],
        "No em-dash in Misha's authored prose.": [0.98, 0.02, 0.0],
        "The bridge daemon binds to loopback.": [0.0, 0.0, 1.0],
    }
    res = scan_redundancy(tmp_path, threshold=0.86, embedder=lambda ts: [table[t] for t in ts])
    assert res["ok"] is True
    names = {(p["a"], p["b"]) for p in res["pairs"]}
    assert ("a.md", "b.md") in names
    assert all("c.md" not in (p["a"], p["b"]) for p in res["pairs"])
    allnames = {p["a"] for p in res["pairs"]} | {p["b"] for p in res["pairs"]}
    assert "MEMORY.md" not in allnames


def test_degrades_when_embedder_unavailable(tmp_path):
    (tmp_path / "a.md").write_text("one", encoding="utf-8")
    (tmp_path / "b.md").write_text("two", encoding="utf-8")

    def boom(ts):
        raise RuntimeError("ollama down")

    res = scan_redundancy(tmp_path, threshold=0.86, embedder=boom)
    assert res["ok"] is False
    assert res["pairs"] == []
    assert "unavailable" in res["note"]


# ============================================================
# Which ollama the default embedder talks to
# ============================================================

def _index_config() -> dict:
    import yaml

    from scripts.utils.workspace import get_workspace_root
    return yaml.safe_load(
        (get_workspace_root() / "config" / "memory-index.yaml").read_text(encoding="utf-8")
    )


def test_the_default_embedder_takes_both_host_and_model_from_the_index(tmp_path, monkeypatch):
    """The split brain this closes.

    Until 2026-08-22 this call site spelled `http://localhost:11434` and `bge-m3`
    out, so the memory index embedded on whichever host and model
    `config/memory-index.yaml` names - the Windows GPU, measured 267s -> 87s on
    the real corpus - while the redundancy scan beside it always embedded on the
    WSL CPU daemon. Two embedders over one corpus, chosen by which function you
    happened to call, and nothing said so.

    The host is asserted against the RESOLVER rather than an address, because the
    address is a probe result: on a machine with the accelerated host down,
    localhost is the correct answer and a test that demanded otherwise would be
    red for the wrong reason. The model has no probe, so it is asserted against
    the config itself.
    """
    (tmp_path / "a.md").write_text("one", encoding="utf-8")
    (tmp_path / "b.md").write_text("two", encoding="utf-8")

    monkeypatch.setattr(
        ollama_host_mod, "resolve_ollama_host",
        lambda preferred=None, **kw: "http://resolver.example:11436",
    )
    seen = {}

    def fake_embed(texts, **kwargs):
        seen.update(kwargs)
        return [[1.0, 0.0] for _ in texts]

    monkeypatch.setattr(embeddings_mod, "embed", fake_embed)

    res = scan_redundancy(tmp_path, threshold=0.86)
    assert res["ok"] is True, res
    assert seen.get("host") == "http://resolver.example:11436", seen
    assert seen.get("model") == _index_config()["model"], seen


def test_the_index_config_is_the_preference_the_resolver_is_handed(monkeypatch):
    """One preference, read where the index reads it, so the two cannot drift."""
    captured = {}

    def fake_resolve(preferred=None, **kw):
        captured["preferred"] = preferred
        captured["env_var"] = kw.get("env_var")
        return "http://localhost:11434"

    monkeypatch.setattr(ollama_host_mod, "resolve_ollama_host", fake_resolve)
    host, model = index_embed_target()

    config = _index_config()
    assert captured["preferred"] == config["host"], captured
    assert captured["env_var"] == "HEADING_OS_OLLAMA_EMBED_HOST", captured
    assert model == config["model"], model
    assert host == "http://localhost:11434", host


def test_a_missing_config_falls_back_and_does_not_raise(monkeypatch, tmp_path):
    """An advisory scan must not take its caller down over a config file - and the
    model it falls back to is the one `scripts/memory-index.py` falls back to."""
    import scripts.utils.workspace as ws

    monkeypatch.setattr(ws, "get_workspace_root", lambda: tmp_path)
    monkeypatch.setattr(
        ollama_host_mod, "resolve_ollama_host",
        lambda preferred=None, **kw: "http://localhost:11434",
    )
    host, model = index_embed_target()
    assert model == embeddings_mod.INDEX_EMBED_MODEL_DEFAULT
    assert host == "http://localhost:11434"


def test_the_fallback_model_matches_the_one_the_index_uses():
    """Two fallbacks that disagree would reopen the split on exactly the machine
    whose config went missing - the case nobody tests by hand."""
    import re

    from scripts.utils.workspace import get_workspace_root
    source = (get_workspace_root() / "scripts" / "memory-index.py").read_text(encoding="utf-8")
    match = re.search(r'setdefault\(\s*"model"\s*,\s*"([^"]+)"\s*\)', source)
    assert match, "memory-index.py no longer sets a default model; re-pin this"
    assert match.group(1) == embeddings_mod.INDEX_EMBED_MODEL_DEFAULT
