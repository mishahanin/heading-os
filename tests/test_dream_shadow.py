"""Regression tests for scripts/dream-shadow.py -- the nightly memory
consolidation worklist (Gap #1). Covers the dormancy list: an old, never- or
not-recently-surfaced fact is dormant; an old fact the retriever keeps
surfacing is not, whatever its type weight; a fact too young to have had its
chance is never dormant; the rendered report proposes no removal. Also covers
a near-duplicate pair surfaced merge-ranked by salience, and that the
detector never mutates auto-memory/.

Fixtures below pair an aged mtime with a recent `last_accessed`. That state is
reachable in production only because the access bump restores the file's mtime
(scripts/utils/memory_touch.py, and tests/test_memory_touch_util.py pins it):
a bump is access metadata, not a content edit. Were mtime restamped, the age
gate would exclude every recently surfaced file before the access clause was
consulted, and these fixtures would be describing a state the system cannot
reach.

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


def _fact(mem_type: str, access_count: int, name: str, last_accessed: str = "") -> str:
    last = f"  last_accessed: {last_accessed}\n" if last_accessed else ""
    return (
        "---\n"
        f"name: {name}\n"
        "description: fixture fact\n"
        "metadata:\n"
        f"  node_type: memory\n"
        f"  type: {mem_type}\n"
        f"  access_count: {access_count}\n"
        f"{last}"
        "---\n\n"
        "Some fact body.\n"
    )


def test_old_and_never_surfaced_is_dormant(tmp_path):
    mod = load_module()
    mem = tmp_path / "auto-memory"
    _write(mem / "quiet.md", _fact("reference", 0, "quiet"), days_old=90)

    now = datetime.now(timezone.utc)
    names = [c["name"] for c in mod.compute_dormant(mem, now)]
    assert "quiet.md" in names


def test_old_but_recently_surfaced_is_not_dormant(tmp_path):
    """Access, not type, is what the list is about now. A reference-type fact
    the retriever keeps surfacing is in active use whatever its type weight.

    The fixture — 90-day-old mtime, surfaced today — is exactly what a bumped
    memory looks like on disk, because the bump restores mtime."""
    mod = load_module()
    mem = tmp_path / "auto-memory"
    today = datetime.now(timezone.utc).date().isoformat()
    _write(mem / "busy.md", _fact("reference", 12, "busy", last_accessed=today), days_old=90)

    now = datetime.now(timezone.utc)
    names = [c["name"] for c in mod.compute_dormant(mem, now)]
    assert "busy.md" not in names


def test_recent_file_is_never_dormant(tmp_path):
    """A memory written yesterday has had no chance to be surfaced."""
    mod = load_module()
    mem = tmp_path / "auto-memory"
    _write(mem / "fresh.md", _fact("reference", 0, "fresh"), days_old=1)

    now = datetime.now(timezone.utc)
    names = [c["name"] for c in mod.compute_dormant(mem, now)]
    assert "fresh.md" not in names


def test_dormancy_is_access_based_not_salience_based(tmp_path):
    """Guards against a silent revert to the retired salience-threshold rule.

    composite_salience("reference", 12) == 0.6603, which clears the old
    PRUNE_SALIENCE_THRESHOLD of 0.6 — under the retired rule this file would
    NOT have been flagged (0.6603 >= 0.6). But its last_accessed is over 200
    days stale (2026-01-01), well past the dormancy window, so under the new
    access-and-recency rule it IS dormant: a decent lifetime count and a
    high-weight type do not exempt a fact the retriever has stopped
    surfacing recently. Every dormancy test above this one would still pass
    if compute_dormant were silently reimplemented as
    `age > 45 AND composite_salience < 0.6` — this is the one case that
    tells the two rules apart.
    """
    mod = load_module()
    mem = tmp_path / "auto-memory"
    _write(
        mem / "cooling.md",
        _fact("reference", 12, "cooling", last_accessed="2026-01-01"),
        days_old=90,
    )

    now = datetime.now(timezone.utc)
    names = [c["name"] for c in mod.compute_dormant(mem, now)]
    assert "cooling.md" in names


def test_report_proposes_no_removal(tmp_path, monkeypatch):
    """The directive is that nothing is ever pruned. The report must not tell a
    reader to remove anything, or a reader will.

    Scoped to ACTIONABLE phrasing rather than bare word stems. Stems were worse
    than useless here: the report legitimately says "is a candidate for removal"
    (negated) and "never proposes removing a fact", neither of which contains
    the stem "remove", so the check passed on removal vocabulary while standing
    ready to fail an honest rewording that happened to use the word.
    """
    mod = load_module()
    mem = tmp_path / "auto-memory"
    _write(mem / "quiet.md", _fact("reference", 0, "quiet"), days_old=90)

    monkeypatch.setattr(mod, "get_auto_memory_dir", lambda: mem)
    monkeypatch.setattr(mod, "scan_redundancy", lambda memory_dir, threshold=0.86, embedder=None, timeout=120: {
        "ok": True, "pairs": [], "note": "fewer than 2 memory files"
    })
    text = mod.render_report(mod.gather(), "2026-08-08T03:10:00+00:00").lower()
    assert "quiet.md" in text

    # Every phrasing a report would use to PROPOSE a deletion, including the
    # retired section header and the command the retired flow told /dream to run.
    for phrase in (
        "prune candidate",
        "retire-memory.py",
        "retire it",
        "safe to remove",
        "safe to delete",
        "consider removing",
        "recommend removing",
        "should be removed",
        "can be removed",
        "candidates for removal",
    ):
        assert phrase not in text, f"the report proposes a deletion: {phrase!r}"

    # And the standing disclaimer is present in words, so a rewrite that merely
    # dropped the vocabulary without keeping the promise still fails.
    assert "nothing listed here is a candidate for removal" in text
    assert "never proposes removing a fact" in text


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
