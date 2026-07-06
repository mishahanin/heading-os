import importlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _fresh_module(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKSPACE_LOG_DIR", str(tmp_path / ".logs"))
    import scripts.utils.memory_ops_log as m
    importlib.reload(m)
    return m


def test_log_recall_writes_full_record_when_not_sensitive(monkeypatch, tmp_path):
    m = _fresh_module(monkeypatch, tmp_path)
    monkeypatch.setattr(m, "is_sensitive", lambda: False)
    m.log_recall(query_snippet="what do we know about X", collection="content",
                 layer="odin", top_score=0.71, gap=False, n_hits=5, threshold=0.55,
                 latency_ms=42, hit_paths=["a.md", "b.md"])
    lines = m.read_recall_log()
    assert len(lines) == 1
    rec = lines[0]
    assert rec["gap"] is False
    assert rec["layer"] == "odin"
    assert rec["query_snippet"] == "what do we know about X"
    assert rec["hit_paths"] == ["a.md", "b.md"]
    assert "ts" in rec


def test_log_recall_redacts_snippet_when_sensitive(monkeypatch, tmp_path):
    m = _fresh_module(monkeypatch, tmp_path)
    monkeypatch.setattr(m, "is_sensitive", lambda: True)
    m.log_recall(query_snippet="secret query text", collection="content", layer=None,
                 top_score=0.9, gap=False, n_hits=1, threshold=0.55, latency_ms=1,
                 hit_paths=["c.md"])
    rec = m.read_recall_log()[0]
    assert rec["query_snippet"] is None
    assert rec["top_score"] == 0.9
    assert rec["hit_paths"] == ["c.md"]


def test_log_recall_never_raises(monkeypatch, tmp_path):
    m = _fresh_module(monkeypatch, tmp_path)
    monkeypatch.setattr(m, "is_sensitive", lambda: False)
    monkeypatch.setattr(m, "_recall_log_path", lambda: (_ for _ in ()).throw(OSError("boom")))
    m.log_recall(query_snippet="x", collection="content", layer=None, top_score=0.1,
                 gap=True, n_hits=0, threshold=0.55, latency_ms=1, hit_paths=[])
