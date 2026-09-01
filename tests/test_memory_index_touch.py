"""Tests for `memory-index.py query --touch` — retrieval reinforcement.

The bump is a ranking signal written on the read path, so the rules that keep
it honest are the ones under test: only confident results count, only
auto-memory files count, and a failure must never break a recall.

Run: .venv/bin/python -m pytest tests/test_memory_index_touch.py
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "memory-index.py"

FACT = (
    "---\n"
    "name: example-fact\n"
    "description: fixture fact\n"
    "metadata:\n"
    "  node_type: memory\n"
    "  type: feedback\n"
    "---\n\n"
    "Some fact body.\n"
)


def load_module():
    spec = importlib.util.spec_from_file_location("memory_index_mod", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def memory_dir(tmp_path: Path, monkeypatch) -> Path:
    mdir = tmp_path / "auto-memory"
    mdir.mkdir(parents=True)
    (mdir / "example-fact.md").write_text(FACT, encoding="utf-8")
    import scripts.utils.workspace as ws
    monkeypatch.setattr(ws, "get_auto_memory_dir", lambda: mdir)
    return mdir


def _access_count(path: Path) -> int:
    from scripts.utils.markdown import parse_frontmatter
    meta, _ = parse_frontmatter(path.read_text(encoding="utf-8"))
    nested = meta.get("metadata")
    nested = nested if isinstance(nested, dict) else {}
    return int(nested.get("access_count") or 0)


def _hit(path: str, layer: str = "memory") -> dict:
    return {"path": path, "title": "Example", "layer": layer, "score": 0.7}


def test_bumps_a_confident_memory_layer_hit(memory_dir):
    mod = load_module()
    assert mod._touch_memory_hits([_hit("auto-memory/example-fact.md")]) == 1
    assert _access_count(memory_dir / "example-fact.md") == 1


def test_ignores_non_memory_layers(memory_dir):
    """The negative case has to be a file the bump COULD have written.

    Until 2026-09-01 this passed a `knowledge/odin-brain/` path, which
    `memory_touch._resolve` refuses outright as outside the auto-memory
    directory. Deleting the `layer == "memory"` filter left the test green:
    the refusal that answered was the path guard, not the layer gate, and the
    only line under test was never reached. MEASURED that day by removing the
    filter -- the file stayed green. The fixture below lives INSIDE auto-memory,
    so nothing but the layer gate can decline it.
    """
    mod = load_module()
    stray = memory_dir / "odin-shaped.md"
    stray.write_text(FACT, encoding="utf-8")

    assert mod._touch_memory_hits([_hit("auto-memory/odin-shaped.md", layer="odin")]) == 0

    assert _access_count(stray) == 0
    assert _access_count(memory_dir / "example-fact.md") == 0


def test_deduplicates_repeated_paths_within_one_result_set(memory_dir, monkeypatch):
    """A multi-chunk file returns several hits pointing at one file.

    The count and the file contents cannot see this dedup: `touch_if_stale`
    debounces on the same date, so a second call for the same path returns None
    and changes nothing either way. MEASURED 2026-09-01 by replacing the
    `dict.fromkeys` with a plain list -- the file stayed green, so it was
    measuring the debounce in `scripts/utils/memory_touch.py` and not the line
    it names. The call count is the only place the dedup is visible, so that is
    what is asserted, alongside the effect.
    """
    mod = load_module()
    import scripts.utils.memory_touch as mt
    calls = []
    real = mt.touch_if_stale

    def counting(raw_path, auto_memory_dir, today):
        calls.append(raw_path)
        return real(raw_path, auto_memory_dir, today)

    monkeypatch.setattr(mt, "touch_if_stale", counting)

    hits = [_hit("auto-memory/example-fact.md"), _hit("auto-memory/example-fact.md")]
    assert mod._touch_memory_hits(hits) == 1
    assert calls == ["auto-memory/example-fact.md"], calls
    assert _access_count(memory_dir / "example-fact.md") == 1


def test_a_missing_file_never_raises(memory_dir, capsys):
    mod = load_module()
    assert mod._touch_memory_hits([_hit("auto-memory/does-not-exist.md")]) == 0
    assert "touch:" in capsys.readouterr().err


class _Args:
    def __init__(self, touch):
        self.touch = touch


def test_gate_closed_without_the_flag():
    """Without --touch the read path must write nothing at all."""
    mod = load_module()
    assert mod._should_touch(_Args(touch=False), near_miss=False) is False


def test_gate_closed_on_a_near_miss_result():
    """A near-miss block says relevance is NOT established. Counting it would
    train the ranking on noise."""
    mod = load_module()
    assert mod._should_touch(_Args(touch=True), near_miss=True) is False


def test_gate_open_only_on_a_confident_result_with_the_flag():
    mod = load_module()
    assert mod._should_touch(_Args(touch=True), near_miss=False) is True


def test_gate_closed_for_a_caller_that_has_no_touch_attribute():
    """/recall and the bare-namespace callers never set it."""
    mod = load_module()
    assert mod._should_touch(object(), near_miss=False) is False


def test_an_undecodable_memory_file_never_breaks_the_query(memory_dir, capsys):
    """UnicodeDecodeError is a ValueError, not an OSError or a TouchError.

    With the per-file handler naming only those two, one badly encoded memory
    file propagated out of cmd_query, exited non-zero, and left the recall hook
    emitting nothing on EVERY prompt until someone read stderr. A bump is a
    ranking nicety; nothing it can hit is worth failing a recall over.
    """
    bad = memory_dir / "undecodable.md"
    bad.write_bytes(b"---\nname: bad\nmetadata:\n  type: feedback\n---\n\n\xff\xfe not utf-8\n")

    mod = load_module()
    assert mod._touch_memory_hits([_hit("auto-memory/undecodable.md")]) == 0
    assert "touch: skipped" in capsys.readouterr().err


def test_the_new_count_reaches_the_index_without_a_rebuild(memory_dir, tmp_path, monkeypatch):
    """The bump preserves the source mtime, so the incremental build skips the
    file and would never notice the new count. The column is written here
    instead, in the module that owns the store."""
    mod = load_module()
    monkeypatch.setattr(mod, "get_data_root", lambda: tmp_path)
    cfg = {
        "collections": {"content": ["memory"]},
        "layers": [{"layer": "memory", "glob": "auto-memory/*.md"}],
    }
    rel = "auto-memory/example-fact.md"
    conn = mod.open_store(tmp_path, mod.STORE_REL)
    mod.upsert_note(conn, id_=rel, path=rel, title="Example", layer="memory",
                    ntype="feedback", mtime=1.0, body="body", vec=[0.1, 0.2],
                    access_count=0, last_accessed="")
    conn.commit()
    conn.close()

    assert mod._touch_memory_hits([_hit(rel)], cfg) == 1

    conn = mod.open_store(tmp_path, mod.STORE_REL)
    row = conn.execute(
        "SELECT access_count, last_accessed FROM notes WHERE path = ?", (rel,)
    ).fetchone()
    conn.close()
    assert row[0] == 1
    assert row[1]                       # today's date, whatever the host TZ says
    assert _access_count(memory_dir / "example-fact.md") == 1


def test_the_index_mirror_never_breaks_a_recall(memory_dir, tmp_path, monkeypatch):
    """No store built yet: the bump still lands, the mirror stays quiet.

    "Quiet" includes creating nothing. `open_store` mkdirs the parent and lets
    sqlite3 create the file before running the schema DDL, so a mirror that
    skipped the existence check would MATERIALIZE an empty store from the read
    path -- after which `stats` can no longer tell "never built" from "built and
    empty", which is the same defect `cmd_query` and `_stats_one_store` each
    carry their own guard against. MEASURED 2026-09-01: with the
    `is_file()` check removed this file stayed green, because nothing asked
    whether the store had appeared.
    """
    mod = load_module()
    monkeypatch.setattr(mod, "get_data_root", lambda: tmp_path)
    cfg = {"collections": {"content": ["memory"]},
           "layers": [{"layer": "memory", "glob": "auto-memory/*.md"}]}
    assert not (tmp_path / mod.STORE_REL).exists()

    assert mod._touch_memory_hits([_hit("auto-memory/example-fact.md")], cfg) == 1
    assert _access_count(memory_dir / "example-fact.md") == 1
    assert not (tmp_path / mod.STORE_REL).exists(), (
        "the read path materialized an index store that was never built")
