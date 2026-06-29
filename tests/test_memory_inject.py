"""Session-start memory injection hook (point 4).

Verifies .claude/hooks/memory-inject.py: builds a tiny temp index with the
memory-index engine (mock embedder, no ollama), points the hook's DB_PATH /
CONFIG_PATH at it, and checks:
  - disabled (default) emits nothing;
  - enabled emits a capped additionalContext block;
  - the `memory` layer is excluded even when listed in inject.layers;
  - air-gapped paths never appear;
  - a missing DB emits nothing and exits 0 (never blocks startup);
  - no ollama/embedding is invoked (the hook does pure SQL).

Run: python3 -m pytest tests/test_memory_inject.py
"""

import importlib.util
import json as _json
import sys
import types
from pathlib import Path

import pytest

WORKSPACE = Path(__file__).resolve().parent.parent
ENGINE = WORKSPACE / "scripts" / "memory-index.py"
HOOK = WORKSPACE / ".claude" / "hooks" / "memory-inject.py"

VOCAB = ["sovereignty", "pipeline", "alpha", "filler", "secret"]


def fake_embed(texts, *, model, host, batch=32, timeout=120):
    out = []
    for t in texts:
        v = [float(t.lower().count(w)) for w in VOCAB]
        out.append(v if any(v) else [1e-6] * len(VOCAB))
    return out


def load(path, name):
    sys.path.insert(0, str(WORKSPACE))
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def write(path: Path, body: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def build_index(tmp_path, monkeypatch, enabled: bool):
    """Build a temp index + write a config carrying the inject block."""
    root = tmp_path
    write(root / "threads/business/deal-acme.md",
          "---\ntitle: ACME deal\n---\n\n# ACME\n\npipeline sovereignty.\n")
    # a memory-layer file (must be excluded from injection)
    mem = root / ".claude/projects/p/memory"
    write(mem / "fact.md", "---\ntitle: A memory fact\n---\n\nalpha.\n")
    write(mem / "MEMORY.md", "# Memory index\n\nalpha.\n")
    # air-gapped
    write(root / "_secure/x/secret.md", "# vault\n\nsecret.\n")

    cfg = (
        "model: bge-m3\n"
        "host: http://localhost:11434\n"
        "threshold: 0.2\n"
        "top_k: 8\n"
        "layers:\n"
        "  - {layer: thread, glob: 'threads/business/*.md'}\n"
        "  - {layer: memory, glob: '.claude/projects/*/memory/*.md'}\n"
        "  - {layer: vaulttest, glob: '_secure/**/*.md'}\n"
        f"inject:\n"
        f"  enabled: {'true' if enabled else 'false'}\n"
        "  max_tokens: 1300\n"
        "  layers: [thread, memory]\n"   # memory listed -> must still be excluded
        "  top_k: 12\n"
        "deny_prefixes: ['_secure/']\n"
        "deny_segments: ['personal']\n"
    )
    write(root / "config/memory-index.yaml", cfg)

    eng = load(ENGINE, "mi_engine_inject")
    # Redirect the data root too: the index DB resolves via get_data_root()
    # (STORE_REL under it), not get_workspace_root(). Without this the build
    # writes into the REAL ../.heading-os-data/.memory-index/index.db and
    # mutates live data. HEADING_OS_DATA wins first in get_data_root().
    monkeypatch.setenv("HEADING_OS_DATA", str(root))
    monkeypatch.setattr(eng, "get_workspace_root", lambda: root)
    monkeypatch.setattr(eng, "embed", fake_embed)
    monkeypatch.setattr(eng, "get_classification", lambda p: "ceo-only")
    assert eng.cmd_build(types.SimpleNamespace(force=True)) == 0
    # Isolation guard: the DB must live under the temp root, never the real
    # data root. STORE_REL is the engine's canonical relative store path.
    assert (root / eng.STORE_REL).is_file()
    return root


def run_hook(monkeypatch, root, capsys, db_exists=True):
    capsys.readouterr()  # drain build-time stdout so only hook output remains
    hook = load(HOOK, "memory_inject_hook")
    monkeypatch.setattr(hook, "CONFIG_PATH", root / "config" / "memory-index.yaml")
    db = root / ".memory-index" / "index.db"
    if not db_exists:
        db = root / ".memory-index" / "nonexistent.db"
    monkeypatch.setattr(hook, "DB_PATH", db)
    with pytest.raises(SystemExit) as e:
        hook.main()
    assert e.value.code in (0, None)
    return capsys.readouterr().out.strip()


def test_disabled_emits_nothing(tmp_path, monkeypatch, capsys):
    root = build_index(tmp_path, monkeypatch, enabled=False)
    assert run_hook(monkeypatch, root, capsys) == ""


def test_enabled_emits_capped_block_excluding_memory_and_airgap(tmp_path, monkeypatch, capsys):
    root = build_index(tmp_path, monkeypatch, enabled=True)
    out = run_hook(monkeypatch, root, capsys)
    assert out, "expected an injection block"
    payload = _json.loads(out)
    ctx = payload["hookSpecificOutput"]["additionalContext"]
    assert "threads/business/deal-acme.md" in ctx          # thread surfaced
    assert "memory" not in ctx.split("\n", 1)[0].lower() or "Recent" in ctx  # header sane
    # memory-layer files and MEMORY.md never injected
    assert "fact.md" not in ctx
    assert "MEMORY.md" not in ctx
    # air-gapped never injected
    assert "_secure/" not in ctx
    assert "secret" not in ctx.lower()
    # token cap respected (~chars/4)
    assert (len(ctx) // 4) <= 1300


def build_quota_index(tmp_path, monkeypatch):
    """Temp index with three layers: 5 plain threads, 5 evergreen odin notes,
    3 context notes -- the shape that previously let evergreen odin crowd out
    everything else under pure evergreen-first ordering."""
    root = tmp_path
    for i in range(5):
        write(root / f"threads/business/deal-{i}.md",
              f"---\ntitle: Deal {i}\n---\n\n# Deal {i}\n\npipeline.\n")
    for i in range(5):
        write(root / f"knowledge/odin-brain/positions/pos-{i}.md",
              f"---\ntitle: Position {i}\nstatus: evergreen\n---\n\nsovereignty.\n")
    for i in range(3):
        write(root / f"context/ctx-{i}.md",
              f"---\ntitle: Context {i}\n---\n\nalpha.\n")
    cfg = (
        "model: bge-m3\n"
        "host: http://localhost:11434\n"
        "threshold: 0.2\n"
        "top_k: 8\n"
        "layers:\n"
        "  - {layer: thread, glob: 'threads/business/*.md'}\n"
        "  - {layer: odin, glob: 'knowledge/odin-brain/**/*.md'}\n"
        "  - {layer: context, glob: 'context/*.md'}\n"
        "inject:\n"
        "  enabled: true\n"
        "  max_tokens: 1300\n"
        "  layers: [thread, odin, context]\n"
        "  quota: {thread: 2, odin: 2, context: 2}\n"
        "  top_k: 12\n"
        "deny_prefixes: ['_secure/']\n"
        "deny_segments: ['personal']\n"
    )
    write(root / "config/memory-index.yaml", cfg)
    eng = load(ENGINE, "mi_engine_quota")
    # Redirect the data root too (see build_index for rationale).
    monkeypatch.setenv("HEADING_OS_DATA", str(root))
    monkeypatch.setattr(eng, "get_workspace_root", lambda: root)
    monkeypatch.setattr(eng, "embed", fake_embed)
    monkeypatch.setattr(eng, "get_classification", lambda p: "ceo-only")
    assert eng.cmd_build(types.SimpleNamespace(force=True)) == 0
    assert (root / eng.STORE_REL).is_file()
    return root


def test_quota_guarantees_per_layer_slots(tmp_path, monkeypatch, capsys):
    root = build_quota_index(tmp_path, monkeypatch)
    out = run_hook(monkeypatch, root, capsys)
    assert out, "expected an injection block"
    ctx = _json.loads(out)["hookSpecificOutput"]["additionalContext"]
    # each layer gets exactly its quota -- no layer crowds out another
    assert ctx.count("- [thread]") == 2
    assert ctx.count("- [odin]") == 2
    assert ctx.count("- [context]") == 2
    # quota beats pure evergreen-first: plain threads survive 5 evergreen odin notes
    assert "- [thread]" in ctx


def test_missing_db_emits_nothing(tmp_path, monkeypatch, capsys):
    root = build_index(tmp_path, monkeypatch, enabled=True)
    assert run_hook(monkeypatch, root, capsys, db_exists=False) == ""


def test_hook_does_no_embedding(tmp_path, monkeypatch, capsys):
    """The hook must not import or call the embedder (pure SQL, ollama-free)."""
    root = build_index(tmp_path, monkeypatch, enabled=True)
    hook = load(HOOK, "memory_inject_hook_noembed")
    # No `embed` symbol on the hook module, and source never references ollama.
    assert not hasattr(hook, "embed")
    src = HOOK.read_text(encoding="utf-8")
    assert "embed(" not in src and "11434" not in src
