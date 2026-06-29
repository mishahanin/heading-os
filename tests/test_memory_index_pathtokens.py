"""Path-token search for the associative-memory index (point 3).

A folder / project / client name that lives only in the file PATH (not the body)
must be findable via the LEXICAL (BM25) channel, while the dense embed text stays
clean. Built on a hermetic temp workspace with a mock embedder (no ollama).

Channel separation is asserted in BOTH directions:
  - the FTS body for the file DOES contain the humanized path token;
  - notes.body (the dense embed text) does NOT.

Run: python3 -m pytest tests/test_memory_index_pathtokens.py
"""

import importlib.util
import sqlite3
import sys
import types
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parent.parent
SCRIPT = WORKSPACE / "scripts" / "memory-index.py"

VOCAB = ["sovereignty", "quantum", "alpha", "contact", "pipeline", "secret"]


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
    spec = importlib.util.spec_from_file_location("memory_index_pathtok_mod", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def write(path: Path, body: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def make_config(root: Path):
    write(
        root / "config/memory-index.yaml",
        "model: bge-m3\n"
        "host: http://localhost:11434\n"
        "threshold: 0.55\n"
        "top_k: 8\n"
        "layers:\n"
        "  - {layer: projects, glob: 'datastore/projects/**/*.md'}\n"
        "deny_prefixes: ['_secure/']\n"
        "deny_segments: ['personal']\n",
    )


def build(mod, root, force=True):
    return mod.cmd_build(types.SimpleNamespace(force=force))


def test_humanize_path_shape():
    mod = load_module()
    assert mod.humanize_path("datastore/projects/Meridian/RFP-extract.md") == \
        "datastore projects Meridian RFP extract"
    assert mod.humanize_path("a/b/c.md#3") == "a b c"


def test_path_token_searchable_and_channel_separated(tmp_path, monkeypatch):
    root = tmp_path
    make_config(root)
    # "projectx" appears ONLY in the folder name, never in the body.
    write(root / "datastore/projects/projectx/brief.md",
          "---\ntitle: Brief\n---\n\n# Brief\n\nsovereignty pipeline alpha.\n")

    mod = load_module()
    # The index DB resolves via get_data_root() (STORE_REL under it), not
    # get_workspace_root(); HEADING_OS_DATA wins first, keeping the build
    # hermetic instead of writing into the REAL data-root index.db.
    monkeypatch.setenv("HEADING_OS_DATA", str(root))
    monkeypatch.setattr(mod, "get_workspace_root", lambda: root)
    monkeypatch.setattr(mod, "embed", fake_embed)
    monkeypatch.setattr(mod, "get_classification", lambda p: "ceo-only")
    assert build(mod, root) == 0
    # Isolation guard: DB under the temp root, never the real data root.
    assert (root / mod.STORE_REL).is_file()

    conn = sqlite3.connect(str(root / ".memory-index" / "index.db"))
    # (a) lexical channel finds the file by the path-only token
    hits = mod._sparse_ids(conn, mod._fts_match_expr("projectx"), 40)
    assert "datastore/projects/projectx/brief.md" in hits, hits
    # (b) the FTS body for that id DOES contain the humanized path token
    fts_body = conn.execute(
        "SELECT body FROM notes_fts WHERE id=?",
        ("datastore/projects/projectx/brief.md",),
    ).fetchone()[0]
    assert "projectx" in fts_body.lower(), fts_body
    # (c) the dense embed text (notes.body) does NOT contain the path token
    note_body = conn.execute(
        "SELECT body FROM notes WHERE id=?",
        ("datastore/projects/projectx/brief.md",),
    ).fetchone()[0]
    assert "projectx" not in note_body.lower(), note_body
    conn.close()


def test_full_query_surfaces_path_only_match(tmp_path, monkeypatch, capsys):
    """The FULL cmd_query (not just _sparse_ids) must return a file matched only
    by its folder name -- the path-match channel bypasses the convergence gate."""
    root = tmp_path
    make_config(root)
    write(root / "datastore/projects/projectx/brief.md",
          "---\ntitle: Brief\n---\n\n# Brief\n\nsovereignty pipeline.\n")
    mod = load_module()
    # HEADING_OS_DATA redirects get_data_root() to the temp root (see above).
    monkeypatch.setenv("HEADING_OS_DATA", str(root))
    monkeypatch.setattr(mod, "get_workspace_root", lambda: root)
    monkeypatch.setattr(mod, "embed", fake_embed)
    monkeypatch.setattr(mod, "get_classification", lambda p: "ceo-only")
    assert build(mod, root) == 0
    assert (root / mod.STORE_REL).is_file()
    capsys.readouterr()

    mod.cmd_query(types.SimpleNamespace(
        text="projectx", layer=None, collection="content",
        top_k=8, threshold=None, json=True))
    import json as _json
    obj = _json.loads(capsys.readouterr().out)
    assert obj["gap"] is False, obj
    paths = [h["path"] for h in obj["hits"]]
    assert "datastore/projects/projectx/brief.md" in paths, obj
    hit = next(h for h in obj["hits"] if h["path"].endswith("brief.md"))
    assert "path" in hit["channels"], hit


def test_path_match_channel_rarity_cap():
    """Rare path tokens are admitted; generic high-df ones (would flood) are not."""
    mod = load_module()
    ids = [f"outputs/intel/f{i}.md" for i in range(30)] + \
          ["datastore/projects/meridian/brief.md"]
    cos = {i: 0.1 for i in ids}
    got = mod._path_match_ids("meridian intel outputs", ids, cos, lambda i: True, df_cap=25)
    assert "datastore/projects/meridian/brief.md" in got, got        # rare -> admitted
    assert all("outputs/intel" not in g for g in got), got          # common -> filtered


def test_path_tokens_survive_incremental_resync(tmp_path, monkeypatch):
    """Point 3 must stand alone: an unrelated incremental build re-runs
    resync_fts, and path tokens must still be present afterwards."""
    root = tmp_path
    make_config(root)
    write(root / "datastore/projects/projectx/brief.md",
          "---\ntitle: Brief\n---\n\n# Brief\n\nsovereignty.\n")

    mod = load_module()
    # HEADING_OS_DATA redirects get_data_root() to the temp root (see above).
    monkeypatch.setenv("HEADING_OS_DATA", str(root))
    monkeypatch.setattr(mod, "get_workspace_root", lambda: root)
    monkeypatch.setattr(mod, "embed", fake_embed)
    monkeypatch.setattr(mod, "get_classification", lambda p: "ceo-only")
    assert build(mod, root, force=True) == 0
    assert (root / mod.STORE_REL).is_file()

    # Add a second file, run an INCREMENTAL build (no --force) -> resync_fts reruns.
    write(root / "datastore/projects/other/note.md",
          "---\ntitle: Other\n---\n\n# Other\n\nquantum.\n")
    assert build(mod, root, force=False) == 0

    conn = sqlite3.connect(str(root / ".memory-index" / "index.db"))
    hits = mod._sparse_ids(conn, mod._fts_match_expr("projectx"), 40)
    conn.close()
    assert "datastore/projects/projectx/brief.md" in hits, hits
