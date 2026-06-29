#!/usr/bin/env python3
"""R8 -- PageRank associative recall over Odin's [[wiki-links]] graph.

Additive enrichment to R7 (recency x importance x relevance). Parses
``[[ID|Label]]`` / ``[[slug]]`` wiki-links from Odin brain notes, builds a
directed graph, and runs Personalized PageRank (pure-Python power iteration --
no new dependency) seeded on the query's matching entities. Returns note IDs
ranked by graph proximity: a note connected to high-ranking entities inherits
their importance, surfacing multi-hop associations that similarity search alone
cannot reach (HippoRAG, NeurIPS 2024).

Read-only with respect to the brain. Disabled by default; the /odin recall mode
activates it via ``config/memory-index.yaml`` section ``pagerank.enabled: true``.

This module is snake_case because tests and the recall mode import it. It is
also runnable as a CLI.

Usage:
    python3 scripts/odin_pagerank.py recall "<query>" [--top-k N] [--mode ppr|r7+ppr|hybrid] [--json]
    python3 scripts/odin_pagerank.py graph-stats [--json]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils.workspace import get_knowledge_dir, get_workspace_root  # noqa: E402
from scripts.utils.air_gap import is_denied  # noqa: E402

try:  # pyyaml is pinned in requirements; degrade to defaults if absent.
    import yaml
except Exception:  # pragma: no cover - yaml is a hard dependency in practice
    yaml = None

# ============================================================
# Configuration
# ============================================================

DAMPING = 0.85  # standard PageRank teleport probability

_DEFAULT_PAGERANK_CFG = {
    "enabled": False,
    "convergence_threshold": 1.0e-6,
    "max_iterations": 100,
    "mode": "r7+ppr",
    "blend_weights": {"relevance": 0.50, "pagerank": 0.50},
    "seed_threshold": 0.45,
}

WIKILINK_RE = re.compile(r"\[\[\s*([^\]|]+?)\s*(?:\|[^\]]*)?\]\]")
FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)

_STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "for", "of", "to", "in", "on", "at",
    "is", "are", "be", "with", "by", "as", "it", "this", "that", "from", "what",
    "how", "why", "when", "do", "does", "we", "our", "you", "your", "about",
}


def load_pagerank_config(root: Path) -> dict:
    """Read the ``pagerank`` section of config/memory-index.yaml, filling
    sensible defaults for any missing key. Never raises -- a missing or
    malformed config yields the disabled-by-default block."""
    cfg = json.loads(json.dumps(_DEFAULT_PAGERANK_CFG))  # deep copy
    path = root / "config" / "memory-index.yaml"
    if yaml is None or not path.exists():
        return cfg
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        section = loaded.get("pagerank") or {}
        for key, default in _DEFAULT_PAGERANK_CFG.items():
            if key == "blend_weights":
                bw = section.get("blend_weights") or {}
                cfg["blend_weights"] = {
                    "relevance": float(bw.get("relevance", bw.get("cosine", default["relevance"]))),
                    "pagerank": float(bw.get("pagerank", default["pagerank"])),
                }
            elif key in section:
                cfg[key] = section[key]
    except Exception:
        return json.loads(json.dumps(_DEFAULT_PAGERANK_CFG))
    return cfg


# ============================================================
# Parsing helpers
# ============================================================

def _slug(text: str) -> str:
    s = re.sub(r"[^\w\s-]", "", (text or "").lower())
    s = re.sub(r"[\s_]+", "-", s).strip("-")
    return s


def _tokenize(text: str) -> set[str]:
    toks = re.split(r"[^a-z0-9]+", (text or "").lower())
    return {t for t in toks if len(t) >= 3 and t not in _STOPWORDS}


def parse_frontmatter(text: str) -> dict:
    """Return the YAML frontmatter as a dict (empty if none / unparseable)."""
    m = FRONTMATTER_RE.match(text)
    if not m:
        return {}
    if yaml is None:
        return {}
    try:
        data = yaml.safe_load(m.group(1))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def parse_wikilinks(text: str) -> list[str]:
    """Extract the target token of every ``[[target|label]]`` / ``[[target]]``
    wiki-link. Malformed openers (``[[`` with no close) are ignored by the regex.
    """
    return [m.strip() for m in WIKILINK_RE.findall(text or "")]


# ============================================================
# Graph
# ============================================================

class BrainGraph:
    def __init__(self) -> None:
        self.nodes: dict[str, dict] = {}        # nodekey -> meta
        self.adjacency: dict[str, set[str]] = {}  # nodekey -> set(nodekey)
        self._resolver: dict[str, str] = {}      # token (id/stem/slug) -> nodekey

    def node_count(self) -> int:
        return len(self.nodes)

    def edge_count(self) -> int:
        return sum(len(v) for v in self.adjacency.values())

    def out_degree(self, key: str) -> int:
        return len(self.adjacency.get(key, ()))


def _confidence_to_weight(conf) -> float:
    return {"high": 1.0, "medium": 0.6, "low": 0.3}.get(str(conf).lower(), 0.6)


def build_graph(brain_root: Path, workspace_root: Path | None = None) -> BrainGraph:
    """Walk Odin brain markdown, parse frontmatter + wiki-links, build a
    directed graph keyed by note id (falling back to filename stem). Air-gapped
    paths (``_secure/``, ``personal`` segment) are never added (is_denied)."""
    if workspace_root is None:
        workspace_root = brain_root
    g = BrainGraph()
    raw_links: dict[str, list[str]] = {}

    for path in sorted(brain_root.rglob("*.md")):
        try:
            rel = str(path.relative_to(workspace_root)).replace("\\", "/")
        except ValueError:
            rel = str(path).replace("\\", "/")
        if is_denied(rel):
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        fm = parse_frontmatter(text)
        node_id = str(fm.get("id") or "").strip()
        stem = path.stem
        key = node_id or stem
        if key in g.nodes:
            # Deterministic dedup: first file wins; skip the duplicate.
            continue
        title = str(fm.get("title") or stem)
        keywords = fm.get("keywords") or []
        if isinstance(keywords, str):
            keywords = [keywords]
        g.nodes[key] = {
            "id": node_id or stem,
            "title": title,
            "path": str(path),
            "rel": rel,
            "confidence": fm.get("confidence", "medium"),
            "importance": _confidence_to_weight(fm.get("confidence", "medium")),
            "keywords": [str(k) for k in keywords],
            "created": str(fm.get("created") or ""),
            "updated": str(fm.get("updated") or ""),
        }
        g.adjacency.setdefault(key, set())
        # Body links (strip the frontmatter region so frontmatter refs are ignored).
        body = FRONTMATTER_RE.sub("", text, count=1)
        raw_links[key] = parse_wikilinks(body)
        # Register resolver tokens (most specific first).
        for token in {node_id, stem, _slug(stem), _slug(title)}:
            if token:
                g._resolver.setdefault(token, key)

    # Second pass: resolve link targets to known node keys.
    for src, targets in raw_links.items():
        for tok in targets:
            dst = g._resolver.get(tok) or g._resolver.get(_slug(tok))
            if dst and dst != src:
                g.adjacency[src].add(dst)
    return g


# ============================================================
# Personalized PageRank (pure-Python power iteration)
# ============================================================

def personalized_pagerank(
    graph: BrainGraph,
    seeds: dict[str, float] | None = None,
    alpha: float = DAMPING,
    tol: float = 1.0e-6,
    max_iter: int = 100,
) -> dict[str, float]:
    """Power-iteration PPR. ``seeds`` is the personalization vector (nodekey ->
    weight); empty/None means uniform teleport (= standard PageRank). Dangling
    nodes redistribute their mass via the teleport vector. Scores sum to ~1.0."""
    nodes = list(graph.nodes)
    n = len(nodes)
    if n == 0:
        return {}

    # Personalization / teleport vector p.
    pos = {k: float(seeds.get(k, 0.0)) for k in nodes} if seeds else {}
    total = sum(pos.values())
    if total > 0:
        p = {k: pos[k] / total for k in nodes}
    else:
        p = {k: 1.0 / n for k in nodes}

    rank = dict(p)
    out_deg = {k: graph.out_degree(k) for k in nodes}

    for _ in range(max_iter):
        dangling = sum(rank[k] for k in nodes if out_deg[k] == 0)
        new = {k: (1.0 - alpha) * p[k] + alpha * dangling * p[k] for k in nodes}
        for src in nodes:
            d = out_deg[src]
            if d:
                share = alpha * rank[src] / d
                for dst in graph.adjacency[src]:
                    new[dst] += share
        diff = sum(abs(new[k] - rank[k]) for k in nodes)
        rank = new
        if diff < tol:
            break
    return rank


# ============================================================
# Seeding + recall
# ============================================================

def seed_from_query(query: str, graph: BrainGraph) -> dict[str, float]:
    """Lexical seed selection: score each node by token overlap between the
    query and the note's title + keywords. Returns {nodekey: weight} for nodes
    with any overlap (empty -> uniform PPR). This is the dependency-free default;
    the recall mode may instead pass embedding-cosine seeds for semantic match."""
    q = _tokenize(query)
    if not q:
        return {}
    seeds: dict[str, float] = {}
    for key, meta in graph.nodes.items():
        hay = _tokenize(meta["title"] + " " + " ".join(meta.get("keywords", [])))
        overlap = len(q & hay)
        if overlap:
            seeds[key] = float(overlap)
    return seeds


def recall_by_graph(
    query: str,
    brain_root: Path,
    workspace_root: Path | None = None,
    cfg: dict | None = None,
    top_k: int = 8,
    mode: str | None = None,
    seeds: dict[str, float] | None = None,
) -> list[dict]:
    """Build the graph, seed PPR from the query (or explicit ``seeds``), and
    return the top-k notes ranked by graph proximity. ``mode``:
      - ``ppr``    : rank purely by PageRank score.
      - ``r7+ppr`` : blend PageRank with lexical relevance (config blend_weights).
      - ``hybrid`` : same blend, but the candidate pool unions PPR-ranked and
                     lexically-relevant notes before re-ranking.
    """
    cfg = cfg or _DEFAULT_PAGERANK_CFG
    mode = mode or cfg.get("mode", "r7+ppr")
    g = build_graph(brain_root, workspace_root)
    if g.node_count() < 3:
        return []

    seed_vec = seeds if seeds is not None else seed_from_query(query, g)
    rank = personalized_pagerank(
        g,
        seeds=seed_vec,
        tol=float(cfg.get("convergence_threshold", 1.0e-6)),
        max_iter=int(cfg.get("max_iterations", 100)),
    )

    # Lexical relevance, normalised to [0,1] for blending.
    rel_raw = seed_from_query(query, g)
    rel_max = max(rel_raw.values()) if rel_raw else 0.0
    rel = {k: (rel_raw.get(k, 0.0) / rel_max if rel_max else 0.0) for k in g.nodes}
    ppr_max = max(rank.values()) if rank else 0.0
    ppr_norm = {k: (rank.get(k, 0.0) / ppr_max if ppr_max else 0.0) for k in g.nodes}

    bw = cfg.get("blend_weights", {"relevance": 0.5, "pagerank": 0.5})
    w_rel = float(bw.get("relevance", bw.get("cosine", 0.5)))
    w_ppr = float(bw.get("pagerank", 0.5))

    candidates = list(g.nodes)
    if mode == "hybrid":
        # Pool: notes that are either graph-ranked or lexically relevant.
        candidates = [k for k in g.nodes if ppr_norm[k] > 0 or rel[k] > 0]

    rows = []
    for k in candidates:
        meta = g.nodes[k]
        if mode == "ppr":
            combined = rank.get(k, 0.0)
        else:  # r7+ppr or hybrid
            combined = w_rel * rel[k] + w_ppr * ppr_norm[k]
        rows.append({
            "id": meta["id"],
            "title": meta["title"],
            "path": meta["rel"],
            "ppr_score": round(rank.get(k, 0.0), 6),
            "relevance_score": round(rel[k], 6),
            "combined_score": round(combined, 6),
            "rank_source": mode,
        })
    rows.sort(key=lambda r: r["combined_score"], reverse=True)
    return rows[:top_k]


# ============================================================
# CLI
# ============================================================

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Odin PageRank associative recall (R8).")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_recall = sub.add_parser("recall", help="Rank brain notes by graph proximity to a query.")
    p_recall.add_argument("query", help="Topic / question to seed the graph walk.")
    p_recall.add_argument("--top-k", type=int, default=8)
    p_recall.add_argument("--mode", choices=["ppr", "r7+ppr", "hybrid"], default=None)
    p_recall.add_argument("--json", action="store_true")

    p_stats = sub.add_parser("graph-stats", help="Report graph node/edge counts.")
    p_stats.add_argument("--json", action="store_true")

    args = parser.parse_args(argv)
    root = get_workspace_root()
    brain_root = get_knowledge_dir() / "odin-brain"
    cfg = load_pagerank_config(root)

    if not brain_root.exists():
        print(f"Odin brain not found at {brain_root}", file=sys.stderr)
        return 2

    if args.cmd == "graph-stats":
        g = build_graph(brain_root, root)
        stats = {"nodes": g.node_count(), "edges": g.edge_count()}
        print(json.dumps(stats) if args.json else f"nodes={stats['nodes']} edges={stats['edges']}")
        return 0

    rows = recall_by_graph(args.query, brain_root, root, cfg, top_k=args.top_k, mode=args.mode)
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
    elif not rows:
        print("(graph too small or no matches; fall back to R7 recall)")
    else:
        for i, r in enumerate(rows, 1):
            print(f"{i:2d}. [{r['combined_score']:.3f}] {r['title']}  ({r['rank_source']}; ppr={r['ppr_score']:.4f})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
