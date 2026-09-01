"""`memory-index.py stats --top-access N` -- the reinforcement-loop diagnostic.

The design commits to a check 60 days after reinforcement ships: list the most
accessed memories, and if that set is frozen month over month, retrieval is
reinforcing what retrieval already surfaces and REINFORCE_K must come down.
Nothing listed that check before this flag; `stats` printed per-layer counts.

Run: .venv/bin/python -m pytest tests/test_memory_index_top_access.py
"""
from __future__ import annotations

import importlib.util
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "memory-index.py"


def load_module():
    spec = importlib.util.spec_from_file_location("memory_index_top_mod", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def store(tmp_path: Path):
    """A built store: four memories with different counts, plus one non-memory.

    `legacy.md` carries `last_accessed = NULL`, which is not a contrived value:
    `open_store`'s Gap #2 migration adds the column with no default, so every
    row written by a pre-reinforcement build reads NULL until the next
    `build --force`. Nothing else in this file produces one -- `upsert_note`
    defaults to `""` -- so the `COALESCE` in `top_access_rows` was never
    reached. MEASURED 2026-09-01 by dropping the `COALESCE`: this file stayed
    green while the query started returning `None` for a never-surfaced memory.
    """
    mod = load_module()
    conn = mod.open_store(tmp_path, mod.STORE_REL)
    rows = [
        ("auto-memory/busy.md", "memory", 42, "2026-08-07"),
        ("auto-memory/middling.md", "memory", 7, "2026-06-01"),
        ("auto-memory/legacy.md", "memory", 3, None),
        ("auto-memory/quiet.md", "memory", 0, ""),
        ("knowledge/odin-brain/loud.md", "odin", 99, "2026-08-07"),
    ]
    for path, layer, count, last in rows:
        mod.upsert_note(conn, id_=path, path=path, title=path, layer=layer,
                        ntype="", mtime=1.0, body="b", vec=[0.1, 0.2],
                        access_count=count, last_accessed=last)
    conn.commit()
    conn.close()
    return mod, tmp_path


def test_ranks_memory_entries_by_access_count(store):
    mod, root = store
    conn = mod.open_store(root, mod.STORE_REL)
    rows = mod.top_access_rows(conn, 20)
    conn.close()

    assert [r[0] for r in rows] == [
        "auto-memory/busy.md", "auto-memory/middling.md",
        "auto-memory/legacy.md", "auto-memory/quiet.md",
    ], "a non-memory layer must not appear, and the order is count-descending"
    assert rows[0][1] == 42
    assert rows[0][2] == "2026-08-07"
    assert rows[3][2] == "", "never surfaced reads as empty, not as a missing row"


def test_a_null_last_accessed_reads_as_empty_not_as_none(store):
    """A row migrated in from a pre-reinforcement build carries NULL here.

    The caller formats the value (`last or 'never'`), so a None would print the
    same word -- but every other consumer of this tuple gets a type that the
    docstring's "(path, access_count, last_accessed)" does not admit, and a
    `sorted()` or a `max()` over the column raises on the mix. The COALESCE is
    what keeps the column a string; this is the row that reaches it.
    """
    mod, root = store
    conn = mod.open_store(root, mod.STORE_REL)
    rows = mod.top_access_rows(conn, 20)
    conn.close()
    by_path = {r[0]: r for r in rows}
    assert by_path["auto-memory/legacy.md"] == ("auto-memory/legacy.md", 3, "")
    assert all(isinstance(r[2], str) for r in rows), rows


def test_a_never_surfaced_memory_is_listed_not_hidden(store):
    """A zero count is a ranking position, never a removal candidate. It has to
    stay visible, or the diagnostic quietly becomes a shortlist."""
    mod, root = store
    conn = mod.open_store(root, mod.STORE_REL)
    rows = mod.top_access_rows(conn, 20)
    conn.close()
    assert ("auto-memory/quiet.md", 0, "") in rows


def test_the_limit_is_honoured(store):
    mod, root = store
    conn = mod.open_store(root, mod.STORE_REL)
    rows = mod.top_access_rows(conn, 2)
    conn.close()
    assert len(rows) == 2


def test_stats_prints_the_block_only_when_asked(store, monkeypatch, capsys):
    mod, root = store
    monkeypatch.setattr(mod, "get_data_root", lambda: root)
    monkeypatch.setattr(mod, "load_config", lambda _root: {
        "collections": {"content": ["memory", "odin"]},
        "layers": [{"layer": "memory", "glob": "auto-memory/*.md"}],
    })

    assert mod.cmd_stats(types.SimpleNamespace(top_access=0)) == 0
    assert "access_count" not in capsys.readouterr().out

    assert mod.cmd_stats(types.SimpleNamespace(top_access=20)) == 0
    out = capsys.readouterr().out
    assert "top 20 memories by access_count" in out
    assert "busy.md" in out
    assert "quiet.md" in out
    assert "loud.md" not in out
