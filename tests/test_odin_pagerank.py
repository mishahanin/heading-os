"""Tests for R8 -- PageRank associative recall over Odin wiki-links.

Covers: wiki-link parsing edge cases, deterministic graph construction,
PPR convergence + stochastic property, seeding effect, dangling-node handling,
cluster isolation, air-gap exclusion, recall ordering, and config defaults.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts import odin_pagerank as pr


def _note(dirpath: Path, node_id: str, title: str, keywords, body_links, confidence="high"):
    kw = ", ".join(keywords)
    links = "\n".join(f"- [[{t}]]" for t in body_links)
    text = (
        f"---\n"
        f'id: "{node_id}"\n'
        f'title: "{title}"\n'
        f"type: principle\n"
        f"confidence: {confidence}\n"
        f"keywords: [{kw}]\n"
        f"created: 2026-05-01\n"
        f"updated: 2026-05-01\n"
        f"---\n\n"
        f"# {title}\n\n## Principle\nBody.\n\n## Links\n{links}\n"
    )
    (dirpath / f"{title.lower().replace(' ', '-')}.md").write_text(text, encoding="utf-8")


@pytest.fixture
def brain(tmp_path):
    root = tmp_path
    bdir = root / "knowledge" / "odin-brain" / "principles"
    bdir.mkdir(parents=True)
    # Interconnected cluster A<->B<->C (cycle) + A->C
    _note(bdir, "1001", "Negotiation tactical empathy", ["negotiation", "empathy"], ["1002", "1003"])
    _note(bdir, "1002", "Relationship trust building", ["relationship", "trust"], ["1003"])
    _note(bdir, "1003", "Closing the deal", ["closing", "deal"], ["1001"])
    # Small isolated pair D<-E
    _note(bdir, "1004", "Isolated sailing note", ["sailing"], [])  # dangling (no outlinks)
    _note(bdir, "1005", "Another sailing note", ["sailing"], ["1004"])
    return root, root / "knowledge" / "odin-brain"


def test_parse_wikilinks_edge_cases():
    text = "see [[1002|multi word label]] and [[1003]] but [[unclosed and [plain bracket]"
    assert pr.parse_wikilinks(text) == ["1002", "1003"]


def test_graph_build_adjacency_deterministic(brain):
    root, bdir = brain
    g1 = pr.build_graph(bdir, root)
    g2 = pr.build_graph(bdir, root)
    assert g1.node_count() == 5
    assert g1.adjacency == g2.adjacency  # deterministic
    assert g1.adjacency["1001"] == {"1002", "1003"}
    assert g1.adjacency["1003"] == {"1001"}
    assert g1.adjacency["1004"] == set()  # dangling


def test_ppr_converges_and_sums_to_one(brain):
    root, bdir = brain
    g = pr.build_graph(bdir, root)
    rank = pr.personalized_pagerank(g)
    assert abs(sum(rank.values()) - 1.0) < 1e-6
    assert all(v >= 0 for v in rank.values())


def test_seeding_boosts_seeded_node(brain):
    root, bdir = brain
    g = pr.build_graph(bdir, root)
    uniform = pr.personalized_pagerank(g)
    seeded = pr.personalized_pagerank(g, seeds={"1001": 1.0})
    assert seeded["1001"] > uniform["1001"]


def test_dangling_node_handled(brain):
    root, bdir = brain
    g = pr.build_graph(bdir, root)
    rank = pr.personalized_pagerank(g, seeds={"1005": 1.0})
    # mass flows E(1005) -> D(1004); D must receive rank despite being a sink
    assert rank["1004"] > 0
    assert abs(sum(rank.values()) - 1.0) < 1e-6


def test_cluster_isolation(brain):
    root, bdir = brain
    g = pr.build_graph(bdir, root)
    # Seed the interconnected cluster; its members should outrank the isolated pair.
    rank = pr.personalized_pagerank(g, seeds={"1001": 1.0})
    cluster = min(rank["1001"], rank["1002"], rank["1003"])
    isolated = max(rank["1004"], rank["1005"])
    assert cluster > isolated


def test_recall_orders_by_combined_score(brain):
    root, bdir = brain
    rows = pr.recall_by_graph("negotiation and closing the deal", bdir, root, top_k=3)
    assert len(rows) == 3
    scores = [r["combined_score"] for r in rows]
    assert scores == sorted(scores, reverse=True)
    # negotiation-relevant nodes should surface in the top results
    top_ids = {r["id"] for r in rows}
    assert "1001" in top_ids or "1003" in top_ids


def test_recall_skips_tiny_graph(tmp_path):
    bdir = tmp_path / "knowledge" / "odin-brain"
    (bdir / "principles").mkdir(parents=True)
    _note(bdir / "principles", "1", "Only note", ["x"], [])
    assert pr.recall_by_graph("anything", bdir, tmp_path) == []


def test_airgap_excludes_denied_paths(brain):
    root, bdir = brain
    # A note under a 'personal' segment must never enter the graph.
    pdir = bdir / "personal"
    pdir.mkdir()
    _note(pdir, "9999", "Secret personal note", ["secret"], ["1001"])
    g = pr.build_graph(bdir, root)
    assert "9999" not in g.nodes
    assert g.node_count() == 5


def test_config_defaults_when_missing(tmp_path):
    cfg = pr.load_pagerank_config(tmp_path)  # no config dir
    assert cfg["enabled"] is False
    assert cfg["mode"] == "r7+ppr"
    assert cfg["max_iterations"] == 100
    assert cfg["blend_weights"]["pagerank"] == 0.5


def test_modes_produce_rankings(brain):
    root, bdir = brain
    for mode in ("ppr", "r7+ppr", "hybrid"):
        rows = pr.recall_by_graph("negotiation", bdir, root, top_k=5, mode=mode)
        assert rows, f"mode {mode} returned nothing"
        assert all(r["rank_source"] == mode for r in rows)
