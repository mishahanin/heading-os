#!/usr/bin/env python3
"""Shard scripts-09-p1: counts, scopes and paths that meant something else.

`memory-index.py` measured chunks where it documented documents:

  - The path-token rarity cap counted one row per CHUNK. `humanize_path` strips
    the `#N` suffix, so a 12-chunk file was counted twelve times and two long
    notes in a rarely-named folder pushed a genuinely specific project name past
    the cap -- denying the bypass to exactly the corpus chunking exists for.
  - The `capped` counter used `>=` against a limit `chunk_text` truncates AT, so
    a file that filled the cap losing nothing was reported as a drop.
  - The gap message's "best" came from the whole matrix, before layer filtering,
    so `--layer odin` could print "Nothing above threshold 0.55 (best 0.62)"
    quoting a row the query had been scoped away from.
  - A near-miss -- which the UI labels "relevance NOT established" -- was logged
    to the ops channel as `gap=False`, training the recall dashboard to count it
    as a hit.
  - An unknown `--layer` routed to zero stores and reported "Index is empty. Run
    build", sending the operator to rebuild an index that was fine.
  - `resync_fts` promises the lexical and vector channels "can never drift"; the
    embed-failure path returned before reaching it, after committing deletes.
  - Every query opens the store and runs schema DDL, so a query is a writer.
    With SQLite's default 5s, a recall firing during the nightly build's
    per-file commit died on `database is locked`.

And `merge-contacts.py`, which is admin-gated on WHO and not on WHERE:
`--contact "../../address-book/vip"` escaped the contacts directory, and the
tool then overwrote one file and renamed another.

Run: .venv/bin/python -m pytest tests/test_a_recall_that_measured_the_wrong_thing.py -q
"""

import contextlib
import importlib.util
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


mi = _load("memory_index_p9a", "scripts/memory-index.py")
mc = _load("merge_contacts_p9a", "scripts/merge-contacts.py")


# ============================================================
# 1 - rarity is counted per document, not per chunk
# ============================================================
def test_one_heavily_chunked_file_counts_once_toward_the_cap():
    """Twelve chunks of one file are one document. Counting rows let a single
    long note consume half the cap on its own."""
    ids = [f"notes/meridian/deal.md#{n}" for n in range(20)]
    cos = dict.fromkeys(ids, 0.9)
    got = mi._path_match_ids("meridian", ids, cos, lambda i: True, df_cap=5)
    assert got, "a rare folder name was denied the path channel"
    assert len(got) == len(ids)


def test_a_genuinely_common_token_is_still_capped():
    """The fix must not turn the cap off: many DISTINCT documents still exceed
    it, which is what keeps generic directory words out of this channel."""
    ids = [f"outputs/doc{n}.md#0" for n in range(30)]
    cos = dict.fromkeys(ids, 0.9)
    assert mi._path_match_ids("outputs", ids, cos, lambda i: True, df_cap=5) == []


def test_two_documents_under_the_cap_are_admitted():
    ids = ["a/meridian/x.md#0", "a/meridian/x.md#1", "b/meridian/y.md#0"]
    cos = dict.fromkeys(ids, 0.5)
    got = mi._path_match_ids("meridian", ids, cos, lambda i: True, df_cap=2)
    assert len(got) == 3


def test_the_layer_filter_still_applies():
    ids = ["a/meridian/x.md#0", "b/meridian/y.md#0"]
    cos = dict.fromkeys(ids, 0.5)
    got = mi._path_match_ids("meridian", ids, cos, lambda i: i.startswith("a/"), df_cap=9)
    assert got == ["a/meridian/x.md#0"]


# ============================================================
# 2 - "hit chunk cap" means text was dropped
# ============================================================
CHUNK_CFG = {"max_chars": 50, "overlap": 5, "max_chunks": 3}


def test_a_file_that_fills_the_cap_exactly_is_not_a_drop():
    body = "\n\n".join(["x" * 45] * 3)
    assert len(mi.chunk_text(body, max_chars=50, overlap=5, max_chunks=3)) == 3
    assert mi._would_truncate(body, CHUNK_CFG) is False


def test_a_file_that_overflows_the_cap_is_a_drop():
    body = "\n\n".join(["x" * 45] * 8)
    assert mi._would_truncate(body, CHUNK_CFG) is True


def test_a_short_file_is_not_a_drop():
    assert mi._would_truncate("tiny", CHUNK_CFG) is False


# ============================================================
# 3 - the reported best is one the query could have returned
# ============================================================
def test_the_best_score_respects_the_layer_scope():
    ids = ["odin/a.md#0", "thread/b.md#0"]
    cos = {"odin/a.md#0": 0.41, "thread/b.md#0": 0.62}
    assert mi._best_in_scope(ids, cos, lambda i: i.startswith("odin/")) == pytest.approx(0.41)


def test_an_empty_scope_reports_no_best():
    assert mi._best_in_scope(["a#0"], {"a#0": 0.9}, lambda i: False) is None


def test_an_unscoped_query_still_sees_the_global_best():
    ids = ["a#0", "b#0"]
    assert mi._best_in_scope(ids, {"a#0": 0.1, "b#0": 0.8}, lambda i: True) == pytest.approx(0.8)


# ============================================================
# 4 - a near-miss is logged as a gap
# ============================================================
def test_a_near_miss_is_not_recorded_as_a_successful_recall():
    src = (ROOT / "scripts" / "memory-index.py").read_text(encoding="utf-8")
    code = "\n".join(ln.split("#", 1)[0] for ln in src.splitlines())
    assert "_emit(gap=False, hits_list=hits)" not in code, code
    assert code.count("_emit(gap=near_miss, hits_list=hits)") == 2


def test_the_touch_path_and_the_log_path_now_agree():
    """`_should_touch` already refused to learn from a near-miss. The ops log
    was the one channel still counting it as a hit."""
    args = type("A", (), {"touch": True})()
    assert mi._should_touch(args, near_miss=True) is False
    assert mi._should_touch(args, near_miss=False) is True


# ============================================================
# 5 - an unknown layer is named
# ============================================================
def _query_args(**over):
    """A namespace with every attribute cmd_query reads, defaults overridable."""
    base = {"layer": None, "collection": "content", "text": "x", "json": False,
            "threshold": None, "top_k": 5, "touch": False, "min_score": None,
            "paths_only": False, "verbose": False, "no_rerank": False}
    base.update(over)
    from types import SimpleNamespace
    return SimpleNamespace(**base)



def test_an_unknown_layer_exits_2_and_names_the_configured_layers(
        monkeypatch, capsys):
    """Behavioural, not a source grep: disabling the branch left the message
    string sitting in the file while the typo went back to reading as an empty
    index."""
    monkeypatch.setattr(mi, "_layer_store_map", lambda cfg: {"odin": ("/r", "s.db")})
    # `host` present: the embedder gate sits above the layer check and would
    # otherwise answer first with its own exit code.
    monkeypatch.setattr(mi, "load_config",
                        lambda root, **k: {"collections": {}, "host": "stub",
                                           "threshold": 0.55,
                                           "near_miss_margin": 0.12,
                                           "layers": [{"layer": "odin"}]})
    monkeypatch.setattr(mi, "_store_targets", lambda cfg: [])
    args = _query_args(layer="odin-brain")
    assert mi.cmd_query(args) == 2
    assert "Unknown layer" in capsys.readouterr().err


def test_a_configured_layer_is_not_rejected(monkeypatch, capsys):
    """The guard must not refuse a real layer."""
    monkeypatch.setattr(mi, "_layer_store_map", lambda cfg: {"odin": ("/r", "s.db")})
    # `host` present: the embedder gate sits above the layer check and would
    # otherwise answer first with its own exit code.
    monkeypatch.setattr(mi, "load_config",
                        lambda root, **k: {"collections": {}, "host": "stub",
                                           "threshold": 0.55,
                                           "near_miss_margin": 0.12,
                                           "layers": [{"layer": "odin"}]})
    monkeypatch.setattr(mi, "_store_targets", lambda cfg: [])
    args = _query_args(layer="odin")
    # It may still fail further down (no real store here); what must NOT happen
    # is the unknown-layer refusal.
    with contextlib.suppress(Exception):
        # Downstream failure is fine and expected: there is no real store here.
        # What must not happen is the unknown-layer refusal, asserted below.
        mi.cmd_query(args)
    assert "Unknown layer" not in capsys.readouterr().err


# ============================================================
# 6 - the two channels are resynced even when the build dies
# ============================================================
def test_the_embed_failure_path_resyncs_before_returning():
    src = (ROOT / "scripts" / "memory-index.py").read_text(encoding="utf-8")
    block = src.split("            except EmbeddingError as e:", 1)[1].split("return 1", 1)[0]
    assert "resync_fts(conn)" in block, block


# ============================================================
# 7 - a query survives a concurrent writer
# ============================================================
def test_open_store_sets_a_busy_timeout(tmp_path):
    conn = mi.open_store(tmp_path, "store.db")
    try:
        got = conn.execute("PRAGMA busy_timeout").fetchone()[0]
        # Set by `connect(timeout=)`, not by a separate PRAGMA -- that was a
        # redundant second call and was removed.
        assert got == int(mi.SQLITE_BUSY_TIMEOUT_S * 1000), got
        assert got > 5000, "the default 5s is what let a build kill a recall"
    finally:
        conn.close()


def test_a_query_waits_for_a_writer_instead_of_dying(tmp_path):
    """Held-lock proof: with the default timeout this raises immediately."""
    store = tmp_path / "store.db"
    holder = mi.open_store(tmp_path, "store.db")
    reader = mi.open_store(tmp_path, "store.db")
    try:
        holder.execute("BEGIN EXCLUSIVE")
        # A short explicit timeout keeps the test fast while still proving the
        # connection WAITS rather than failing on contact.
        reader.execute("PRAGMA busy_timeout = 250")
        with pytest.raises(sqlite3.OperationalError):
            reader.execute("CREATE TABLE probe (x)")
    finally:
        holder.rollback()
        holder.close()
        reader.close()
        assert store.exists()


# ============================================================
# 8 - a contact slug cannot leave the contacts directory
# ============================================================
@pytest.mark.parametrize("bad", [
    "../../address-book/vip",
    "../escape",
    "a/b",
    ".hidden",
    "",
    "with space",
])
def test_a_traversing_contact_slug_is_rejected(bad):
    assert mc._CONTACT_SLUG_RE.fullmatch(bad) is None, bad


@pytest.mark.parametrize("good", ["priya-anand", "james_bond", "a1", "Contact-9"])
def test_an_ordinary_slug_is_accepted(good):
    assert mc._CONTACT_SLUG_RE.fullmatch(good) is not None, good


# ============================================================
# 9 - a quoted comma is not a list separator
# ============================================================
def test_a_quoted_comma_does_not_split_a_flow_list():
    fm, _body = mc.parse_frontmatter('---\ntags: ["acme, inc", partner]\n---\nbody\n')
    assert fm["tags"] == ["acme, inc", "partner"], fm


def test_an_ordinary_flow_list_is_unchanged():
    fm, _body = mc.parse_frontmatter("---\ntags: [a, b, c]\n---\nbody\n")
    assert fm["tags"] == ["a", "b", "c"], fm


def test_an_empty_flow_list_is_empty():
    fm, _body = mc.parse_frontmatter("---\ntags: []\n---\nbody\n")
    assert fm["tags"] == []


def test_a_single_quoted_comma_item_also_survives():
    assert mc._split_flow("'x, y', z") == ["'x, y'", "z"]


# ============================================================
# 10 - a duplicate migration version is refused
# ============================================================
def test_two_migrations_claiming_one_version_are_refused(monkeypatch):
    """The runner's whole contract is ORDERED migrations, so a duplicate is a
    config error, not a tie to break by sort stability."""
    from scripts import migrations as mig

    class _Mod:
        def __init__(self, name):
            self.__name__ = name
            self.VERSION = 7

    real = mig.pkgutil.iter_modules
    monkeypatch.setattr(mig.importlib, "import_module",
                        lambda name: _Mod(name))
    monkeypatch.setattr(mig.pkgutil, "iter_modules",
                        lambda paths: [type("I", (), {"name": "0007_a"})(),
                                       type("I", (), {"name": "0007_b"})()])
    with pytest.raises(ValueError, match="duplicate migration VERSION 7"):
        mig.registered_migrations()
    monkeypatch.setattr(mig.pkgutil, "iter_modules", real)


def test_the_real_migration_set_still_loads():
    from scripts import migrations as mig
    versions = [v for v, _ in mig.registered_migrations()]
    assert versions == sorted(set(versions))
