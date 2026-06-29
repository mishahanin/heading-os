"""Two-store tests: the engine-side `code` index vs the data-side `content` index.

Verifies the seam split added 2026-06-16: content layers build from the DATA
root into STORE_REL; the `code` collection (skill/rule) builds from the ENGINE
root into CODE_STORE_REL. Build routing, per-store query routing, pooled
`--collection all` fusion, the id-namespace invariant, and `open_store`
back-compat are all checked on a hermetic temp pair of roots with a mock embedder.

Patch discipline (the M2 scrutiny finding): the two-store case needs
content-root != engine-root, which the single-root HEADING_OS_DATA env approach
cannot express. So both roots are monkeypatched ON THE LOADED MODULE and
HEADING_OS_DATA is NOT set (the env branch in get_data_root would otherwise
shadow the patch).

Run: python3 -m pytest tests/test_memory_index_code_store.py
"""
import importlib.util
import sys
import types
from pathlib import Path

import pytest

WORKSPACE = Path(__file__).resolve().parent.parent
SCRIPT = WORKSPACE / "scripts" / "memory-index.py"

# Orthogonal one-hot vocab: each note carries exactly one term, so a single-term
# query matches only its note (cosine 1.0) and is orthogonal (0.0) to the others.
# Every fixture note contains a term, so the empty-text fallback never fires.
VOCAB = ["alpha", "beta", "gamma"]


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
    spec = importlib.util.spec_from_file_location("memory_index_code_mod", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def write(path: Path, body: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


CONFIG = (
    "model: bge-m3\nhost: http://localhost:11434\nthreshold: 0.55\ntop_k: 8\n"
    "layers:\n"
    "  - {layer: odin, glob: 'knowledge/odin-brain/positions/*.md'}\n"
    "  - {layer: skill, glob: '.claude/skills/**/SKILL.md'}\n"
    "  - {layer: rule, glob: '.claude/rules/*.md'}\n"
    "collections:\n"
    "  content: [odin]\n"
    "  code: [skill, rule]\n"
    "deny_prefixes: ['_secure/']\ndeny_segments: ['personal']\n"
)


@pytest.fixture
def two_store(tmp_path, monkeypatch):
    content_root = tmp_path / "data"
    engine_root = tmp_path / "engine"

    # Content lives on the DATA side.
    write(content_root / "knowledge/odin-brain/positions/alpha-pos.md",
          "---\ntitle: Alpha Position\ntype: position\n---\n# Alpha Position\n\nalpha alpha alpha.\n")
    # Code lives on the ENGINE side: config + a skill + a rule.
    write(engine_root / "config/memory-index.yaml", CONFIG)
    write(engine_root / ".claude/skills/betaskill/SKILL.md",
          "---\nname: betaskill\n---\n# Beta Skill\n\nbeta beta beta.\n")
    write(engine_root / ".claude/rules/gammarule.md", "# Gamma Rule\n\ngamma gamma gamma.\n")

    mod = load_module()
    # M2: patch BOTH roots on the module; do NOT set HEADING_OS_DATA.
    monkeypatch.setattr(mod, "get_data_root", lambda: content_root)
    monkeypatch.setattr(mod, "get_workspace_root", lambda: engine_root)
    monkeypatch.setattr(mod, "embed", fake_embed)
    assert mod.cmd_build(types.SimpleNamespace(force=True)) == 0
    return mod, content_root, engine_root


def _ids(mod, root, store_rel):
    conn = mod.open_store(root, store_rel)
    ids, _metas, _matrix = mod._load_index(conn)
    conn.close()
    return ids


def _query(mod, capsys, text, collection):
    capsys.readouterr()  # drop prior output
    args = types.SimpleNamespace(text=text, layer=None, collection=collection,
                                 top_k=0, threshold=None, json=False)
    assert mod.cmd_query(args) == 0
    return capsys.readouterr().out


# --- Build: two stores, layers routed by seam --------------------------------

def test_build_creates_two_stores(two_store):
    mod, content_root, engine_root = two_store
    assert (content_root / mod.STORE_REL).is_file()
    assert (engine_root / mod.CODE_STORE_REL).is_file()


def test_isolation_no_write_to_real_roots(two_store):
    mod, content_root, engine_root = two_store
    # Both stores must land under the temp roots, never the real workspace.
    assert str(WORKSPACE) not in str(content_root)
    assert (content_root / mod.STORE_REL).is_file()
    assert (engine_root / mod.CODE_STORE_REL).is_file()
    # The real engine code store is not what this test wrote to.
    assert content_root != WORKSPACE and engine_root != WORKSPACE


def test_code_layers_only_in_code_store(two_store):
    mod, content_root, engine_root = two_store
    content_ids = _ids(mod, content_root, mod.STORE_REL)
    code_ids = _ids(mod, engine_root, mod.CODE_STORE_REL)
    # content store holds only the odin position
    assert any("alpha-pos.md" in i for i in content_ids)
    assert not any(".claude/skills" in i or ".claude/rules" in i for i in content_ids)
    # code store holds the skill + rule, no content
    assert any(".claude/skills/betaskill/SKILL.md" == i for i in code_ids)
    assert any(".claude/rules/gammarule.md" == i for i in code_ids)
    assert not any("odin-brain" in i for i in code_ids)


def test_id_namespace_invariant(two_store):
    """No content-store id is a code path -> ids never collide when pooled."""
    mod, content_root, _ = two_store
    for i in _ids(mod, content_root, mod.STORE_REL):
        assert not i.startswith(".claude/skills/")
        assert not i.startswith(".claude/rules/")


# --- Query routing + fusion --------------------------------------------------

def test_content_query_excludes_code(two_store, capsys):
    mod, *_ = two_store
    out = _query(mod, capsys, "beta", "content")  # beta is a code term
    assert ".claude/skills" not in out and ".claude/rules" not in out


def test_code_query_returns_skill(two_store, capsys):
    mod, *_ = two_store
    out = _query(mod, capsys, "beta", "code")
    assert ".claude/skills/betaskill/SKILL.md" in out


def test_code_query_returns_rule(two_store, capsys):
    mod, *_ = two_store
    out = _query(mod, capsys, "gamma", "code")
    assert ".claude/rules/gammarule.md" in out


def test_all_collection_pools_both_stores(two_store, capsys):
    mod, *_ = two_store
    # alpha (content) and beta (code) both present -> a query for each, under
    # --collection all, must reach the right store.
    out_content = _query(mod, capsys, "alpha", "all")
    assert "alpha-pos.md" in out_content
    out_code = _query(mod, capsys, "beta", "all")
    assert ".claude/skills/betaskill/SKILL.md" in out_code


# --- Back-compat -------------------------------------------------------------

def test_open_store_default_path_unchanged(two_store):
    mod, content_root, _ = two_store
    # open_store() with no store_rel still resolves STORE_REL under the root.
    conn = mod.open_store(content_root)
    assert conn is not None
    conn.close()
    assert (content_root / mod.STORE_REL).is_file()
