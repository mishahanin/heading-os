"""Workspace-wide coverage guarantees for the associative-memory index.

Companion to test_memory_index_airgap.py. Builds the index on a hermetic temp
workspace with a MOCK embedder (no ollama dependency) and asserts the
2026-06-11 coverage extension behaves:

  - classification: every indexed row carries the resolver's verdict;
  - collections: --collection code returns only code layers, content excludes
                 them, all spans both, an unknown name errors (rc=1);
  - --json: hits object shape on a hit; the gap object on an out-of-corpus query;
  - air-gap regression: vault prefix + a `personal` segment never enter the
                        store even when the new globs would otherwise reach them.

NB: fixtures deliberately use a NON-threads `personal` segment (e.g.
`tmp/personal/...`). A literal threads-personal path would trip the
protect-personal-threads PreToolUse hook when this test file is written.

Run: python3 -m pytest tests/test_memory_index_coverage.py
"""

import importlib.util
import json
import sqlite3
import sys
import types
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parent.parent
SCRIPT = WORKSPACE / "scripts" / "memory-index.py"

# Fixed vocabulary -> deterministic lexical embedding (word overlap == cosine).
VOCAB = ["sovereignty", "quantum", "alpha", "contact", "pipeline",
         "recall", "skill", "rule", "secret", "diary"]


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
    spec = importlib.util.spec_from_file_location("memory_index_cov_mod", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def write(path: Path, body: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def build_index(tmp_path, monkeypatch):
    """Populate a temp workspace with content + code + air-gapped fixtures and
    a config carrying collections, then build with a mock embedder + classifier."""
    root = tmp_path

    # content collection
    write(root / "context/strategy.md",
          "---\ntitle: Strategy\n---\n\n# Strategy\n\npipeline sovereignty.\n")
    write(root / "crm/contacts/alpha.md",
          "---\ntitle: Alpha\n---\n\n# Alpha\n\nalpha contact.\n")
    # code collection
    write(root / ".claude/skills/recall/SKILL.md",
          "---\nname: recall\n---\n\n# Recall\n\nrecall skill body.\n")
    write(root / ".claude/rules/voice.md",
          "# Voice\n\nrule rule rule.\n")
    # air-gapped (must NEVER be indexed even though globs below reach them)
    write(root / "_secure/proj/secret.md", "# vault\n\nsecret sovereignty.\n")
    write(root / "tmp/personal/diary.md", "# personal\n\ndiary pipeline.\n")

    write(
        root / "config/memory-index.yaml",
        "model: bge-m3\n"
        "host: http://localhost:11434\n"
        "threshold: 0.55\n"
        "top_k: 8\n"
        "layers:\n"
        "  - {layer: context, glob: 'context/*.md'}\n"
        "  - {layer: crm, glob: 'crm/contacts/*.md'}\n"
        "  - {layer: skill, glob: '.claude/skills/**/SKILL.md'}\n"
        "  - {layer: rule, glob: '.claude/rules/*.md'}\n"
        "  - {layer: vaulttest, glob: '_secure/**/*.md'}\n"
        "  - {layer: segtest, glob: 'tmp/**/*.md'}\n"
        "collections:\n"
        "  content: [context, crm]\n"
        "  code: [skill, rule]\n"
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
    # Deterministic classifier: crm is ceo-only, everything else corporate.
    monkeypatch.setattr(
        mod, "get_classification",
        lambda p: "ceo-only" if p.startswith("crm/") else "corporate",
    )
    rc = mod.cmd_build(types.SimpleNamespace(force=True))
    assert rc == 0
    # Isolation guard: DB under the temp root, never the real data root.
    assert (root / mod.STORE_REL).is_file()
    return mod, root


def rows(root: Path):
    # Post engine/data split the index is two physical stores: content under
    # .memory-index/ and code (skill, rule) under .memory-index-code/. Coverage
    # and air-gap guarantees span BOTH, so union them here.
    out = []
    for store_rel in (".memory-index/index.db", ".memory-index-code/index.db"):
        db = root / store_rel
        if not db.is_file():
            continue
        conn = sqlite3.connect(str(db))
        out.extend(conn.execute(
            "SELECT path, layer, classification FROM notes").fetchall())
        conn.close()
    return out


def query_ns(text, **kw):
    base = dict(text=text, layer=None, collection="content",
                top_k=8, threshold=None, json=False)
    base.update(kw)
    return types.SimpleNamespace(**base)


# --- classification --------------------------------------------------------

def test_every_row_is_classified(tmp_path, monkeypatch):
    mod, root = build_index(tmp_path, monkeypatch)
    by_path = {p: c for p, _, c in rows(root)}
    assert by_path, "index is empty"
    assert all(c in ("corporate", "ceo-only") for c in by_path.values()), by_path
    assert by_path["crm/contacts/alpha.md"] == "ceo-only"
    assert by_path["context/strategy.md"] == "corporate"
    assert by_path[".claude/rules/voice.md"] == "corporate"


# --- collections -----------------------------------------------------------

def test_collection_code_excludes_content_and_vice_versa(tmp_path, monkeypatch, capsys):
    mod, root = build_index(tmp_path, monkeypatch)
    capsys.readouterr()  # drain build-time stdout so only query JSON remains

    mod.cmd_query(query_ns("rule", collection="code", json=True))
    code_hits = json.loads(capsys.readouterr().out)["hits"]
    code_layers = {h["layer"] for h in code_hits}
    assert code_layers and code_layers <= {"skill", "rule"}, code_hits

    mod.cmd_query(query_ns("alpha sovereignty pipeline", collection="content", json=True))
    content_hits = json.loads(capsys.readouterr().out)["hits"]
    content_layers = {h["layer"] for h in content_hits}
    assert content_layers and content_layers <= {"context", "crm"}, content_hits
    assert "skill" not in content_layers and "rule" not in content_layers


def test_collection_all_spans_both(tmp_path, monkeypatch, capsys):
    mod, root = build_index(tmp_path, monkeypatch)
    capsys.readouterr()  # drain build-time stdout
    mod.cmd_query(query_ns("rule alpha pipeline sovereignty", collection="all",
                           json=True, threshold=0.0))
    hits = json.loads(capsys.readouterr().out)["hits"]
    layers = {h["layer"] for h in hits}
    assert layers & {"skill", "rule"} and layers & {"context", "crm"}, hits


def test_unknown_collection_errors(tmp_path, monkeypatch):
    mod, root = build_index(tmp_path, monkeypatch)
    rc = mod.cmd_query(query_ns("alpha", collection="bogus"))
    assert rc == 1


# --- --json shape ----------------------------------------------------------

def test_json_hit_shape(tmp_path, monkeypatch, capsys):
    mod, root = build_index(tmp_path, monkeypatch)
    capsys.readouterr()  # drain build-time stdout
    mod.cmd_query(query_ns("alpha", json=True))
    obj = json.loads(capsys.readouterr().out)
    assert obj["gap"] is False
    assert obj["hits"], obj
    h = obj["hits"][0]
    for key in ("path", "title", "layer", "ntype", "classification",
                "collection", "score", "channels"):
        assert key in h, h
    assert isinstance(h["channels"], list)


def test_json_gap_shape(tmp_path, monkeypatch, capsys):
    mod, root = build_index(tmp_path, monkeypatch)
    capsys.readouterr()  # drain build-time stdout
    # 'secret diary' appears only in the air-gapped fixtures -> never indexed.
    mod.cmd_query(query_ns("secret diary", collection="all", json=True))
    obj = json.loads(capsys.readouterr().out)
    assert obj["gap"] is True, obj
    assert obj["hits"] == []
    assert "best" in obj and "threshold" in obj


# --- air-gap regression across the new globs -------------------------------

def test_airgap_holds_under_new_globs(tmp_path, monkeypatch):
    mod, root = build_index(tmp_path, monkeypatch)
    paths = [p for p, _, _ in rows(root)]
    assert not any(p.startswith("_secure/") for p in paths), paths
    assert not any("personal" in p.split("/") for p in paths), paths
    # positive control: code + content content DID enter
    assert ".claude/skills/recall/SKILL.md" in paths
    assert "context/strategy.md" in paths
