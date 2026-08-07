"""Tests for `memory-index.py query --touch` — retrieval reinforcement.

The bump is a ranking signal written on the read path, so the rules that keep
it honest are the ones under test: only confident results count, only
auto-memory files count, and a failure must never break a recall.

Run: .venv/bin/python -m pytest tests/test_memory_index_touch.py
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "memory-index.py"

FACT = (
    "---\n"
    "name: example-fact\n"
    "description: fixture fact\n"
    "metadata:\n"
    "  node_type: memory\n"
    "  type: feedback\n"
    "---\n\n"
    "Some fact body.\n"
)


def load_module():
    spec = importlib.util.spec_from_file_location("memory_index_mod", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def memory_dir(tmp_path: Path, monkeypatch) -> Path:
    mdir = tmp_path / "auto-memory"
    mdir.mkdir(parents=True)
    (mdir / "example-fact.md").write_text(FACT, encoding="utf-8")
    import scripts.utils.workspace as ws
    monkeypatch.setattr(ws, "get_auto_memory_dir", lambda: mdir)
    return mdir


def _access_count(path: Path) -> int:
    from scripts.utils.markdown import parse_frontmatter
    meta, _ = parse_frontmatter(path.read_text(encoding="utf-8"))
    nested = meta.get("metadata")
    nested = nested if isinstance(nested, dict) else {}
    return int(nested.get("access_count") or 0)


def _hit(path: str, layer: str = "memory") -> dict:
    return {"path": path, "title": "Example", "layer": layer, "score": 0.7}


def test_bumps_a_confident_memory_layer_hit(memory_dir):
    mod = load_module()
    assert mod._touch_memory_hits([_hit("auto-memory/example-fact.md")]) == 1
    assert _access_count(memory_dir / "example-fact.md") == 1


def test_ignores_non_memory_layers(memory_dir):
    mod = load_module()
    assert mod._touch_memory_hits([_hit("knowledge/odin-brain/x.md", layer="odin")]) == 0
    assert _access_count(memory_dir / "example-fact.md") == 0


def test_deduplicates_repeated_paths_within_one_result_set(memory_dir):
    """A multi-chunk file returns several hits pointing at one file."""
    mod = load_module()
    hits = [_hit("auto-memory/example-fact.md"), _hit("auto-memory/example-fact.md")]
    assert mod._touch_memory_hits(hits) == 1
    assert _access_count(memory_dir / "example-fact.md") == 1


def test_a_missing_file_never_raises(memory_dir, capsys):
    mod = load_module()
    assert mod._touch_memory_hits([_hit("auto-memory/does-not-exist.md")]) == 0
    assert "touch:" in capsys.readouterr().err


class _Args:
    def __init__(self, touch):
        self.touch = touch


def test_gate_closed_without_the_flag():
    """Without --touch the read path must write nothing at all."""
    mod = load_module()
    assert mod._should_touch(_Args(touch=False), near_miss=False) is False


def test_gate_closed_on_a_near_miss_result():
    """A near-miss block says relevance is NOT established. Counting it would
    train the ranking on noise."""
    mod = load_module()
    assert mod._should_touch(_Args(touch=True), near_miss=True) is False


def test_gate_open_only_on_a_confident_result_with_the_flag():
    mod = load_module()
    assert mod._should_touch(_Args(touch=True), near_miss=False) is True


def test_gate_closed_for_a_caller_that_has_no_touch_attribute():
    """/recall and the bare-namespace callers never set it."""
    mod = load_module()
    assert mod._should_touch(object(), near_miss=False) is False
