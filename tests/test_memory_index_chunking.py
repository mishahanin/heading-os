"""Chunking guarantees for the associative-memory index (point 2).

Long documents are split into overlapping chunks, each embedded as its own row
(id = path for chunk 0, path#N otherwise), so content past the first window is
recallable; short files stay one row; query dedups to one hit per file. Built on
a hermetic temp workspace with a mock embedder (no ollama).

Includes the H1 coverage under chunking ON: the FTS channel id-set equals the
notes id-set (not the path-set), and no FTS id's path-component is air-gapped.

Run: python3 -m pytest tests/test_memory_index_chunking.py
"""

import importlib.util
import json as _json
import sqlite3
import sys
import types
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parent.parent
SCRIPT = WORKSPACE / "scripts" / "memory-index.py"

# Distinct marker words so a query can target a specific chunk position.
EARLY = "alphamarker"
LATE = "omegamarker"
VOCAB = [EARLY, LATE, "filler", "sovereignty", "secret", "diary"]


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
    spec = importlib.util.spec_from_file_location("memory_index_chunk_mod", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def write(path: Path, body: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def config_with_chunking(root: Path):
    write(
        root / "config/memory-index.yaml",
        "model: bge-m3\n"
        "host: http://localhost:11434\n"
        "threshold: 0.20\n"
        "top_k: 8\n"
        "layers:\n"
        "  - {layer: outputs, glob: 'outputs/**/*.md'}\n"
        "  - {layer: crm, glob: 'crm/contacts/*.md'}\n"
        "  - {layer: vaulttest, glob: '_secure/**/*.md'}\n"
        "  - {layer: segtest, glob: 'tmp/**/*.md'}\n"
        "collections:\n"
        "  content: [outputs, crm]\n"
        "chunk:\n"
        "  enabled_layers: [outputs]\n"
        "  max_chars: 200\n"
        "  overlap: 30\n"
        "  max_chunks: 12\n"
        "deny_prefixes: ['_secure/']\n"
        "deny_segments: ['personal']\n",
    )


def long_body():
    # EARLY appears only in the first chunk, LATE only deep in the doc.
    head = f"# Doc\n\n{EARLY} " + ("filler " * 40)
    mid = "\n\n" + ("filler " * 40)
    tail = "\n\n" + ("filler " * 40) + f" {LATE}\n"
    return head + mid + tail


def build_env(tmp_path, monkeypatch):
    root = tmp_path
    config_with_chunking(root)
    write(root / "outputs/intel/longdoc.md", long_body())          # chunked (outputs)
    write(root / "crm/contacts/alpha.md",
          "---\ntitle: Alpha\n---\n\n# Alpha\n\nsovereignty.\n")      # one-row (crm)
    write(root / "_secure/proj/secret.md", "# vault\n\nsecret.\n")    # air-gapped
    write(root / "tmp/personal/diary.md", "# personal\n\ndiary.\n")   # air-gapped

    mod = load_module()
    # The index DB resolves via get_data_root() (STORE_REL under it), not
    # get_workspace_root(). HEADING_OS_DATA wins first in get_data_root(), so
    # this keeps the build hermetic instead of writing into the REAL
    # ../.heading-os-data/.memory-index/index.db.
    monkeypatch.setenv("HEADING_OS_DATA", str(root))
    monkeypatch.setattr(mod, "get_workspace_root", lambda: root)
    monkeypatch.setattr(mod, "embed", fake_embed)
    monkeypatch.setattr(mod, "get_classification", lambda p: "ceo-only")
    assert mod.cmd_build(types.SimpleNamespace(force=True)) == 0
    # Isolation guard: DB under the temp root, never the real data root.
    assert (root / mod.STORE_REL).is_file()
    return mod, root


def rows(root):
    conn = sqlite3.connect(str(root / ".memory-index" / "index.db"))
    out = conn.execute("SELECT id, path, chunk FROM notes").fetchall()
    conn.close()
    return out


def query_json(mod, capsys, text, **kw):
    base = dict(text=text, layer=None, collection="content",
                top_k=8, threshold=None, json=True)
    base.update(kw)
    capsys.readouterr()
    mod.cmd_query(types.SimpleNamespace(**base))
    return _json.loads(capsys.readouterr().out)


def test_long_file_yields_multiple_chunks_short_stays_one(tmp_path, monkeypatch):
    mod, root = build_env(tmp_path, monkeypatch)
    r = rows(root)
    long_rows = [x for x in r if x[1] == "outputs/intel/longdoc.md"]
    crm_rows = [x for x in r if x[1] == "crm/contacts/alpha.md"]
    assert len(long_rows) > 1, long_rows               # chunked
    assert len(crm_rows) == 1, crm_rows                # one-row layer
    # chunk 0 keeps id == path (back-compat); later chunks are path#N
    ids = {x[0] for x in long_rows}
    assert "outputs/intel/longdoc.md" in ids
    assert any("#" in i for i in ids), ids


def test_mid_doc_query_hits_and_is_deduped(tmp_path, monkeypatch, capsys):
    mod, root = build_env(tmp_path, monkeypatch)
    # LATE lives only in a late chunk -> must still surface the file...
    obj = query_json(mod, capsys, LATE)
    paths = [h["path"] for h in obj["hits"]]
    assert "outputs/intel/longdoc.md" in paths, obj
    # ...and the multi-chunk file appears as exactly ONE hit (deduped).
    assert paths.count("outputs/intel/longdoc.md") == 1, paths
    hit = next(h for h in obj["hits"] if h["path"].endswith("longdoc.md"))
    assert hit["chunks_total"] > 1, hit


def test_fts_parity_and_airgap_under_chunking(tmp_path, monkeypatch):
    """H1 coverage with chunking ON: FTS id-set == notes id-set (NOT path-set),
    and no FTS id's path-component is air-gapped."""
    mod, root = build_env(tmp_path, monkeypatch)
    conn = sqlite3.connect(str(root / ".memory-index" / "index.db"))
    note_ids = {r[0] for r in conn.execute("SELECT id FROM notes")}
    fts_ids = {r[0] for r in conn.execute("SELECT id FROM notes_fts")}
    conn.close()
    assert fts_ids == note_ids, (fts_ids ^ note_ids)
    # at least one chunked id is path#N (so the path-set would NOT have matched)
    assert any("#" in i for i in note_ids)
    for i in fts_ids:
        base = i.split("#", 1)[0]
        assert not base.startswith("_secure/")
        assert "personal" not in base.split("/")


def test_change_replaces_chunks_no_orphans(tmp_path, monkeypatch):
    mod, root = build_env(tmp_path, monkeypatch)
    before = [x for x in rows(root) if x[1] == "outputs/intel/longdoc.md"]
    assert len(before) > 1
    # Shrink the file to a single short chunk; incremental build must drop orphans.
    write(root / "outputs/intel/longdoc.md", "# Doc\n\nshort now.\n")
    assert mod.cmd_build(types.SimpleNamespace(force=False)) == 0
    after = [x for x in rows(root) if x[1] == "outputs/intel/longdoc.md"]
    assert len(after) == 1, after
    assert after[0][0] == "outputs/intel/longdoc.md"   # back to a single chunk-0 row


def test_prune_removes_all_chunks_of_deleted_file(tmp_path, monkeypatch):
    mod, root = build_env(tmp_path, monkeypatch)
    (root / "outputs/intel/longdoc.md").unlink()
    assert mod.cmd_build(types.SimpleNamespace(force=False)) == 0
    remaining = [x for x in rows(root) if x[1] == "outputs/intel/longdoc.md"]
    assert remaining == [], remaining


def test_unchanged_file_is_skipped(tmp_path, monkeypatch, capsys):
    mod, root = build_env(tmp_path, monkeypatch)
    capsys.readouterr()
    # second incremental build with no changes -> 0 chunks to embed
    mod.cmd_build(types.SimpleNamespace(force=False))
    out = capsys.readouterr().out
    assert "0 chunks to embed" in out, out
