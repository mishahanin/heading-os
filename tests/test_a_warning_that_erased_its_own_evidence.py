#!/usr/bin/env python3
"""Shard 10-p1: the memory index, and five promises it did not keep.

The shard's name comes from its worst one. `_build_store` printed a warning
saying, in the operator's own terms, that the rows it did not re-embed keep
their old vectors and the store is now two embedders deep. Two dozen lines
later, on the same code path, it called `record_provenance` unconditionally and
stamped the NEW embedder over the meta row. From the next run onward
`provenance_mismatch` compared that stamp against the same embedder, matched,
and returned None. The mix was real, permanent, and undetectable: the only
evidence on disk had been destroyed by the function that printed the warning.

The other four are smaller and share one shape -- a stated contract that the
code beside it does not honour:

  - `query --json` promised JSON on EVERY exit and printed nothing at all when
    the embed call failed after host resolution had already succeeded;
  - `memory-touch.py` caught `TouchError` alone, so one file that is not valid
    UTF-8 killed the whole batch mid-way with a traceback;
  - `cmd_query` CREATED a store it was only supposed to read, contradicting the
    guard its two sibling read paths already carry;
  - `parse_note` documented "a top-level key wins if present", implemented it
    with `dict.get(k, default)` (which falls back only on ABSENCE), and threw
    away a real nested count whenever the top-level key was written null.

Run: .venv/bin/python -m pytest tests/test_a_warning_that_erased_its_own_evidence.py
"""
from __future__ import annotations

import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_INDEX_SRC = ROOT / "scripts" / "memory-index.py"
_spec = importlib.util.spec_from_file_location("memory_index_10p1", _INDEX_SRC)
mi = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mi)


def load_touch_cli():
    src = ROOT / "scripts" / "memory-touch.py"
    spec = importlib.util.spec_from_file_location("memory_touch_cli_10p1", src)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ============================================================
# Shared fixtures
# ============================================================

VOCAB = ["alpha", "beta", "gamma", "delta"]


def fake_embed(texts, *, model, host, batch=32, timeout=120):
    """Deterministic lexical vectors. No ollama anywhere in this file."""
    out = []
    for t in texts:
        low = t.lower()
        v = [float(low.count(w)) for w in VOCAB]
        out.append(v if any(v) else [1e-6] * len(VOCAB))
    return out


def write(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def build_cfg(model: str, host: str) -> dict:
    """The subset of the config `_build_store` actually reads."""
    return {
        "model": model,
        "host": host,
        "deny_prefixes": [],
        "deny_segments": [],
        "chunk": {"enabled_layers": [], "max_chars": 700,
                  "overlap": 120, "max_chunks": 40},
        "layers": [
            {"layer": "notes", "glob": "notes/*.md"},
            {"layer": "extra", "glob": "extra/*.md"},
        ],
    }


STORE_REL = ".idx/index.db"


@pytest.fixture
def tree(tmp_path, monkeypatch):
    """A two-layer corpus and a module wired to a mock embedder."""
    write(tmp_path / "notes/one.md", "---\ntitle: One\n---\n\nalpha beta.\n")
    write(tmp_path / "notes/two.md", "---\ntitle: Two\n---\n\ngamma.\n")
    write(tmp_path / "extra/three.md", "---\ntitle: Three\n---\n\ndelta.\n")
    monkeypatch.setattr(mi, "embed", fake_embed)
    monkeypatch.setattr(mi, "get_classification", lambda p: "corporate")
    monkeypatch.setattr(mi, "model_digest", lambda **k: None)
    return tmp_path


def build(root, *, model="bge-m3", host="http://a:1", digest=None,
          layers=("notes", "extra"), force=False, monkeypatch=None):
    if monkeypatch is not None:
        monkeypatch.setattr(mi, "model_digest", lambda **k: digest)
    return mi._build_store(build_cfg(model, host), root, STORE_REL,
                           set(layers), force)


def meta_of(root) -> dict:
    conn = mi.open_store(root, STORE_REL)
    try:
        return dict(conn.execute("SELECT key, val FROM meta"))
    finally:
        conn.close()


def drift_now(root, *, model, host, digest=None) -> str | None:
    conn = mi.open_store(root, STORE_REL)
    try:
        return mi.provenance_mismatch(conn, model=model, host=host, digest=digest)
    finally:
        conn.close()


# ============================================================
# Finding 1 -- the warning that erased its own evidence
# ============================================================

def test_a_first_build_stamps_the_embedder_that_wrote_every_row(tree, monkeypatch):
    assert build(tree, host="http://a:1", monkeypatch=monkeypatch) == 0
    m = meta_of(tree)
    assert m["model"] == "bge-m3"
    assert m["embed_host"] == "http://a:1"
    assert mi.MIXED_PROVENANCE_KEY not in m, "a single-embedder store is not mixed"


def test_an_incremental_build_on_a_second_embedder_keeps_the_old_stamp(
        tree, monkeypatch):
    """The core regression. Host A wrote the corpus; host B re-embeds one file.

    The stamp must keep naming A, because A's vectors are still the majority of
    the store. Stamping B was what made the mix invisible.
    """
    build(tree, host="http://a:1", monkeypatch=monkeypatch)
    # One file changes, so exactly one is re-embedded on B.
    p = tree / "notes/one.md"
    write(p, "---\ntitle: One\n---\n\nalpha beta gamma.\n")
    build(tree, host="http://b:2", monkeypatch=monkeypatch)

    m = meta_of(tree)
    assert m["embed_host"] == "http://a:1", (
        "the incremental build stamped its own host over the store it did not "
        "rewrite; that is the erasure this shard exists for")


def test_the_mix_is_recorded_on_disk_and_names_the_embedder_that_was_mixed_in(
        tree, monkeypatch):
    build(tree, host="http://a:1", monkeypatch=monkeypatch)
    write(tree / "notes/one.md", "---\ntitle: One\n---\n\nalpha beta gamma.\n")
    build(tree, host="http://b:2", monkeypatch=monkeypatch)

    flag = meta_of(tree)[mi.MIXED_PROVENANCE_KEY]
    assert "http://b:2" in flag and "bge-m3" in flag


def test_the_mix_stays_reportable_after_the_original_host_comes_back(
        tree, monkeypatch):
    """Step 3 of the audit's repro, and the whole point of the fix.

    Host A returns. Model, host and digest all match the stamp again, so no
    live comparison can see anything wrong -- and host B's vectors are still
    sitting in the store. Only a recorded fact can answer here.
    """
    build(tree, host="http://a:1", monkeypatch=monkeypatch)
    write(tree / "notes/one.md", "---\ntitle: One\n---\n\nalpha beta gamma.\n")
    build(tree, host="http://b:2", monkeypatch=monkeypatch)

    msg = drift_now(tree, model="bge-m3", host="http://a:1")
    assert msg is not None, "the store is mixed and said so was the whole promise"
    assert "MIXED" in msg
    assert "http://b:2" in msg


def test_a_drifted_build_that_embedded_nothing_leaves_the_stamp_alone(
        tree, monkeypatch):
    """No file changed, so no vector was written: the store is still pure A.

    Flagging it would be a false alarm, and re-stamping it to B would be the
    original bug. Neither may happen.
    """
    build(tree, host="http://a:1", monkeypatch=monkeypatch)
    rc = build(tree, host="http://b:2", monkeypatch=monkeypatch)
    assert rc == 0

    m = meta_of(tree)
    assert m["embed_host"] == "http://a:1"
    assert mi.MIXED_PROVENANCE_KEY not in m


def test_a_whole_store_force_rebuild_clears_the_flag_and_restamps(
        tree, monkeypatch):
    """`--force` re-embeds every row, so the store really is one provenance
    again. This is the only thing that may clear the flag."""
    build(tree, host="http://a:1", monkeypatch=monkeypatch)
    write(tree / "notes/one.md", "---\ntitle: One\n---\n\nalpha beta gamma.\n")
    build(tree, host="http://b:2", monkeypatch=monkeypatch)
    assert mi.MIXED_PROVENANCE_KEY in meta_of(tree)

    build(tree, host="http://b:2", force=True, monkeypatch=monkeypatch)
    m = meta_of(tree)
    assert mi.MIXED_PROVENANCE_KEY not in m
    assert m["embed_host"] == "http://b:2"
    assert drift_now(tree, model="bge-m3", host="http://b:2") is None


def test_a_force_rebuild_of_only_some_layers_does_not_claim_the_whole_store(
        tree, monkeypatch):
    """`force` alone is not "every row was rewritten".

    A build restricted to `notes` never walks `extra`, so those rows keep host
    A's vectors. Clearing the flag there would launder a store that is still
    mixed.
    """
    build(tree, host="http://a:1", monkeypatch=monkeypatch)
    write(tree / "notes/one.md", "---\ntitle: One\n---\n\nalpha beta gamma.\n")
    build(tree, host="http://b:2", monkeypatch=monkeypatch)
    assert mi.MIXED_PROVENANCE_KEY in meta_of(tree)

    build(tree, host="http://b:2", layers=("notes",), force=True,
          monkeypatch=monkeypatch)
    assert mi.MIXED_PROVENANCE_KEY in meta_of(tree), (
        "a partial force rebuild cleared a flag it had no standing to clear")


def test_the_operator_is_told_the_store_was_flagged(tree, monkeypatch, capsys):
    build(tree, host="http://a:1", monkeypatch=monkeypatch)
    capsys.readouterr()
    write(tree / "notes/one.md", "---\ntitle: One\n---\n\nalpha beta gamma.\n")
    build(tree, host="http://b:2", monkeypatch=monkeypatch)

    err = capsys.readouterr().err
    assert "mixed provenance" in err
    assert "build --force" in err


def test_the_flag_message_does_not_claim_readers_that_do_not_read_it(
        tree, monkeypatch, capsys):
    """`stats` and `query` never call `provenance_mismatch`. Saying they report
    the flag would be exactly the over-claim `.claude/rules/scope-claims.md`
    forbids, in the fix for an under-reporting bug."""
    build(tree, host="http://a:1", monkeypatch=monkeypatch)
    capsys.readouterr()
    write(tree / "notes/one.md", "---\ntitle: One\n---\n\nalpha beta gamma.\n")
    build(tree, host="http://b:2", monkeypatch=monkeypatch)

    err = capsys.readouterr().err
    assert "do not read this flag" in err

    # Behavioural, not a source grep: run `stats` against the flagged store and
    # confirm the flag really is absent from what it prints. If a later change
    # teaches `stats` to surface it, this fails and the message above gets
    # corrected instead of quietly becoming an under-claim.
    monkeypatch.setattr(mi, "load_config", lambda root, **k: {})
    monkeypatch.setattr(mi, "_store_targets", lambda cfg: [
        {"name": "flagged", "root": tree, "store_rel": STORE_REL,
         "layers": {"notes", "extra"}},
    ])
    assert mi.cmd_stats(types.SimpleNamespace(top_access=0)) == 0
    stats_out = capsys.readouterr().out
    assert "MIXED" not in stats_out
    assert mi.MIXED_PROVENANCE_KEY not in stats_out


def test_the_flag_is_binary_and_keeps_the_first_embedder_it_recorded(tmp_path):
    """It is a flag, not a log. A second mix must not rewrite the first name,
    or the record grows without bound on every build."""
    conn = mi.open_store(tmp_path, STORE_REL)
    mi.record_provenance(conn, model="bge-m3", host="http://a:1")
    mi.record_mixed_provenance(conn, model="bge-m3", host="http://b:2")
    mi.record_mixed_provenance(conn, model="bge-m3", host="http://c:3")
    val = dict(conn.execute("SELECT key, val FROM meta"))[mi.MIXED_PROVENANCE_KEY]
    conn.close()
    assert "http://b:2" in val and "http://c:3" not in val


def test_the_flag_records_the_digest_when_the_host_reported_one(tmp_path):
    conn = mi.open_store(tmp_path, STORE_REL)
    mi.record_provenance(conn, model="bge-m3", host="http://a:1")
    mi.record_mixed_provenance(conn, model="bge-m3", host="http://b:2",
                               digest="abcdef0123456789")
    val = dict(conn.execute("SELECT key, val FROM meta"))[mi.MIXED_PROVENANCE_KEY]
    conn.close()
    assert "abcdef012345" in val


def test_a_mixed_store_reports_the_flag_alongside_a_live_drift(tmp_path):
    """Both facts travel. The flag is history; the model change is now."""
    conn = mi.open_store(tmp_path, STORE_REL)
    mi.record_provenance(conn, model="bge-m3", host="http://a:1")
    mi.record_mixed_provenance(conn, model="bge-m3", host="http://b:2")
    msg = mi.provenance_mismatch(conn, model="nomic-embed-text", host="http://a:1")
    conn.close()
    assert msg and "MIXED" in msg and "nomic-embed-text" in msg


def test_clearing_the_flag_makes_a_matching_store_silent_again(tmp_path):
    conn = mi.open_store(tmp_path, STORE_REL)
    mi.record_provenance(conn, model="bge-m3", host="http://a:1")
    mi.record_mixed_provenance(conn, model="bge-m3", host="http://b:2")
    assert mi.provenance_mismatch(conn, model="bge-m3", host="http://a:1")
    mi.clear_mixed_provenance(conn)
    assert mi.provenance_mismatch(conn, model="bge-m3", host="http://a:1") is None
    conn.close()


def test_a_store_that_predates_provenance_is_still_judged_silently(tmp_path):
    """Unchanged behaviour, pinned: the new key must not turn an unstamped
    store into a mismatch."""
    conn = mi.open_store(tmp_path, STORE_REL)
    assert mi.provenance_mismatch(conn, model="bge-m3", host="http://a:1") is None
    conn.close()


def test_an_unchanged_embedder_still_refreshes_the_stamp(tree, monkeypatch):
    """No drift means this build speaks for the store, whatever it walked. A
    digest that only became readable now must still land."""
    build(tree, host="http://a:1", digest=None, monkeypatch=monkeypatch)
    assert "model_digest" not in meta_of(tree)
    write(tree / "notes/one.md", "---\ntitle: One\n---\n\nalpha beta gamma.\n")
    # The digest below is a made-up hex string standing in for an embedder
    # fingerprint. It is not a credential, and the entropy detector cannot tell
    # the difference, so each line carries the allowlist marker.
    build(tree, host="http://a:1",
          digest="feedface00112233",  # pragma: allowlist secret
          monkeypatch=monkeypatch)
    assert meta_of(tree)["model_digest"] == "feedface00112233"  # pragma: allowlist secret


# ============================================================
# Finding 2 -- `--json` printed nothing when the embed call failed
# ============================================================

def _query_args(**over):
    base = {"layer": None, "collection": "content", "text": "alpha",
            "json": False, "threshold": None, "top_k": 5, "touch": False,
            "min_score": None, "paths_only": False, "verbose": False,
            "no_rerank": False}
    base.update(over)
    return types.SimpleNamespace(**base)


def _query_cfg(**over):
    base = {"model": "bge-m3", "host": "stub", "host_preferred": "stub",
            "host_error": None, "collections": {}, "threshold": 0.55,
            "near_miss_margin": 0.12, "top_k": 5, "layers": [],
            "rank_weights": {"semantic": 0.60, "recency": 0.20,
                             "importance": 0.20},
            "recency_decay": "exponential", "recency_halflife_days": 180}
    base.update(over)
    return base


@pytest.fixture
def hermetic_query(tmp_path, monkeypatch):
    """cmd_query with no config file, no real store and no ops log on disk."""
    monkeypatch.setenv("HEADING_OS_DATA", str(tmp_path))
    monkeypatch.setattr(mi, "load_config", lambda root, **k: _query_cfg())
    from scripts.utils import memory_ops_log
    monkeypatch.setattr(memory_ops_log, "log_recall", lambda **k: None)
    return tmp_path


def _raise_embed(*a, **k):
    raise mi.EmbeddingError("model 'bge-m3' not found on the pinned host")


def test_an_embed_failure_in_json_mode_still_emits_json(
        hermetic_query, monkeypatch, capsys):
    monkeypatch.setattr(mi, "_store_targets", lambda cfg: [])
    monkeypatch.setattr(mi, "embed", _raise_embed)
    rc = mi.cmd_query(_query_args(json=True))
    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["hits"] == []
    assert payload["gap"] is True
    assert "not found" in payload["embed_unavailable"]["reason"]


def test_the_json_refusal_carries_the_embedder_s_own_message(
        hermetic_query, monkeypatch, capsys):
    """A generic "unavailable" would send the operator looking in the wrong
    place. The reason the embedder gave is the diagnostic."""
    monkeypatch.setattr(mi, "_store_targets", lambda cfg: [])

    def boom(*a, **k):
        raise mi.EmbeddingError("connection refused to http://172.30.48.1:11436")
    monkeypatch.setattr(mi, "embed", boom)
    mi.cmd_query(_query_args(json=True))
    payload = json.loads(capsys.readouterr().out)
    assert "172.30.48.1:11436" in payload["embed_unavailable"]["reason"]


def test_an_embed_failure_without_json_still_speaks_prose_on_stderr(
        hermetic_query, monkeypatch, capsys):
    monkeypatch.setattr(mi, "_store_targets", lambda cfg: [])
    monkeypatch.setattr(mi, "embed", _raise_embed)
    assert mi.cmd_query(_query_args(json=False)) == 1
    cap = capsys.readouterr()
    assert "Embedding failed" in cap.err
    assert cap.out == "", "prose mode must not print a JSON payload"


def test_the_json_refusal_is_the_same_shape_the_host_down_path_uses(
        hermetic_query, monkeypatch, capsys):
    """Both refusals reach the same consumer, so they must be one shape: a
    naive reader sees an ordinary gap, an aware one sees the cause."""
    monkeypatch.setattr(mi, "_store_targets", lambda cfg: [])
    monkeypatch.setattr(mi, "embed", _raise_embed)
    mi.cmd_query(_query_args(json=True))
    got = json.loads(capsys.readouterr().out)
    assert set(got) == set(mi.embed_unavailable_payload("x"))


# ============================================================
# Finding 4 -- a read path that created what it read
# ============================================================

def test_a_query_against_a_never_built_store_does_not_create_it(
        hermetic_query, monkeypatch, capsys):
    root = hermetic_query
    monkeypatch.setattr(mi, "_store_targets", lambda cfg: [
        {"name": "code", "root": root, "store_rel": ".memory-index-code/index.db",
         "layers": {"skill"}},
    ])
    monkeypatch.setattr(mi, "embed", lambda *a, **k: [[0.1, 0.2, 0.3, 0.4]])

    rc = mi.cmd_query(_query_args(json=True))
    assert rc == 0
    assert not (root / ".memory-index-code").exists(), (
        "a read command materialised a store, so `stats` can no longer tell "
        "never-built from built-and-empty")
    assert json.loads(capsys.readouterr().out)["empty_index"] is True


def test_an_absent_store_does_not_hide_the_stores_that_do_exist(
        tree, hermetic_query, monkeypatch, capsys):
    """`continue`, not `return`. A missing second store must cost the first one
    nothing."""
    monkeypatch.setattr(mi, "model_digest", lambda **k: None)
    build(tree, host="http://a:1", monkeypatch=monkeypatch)
    monkeypatch.setattr(mi, "_store_targets", lambda cfg: [
        {"name": "gone", "root": hermetic_query,
         "store_rel": ".nowhere/index.db", "layers": {"skill"}},
        {"name": "real", "root": tree, "store_rel": STORE_REL,
         "layers": {"notes", "extra"}},
    ])
    monkeypatch.setattr(mi, "embed", fake_embed)
    capsys.readouterr()          # drop the build's progress lines

    rc = mi.cmd_query(_query_args(json=True, text="alpha beta", threshold=0.1))
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["hits"], "the built store answered nothing after a missing sibling"
    assert not (hermetic_query / ".nowhere").exists()


def test_the_existing_guard_on_the_sibling_read_paths_is_still_there(tmp_path):
    """`_mirror_access_counts` is where the convention was already written
    down. If it ever loses the guard, the fix above stands alone and the
    convention is gone."""
    cfg = {"layers": [{"layer": "memory"}]}
    conn_before = list(tmp_path.iterdir())
    mi._mirror_access_counts(
        {**cfg, "collections": {}},
        {"auto-memory/x.md": 3}, "2026-08-25",
    )
    assert list(tmp_path.iterdir()) == conn_before


# ============================================================
# Finding 5 -- "wins if present" implemented as "wins if absent"
# ============================================================

def _fm(body: str) -> dict:
    return mi.parse_note(body)


def test_a_top_level_count_wins_over_the_nested_one():
    info = _fm("---\naccess_count: 9\nmetadata:\n  access_count: 5\n---\n\nbody\n")
    assert info["access_count"] == 9


def test_a_null_top_level_count_defers_to_the_nested_one():
    """The bug: `access_count:` with no value is YAML null, and
    `dict.get(k, default)` only falls back when the key is ABSENT. The real
    count was thrown away and the note ranked as never cited."""
    info = _fm("---\naccess_count:\nmetadata:\n  access_count: 5\n---\n\nbody\n")
    assert info["access_count"] == 5


def test_an_absent_top_level_count_still_reads_the_nested_one():
    info = _fm("---\nmetadata:\n  access_count: 5\n---\n\nbody\n")
    assert info["access_count"] == 5


def test_an_explicit_top_level_zero_is_a_value_and_wins():
    """0 is a real answer -- "surfaced, never cited" -- not a missing one. A
    plain `or` would have quietly promoted the nested 5 here."""
    info = _fm("---\naccess_count: 0\nmetadata:\n  access_count: 5\n---\n\nbody\n")
    assert info["access_count"] == 0


def test_neither_level_present_reads_zero():
    info = _fm("---\ntitle: x\n---\n\nbody\n")
    assert info["access_count"] == 0


def test_a_non_numeric_count_reads_zero_instead_of_raising():
    info = _fm("---\naccess_count: soon\n---\n\nbody\n")
    assert info["access_count"] == 0


def test_last_accessed_keeps_falling_back_through_an_empty_string():
    """The sibling line, pinned as-is. For a string, "" and null both mean "no
    value", so `or` is correct there and the two lines now agree in meaning."""
    info = _fm("---\nlast_accessed:\nmetadata:\n  last_accessed: '2026-08-01'\n"
               "---\n\nbody\n")
    assert info["last_accessed"] == "2026-08-01"


# ============================================================
# Finding 3 -- one bad file ended the whole batch
# ============================================================

MEMO = "---\nname: {n}\nmetadata:\n  type: user\n---\n\nbody of {n}\n"


@pytest.fixture
def touch_cli(tmp_path, monkeypatch):
    mod = load_touch_cli()
    mem = tmp_path / "auto-memory"
    mem.mkdir(parents=True)
    monkeypatch.setattr(mod, "get_auto_memory_dir", lambda: mem)
    return mod, mem


def _run(mod, monkeypatch, *paths) -> int:
    monkeypatch.setattr(sys, "argv", ["memory-touch.py", *paths])
    return mod.main()


def test_an_undecodable_file_is_refused_and_the_batch_continues(
        touch_cli, monkeypatch, capsys):
    """The reported failure. `read_text` raises UnicodeDecodeError, which is a
    ValueError and not a TouchError, so it escaped `main` as a traceback and
    every later path went untouched while earlier ones were already written."""
    mod, mem = touch_cli
    (mem / "good.md").write_text(MEMO.format(n="good"), encoding="utf-8")
    (mem / "broken.md").write_bytes(b"---\nname: broken\n---\n\n\xff\xfe body\n")
    (mem / "later.md").write_text(MEMO.format(n="later"), encoding="utf-8")

    rc = _run(mod, monkeypatch, "good.md", "broken.md", "later.md")

    assert rc == 1
    out = capsys.readouterr()
    assert "access_count=1" in out.out
    assert out.out.count("touched") == 2, "the batch stopped at the bad file"
    assert "broken.md" in out.err
    assert "access_count: 1" in (mem / "later.md").read_text(encoding="utf-8")


def test_the_refusal_names_the_path_the_caller_gave(touch_cli, monkeypatch, capsys):
    """A decode error's own text says nothing about which file it came from."""
    mod, mem = touch_cli
    (mem / "broken.md").write_bytes(b"---\nname: b\n---\n\n\xff\xfe\n")
    _run(mod, monkeypatch, "broken.md")
    assert "broken.md" in capsys.readouterr().err


def test_an_unreadable_file_also_refuses_instead_of_aborting(
        touch_cli, monkeypatch, capsys):
    """PermissionError is an OSError, the other type the narrow catch missed."""
    mod, mem = touch_cli
    (mem / "locked.md").write_text(MEMO.format(n="locked"), encoding="utf-8")
    (mem / "after.md").write_text(MEMO.format(n="after"), encoding="utf-8")
    real = Path.read_text

    def guarded(self, *a, **k):
        if self.name == "locked.md":
            raise PermissionError(13, "Permission denied")
        return real(self, *a, **k)
    monkeypatch.setattr(Path, "read_text", guarded)

    rc = _run(mod, monkeypatch, "locked.md", "after.md")
    assert rc == 1
    assert "touched" in capsys.readouterr().out


def test_a_path_outside_auto_memory_is_still_refused_by_name(
        touch_cli, monkeypatch, capsys):
    """Unchanged behaviour, pinned: widening the catch must not turn the
    containment refusal into something vaguer."""
    mod, mem = touch_cli
    rc = _run(mod, monkeypatch, "../escape.md")
    assert rc == 1
    assert "outside auto-memory directory" in capsys.readouterr().err


def test_every_good_path_is_still_touched_when_nothing_goes_wrong(
        touch_cli, monkeypatch, capsys):
    mod, mem = touch_cli
    for n in ("a", "b", "c"):
        (mem / f"{n}.md").write_text(MEMO.format(n=n), encoding="utf-8")
    assert _run(mod, monkeypatch, "a.md", "b.md", "c.md") == 0
    assert capsys.readouterr().out.count("touched") == 3


def test_a_refusal_is_never_swallowed_silently(touch_cli, monkeypatch, capsys):
    """The wide catch is only acceptable because nothing is hidden: every
    failure prints, and the exit code carries it out of the process."""
    mod, mem = touch_cli
    (mem / "broken.md").write_bytes(b"---\nname: b\n---\n\n\xff\xfe\n")
    rc = _run(mod, monkeypatch, "broken.md")
    assert rc == 1
    assert capsys.readouterr().err.strip() != ""
