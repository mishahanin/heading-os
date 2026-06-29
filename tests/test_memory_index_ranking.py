"""Ranking tests for the R7 recency x importance x relevance combiner.

Two layers:
  - unit tests of _recency_score / _importance_score / _combined (clock-fed, so
    deterministic regardless of the machine clock);
  - an end-to-end build+query on a hermetic temp workspace with a MOCK embedder
    that gives every candidate the SAME cosine, so the combiner alone decides
    order: a recent high-confidence note must outrank a stale low-confidence one,
    and a status:evergreen note must NOT be buried by age.

Run: python3 -m pytest tests/test_memory_index_ranking.py
"""
import importlib.util
import sys
import types
from pathlib import Path

import pytest

WORKSPACE = Path(__file__).resolve().parent.parent
SCRIPT = WORKSPACE / "scripts" / "memory-index.py"

# Vocabulary EXCLUDES title words on purpose: only body terms drive the vector,
# so all four notes (identical bodies) get identical cosine and the combiner is
# the sole tie-breaker.
VOCAB = ["leverage", "negotiation"]


def fake_embed(texts, *, model, host, batch=32, timeout=120):
    vecs = []
    for t in texts:
        low = t.lower()
        v = [float(low.count(w)) for w in VOCAB]
        if not any(v):
            v = [1e-6] * len(VOCAB)
        vecs.append(v)
    return vecs


def load_module():
    sys.path.insert(0, str(WORKSPACE))
    spec = importlib.util.spec_from_file_location("memory_index_rank_mod", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def write(path: Path, body: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


# --- Unit tests: combiner pieces (clock-independent) ----------------------

def test_recency_evergreen_floor_ignores_age():
    mod = load_module()
    now = mod._date_to_ts("2026-06-03")
    # Ancient created date, but evergreen -> floored, not decayed.
    s = mod._recency_score({"status": "evergreen", "created": "2000-01-01"}, 180, now)
    assert s == pytest.approx(0.7)


def test_recency_recent_beats_stale():
    mod = load_module()
    now = mod._date_to_ts("2026-06-03")
    recent = mod._recency_score({"created": "2026-06-01"}, 180, now)
    stale = mod._recency_score({"created": "2023-01-01"}, 180, now)
    assert recent > stale
    assert recent <= 1.0 and stale >= 0.0


def test_recency_falls_back_to_mtime():
    mod = load_module()
    now = mod._date_to_ts("2026-06-03")
    one_day_old = mod._recency_score({"mtime": now - 86400}, 180, now)
    assert 0.9 < one_day_old <= 1.0


def test_importance_confidence_and_episode_bias():
    mod = load_module()
    assert mod._importance_score({"confidence": "high"}) == pytest.approx(1.0)
    assert mod._importance_score({"confidence": "low"}) == pytest.approx(0.4)
    assert mod._importance_score({}) == pytest.approx(0.7)  # default medium
    assert mod._importance_score({"confidence": "high", "ntype": "episode"}) == pytest.approx(0.8)


def test_combined_is_weighted_sum():
    mod = load_module()
    w = {"semantic": 0.60, "recency": 0.20, "importance": 0.20}
    assert mod._combined(1.0, 1.0, 1.0, w) == pytest.approx(1.0)
    assert mod._combined(0.9, 1, 1, w) > mod._combined(0.5, 1, 1, w)


# --- End-to-end: same cosine, combiner decides order ----------------------

@pytest.fixture
def ranked_index(tmp_path, monkeypatch):
    root = tmp_path
    body = "\n\nleverage negotiation leverage negotiation.\n"
    write(root / "knowledge/odin-brain/positions/recent-high.md",
          "---\ntitle: Recent High\ntype: position\ncreated: 2026-06-01\nupdated: 2026-06-01\nconfidence: high\n---\n# Recent High" + body)
    write(root / "knowledge/odin-brain/positions/stale-low.md",
          "---\ntitle: Stale Low\ntype: position\ncreated: 2020-01-01\nupdated: 2020-01-01\nconfidence: low\n---\n# Stale Low" + body)
    write(root / "knowledge/odin-brain/positions/evergreen-old.md",
          "---\ntitle: Evergreen Old\ntype: position\ncreated: 2019-01-01\nstatus: evergreen\nconfidence: high\n---\n# Evergreen Old" + body)
    write(root / "knowledge/odin-brain/positions/no-fields.md",
          "---\ntitle: No Fields\ntype: position\n---\n# No Fields" + body)
    write(root / "config/memory-index.yaml",
          "model: bge-m3\nhost: http://localhost:11434\nthreshold: 0.55\ntop_k: 8\n"
          "layers:\n  - {layer: odin, glob: 'knowledge/odin-brain/positions/*.md'}\n"
          "deny_prefixes: ['_secure/']\ndeny_segments: ['personal']\n")

    mod = load_module()
    # The index DB resolves via get_data_root() (STORE_REL under it), not
    # get_workspace_root(). HEADING_OS_DATA wins first in get_data_root(), so
    # this keeps the build hermetic instead of writing into the REAL
    # ../.heading-os-data/.memory-index/index.db.
    monkeypatch.setenv("HEADING_OS_DATA", str(root))
    monkeypatch.setattr(mod, "get_workspace_root", lambda: root)
    monkeypatch.setattr(mod, "embed", fake_embed)
    assert mod.cmd_build(types.SimpleNamespace(force=True)) == 0
    # Isolation guard: DB under the temp root, never the real data root.
    assert (root / mod.STORE_REL).is_file()
    return mod, root


def _order(out: str, titles):
    """Return titles in the order they appear in the query output."""
    positions = [(out.find(t), t) for t in titles]
    return [t for pos, t in sorted(positions) if pos >= 0]


def test_recent_high_outranks_stale_low(ranked_index, capsys):
    mod, _ = ranked_index
    capsys.readouterr()  # drop build output
    mod.cmd_query(types.SimpleNamespace(text="leverage negotiation", layer=None, top_k=8, threshold=None))
    out = capsys.readouterr().out
    order = _order(out, ["Recent High", "Stale Low", "Evergreen Old", "No Fields"])
    assert "Recent High" in order and "Stale Low" in order, out
    assert order.index("Recent High") < order.index("Stale Low"), order
    # Evergreen high-confidence must not be buried under the stale low-confidence note.
    assert order.index("Evergreen Old") < order.index("Stale Low"), order
    # Recent high-confidence is the top hit at equal cosine.
    assert order[0] == "Recent High", order


def test_missing_fields_do_not_crash_and_surface(ranked_index, capsys):
    mod, _ = ranked_index
    capsys.readouterr()
    mod.cmd_query(types.SimpleNamespace(text="leverage negotiation", layer=None, top_k=8, threshold=None))
    out = capsys.readouterr().out
    assert "No Fields" in out, out  # NULL created/confidence handled via fallbacks
