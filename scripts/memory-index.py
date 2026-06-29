#!/usr/bin/env python3
"""Local associative-memory index -- build / query / stats.

Semantic recall across the workspace's ceo-only business memory (Odin brain,
business threads, business CRM), computed entirely locally via ollama bge-m3.
Finds notes by MEANING, cross-lingual (RU <-> EN), where grep finds only exact
words. The score itself is a salience signal (hits ~0.56-0.61, empty ~0.49-0.53
on the tested Odin corpus; default threshold 0.55).

Usage:
    python3 scripts/memory-index.py build [--force]
    python3 scripts/memory-index.py query "<text>" [--layer L] [--top-k N] [--threshold T]
    python3 scripts/memory-index.py stats

Recall is HYBRID: a dense channel (bge-m3 cosine, gated by the threshold) and a
sparse channel (SQLite FTS5 BM25 lexical match), fused by Reciprocal Rank Fusion.
The dense gate is untouched, so the honest-gap signal survives: a query reports
"nothing" only when BOTH channels are empty. BM25 rescues the known near-miss
where the material is distributed across notes with no single dedicated one for
a dense vector to crystallise above threshold; a BM25-only hit must also clear a
semantic-adjacency gate, so a generic-term match cannot leak through as noise.
FTS5 + bm25() ship with the stdlib sqlite3 -- no new dependency.

The index ("hippocampus") lives at .memory-index/index.db -- gitignored, a
rebuildable cache, never source of truth. The layers ("neocortex") stay in git.

Air-gap is structural: denied paths are NEVER read, so they can never be
retrieved. Two classes are denied (config + hard-coded belt-and-braces):
  - prefix  _secure/   (the vault)
  - segment personal   (personal thread branches, any future personal CRM)
Business CRM (crm/contacts/, flat, no `personal` segment) IS indexed on
purpose -- this is a ceo-only tool and that content is what we want to recall.
"""

import argparse
import json
import re
import sqlite3
import sys
import time
from datetime import datetime, timezone
from itertools import groupby
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    import numpy as np
    import yaml
except ImportError as exc:
    sys.stderr.write(
        f"Missing runtime dependency: {exc.name}. "
        f"Install with: pip install numpy pyyaml\n"
    )
    sys.exit(1)

from scripts.utils.air_gap import is_denied
from scripts.utils.colors import BOLD, CYAN, GRAY, GREEN, RED, RESET, YELLOW
from scripts.utils.embeddings import EmbeddingError, embed
from scripts.utils.workspace import get_classification, get_data_root, get_workspace_root

# ============================================================
# Configuration
# ============================================================

CONFIG_REL = "config/memory-index.yaml"
STORE_REL = ".memory-index/index.db"
# Code-collection store. Content layers index the CEO's private DATA tree into
# STORE_REL (data root); the `code` collection (skill/rule) indexes the ENGINE
# tree into this store inside the engine clone. Each artifact on its own side of
# the engine/data seam: no cross-seam join, engine self-sufficient, no
# public-code rows in the private data repo. Gitignored, rebuildable cache.
CODE_STORE_REL = ".memory-index-code/index.db"
SNIPPET_CHARS = 500  # body snippet length for embedding; also query truncation

# Hybrid recall: sparse (BM25) channel + Reciprocal Rank Fusion.
RRF_K = 60            # RRF damping constant (standard); higher = flatter rank weighting
SPARSE_LIMIT = 40     # max BM25 candidates pulled from FTS before fusion
RANK_CAP = 50         # max dense candidates carried into fusion
# Convergence gate: a BM25-only hit surfaces only if it is ALSO semantically
# adjacent (cosine within this margin below the dense threshold). Drops the
# fan-out noise where a generic term ("value", "strategy") lexically matches
# unrelated notes; keeps genuine near-misses sitting just below threshold.
SPARSE_COS_MARGIN = 0.05
# Path-match channel: a query token that matches a file's PATH (folder/project/
# client name) is a high-precision identifier and is admitted BYPASSING the
# convergence gate -- but only when the token is RARE (document-frequency at or
# below this cap). A token matching more files than this is a generic directory
# word (outputs, datastore, a year) and stays gated, so it cannot flood results.
PATH_TOKEN_DF_CAP = 25
TOKEN_RE = re.compile(r"\w+", re.UNICODE)  # query -> bare word tokens (RU/EN)

# Air-gap predicate (is_denied + hard-coded denies) lives in scripts/utils/air_gap.py,
# the single shared source of truth, imported above. Do not re-inline a copy here.


def load_config(root: Path) -> dict:
    """Load config/memory-index.yaml. Clear error if absent or unparseable."""
    path = root / CONFIG_REL
    if not path.exists():
        sys.stderr.write(f"Config not found: {path}\n")
        sys.exit(1)
    try:
        cfg = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        sys.stderr.write(f"Cannot parse {path}: {e}\n")
        sys.exit(1)
    cfg.setdefault("model", "bge-m3")
    cfg.setdefault("host", "http://localhost:11434")
    cfg.setdefault("threshold", 0.55)
    cfg.setdefault("top_k", 8)
    cfg.setdefault("layers", [])
    cfg.setdefault("collections", {})
    cfg.setdefault("deny_prefixes", [])
    cfg.setdefault("deny_segments", [])
    # R7 ranking knobs: recency x importance x relevance combiner over the RRF
    # candidate set. Defaults ship per plan (Park et al.-style weighting);
    # tune from observed recall. Missing sub-keys are filled defensively.
    cfg.setdefault("rank_weights", {})
    weights = cfg["rank_weights"] if isinstance(cfg["rank_weights"], dict) else {}
    weights.setdefault("semantic", 0.60)
    weights.setdefault("recency", 0.20)
    weights.setdefault("importance", 0.20)
    cfg["rank_weights"] = weights
    cfg.setdefault("recency_decay", "exponential")
    cfg.setdefault("recency_halflife_days", 180)
    # Chunking knobs (point 2). Missing block / sub-keys filled defensively;
    # an empty enabled_layers list means "chunk nothing" (one row per file).
    cfg.setdefault("chunk", {})
    chunk = cfg["chunk"] if isinstance(cfg["chunk"], dict) else {}
    chunk.setdefault("enabled_layers", [])
    chunk.setdefault("max_chars", 700)
    chunk.setdefault("overlap", 120)
    chunk.setdefault("max_chunks", 12)
    cfg["chunk"] = chunk
    return cfg


# ============================================================
# Store (SQLite)
# ============================================================

SCHEMA = """
CREATE TABLE IF NOT EXISTS notes (
    id        TEXT PRIMARY KEY,
    path      TEXT NOT NULL,
    title     TEXT,
    layer     TEXT NOT NULL,
    ntype     TEXT,
    mtime     REAL NOT NULL,
    dim       INTEGER NOT NULL,
    body      TEXT,
    created    TEXT,
    updated    TEXT,
    confidence TEXT,
    status     TEXT,
    classification TEXT,
    chunk     INTEGER NOT NULL DEFAULT 0,
    embedding BLOB NOT NULL
);
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    val TEXT
);
CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts USING fts5(id UNINDEXED, body);
"""


def open_store(root: Path, store_rel: str = STORE_REL) -> sqlite3.Connection:
    store_path = root / store_rel
    store_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(store_path))
    conn.executescript(SCHEMA)
    # Migrate a pre-hybrid store: add the body column the FTS channel reads from.
    cols = {row[1] for row in conn.execute("PRAGMA table_info(notes)")}
    if "body" not in cols:
        conn.execute("ALTER TABLE notes ADD COLUMN body TEXT")
    # R7 migration (non-destructive, vs scrutiny M2's drop+recreate): an existing
    # index gains created/updated/confidence/status as NULL columns, so query
    # never hits a stale-schema error. NULLs fall back to mtime (recency) and
    # medium (importance) in the combiner; `build --force` repopulates them from
    # frontmatter. Preserving the embeddings avoids a needless full re-embed.
    for _col in ("created", "updated", "confidence", "status"):
        if _col not in cols:
            conn.execute(f"ALTER TABLE notes ADD COLUMN {_col} TEXT")
    # Classification migration (non-destructive, same pattern as R7): an existing
    # index gains a NULL `classification` column; `build` repopulates it from the
    # workspace resolver. Lets query scope corporate vs ceo-only and keeps the
    # schema exec-safe-by-construction. The whole index stays ceo-only regardless.
    if "classification" not in cols:
        conn.execute("ALTER TABLE notes ADD COLUMN classification TEXT")
    # Chunking migration (point 2): an existing index gains a `chunk` column
    # defaulting to 0, so every pre-chunk row reads as chunk 0 (id == path) until
    # `build` re-chunks the long-form layers. Non-destructive, like the above.
    if "chunk" not in cols:
        conn.execute("ALTER TABLE notes ADD COLUMN chunk INTEGER NOT NULL DEFAULT 0")
    return conn


def upsert_note(conn, *, id_, path, title, layer, ntype, mtime, body, vec,
                created="", updated="", confidence="", status="",
                classification="", chunk=0) -> None:
    blob = np.asarray(vec, dtype=np.float32).tobytes()
    conn.execute(
        """
        INSERT INTO notes (id, path, title, layer, ntype, mtime, dim, body,
                           created, updated, confidence, status, classification,
                           chunk, embedding)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            path=excluded.path, title=excluded.title, layer=excluded.layer,
            ntype=excluded.ntype, mtime=excluded.mtime, dim=excluded.dim,
            body=excluded.body, created=excluded.created, updated=excluded.updated,
            confidence=excluded.confidence, status=excluded.status,
            classification=excluded.classification, chunk=excluded.chunk,
            embedding=excluded.embedding
        """,
        (id_, path, title, layer, ntype, mtime, len(vec), body,
         created, updated, confidence, status, classification, chunk, blob),
    )


def resync_fts(conn) -> None:
    """Rebuild the FTS5 channel from the notes table (cheap; runs every build).

    A full re-index of the short snippets is milliseconds and sidesteps per-row
    FTS upsert/prune bookkeeping: after the dense pass settles `notes` (embeds,
    prunes stale, migrates), the lexical channel is derived deterministically
    from it, so the two channels can never drift.
    """
    conn.execute("DELETE FROM notes_fts")
    rows = conn.execute("SELECT id, COALESCE(body, '') FROM notes").fetchall()
    # Prepend humanized path tokens so folder/file names are BM25-searchable.
    # This touches the LEXICAL channel only -- notes.body (the dense embed text)
    # is left clean, so the semantic vector is never diluted by path words.
    conn.executemany(
        "INSERT INTO notes_fts(id, body) VALUES (?, ?)",
        [(id_, f"{humanize_path(id_)} {body}") for id_, body in rows],
    )


# ============================================================
# Indexer (build)
# ============================================================

FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def humanize_path(rel: str) -> str:
    """Path -> searchable tokens for the LEXICAL (BM25) channel ONLY.

    Strips the chunk suffix (`path#N`) and a trailing `.md`, turns `/ - _` into
    spaces, collapses whitespace -- so a folder / project / client name that lives
    only in the PATH (e.g. 'Meridian') becomes a BM25-matchable token. NEVER added
    to the dense embed text: that would dilute the semantic vector. All path
    segments are kept (no stop-word / segment-skip rule): generic directory words
    like 'outputs' or 'datastore' are caught by the existing SPARSE_COS_MARGIN
    convergence gate (a BM25-only hit must sit within 0.05 cosine of threshold),
    so they cannot leak through as noise. Do not "optimise" by stripping segments.
    """
    base = rel.split("#", 1)[0]
    if base.endswith(".md"):
        base = base[:-3]
    return re.sub(r"\s+", " ", re.sub(r"[/_-]", " ", base)).strip()


def expand_brace_glob(pattern: str):
    """Expand a single/nested {a,b,c} brace group into concrete glob patterns.

    pathlib/glob do not support brace expansion; the config uses it
    (e.g. odin-brain/{principles,positions,sources}/*.md).
    """
    m = re.search(r"\{([^{}]*)\}", pattern)
    if not m:
        return [pattern]
    pre, post = pattern[: m.start()], pattern[m.end() :]
    results = []
    for option in m.group(1).split(","):
        results.extend(expand_brace_glob(pre + option + post))
    return results


def chunk_text(body: str, *, max_chars, overlap, max_chunks):
    """Split body into >=1 overlapping chunks of ~max_chars, paragraph-aware.

    A short body (<= max_chars) returns a single chunk (today's behaviour). Longer
    bodies are packed on blank-line paragraph boundaries; a paragraph longer than
    max_chars is hard-split with `overlap` carried between slices. When a new chunk
    starts, it is seeded with the previous chunk's `overlap` trailing chars for
    continuity. Capped at max_chunks -- the tail is dropped (the cap bounds embed
    cost on huge extract tables; cmd_build logs the drop).
    """
    text = (body or "").strip()
    if not text:
        return [""]
    if len(text) <= max_chars:
        return [text]
    paras = re.split(r"\n\s*\n", text)
    chunks, cur = [], ""
    for p in paras:
        p = p.strip()
        if not p:
            continue
        if len(p) > max_chars:
            if cur:
                chunks.append(cur)
                cur = ""
            step = max(1, max_chars - overlap)
            for i in range(0, len(p), step):
                chunks.append(p[i:i + max_chars])
            continue
        if cur and len(cur) + 1 + len(p) > max_chars:
            chunks.append(cur)
            cur = (cur[-overlap:] + "\n" + p) if overlap else p
        else:
            cur = (cur + "\n" + p) if cur else p
    if cur:
        chunks.append(cur)
    return chunks[:max_chunks] if chunks else [text[:max_chars]]


def parse_note(text: str) -> dict:
    """Parse a markdown note. Returns a dict with title, ntype, embed_text and
    the R7 ranking inputs (created, updated, confidence, status) from frontmatter.

    All Odin notes carry created+updated (YYYY-MM-DD) + confidence
    (high/medium/low); positions/sources carry status: evergreen. Missing fields
    return "" - the combiner applies fallbacks (mtime for recency, medium for
    importance).
    """
    title, ntype = "", ""
    created = updated = confidence = status = ""
    body = text
    fm = FRONTMATTER_RE.match(text)
    if fm:
        body = text[fm.end() :]
        try:
            meta = yaml.safe_load(fm.group(1)) or {}
            if isinstance(meta, dict):
                title = str(meta.get("title", "") or "")
                ntype = str(meta.get("type", "") or "")
                created = str(meta.get("created", "") or "")
                updated = str(meta.get("updated", "") or "")
                confidence = str(meta.get("confidence", "") or "")
                status = str(meta.get("status", "") or "")
        except yaml.YAMLError:
            pass
    if not title:
        h1 = re.search(r"^#\s+(.+)$", body, re.MULTILINE)
        if h1:
            title = h1.group(1).strip()
    snippet = re.sub(r"\s+", " ", body).strip()[:SNIPPET_CHARS]
    embed_text = f"{title}\n{snippet}".strip() if title else snippet
    return {
        "title": title, "ntype": ntype, "embed_text": embed_text,
        "raw_body": body,   # frontmatter-stripped, newline-preserving (for chunk_text)
        "created": created, "updated": updated,
        "confidence": confidence, "status": status,
    }


def _store_targets(cfg):
    """The physical-store plan: one entry per store, each on its own side of the
    engine/data seam.

    content layers build from the DATA root (`get_data_root()`) into STORE_REL;
    the `code` collection (skill/rule) builds from the ENGINE root
    (`get_workspace_root()`) into CODE_STORE_REL. Returns a list of dicts
    {name, root, store_rel, layers(set)}. The roots are resolved here (not at
    import) so tests can monkeypatch get_data_root/get_workspace_root on this
    module. A configured layer that belongs to no collection is reported once and
    simply not built (it would otherwise silently route nowhere)."""
    colls = cfg.get("collections") or {}
    if colls:
        content_layers = set(colls.get("content", []))
        code_layers = set(colls.get("code", []))
    else:
        # Back-compat: no collections map -> every layer builds into the single
        # content store (the pre-split behaviour; keeps minimal configs working).
        content_layers = {lc["layer"] for lc in cfg.get("layers", [])}
        code_layers = set()
    targets = [{
        "name": "content", "root": get_data_root(),
        "store_rel": STORE_REL, "layers": content_layers,
    }]
    if code_layers:
        targets.append({
            "name": "code", "root": get_workspace_root(),
            "store_rel": CODE_STORE_REL, "layers": code_layers,
        })
    covered = content_layers | code_layers
    orphan = [lc["layer"] for lc in cfg.get("layers", []) if lc["layer"] not in covered]
    if orphan:
        sys.stderr.write(
            f"{YELLOW}warn:{RESET} layers in no collection (not built/queried): "
            f"{', '.join(sorted(set(orphan)))}\n"
        )
    return targets


def _layer_store_map(cfg):
    """layer -> (root, store_rel) for query routing. A layer appearing in two
    collections is an ambiguous route and raises (config error)."""
    m = {}
    for t in _store_targets(cfg):
        for lyr in t["layers"]:
            if lyr in m:
                raise ValueError(
                    f"layer '{lyr}' is in two stores; collection routing is ambiguous"
                )
            m[lyr] = (t["root"], t["store_rel"])
    return m


def cmd_build(args) -> int:
    cfg = load_config(get_workspace_root())  # memory-index.yaml is engine config
    targets = _store_targets(cfg)
    rc = 0
    for t in targets:
        store_rc = _build_store(cfg, t["root"], t["store_rel"], t["layers"], args.force)
        rc = rc or store_rc
    return rc


def _build_store(cfg, root, store_rel, layers, force) -> int:
    conn = open_store(root, store_rel)

    deny_prefixes = cfg["deny_prefixes"]
    deny_segments = cfg["deny_segments"]
    chunk_cfg = cfg["chunk"]
    chunk_layers = set(chunk_cfg["enabled_layers"])

    # Per-file mtime (a file's chunks all share its mtime), keyed by path.
    existing_by_path = {p: m for p, m in conn.execute("SELECT path, mtime FROM notes")}

    claimed = set()        # paths claimed this pass (first layer wins; prune basis)
    changed_paths = set()  # paths to delete-then-reinsert (new / changed / forced)
    pending = []           # (cid, rel, layer, chunk_idx, mtime, info, embed_text)
    skipped_uptodate = 0
    denied_count = 0
    capped = 0

    for layer_cfg in cfg["layers"]:
        layer = layer_cfg["layer"]
        if layer not in layers:
            continue            # this store builds only its collection's layers
        for sub in expand_brace_glob(layer_cfg["glob"]):
            for fpath in sorted(root.glob(sub)):
                if not fpath.is_file():
                    continue
                rel = fpath.relative_to(root).as_posix()
                if is_denied(rel, deny_prefixes, deny_segments):
                    denied_count += 1
                    continue            # NEVER read denied content
                if rel in claimed:
                    continue            # first layer to claim keeps the label
                claimed.add(rel)
                mtime = fpath.stat().st_mtime
                if (
                    not force
                    and rel in existing_by_path
                    and abs(existing_by_path[rel] - mtime) < 1e-6
                ):
                    skipped_uptodate += 1
                    continue
                changed_paths.add(rel)
                info = parse_note(
                    fpath.read_text(encoding="utf-8", errors="replace")
                )
                title = info["title"]
                if layer in chunk_layers:
                    pieces = chunk_text(
                        info["raw_body"],
                        max_chars=chunk_cfg["max_chars"],
                        overlap=chunk_cfg["overlap"],
                        max_chunks=chunk_cfg["max_chunks"],
                    )
                    if len(pieces) >= chunk_cfg["max_chunks"]:
                        capped += 1
                    embed_texts = [
                        f"{title}\n{pc}".strip() if title else pc.strip()
                        for pc in pieces
                    ]
                else:
                    embed_texts = [info["embed_text"]]  # title + snippet, as before
                for idx, etext in enumerate(embed_texts):
                    cid = rel if idx == 0 else f"{rel}#{idx}"
                    pending.append((cid, rel, layer, idx, mtime, info, etext))

    # Prune paths no longer matched / now denied -> delete all their chunks (air-gap honest).
    stale_paths = [p for p in existing_by_path if p not in claimed]
    for p in stale_paths:
        conn.execute("DELETE FROM notes WHERE path=?", (p,))
    # Delete old chunk rows of changed/new files so a now-smaller chunk count leaves no orphans.
    for p in changed_paths:
        conn.execute("DELETE FROM notes WHERE path=?", (p,))

    print(
        f"{CYAN}build:{RESET} {len(claimed)} files in scope "
        f"({skipped_uptodate} up-to-date, {len(changed_paths)} changed -> "
        f"{len(pending)} chunks to embed, {denied_count} denied, "
        f"{len(stale_paths)} files pruned"
        + (f", {capped} hit chunk cap" if capped else "") + ")"
    )

    if pending:
        # Embed and commit per FILE. A file's chunks are contiguous in `pending`
        # (built file-by-file above), so committing at file boundaries keeps the
        # index resumable: on this CPU a full build embeds ~0.7s/chunk (thousands
        # of chunks -> tens of minutes), and a single commit-at-end means any
        # interruption loses everything and the store reads as 0 notes mid-build
        # (looks hung). Per-file commit makes the store grow live, prints progress,
        # and lets an interrupted run resume -- the mtime/delete bookkeeping above
        # treats a not-yet-committed file as new (its rows were deleted, its mtime
        # absent on re-read) and re-embeds only what's missing.
        total_chunks = len(pending)
        done_chunks = 0
        done_files = 0
        for rel, grp in groupby(pending, key=lambda p: p[1]):
            items = list(grp)
            try:
                vectors = embed(
                    [it[6] for it in items],
                    model=cfg["model"],
                    host=cfg["host"],
                )
            except EmbeddingError as e:
                conn.commit()  # preserve files already embedded this run
                sys.stderr.write(f"{RED}Embedding failed:{RESET} {e}\n")
                sys.stderr.write(
                    f"{YELLOW}Committed {done_files} files "
                    f"({done_chunks}/{total_chunks} chunks) before failure; "
                    f"re-run to resume.{RESET}\n"
                )
                conn.close()
                return 1
            for (cid, r, layer, idx, mtime, info, etext), vec in zip(items, vectors):
                upsert_note(
                    conn, id_=cid, path=r, title=info["title"], layer=layer,
                    ntype=info["ntype"], mtime=mtime, body=etext, vec=vec,
                    created=info["created"], updated=info["updated"],
                    confidence=info["confidence"], status=info["status"],
                    classification=get_classification(r), chunk=idx,
                )
            conn.commit()  # file boundary: durable + resumable
            done_chunks += len(items)
            done_files += 1
            n = len(items)
            suffix = f" {GRAY}({n} chunks){RESET}" if n > 1 else ""
            pct = done_chunks * 100 // total_chunks
            print(
                f"  {GREEN}+{RESET} {GRAY}{rel}{RESET}{suffix} "
                f"{CYAN}[{pct}%]{RESET}",
                flush=True,
            )

    # Derive the lexical channel from the settled notes table (handles the
    # incremental, prune, and pre-hybrid-migration cases in one cheap pass).
    resync_fts(conn)

    conn.execute(
        "INSERT INTO meta (key, val) VALUES ('model', ?) "
        "ON CONFLICT(key) DO UPDATE SET val=excluded.val",
        (cfg["model"],),
    )
    conn.commit()
    total = conn.execute("SELECT COUNT(*) FROM notes").fetchone()[0]
    conn.close()
    print(f"{BOLD}Index ready:{RESET} {total} notes -> {root / store_rel}")
    return 0


# ============================================================
# Query
# ============================================================

def _load_index(conn):
    """Return (ids, metas_by_id, normalized matrix) for the whole index.

    ids align row-for-row with the matrix; metas_by_id maps id -> meta dict.
    Layer filtering is applied later in Python so the dense and sparse channels
    share one consistent universe of ids.
    """
    rows = conn.execute(
        "SELECT id, path, title, layer, ntype, dim, mtime, "
        "created, updated, confidence, status, classification, chunk, embedding FROM notes"
    ).fetchall()
    if not rows:
        return [], {}, None
    ids, metas, vecs = [], {}, []
    for id_, path, title, lyr, ntype, dim, mtime, created, updated, confidence, status, classification, chunk, blob in rows:
        ids.append(id_)
        metas[id_] = {
            "path": path, "title": title, "layer": lyr, "ntype": ntype,
            "mtime": mtime, "created": created, "updated": updated,
            "confidence": confidence, "status": status,
            "classification": classification or "", "chunk": chunk or 0,
        }
        vecs.append(np.frombuffer(blob, dtype=np.float32, count=dim))
    matrix = np.vstack(vecs).astype(np.float32)
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return ids, metas, matrix / norms


def _fts_match_expr(text: str):
    """Build a safe FTS5 MATCH string: OR over double-quoted word tokens.

    Quoting each token as a string literal neutralises FTS5 operators, so an
    arbitrary RU/EN query can never become a syntax error. Returns None when the
    query has no usable tokens (sparse channel then contributes nothing).
    """
    seen, toks = set(), []
    for raw in TOKEN_RE.findall(text.lower()):
        if len(raw) >= 2 and raw not in seen:
            seen.add(raw)
            toks.append(raw)
    if not toks:
        return None
    return " OR ".join(f'"{t}"' for t in toks)


def _sparse_ids(conn, match_expr, limit):
    """Return note ids matched by BM25, best-first. Empty if no match expr."""
    if not match_expr:
        return []
    rows = conn.execute(
        "SELECT id FROM notes_fts WHERE notes_fts MATCH ? "
        "ORDER BY bm25(notes_fts) LIMIT ?",
        (match_expr, limit),
    ).fetchall()
    return [r[0] for r in rows]


def _path_match_ids(text, ids, cos_by_id, in_layer_fn, df_cap=PATH_TOKEN_DF_CAP):
    """Ids whose humanized PATH contains a RARE query token, best-cosine first.

    A folder/project/client name (e.g. 'Meridian') is a high-precision identifier:
    these matches are admitted to the candidate pool without the convergence gate,
    so a pure path-name query surfaces the file even when its body is semantically
    unrelated to the bare name. The rarity cap (document-frequency <= df_cap) keeps
    generic path words (outputs, datastore, a year) -- which match many files --
    OUT of this channel; those remain subject to the normal gated sparse channel.
    """
    toks = {t for t in TOKEN_RE.findall(text.lower()) if len(t) >= 2}
    if not toks:
        return []
    by_tok = {}
    for id_ in ids:
        ptoks = set(humanize_path(id_).lower().split())
        for t in toks & ptoks:
            by_tok.setdefault(t, []).append(id_)
    admitted = set()
    for t, lst in by_tok.items():
        if len(lst) <= df_cap:            # rare token -> specific identifier
            admitted.update(i for i in lst if in_layer_fn(i))
    return sorted(admitted, key=lambda i: cos_by_id.get(i, 0.0), reverse=True)


def _rrf_fuse(dense_ids, sparse_ids, k=RRF_K):
    """Reciprocal Rank Fusion of two ranked id lists -> (ranked_ids, scores)."""
    scores = {}
    for ranked in (dense_ids, sparse_ids):
        for rank, id_ in enumerate(ranked, start=1):
            scores[id_] = scores.get(id_, 0.0) + 1.0 / (k + rank)
    ranked = sorted(scores, key=lambda i: scores[i], reverse=True)
    return ranked, scores


# ============================================================
# R7: recency x importance x relevance combiner
# ============================================================

_CONFIDENCE_SCORE = {"high": 1.0, "medium": 0.7, "med": 0.7, "low": 0.4}
_EVERGREEN_FLOOR = 0.7  # neutral recency for timeless / unknown-age notes


def _date_to_ts(s):
    """Parse an ISO date / datetime string to a UTC epoch, or None."""
    if not s:
        return None
    s = str(s).strip()
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def _recency_score(meta, halflife_days, now_ts):
    """Exponential recency in (0, 1]. ``status: evergreen`` floors at ~0.7 so
    timeless positions/sources are not buried by age (e.g. the 2026-03-18
    valuation-path position). Falls back to mtime when no created/updated date
    is present; unknown age stays neutral rather than penalised."""
    if (meta.get("status") or "").strip().lower() == "evergreen":
        return _EVERGREEN_FLOOR
    ts = _date_to_ts(meta.get("updated")) or _date_to_ts(meta.get("created"))
    if ts is None:
        ts = meta.get("mtime")
    if not ts:
        return _EVERGREEN_FLOOR
    age_days = max(0.0, (now_ts - ts) / 86400.0)
    halflife = halflife_days if halflife_days and halflife_days > 0 else 180
    return float(0.5 ** (age_days / halflife))


def _importance_score(meta):
    """Importance from confidence (high/medium/low -> 1.0/0.7/0.4); episodes
    biased down (event logs are less load-bearing for advice). Default medium."""
    base = _CONFIDENCE_SCORE.get((meta.get("confidence") or "").strip().lower(), 0.7)
    if (meta.get("ntype") or "").strip().lower() == "episode":
        base *= 0.8
    return base


def _combined(cos, recency, importance, weights):
    """Weighted sum of semantic relevance, recency, and importance."""
    return (weights["semantic"] * cos
            + weights["recency"] * recency
            + weights["importance"] * importance)


def _query_store(conn, qvec, full_text, *, threshold, layer, allowed):
    """Full hybrid retrieval (dense + BM25 + path-token) for ONE store.

    Returns per-store results carrying raw signals so the caller can pool
    candidates across stores and run a SINGLE RRF + R7 fusion. The BM25 and
    path channels are FTS-bound to this store's connection, so they must run
    here (per store) -- not over a concatenated matrix. ``best`` is the store's
    max cosine (None for an unbuilt/empty store). Dense uses the truncated query
    vector; sparse/path use ``full_text`` -- exactly as the pre-split path did."""
    ids, metas, matrix = _load_index(conn)
    if matrix is None:
        return {"dense_ids": [], "sparse_ids": [], "path_ids": [],
                "cos_by_id": {}, "metas": {}, "chunks_total": {}, "best": None}
    q = np.asarray(qvec, dtype=np.float32)
    qn = np.linalg.norm(q) or 1.0
    scores = matrix @ (q / qn)
    cos_by_id = {ids[i]: float(scores[i]) for i in range(len(ids))}

    # Chunk -> file collapse: a file is represented by its BEST-scoring chunk, so a
    # multi-chunk document yields ONE hit, not N fragments. `_collapse` maps any
    # chunk id to its file's best chunk and dedups, order-preserving.
    best_chunk_by_path, chunks_total = {}, {}
    for _id in ids:
        _p = metas[_id]["path"]
        chunks_total[_p] = chunks_total.get(_p, 0) + 1
        if _p not in best_chunk_by_path or cos_by_id[_id] > cos_by_id[best_chunk_by_path[_p]]:
            best_chunk_by_path[_p] = _id

    def _collapse(id_list):
        out, seen = [], set()
        for i in id_list:
            b = best_chunk_by_path[metas[i]["path"]]
            if b not in seen:
                seen.add(b)
                out.append(b)
        return out

    def in_layer(id_):
        if layer:
            return metas[id_]["layer"] == layer
        return allowed is None or metas[id_]["layer"] in allowed

    # Dense channel: cosine gated by the threshold (the honest-gap anchor, untouched).
    dense_ids = [
        ids[i]
        for i in np.argsort(scores)[::-1]
        if cos_by_id[ids[i]] >= threshold and in_layer(ids[i])
    ][:RANK_CAP]

    # Sparse channel: BM25 lexical match, gated by convergence with the dense
    # signal so a generic-term match on an unrelated note cannot leak through.
    sparse_floor = threshold - SPARSE_COS_MARGIN
    sparse_ids = [
        i for i in _sparse_ids(conn, _fts_match_expr(full_text), SPARSE_LIMIT)
        if i in metas and in_layer(i) and cos_by_id[i] >= sparse_floor
    ]

    # Path-match channel: rare query tokens that match a file's PATH (folder /
    # project / client name). Admitted WITHOUT the convergence gate -- a path name
    # is a precise identifier, not generic-content noise.
    path_ids = _path_match_ids(full_text, ids, cos_by_id, in_layer)

    return {
        "dense_ids": _collapse(dense_ids),
        "sparse_ids": _collapse(sparse_ids),
        "path_ids": _collapse(path_ids),
        "cos_by_id": cos_by_id,
        "metas": metas,
        "chunks_total": chunks_total,
        "best": (float(scores.max()) if len(scores) else None),
    }


def cmd_query(args) -> int:
    cfg = load_config(get_workspace_root())  # memory-index.yaml is engine config
    threshold = args.threshold if args.threshold is not None else cfg["threshold"]
    top_k = args.top_k or cfg["top_k"]
    layer = args.layer
    qtext = args.text[:SNIPPET_CHARS]  # symmetry with build-time snippet window

    # Resolve the allowed layer set AND which physical store(s) to open. --layer
    # wins (one layer; preserves /odin's `--layer odin` contract). Else the
    # collection's layers; `all` lifts the restriction. content -> data store
    # only (the /recall default, unchanged); code -> engine code store; all ->
    # both, pooled. A missing collections map falls back to no-restriction over
    # all stores (back-compat). getattr defaults keep bare-namespace callers working.
    collection = getattr(args, "collection", "content")
    want_json = getattr(args, "json", False)
    coll_map = cfg.get("collections") or {}
    targets = _store_targets(cfg)
    if layer:
        allowed = {layer}
        lmap = _layer_store_map(cfg)
        stores = [lmap[layer]] if layer in lmap else []
    elif collection == "all":
        allowed = None
        stores = [(t["root"], t["store_rel"]) for t in targets]
    elif collection in coll_map:
        allowed = set(coll_map[collection])
        stores = [(t["root"], t["store_rel"]) for t in targets if t["layers"] & allowed]
    elif collection == "content" and not coll_map:
        allowed = None
        stores = [(t["root"], t["store_rel"]) for t in targets]
    else:
        known = ", ".join(sorted(coll_map)) or "(none configured)"
        sys.stderr.write(
            f"{RED}Unknown collection '{collection}'.{RESET} "
            f"Known: {known}, or 'all'.\n"
        )
        return 1

    try:
        qvec = embed([qtext], model=cfg["model"], host=cfg["host"])[0]
    except EmbeddingError as e:
        sys.stderr.write(f"{RED}Embedding failed:{RESET} {e}\n")
        return 1

    # Per-store hybrid retrieval, then pool. Each store runs its own dense + BM25
    # + path channels (BM25/path are FTS-bound per store); we pool the candidates
    # and run ONE RRF + R7 fusion below. For a single store the pooled lists equal
    # that store's lists in order -> byte-identical to the pre-split path (the
    # /recall content-regression guard).
    cos_by_id, metas, chunks_total = {}, {}, {}
    all_dense, all_sparse, all_path = [], [], []
    best = None
    for s_root, s_rel in stores:
        conn = open_store(s_root, s_rel)
        res = _query_store(conn, qvec, args.text,
                           threshold=threshold, layer=layer, allowed=allowed)
        conn.close()
        cos_by_id.update(res["cos_by_id"])
        metas.update(res["metas"])
        chunks_total.update(res["chunks_total"])
        all_dense.extend(res["dense_ids"])
        all_sparse.extend(res["sparse_ids"])
        all_path.extend(res["path_ids"])
        if res["best"] is not None:
            best = res["best"] if best is None else max(best, res["best"])

    if not metas:
        print(f"{YELLOW}Index is empty.{RESET} Run: python3 scripts/memory-index.py build")
        return 0

    # Pool: dense re-sorted globally by cosine (a no-op for a single store, which
    # already returns cosine-descending); sparse/path concatenated with each
    # store's internal order preserved. Single store -> byte-identical lists.
    dense_ids = sorted(all_dense, key=lambda i: cos_by_id.get(i, 0.0), reverse=True)
    sparse_ids = all_sparse
    path_ids = all_path
    combined_sparse = list(dict.fromkeys(path_ids + sparse_ids))

    if not dense_ids and not combined_sparse:
        best_val = best if best is not None else 0.0
        if want_json:
            print(json.dumps(
                {"hits": [], "gap": True, "best": round(best_val, 4),
                 "threshold": round(float(threshold), 4)},
                ensure_ascii=False,
            ))
            return 0
        print(
            f"{YELLOW}Nothing above threshold {threshold:.2f}{RESET} "
            f"(best {best_val:.3f}, no lexical match) -- a gap in this area of memory."
        )
        return 0

    fused, _ = _rrf_fuse(dense_ids, combined_sparse)
    dense_set, sparse_set, path_set = set(dense_ids), set(sparse_ids), set(path_ids)

    # R7: re-rank the RRF candidate set by recency x importance x relevance.
    # RRF still generates the candidates (and the dense gate still guards the
    # honest-gap signal); this combiner only reorders what survived.
    weights = cfg["rank_weights"]
    halflife = cfg["recency_halflife_days"]
    now_ts = time.time()

    def _rank_score(id_):
        m = metas[id_]
        return _combined(
            cos_by_id[id_],
            _recency_score(m, halflife, now_ts),
            _importance_score(m),
            weights,
        )

    fused = sorted(fused, key=_rank_score, reverse=True)[:top_k]

    # Reverse layer -> collection map, so each hit carries its collection tag.
    coll_of = {}
    for cname, lyrs in coll_map.items():
        for lyr in lyrs:
            coll_of.setdefault(lyr, cname)

    hits = []
    for id_ in fused:
        m = metas[id_]
        channels = []
        if id_ in dense_set:
            channels.append("dense")
        if id_ in sparse_set:
            channels.append("bm25")
        if id_ in path_set:
            channels.append("path")
        hits.append({
            "path": m["path"],
            "title": m["title"] or m["path"],
            "layer": m["layer"],
            "ntype": m["ntype"] or "",
            "classification": m.get("classification") or "",
            "collection": coll_of.get(m["layer"], ""),
            "score": round(cos_by_id[id_], 4),
            "channels": channels,
            "chunk": m.get("chunk", 0),
            "chunks_total": chunks_total.get(m["path"], 1),
        })

    if want_json:
        print(json.dumps({"hits": hits, "gap": False}, ensure_ascii=False))
        return 0

    print(f"{BOLD}Associative recall{RESET} {GRAY}(recency x importance x relevance){RESET}")
    for h in hits:
        tag = "+".join(h["channels"])
        ntype = f" {GRAY}{h['ntype']}{RESET}" if h["ntype"] else ""
        cls = f" {GRAY}[{h['classification']}]{RESET}" if h["classification"] else ""
        chunk = (f" {GRAY}(chunk {h['chunk'] + 1}/{h['chunks_total']}){RESET}"
                 if h["chunks_total"] > 1 else "")
        print(
            f"  {GREEN}{h['score']:.3f}{RESET} {GRAY}{tag:10}{RESET} {CYAN}{h['layer']:10}{RESET}"
            f"{ntype}{cls}  {h['title']}{chunk}"
        )
        print(f"          {GRAY}{h['path']}{RESET}")
    return 0


# ============================================================
# Stats
# ============================================================

def cmd_stats(args) -> int:
    cfg = load_config(get_workspace_root())  # memory-index.yaml is engine config
    for i, t in enumerate(_store_targets(cfg)):
        if i:
            print()
        _stats_one_store(t["name"], t["root"], t["store_rel"])
    return 0


def _stats_one_store(name, root, store_rel) -> None:
    """Print one store's layer/classification breakdown. Existence is checked
    BEFORE opening (open_store would create an empty file otherwise); an existing
    store with zero rows is reported as unbuilt per the empty-matrix predicate."""
    store_path = root / store_rel
    label = f"{BOLD}Memory index{RESET} {GRAY}[{name}]{RESET}  {GRAY}{store_path}{RESET}"
    if not store_path.exists():
        print(label)
        print(f"  {YELLOW}not built yet{RESET} -- run: python3 scripts/memory-index.py build")
        return
    conn = open_store(root, store_rel)
    rows = conn.execute(
        "SELECT layer, COUNT(*), COUNT(DISTINCT path), MAX(dim) "
        "FROM notes GROUP BY layer ORDER BY layer"
    ).fetchall()
    total = conn.execute("SELECT COUNT(*) FROM notes").fetchone()[0]
    files = conn.execute("SELECT COUNT(DISTINCT path) FROM notes").fetchone()[0]
    fts_rows = conn.execute("SELECT COUNT(*) FROM notes_fts").fetchone()[0]
    last_mtime = conn.execute("SELECT MAX(mtime) FROM notes").fetchone()[0]
    cls_rows = conn.execute(
        "SELECT COALESCE(NULLIF(classification, ''), '(unset)'), COUNT(*) "
        "FROM notes GROUP BY 1 ORDER BY 1"
    ).fetchall()
    model = conn.execute("SELECT val FROM meta WHERE key='model'").fetchone()
    conn.close()

    print(label)
    if total == 0:
        print(f"  {YELLOW}not built yet{RESET} -- run: python3 scripts/memory-index.py build")
        return
    print(f"  model:  {model[0] if model else '?'}")
    print(f"  notes:  {total} chunks across {files} files  {GRAY}(bm25 channel: {fts_rows}){RESET}")
    for layer, count, nfiles, dim in rows:
        chunked = f"  {GRAY}/ {nfiles} files{RESET}" if count != nfiles else ""
        print(f"    {CYAN}{layer:16}{RESET} {count:4}  dim={dim}{chunked}")
    print(f"  {BOLD}classification:{RESET}")
    for cls, count in cls_rows:
        print(f"    {CYAN}{cls:16}{RESET} {count:4}")
    if last_mtime:
        age_days = max(0.0, (time.time() - last_mtime) / 86400.0)
        print(f"  newest source: {last_mtime:.0f}  {GRAY}({age_days:.1f} days ago){RESET}")


# ============================================================
# CLI
# ============================================================

def main() -> int:
    parser = argparse.ArgumentParser(description="Local associative-memory index.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_build = sub.add_parser("build", help="(re)build the index from allowlisted layers")
    p_build.add_argument("--force", action="store_true", help="re-embed all, ignore mtime")
    p_build.set_defaults(func=cmd_build)

    p_query = sub.add_parser("query", help="semantic recall over the index")
    p_query.add_argument("text", help="query text (RU or EN)")
    p_query.add_argument("--layer", help="restrict to one layer (e.g. odin|thread|crm|outputs|skill); overrides --collection")
    p_query.add_argument("--collection", default="content",
                         help="layer group to search: content (default) | code | all")
    p_query.add_argument("--top-k", type=int, default=0, help="max hits (default from config)")
    p_query.add_argument("--threshold", type=float, default=None, help="min score (default from config)")
    p_query.add_argument("--json", action="store_true", help="emit machine-readable JSON (hits + gap object)")
    p_query.set_defaults(func=cmd_query)

    p_stats = sub.add_parser("stats", help="index summary by layer")
    p_stats.set_defaults(func=cmd_stats)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
