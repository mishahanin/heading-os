"""Near-miss recall: when nothing clears the confidence threshold but the best
hit is within `near_miss_margin` of it, return the hits flagged as below
threshold instead of declaring an honest gap.

The real defect this guards (measured 2026-08-07): the query
"почему мы не пошли в Омегу" scored 0.481 and 0.459 on the two correct
documents against a 0.55 threshold, and recall answered "a gap in this area of
memory" while holding the answer at rank 1 and 2.

Run: .venv/bin/python -m pytest tests/test_memory_index_near_miss.py
"""

import importlib.util
import json as _json
import sys
import types
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parent.parent
SCRIPT = WORKSPACE / "scripts" / "memory-index.py"

VOCAB = ["omega", "prohibited", "tier", "sailing", "regatta", "pricing"]


def fake_embed(texts, *, model, host, batch=32, timeout=120):
    """Deterministic embedder: a vector of term counts over VOCAB.

    Cosine is then a pure function of shared vocabulary, so a test can place a
    document at a chosen distance from a query without touching ollama.
    """
    out = []
    for t in texts:
        low = t.lower()
        v = [float(low.count(w)) for w in VOCAB]
        out.append(v if any(v) else [1e-6] * len(VOCAB))
    return out


def load_module():
    sys.path.insert(0, str(WORKSPACE))
    spec = importlib.util.spec_from_file_location("memory_index_near_miss_mod", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def write(path: Path, body: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def pin_the_embedder(mod, monkeypatch):
    """Mocking `embed` is not the only route off this machine.

    `load_config` resolves the pinned host through `_resolve_embed_host` and
    `cmd_build` asks that host for the model's weight digest through
    `model_digest`; both dial the `host:` line in the fixture config, which is a
    real address. MEASURED 2026-09-01 with `socket.socket.connect` counted over
    a run of this file alone: 4 connects to 127.0.0.1:11434, so the file's
    "no ollama" claim held for the embedding call and nothing else. A unit test
    that reaches the embedder passes or fails on whether a Windows-side ollama
    happens to be up, which is a fact about the host and not about the code, and
    it cannot run on a public clone at all. Same shape as
    tests/test_five_claims_that_covered_one_path_of_several.py.
    """
    monkeypatch.setattr(mod, "model_digest", lambda **k: None)
    monkeypatch.setattr(mod, "_resolve_embed_host", lambda host=None, **k: host)


def make_config(root: Path, *, threshold: str, margin: str):
    write(
        root / "config/memory-index.yaml",
        "model: bge-m3\n"
        "host: http://localhost:11434\n"
        f"threshold: {threshold}\n"
        f"near_miss_margin: {margin}\n"
        "top_k: 8\n"
        "layers:\n"
        "  - {layer: odin, glob: 'knowledge/odin-brain/**/*.md'}\n"
        "deny_prefixes: ['_secure/']\n"
        "deny_segments: ['personal']\n",
    )


def build_corpus(root: Path):
    """Filenames deliberately share NO token with any test query.

    The path-match channel (`_path_match_ids`) admits a document whose humanized
    PATH contains a query token, and it bypasses the convergence gate. A fixture
    named `omega-tier1.md` queried with "omega tier" is therefore admitted
    through the path channel, `combined_sparse` is non-empty, and the near-miss
    branch is never reached -- the test would pass or fail for the wrong reason.
    Verified 2026-08-07: `_path_match_ids("omega tier", [...])` returns that file.
    Single letters are safe (`humanize_path` tokens are filtered at len >= 2),
    but plain neutral stems are clearer.
    """
    # Target note: shares vocabulary with the query -> cosine below threshold but
    # inside the margin. No lexical (FTS) and no PATH overlap with the query.
    write(root / "knowledge/odin-brain/episodes/note-alpha.md",
          "---\ntitle: Omega opportunity left alone\n---\n\nomega prohibited prohibited\n")
    # Far note: shares nothing with the query.
    write(root / "knowledge/odin-brain/episodes/note-beta.md",
          "---\ntitle: Sailing regatta\n---\n\nsailing sailing regatta\n")


def run_query(mod, text, *, threshold=None):
    mod.cmd_query(types.SimpleNamespace(
        text=text, layer=None, collection="content",
        top_k=8, threshold=threshold, json=True))


def prepare(tmp_path, monkeypatch, *, threshold, margin):
    root = tmp_path
    make_config(root, threshold=threshold, margin=margin)
    build_corpus(root)
    mod = load_module()
    monkeypatch.setenv("HEADING_OS_DATA", str(root))
    monkeypatch.setattr(mod, "get_workspace_root", lambda: root)
    monkeypatch.setattr(mod, "embed", fake_embed)
    monkeypatch.setattr(mod, "get_classification", lambda p: "ceo-only")
    pin_the_embedder(mod, monkeypatch)
    assert mod.cmd_build(types.SimpleNamespace(force=True)) == 0
    # Isolation guard: the DB must live under the temp root, never the real data
    # root. Without it, a future change to HEADING_OS_DATA resolution would let
    # this test rebuild the production index with a 6-dimensional fake embedder.
    assert (root / mod.STORE_REL).is_file()
    return mod


def test_near_miss_returns_hits_instead_of_gap(tmp_path, monkeypatch, capsys):
    """Threshold 0.99 puts everything below it; margin 0.5 admits the best hit."""
    mod = prepare(tmp_path, monkeypatch, threshold="0.99", margin="0.5")
    capsys.readouterr()
    run_query(mod, "omega tier")
    obj = _json.loads(capsys.readouterr().out)

    assert obj["gap"] is False, obj
    assert obj.get("near_miss") is True, obj
    assert obj.get("confident") is False, obj
    paths = [h["path"] for h in obj["hits"]]
    assert "knowledge/odin-brain/episodes/note-alpha.md" in paths, obj
    hit = next(h for h in obj["hits"] if h["path"].endswith("note-alpha.md"))
    assert hit["below_threshold"] is True, hit


def test_honest_gap_survives_outside_margin(tmp_path, monkeypatch, capsys):
    """A query far from everything must STILL report a gap. The near-miss channel
    widens the window by a fixed margin; it does not abolish the window."""
    mod = prepare(tmp_path, monkeypatch, threshold="0.99", margin="0.01")
    capsys.readouterr()
    run_query(mod, "omega tier")
    obj = _json.loads(capsys.readouterr().out)

    assert obj["gap"] is True, obj
    assert obj["hits"] == [], obj


def test_near_miss_collapses_chunks_of_one_file(tmp_path, monkeypatch, capsys):
    """A near-miss on a chunked document must yield ONE hit, not one per chunk.

    Every other channel passes through `_collapse`; the near-miss channel must
    too, or a long thread returns up to 12 rows of itself.
    """
    root = tmp_path
    make_config(root, threshold="0.99", margin="0.5")
    # `thread` is in the chunked-layers allowlist; make the body long enough to
    # split into several chunks, all sharing the query vocabulary.
    #
    # NOTE (deviation from the brief, 2026-08-07): the brief's version of this
    # fixture appended the new layer line and a `chunking:` key AFTER
    # `deny_segments:`, which breaks the `layers:` block sequence (invalid
    # YAML) and used a key name (`chunking`) the schema does not read (the
    # real key is `chunk:`, see config/memory-index.yaml and
    # `load_config`'s `cfg.setdefault("chunk", {})`). Left as written, the
    # config either fails to parse or silently chunks nothing, so the test
    # can never exercise the multi-chunk collapse it documents. Fixed here by
    # inserting the layer line inside the `layers:` block and using the real
    # `chunk:` key, with the same field values the brief specified.
    cfg_text = (root / "config/memory-index.yaml").read_text(encoding="utf-8")
    cfg_text = cfg_text.replace(
        "  - {layer: odin, glob: 'knowledge/odin-brain/**/*.md'}\n",
        "  - {layer: odin, glob: 'knowledge/odin-brain/**/*.md'}\n"
        "  - {layer: thread, glob: 'threads/**/*.md'}\n",
    )
    cfg_text += (
        "chunk:\n  enabled_layers: [thread]\n  max_chars: 200\n"
        "  overlap: 20\n  max_chunks: 12\n"
    )
    write(root / "config/memory-index.yaml", cfg_text)
    write(root / "threads/business/note-gamma.md",
          "---\ntitle: Long thread\n---\n\n" + ("omega prohibited tier filler. " * 40))
    build_corpus(root)

    mod = load_module()
    monkeypatch.setenv("HEADING_OS_DATA", str(root))
    monkeypatch.setattr(mod, "get_workspace_root", lambda: root)
    monkeypatch.setattr(mod, "embed", fake_embed)
    monkeypatch.setattr(mod, "get_classification", lambda p: "ceo-only")
    pin_the_embedder(mod, monkeypatch)
    assert mod.cmd_build(types.SimpleNamespace(force=True)) == 0
    assert (root / mod.STORE_REL).is_file()
    capsys.readouterr()

    run_query(mod, "omega tier")
    obj = _json.loads(capsys.readouterr().out)

    assert obj.get("near_miss") is True, obj
    thread_hits = [h for h in obj["hits"] if h["path"].endswith("note-gamma.md")]
    assert len(thread_hits) == 1, thread_hits
    # Guards against a vacuous pass: this assertion fails if chunking is ever
    # disabled (enabled_layers empty), since an unchunked file collapses to
    # one row trivially and would never exercise `_collapse` at all.
    assert thread_hits[0]["chunks_total"] > 1, thread_hits


def test_normal_hits_carry_no_near_miss_flag(tmp_path, monkeypatch, capsys):
    """When results clear the threshold, output shape is unchanged: no
    `near_miss` key, no `below_threshold` on hits."""
    mod = prepare(tmp_path, monkeypatch, threshold="0.1", margin="0.12")
    capsys.readouterr()
    run_query(mod, "omega prohibited")
    obj = _json.loads(capsys.readouterr().out)

    assert obj["gap"] is False, obj
    assert "near_miss" not in obj, obj
    assert "confident" not in obj, obj
    assert all("below_threshold" not in h for h in obj["hits"]), obj
