"""Air-gap guarantees for the associative-memory index.

Builds the index on a hermetic temp workspace with a MOCK embedder (no ollama
dependency) and asserts the air-gap behaves both ways:
  - negative: `_secure/` (vault) and any `personal` segment never enter the store;
  - positive: business content (CRM contacts, Odin notes) DOES enter the store
              -- without this, the air-gap test could pass green while the index
              is simply empty (the false-confidence trap, scrutinize H1).

The hard-coded deny guard is exercised even with an emptied config, and query
behaviour is checked on an irrelevant query (must report a gap, not noise).

NB: fixtures deliberately use a NON-threads `personal` segment (e.g.
`tmp/personal/...`). A literal `threads/personal/` path would trip the
protect-personal-threads PreToolUse hook when this test file is written.

Run: python3 -m pytest tests/test_memory_index_airgap.py
"""

import importlib.util
import sqlite3
import sys
import types
from pathlib import Path

import pytest

WORKSPACE = Path(__file__).resolve().parent.parent
SCRIPT = WORKSPACE / "scripts" / "memory-index.py"

# Small fixed vocabulary -> deterministic lexical embedding. Cosine reflects
# word overlap, so a query with no shared words scores near-zero (a gap).
VOCAB = ["sovereignty", "quantum", "alpha", "contact", "crm", "pilot", "secret", "diary"]


def fake_embed(texts, *, model, host, batch=32, timeout=120):
    vecs = []
    for t in texts:
        low = t.lower()
        v = [float(low.count(w)) for w in VOCAB]
        if not any(v):
            v = [1e-6] * len(VOCAB)  # avoid a zero vector -> low cosine everywhere
        vecs.append(v)
    return vecs


def load_module():
    """Load scripts/memory-index.py (hyphenated -> importlib, not import)."""
    sys.path.insert(0, str(WORKSPACE))
    spec = importlib.util.spec_from_file_location("memory_index_mod", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def write(path: Path, body: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


@pytest.fixture
def built_index(tmp_path, monkeypatch):
    """Populate a temp workspace, patch root + embedder, run build, yield (mod, root)."""
    root = tmp_path

    # Business content (must be indexed)
    write(
        root / "knowledge/odin-brain/principles/sovereignty.md",
        "---\ntitle: Sovereignty principle\ntype: principle\n---\n\n# Sovereignty principle\n\nsovereignty quantum.\n",
    )
    write(
        root / "crm/contacts/alpha.md",
        "---\ntitle: Alpha contact\n---\n\n# Alpha\n\nalpha contact crm.\n",
    )
    # Air-gapped content (must NEVER be indexed)
    write(root / "_secure/proj/secret.md", "# vault\n\nsovereignty secret.\n")
    write(root / "tmp/personal/diary.md", "# personal\n\npilot diary.\n")

    # Temp config whose globs DO reach the denied dirs, so the deny guard
    # (not the glob) is what excludes them.
    write(
        root / "config/memory-index.yaml",
        "model: bge-m3\n"
        "host: http://localhost:11434\n"
        "threshold: 0.55\n"
        "top_k: 8\n"
        "layers:\n"
        "  - {layer: odin, glob: 'knowledge/odin-brain/{principles,positions}/*.md'}\n"
        "  - {layer: crm, glob: 'crm/contacts/*.md'}\n"
        "  - {layer: vaulttest, glob: '_secure/**/*.md'}\n"
        "  - {layer: segtest, glob: 'tmp/**/*.md'}\n"
        "deny_prefixes: ['_secure/']\n"
        "deny_segments: ['personal']\n",
    )

    mod = load_module()
    # The index DB resolves via get_data_root() (STORE_REL under it), not
    # get_workspace_root(). HEADING_OS_DATA wins first in get_data_root(), so
    # this keeps the build hermetic instead of writing into the REAL
    # ../.heading-os-data/.memory-index/index.db.
    monkeypatch.setenv("HEADING_OS_DATA", str(root))
    monkeypatch.setattr(mod, "get_workspace_root", lambda: root)
    monkeypatch.setattr(mod, "embed", fake_embed)

    rc = mod.cmd_build(types.SimpleNamespace(force=True))
    assert rc == 0
    # Isolation guard: DB under the temp root, never the real data root.
    assert (root / mod.STORE_REL).is_file()
    return mod, root


def indexed_paths(root: Path):
    db = root / ".memory-index" / "index.db"
    conn = sqlite3.connect(str(db))
    rows = [r[0] for r in conn.execute("SELECT path FROM notes")]
    conn.close()
    return rows


def fts_ids(root: Path):
    db = root / ".memory-index" / "index.db"
    conn = sqlite3.connect(str(db))
    rows = [r[0] for r in conn.execute("SELECT id FROM notes_fts")]
    conn.close()
    return rows


def test_airgap_excludes_secret_and_personal(built_index):
    _, root = built_index
    paths = indexed_paths(root)
    assert not any(p.startswith("_secure/") for p in paths), paths
    assert not any("personal" in p.split("/") for p in paths), paths
    assert "_secure/proj/secret.md" not in paths
    assert "tmp/personal/diary.md" not in paths


def test_business_content_is_indexed(built_index):
    _, root = built_index
    paths = indexed_paths(root)
    assert "crm/contacts/alpha.md" in paths, paths
    assert "knowledge/odin-brain/principles/sovereignty.md" in paths, paths


def test_is_denied_hardcoded_even_with_empty_config():
    mod = load_module()
    # Empty/broken config must not open the air-gap.
    assert mod.is_denied("_secure/x.md", [], [])
    assert mod.is_denied("threads/personal/a.md", [], [])
    assert mod.is_denied("anything/personal/deep/note.md", [], [])
    # Business paths pass.
    assert not mod.is_denied("crm/contacts/alpha.md", [], [])
    assert not mod.is_denied("knowledge/odin-brain/principles/x.md", [], [])


def test_query_gap_vs_hit(built_index, capsys):
    mod, root = built_index
    ns = lambda text: types.SimpleNamespace(text=text, layer=None, top_k=0, threshold=None)

    # Terms present only in the air-gapped fixtures, never in indexed business
    # content -> a genuine gap in the index, must report "nothing above threshold".
    mod.cmd_query(ns("pilot diary secret"))
    out = capsys.readouterr().out
    assert "Nothing above threshold" in out, out

    mod.cmd_query(ns("sovereignty"))
    out = capsys.readouterr().out
    assert "Associative recall" in out, out
    assert "sovereignty" in out.lower(), out


# --- Hybrid (sparse BM25 + RRF) channel -----------------------------------


def test_fts_channel_mirrors_notes_and_keeps_airgap(built_index):
    """The BM25 channel is derived from notes: same ids, never a denied path."""
    _, root = built_index
    note_ids = set(indexed_paths(root))          # id == path in this index
    assert set(fts_ids(root)) == note_ids        # parity -- channels cannot drift
    assert not any(i.startswith("_secure/") for i in fts_ids(root))
    assert not any("personal" in i.split("/") for i in fts_ids(root))


def test_sparse_ids_finds_lexical_match(built_index):
    """_sparse_ids returns the note whose body lexically contains the term."""
    mod, root = built_index
    conn = mod.open_store(root)
    hits = mod._sparse_ids(conn, mod._fts_match_expr("alpha"), 40)
    conn.close()
    assert "crm/contacts/alpha.md" in hits, hits


def test_fts_match_expr_tokenises_and_quotes():
    mod = load_module()
    assert mod._fts_match_expr("Proof of Value!!") == '"proof" OR "of" OR "value"'
    assert mod._fts_match_expr("  ??  ") is None      # no usable tokens
    assert mod._fts_match_expr("a bb a") == '"bb"'    # len<2 dropped, de-duped


def test_rrf_fuse_rewards_agreement_and_rank():
    mod = load_module()
    # 'x' appears in both lists -> must outrank items in only one list.
    ranked, scores = mod._rrf_fuse(["x", "y"], ["z", "x"])
    assert ranked[0] == "x", ranked
    assert scores["x"] > scores["y"] and scores["x"] > scores["z"]
    # Within a single list, earlier rank scores higher.
    assert scores["y"] > 0 and scores["z"] > 0
