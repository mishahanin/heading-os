"""Session-start memory injection hook (point 4).

SCOPE, stated first because it changes what a green result here means:
`.claude/hooks/memory-inject.py` is registered in NO settings file. Its own
module docstring records the measurement (2026-08-31, zero hits for
"memory-inject" across the live settings file and all three per-OS templates);
`.claude/hooks/recall-inject.py` is the registered successor, on
UserPromptSubmit. So this file tests a body that is kept and still works but
does not run in any session. It is a guard against the code rotting before it
is ever wired, not evidence that memory injection is happening. Do not read a
pass here as coverage of a live SessionStart path.

Verifies .claude/hooks/memory-inject.py: builds a tiny temp index with the
memory-index engine (mock embedder, no ollama), points the hook's db_path() /
CONFIG_PATH at it, and checks:
  - disabled (default) emits nothing;
  - enabled emits a capped additionalContext block, and the cap truncates;
  - the `memory` layer is excluded even when listed in inject.layers;
  - air-gapped paths never appear, including a row planted straight into the
    store under an injected layer, which is the only way the hook's own
    `is_denied` filter is asked the question;
  - only chunk 0 of a file is listed;
  - within a layer, evergreen outranks a newer note;
  - a missing DB emits nothing and exits 0 (never blocks startup);
  - no ollama/embedding is invoked (the hook does pure SQL).

Run: python3 -m pytest tests/test_memory_inject.py
"""

import importlib.util
import json as _json
import sqlite3
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


def pin_the_embedder(mod, monkeypatch):
    """Mocking `embed` is not the only route off this machine.

    `load_config` resolves the pinned host through `_resolve_embed_host` and
    `cmd_build` asks that host for the model's weight digest through
    `model_digest`; both dial the `host:` line in the fixture config, which is a
    real address. MEASURED 2026-09-01 with `socket.socket.connect` counted over
    a run of this file alone: 9 connects to 127.0.0.1:11434, so the file's
    "no ollama" claim held for the embedding call and nothing else. A unit test
    that reaches the embedder passes or fails on whether a Windows-side ollama
    happens to be up, which is a fact about the host and not about the code, and
    it cannot run on a public clone at all. Same shape as
    tests/test_five_claims_that_covered_one_path_of_several.py.
    """
    monkeypatch.setattr(mod, "model_digest", lambda **k: None)
    monkeypatch.setattr(mod, "_resolve_embed_host", lambda host=None, **k: host)


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
    pin_the_embedder(eng, monkeypatch)
    monkeypatch.setattr(eng, "get_classification", lambda p: "ceo-only")
    assert eng.cmd_build(types.SimpleNamespace(force=True)) == 0
    # Isolation guard: the DB must live under the temp root, never the real
    # data root. STORE_REL is the engine's canonical relative store path.
    assert (root / eng.STORE_REL).is_file()
    return root


def plant_row(root, *, path, title, layer, chunk=0, status="", mtime=9e9):
    """Write one row straight into the built index.

    The three properties below (air-gap, chunk 0, per-layer salience order) are
    all filters the HOOK applies to rows the builder would never have produced
    in the first place: `cmd_build` drops denied paths before they reach the
    store, and a short fixture note never chunks. A fixture that cannot produce
    the row cannot reach the branch that rejects it -- MEASURED 2026-09-01, the
    `is_denied` and `chunk = 0` filters could both be deleted with this file
    still green. Planting the row is what puts the hook's own filter under test
    rather than the builder's.
    """
    conn = sqlite3.connect(str(root / ".memory-index" / "index.db"))
    conn.execute(
        "INSERT OR REPLACE INTO notes (id, path, title, layer, ntype, mtime, dim,"
        " body, created, updated, confidence, status, classification, chunk,"
        " access_count, last_accessed, embedding)"
        " VALUES (?, ?, ?, ?, '', ?, 1, 'b', '', '', '', ?, 'ceo-only', ?, 0, '', ?)",
        (f"{path}#{chunk}" if chunk else path, path, title, layer, mtime, status,
         chunk, b"\x00\x00\x00\x00"),
    )
    conn.commit()
    conn.close()


def run_hook(monkeypatch, root, capsys, db_exists=True):
    capsys.readouterr()  # drain build-time stdout so only hook output remains
    hook = load(HOOK, "memory_inject_hook")
    monkeypatch.setattr(hook, "CONFIG_PATH", root / "config" / "memory-index.yaml")
    db = root / ".memory-index" / "index.db"
    if not db_exists:
        db = root / ".memory-index" / "nonexistent.db"
    monkeypatch.setattr(hook, "db_path", lambda p=db: p)
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


def test_an_air_gapped_row_already_in_the_store_is_still_refused(
        tmp_path, monkeypatch, capsys):
    """The hook's own `is_denied` filter, not the builder's deny_prefixes.

    The build drops `_secure/` before a row exists, so the assertion above
    ("_secure/ not in ctx") is satisfied by a store that never held the row.
    This plants one under an INJECTED layer, which is the only way the hook's
    belt-and-braces filter is ever asked the question. Its module docstring
    promises the filter; nothing measured it until 2026-09-01.
    """
    root = build_index(tmp_path, monkeypatch, enabled=True)
    plant_row(root, path="_secure/vault/ledger.md", title="Vault ledger",
              layer="thread")
    plant_row(root, path="threads/business/personal/diary.md",
              title="Segment denied", layer="thread")
    ctx = _json.loads(run_hook(monkeypatch, root, capsys))[
        "hookSpecificOutput"]["additionalContext"]

    assert "threads/business/deal-acme.md" in ctx, ctx   # the corpus is non-empty
    assert "_secure/" not in ctx, ctx
    assert "Vault ledger" not in ctx, ctx
    assert "Segment denied" not in ctx, ctx


def test_only_the_first_chunk_of_a_file_is_injected(tmp_path, monkeypatch, capsys):
    """`chunk = 0` is what keeps one long thread from filling the whole block.

    A chunked note carries N rows with the same path and title; without the
    predicate the snapshot lists the same file N times and the layer's slots go
    to one document.
    """
    root = build_index(tmp_path, monkeypatch, enabled=True)
    for n in (1, 2, 3):
        plant_row(root, path="threads/business/long-thread.md",
                  title="Long thread", layer="thread", chunk=n)
    ctx = _json.loads(run_hook(monkeypatch, root, capsys))[
        "hookSpecificOutput"]["additionalContext"]

    assert ctx.count("Long thread") == 0, ctx
    assert "threads/business/deal-acme.md" in ctx, ctx


def test_the_token_cap_actually_truncates(tmp_path, monkeypatch, capsys):
    """A cap that never binds is not a cap.

    The block in the test above holds one line, so `(len(ctx) // 4) <= 1300`
    is true whatever the budget code does -- MEASURED 2026-09-01 by disabling
    the break entirely, which changed nothing there. Here the layer's twelve
    slots are filled with 600-character titles, so ~1300 tokens runs out first
    and the loop has to stop before the rows do.
    """
    root = build_index(tmp_path, monkeypatch, enabled=True)
    slots = 12                      # inject.top_k, split across one live layer
    for n in range(slots + 3):
        plant_row(root, path=f"threads/business/bulk-{n:02d}.md",
                  title=f"Bulk thread {n:02d} " + ("padding " * 74),
                  layer="thread", mtime=8e9 + n)
    ctx = _json.loads(run_hook(monkeypatch, root, capsys))[
        "hookSpecificOutput"]["additionalContext"]

    lines = [ln for ln in ctx.splitlines() if ln.startswith("- [")]
    assert 0 < len(lines) < slots, len(lines)
    assert (len(ctx) // 4) <= 1300 + len(lines[0]) // 4, len(ctx)


def test_evergreen_outranks_a_newer_note_in_the_same_layer(
        tmp_path, monkeypatch, capsys):
    """Salience order inside a layer: evergreen first, then most recent.

    The quota test below counts rows per layer and never looks at their order,
    so the `ORDER BY (status = 'evergreen') DESC` could be reversed with every
    assertion in this file still passing.
    """
    root = build_index(tmp_path, monkeypatch, enabled=True)
    plant_row(root, path="threads/business/timeless.md", title="Timeless note",
              layer="thread", status="evergreen", mtime=1.0)
    plant_row(root, path="threads/business/yesterday.md", title="Fresh note",
              layer="thread", mtime=9.5e9)
    ctx = _json.loads(run_hook(monkeypatch, root, capsys))[
        "hookSpecificOutput"]["additionalContext"]

    assert "Timeless note" in ctx and "Fresh note" in ctx, ctx
    assert ctx.index("Timeless note") < ctx.index("Fresh note"), ctx


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
    pin_the_embedder(eng, monkeypatch)
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
