"""Regression tests for scripts/dream-shadow.py -- the nightly salience-ranked
consolidation worklist (Gap #1). Encodes the plan's Success Signal: a stale +
low-salience fact is flagged as a prune candidate; a stale + high-salience
(feedback-type, high access_count) fact is NOT flagged; a near-duplicate pair
is surfaced merge-ranked by salience; the detector never mutates auto-memory/.

Run: python3 -m pytest tests/test_dream_shadow.py
"""
from __future__ import annotations

import importlib.util
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "dream-shadow.py"


def load_module():
    spec = importlib.util.spec_from_file_location("dream_shadow_mod", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _write(path: Path, text: str, days_old: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    old = time.time() - days_old * 86400
    os.utime(path, (old, old))


def _fact(mem_type: str, access_count: int, name: str) -> str:
    return (
        "---\n"
        f"name: {name}\n"
        "description: fixture fact\n"
        "metadata:\n"
        f"  node_type: memory\n"
        f"  type: {mem_type}\n"
        f"  access_count: {access_count}\n"
        "---\n\n"
        "Some fact body.\n"
    )


def test_stale_low_salience_flagged_as_prune_candidate(tmp_path):
    mod = load_module()
    mem = tmp_path / "auto-memory"
    _write(mem / "stale-low.md", _fact("reference", 0, "stale-low"), days_old=90)

    now = datetime.now(timezone.utc)
    candidates = mod.compute_prune_candidates(mem, now)
    names = [c["name"] for c in candidates]
    assert "stale-low.md" in names


def test_stale_high_salience_not_flagged(tmp_path):
    mod = load_module()
    mem = tmp_path / "auto-memory"
    _write(mem / "stale-high.md", _fact("feedback", 20, "stale-high"), days_old=90)

    now = datetime.now(timezone.utc)
    candidates = mod.compute_prune_candidates(mem, now)
    names = [c["name"] for c in candidates]
    assert "stale-high.md" not in names


def test_recent_low_salience_not_flagged(tmp_path):
    mod = load_module()
    mem = tmp_path / "auto-memory"
    _write(mem / "recent-low.md", _fact("reference", 0, "recent-low"), days_old=1)

    now = datetime.now(timezone.utc)
    candidates = mod.compute_prune_candidates(mem, now)
    names = [c["name"] for c in candidates]
    assert "recent-low.md" not in names


def test_merge_candidates_ranked_by_salience(tmp_path, monkeypatch):
    mod = load_module()
    mem = tmp_path / "auto-memory"
    mem.mkdir(parents=True)
    (mem / "a.md").write_text(_fact("feedback", 10, "a"), encoding="utf-8")
    (mem / "b.md").write_text(_fact("reference", 0, "b"), encoding="utf-8")

    def fake_scan_redundancy(memory_dir, threshold=0.86, embedder=None, timeout=120):
        return {"ok": True, "pairs": [{"a": "a.md", "b": "b.md", "score": 0.9}], "note": "1 pair"}

    monkeypatch.setattr(mod, "scan_redundancy", fake_scan_redundancy)
    result = mod.compute_merge_candidates(mem)
    assert result["ok"] is True
    assert len(result["pairs"]) == 1
    pair = result["pairs"][0]
    # a.md (feedback, access_count=10) must out-salience b.md (reference, 0).
    assert pair["a_salience"] > pair["b_salience"]
    assert pair["rank_salience"] == pytest.approx(pair["a_salience"])


def test_merge_degrades_gracefully_when_embedder_unavailable(tmp_path, monkeypatch):
    mod = load_module()
    mem = tmp_path / "auto-memory"
    mem.mkdir(parents=True)

    def fake_scan_redundancy(memory_dir, threshold=0.86, embedder=None, timeout=120):
        return {"ok": False, "pairs": [], "note": "embedder unavailable: no ollama"}

    monkeypatch.setattr(mod, "scan_redundancy", fake_scan_redundancy)
    result = mod.compute_merge_candidates(mem)
    assert result["ok"] is False
    assert result["pairs"] == []


def test_detector_never_mutates_auto_memory(tmp_path, monkeypatch):
    mod = load_module()
    mem = tmp_path / "auto-memory"
    _write(mem / "stale-low.md", _fact("reference", 0, "stale-low"), days_old=90)
    _write(mem / "stale-high.md", _fact("feedback", 20, "stale-high"), days_old=90)

    monkeypatch.setattr(mod, "get_auto_memory_dir", lambda: mem)
    monkeypatch.setattr(mod, "scan_redundancy", lambda memory_dir, threshold=0.86, embedder=None, timeout=120: {
        "ok": True, "pairs": [], "note": "fewer than 2 memory files"
    })

    before = {p.name: (p.read_text(encoding="utf-8"), p.stat().st_mtime) for p in mem.glob("*.md")}
    result = mod.gather()
    mod.render_report(result, "2026-07-16T03:10:00+00:00")
    after = {p.name: (p.read_text(encoding="utf-8"), p.stat().st_mtime) for p in mem.glob("*.md")}
    assert before == after


def test_write_report_single_file(tmp_path, monkeypatch):
    mod = load_module()
    out_root = tmp_path / "out"
    monkeypatch.setattr(mod, "get_outputs_dir", lambda: out_root)

    path = mod.write_report("# report\n", datetime(2026, 7, 16, 3, 10, 0, tzinfo=timezone.utc))
    report_dir = out_root / "operations" / "dream"
    files = list(report_dir.iterdir())
    assert files == [path]
    assert path.name == "2026-07-16_dream-shadow_report.md"
    assert path.read_text(encoding="utf-8") == "# report\n"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
