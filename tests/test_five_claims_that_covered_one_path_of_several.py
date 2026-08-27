#!/usr/bin/env python3
"""Shard 37: five verdicts reached on one path and printed for all of them.

Each one had more than one route into it and inspected exactly one:

  - `_build_store` decided "this pass wrote every row in the store" from the
    LAYER NAMES it walked. A layer whose source raised takes the
    degrade-and-keep path, so its rows survive with the vectors they already
    had while its name stays in the list. `build --force` then cleared the
    mixed-provenance flag and stamped the store as freshly built.
  - the same function's summary counted air-gap denials in the glob branch
    only. The commit walk and the symbol walk refuse INSIDE their iterators and
    reported nothing back, so a pass that withheld commits printed "0 denied".
    The same line called `len(claimed)` "files in scope" while it also held
    commit ids and symbol ranges.
  - `memory_health._VH_POINTER_RE` ended in a greedy `[^·\\n]*`, and brackets
    and parentheses are inside that class. On a line whose pointers are not
    separated by a middle dot, the first match ate the rest of the line and
    every later pointer went unscanned - the exact misattribution the comment
    above the pattern claims was fixed.
  - `test_every_server_supplied_link_passes_a_scheme_gate` matched the literal
    shape `href="${escapeHtml( ... )}"` with at most one nested call, so a link
    written without `escapeHtml` was invisible to a test named "every".
  - `test_each_page_asks_for_messages_older_than_the_last` asserted
    `maxes == sorted(maxes, reverse=True)[:len(maxes)] or maxes[1:] == [16, 6]`.
    The first clause cannot hold once there is more than one page and is
    vacuously true for one; the hardcoded pair was the only live check.

Run: .venv/bin/python -m pytest tests/test_five_claims_that_covered_one_path_of_several.py
"""
from __future__ import annotations

import importlib.util
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.utils import memory_health as mh  # noqa: E402
from scripts.utils.commit_source import iter_commits  # noqa: E402
from scripts.utils.symbol_source import iter_symbols  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "memory_index_s37", ROOT / "scripts" / "memory-index.py")
mi = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mi)


# ============================================================
# Shared fixtures
# ============================================================

VOCAB = ["alpha", "beta", "gamma", "delta"]
STORE_REL = ".idx/index.db"


def fake_embed(texts, *, model, host, batch=32, timeout=120):
    """Deterministic lexical vectors. No embedder is reached from this file."""
    out = []
    for t in texts:
        low = t.lower()
        v = [float(low.count(w)) for w in VOCAB]
        out.append(v if any(v) else [1e-6] * len(VOCAB))
    return out


def write(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def cfg_with_commit_layer(model: str, host: str) -> dict:
    return {
        "model": model,
        "host": host,
        "deny_prefixes": [],
        "deny_segments": [],
        "chunk": {"enabled_layers": [], "max_chars": 700,
                  "overlap": 120, "max_chunks": 40},
        "layers": [
            {"layer": "notes", "glob": "notes/*.md"},
            {"layer": "commits", "source": "git-log", "repo_label": "engine"},
        ],
    }


def fake_commits(*shas):
    def _iter(repo, *, repo_label, include_paths=True, deny_prefixes=(),
              deny_segments=(), stats=None):
        for sha in shas:
            yield {
                "sha": sha, "id": f"commit:{repo_label}:{sha}",
                "path": f"{repo_label}@{sha}", "title": f"alpha {sha}",
                "ntype": "commit", "mtime": 1.0, "body": "beta",
                "embed_text": f"alpha {sha} beta", "changed": [],
            }
    return _iter


def raising_commits(exc):
    def _iter(repo, **kw):
        raise exc
    return _iter


@pytest.fixture
def tree(tmp_path, monkeypatch):
    write(tmp_path / "notes/one.md", "---\ntitle: One\n---\n\nalpha beta.\n")
    write(tmp_path / "notes/two.md", "---\ntitle: Two\n---\n\ngamma.\n")
    monkeypatch.setattr(mi, "embed", fake_embed)
    monkeypatch.setattr(mi, "get_classification", lambda p: "corporate")
    monkeypatch.setattr(mi, "model_digest", lambda **k: None)
    return tmp_path


def build(root, *, host, force=False, layers=("notes", "commits")):
    return mi._build_store(cfg_with_commit_layer("bge-m3", host), root,
                           STORE_REL, set(layers), force)


def meta_of(root) -> dict:
    conn = mi.open_store(root, STORE_REL)
    try:
        return dict(conn.execute("SELECT key, val FROM meta"))
    finally:
        conn.close()


# ============================================================
# 1. the stamp that spoke for a layer this pass never rebuilt
# ============================================================

def test_a_forced_build_over_a_healthy_store_still_stamps_it(tree, monkeypatch):
    """The honest case must keep working, or the fix below is just a block."""
    monkeypatch.setattr(mi, "iter_commits", fake_commits("aaa", "bbb"))
    assert build(tree, host="http://a:1") == 0
    assert build(tree, host="http://b:2", force=True) == 0

    m = meta_of(tree)
    assert m["embed_host"] == "http://b:2", (
        "every row was re-embedded on B, so B is the honest stamp")
    assert mi.MIXED_PROVENANCE_KEY not in m


def test_a_degraded_layer_forbids_the_whole_store_stamp(tree, monkeypatch):
    """The finding. Host A wrote everything; the commit source then breaks and
    a forced rebuild on host B re-embeds the notes and KEEPS the commit rows."""
    monkeypatch.setattr(mi, "iter_commits", fake_commits("aaa", "bbb"))
    build(tree, host="http://a:1")
    monkeypatch.setattr(
        mi, "iter_commits", raising_commits(RuntimeError("git log failed")))
    build(tree, host="http://b:2", force=True)

    m = meta_of(tree)
    assert m["embed_host"] == "http://a:1", (
        "the commit rows still hold A's vectors, so stamping B claims a "
        "rebuild that did not reach them")


def test_a_degraded_layer_leaves_the_mix_recorded_on_disk(tree, monkeypatch):
    monkeypatch.setattr(mi, "iter_commits", fake_commits("aaa", "bbb"))
    build(tree, host="http://a:1")
    monkeypatch.setattr(
        mi, "iter_commits", raising_commits(RuntimeError("git log failed")))
    build(tree, host="http://b:2", force=True)

    flag = meta_of(tree).get(mi.MIXED_PROVENANCE_KEY, "")
    assert "http://b:2" in flag, (
        "the flag is the only evidence on disk that two embedders wrote this "
        "store; clearing it is what made the mix permanent and invisible")


def test_the_kept_rows_are_not_pruned_by_the_degraded_pass(tree, monkeypatch):
    """The degrade path exists to keep the corpus. Prove it still does."""
    monkeypatch.setattr(mi, "iter_commits", fake_commits("aaa", "bbb"))
    build(tree, host="http://a:1")
    monkeypatch.setattr(
        mi, "iter_commits", raising_commits(RuntimeError("git log failed")))
    build(tree, host="http://b:2", force=True)

    conn = mi.open_store(tree, STORE_REL)
    try:
        kept = conn.execute(
            "SELECT COUNT(*) FROM notes WHERE layer='commits'").fetchone()[0]
    finally:
        conn.close()
    assert kept == 2


def test_a_degraded_symbol_layer_blocks_the_stamp_the_same_way(
        tmp_path, monkeypatch):
    """Both degrade handlers must set the flag, not just the one that was read.

    A fix applied to one of two identical branches is the shape this audit
    keeps finding.
    """
    write(tmp_path / "notes/one.md", "---\ntitle: One\n---\n\nalpha.\n")
    monkeypatch.setattr(mi, "embed", fake_embed)
    monkeypatch.setattr(mi, "get_classification", lambda p: "corporate")
    monkeypatch.setattr(mi, "model_digest", lambda **k: None)

    cfg = cfg_with_commit_layer("bge-m3", "http://a:1")
    cfg["layers"] = [
        {"layer": "notes", "glob": "notes/*.md"},
        {"layer": "symbols", "source": "codegraph"},
    ]
    monkeypatch.setattr(mi, "iter_symbols", lambda *a, **k: iter([
        {"id": "s1", "path": "m.py#1-2", "title": "f", "ntype": "symbol",
         "mtime": 1.0, "body": "alpha", "embed_text": "f alpha"},
    ]))
    mi._build_store(cfg, tmp_path, STORE_REL, {"notes", "symbols"}, False)

    def _boom(*a, **k):
        raise ValueError("CodeGraph index not found")

    monkeypatch.setattr(mi, "iter_symbols", _boom)
    cfg["host"] = "http://b:2"
    mi._build_store(cfg, tmp_path, STORE_REL, {"notes", "symbols"}, True)

    assert meta_of(tmp_path)["embed_host"] == "http://a:1"


# ============================================================
# 2. the denial count taken from one of three walks
# ============================================================

def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True,
                          text=True, check=True).stdout


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "r"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@example.test")
    _git(repo, "config", "user.name", "Test")
    return repo


def _commit(repo: Path, rel: str, subject: str) -> str:
    f = repo / rel
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(f"{subject}\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", subject)
    return _git(repo, "rev-parse", "HEAD").strip()


def test_the_commit_walk_reports_every_commit_it_refused(tmp_path):
    repo = _repo(tmp_path)
    _commit(repo, "docs/ok.md", "a public change")
    _commit(repo, "notes/personal/monday.md", "a private note")
    _commit(repo, "notes/personal/tuesday.md", "another private note")

    stats = {}
    kept = list(iter_commits(repo, repo_label="engine", stats=stats))
    assert len(kept) == 1
    assert stats["denied"] == 2, (
        "the refusal happens inside this walk, so nothing outside it can "
        "count what was withheld")


def test_the_commit_walk_reports_nothing_when_it_refused_nothing(tmp_path):
    repo = _repo(tmp_path)
    _commit(repo, "docs/ok.md", "a public change")

    stats = {}
    list(iter_commits(repo, repo_label="engine", stats=stats))
    assert stats.get("denied", 0) == 0


def test_the_commit_walk_still_runs_without_a_stats_dict(tmp_path):
    """The parameter is optional; the air gap is not."""
    repo = _repo(tmp_path)
    _commit(repo, "docs/ok.md", "a public change")
    secret = _commit(repo, "notes/personal/monday.md", "a private note")

    shas = {c["sha"] for c in iter_commits(repo, repo_label="engine")}
    assert secret not in shas


def _graph(tmp_path: Path, nodes) -> Path:
    db = tmp_path / "codegraph.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE nodes (id TEXT, kind TEXT, name TEXT, qualified_name TEXT,"
        " file_path TEXT, language TEXT, start_line INT, end_line INT,"
        " docstring TEXT, signature TEXT)")
    conn.executemany("INSERT INTO nodes VALUES (?,?,?,?,?,?,?,?,?,?)", nodes)
    conn.commit()
    conn.close()
    return db


def test_the_symbol_walk_reports_every_symbol_it_refused(tmp_path):
    write(tmp_path / "m.py", "def f():\n    return 1\n")
    write(tmp_path / "personal" / "s.py", "def g():\n    return 2\n")
    db = _graph(tmp_path, [
        ("n1", "function", "f", "m.f", "m.py", "python", 1, 2, "", "def f()"),
        ("n2", "function", "g", "s.g", "personal/s.py", "python", 1, 2, "",
         "def g()"),
    ])

    stats = {}
    kept = list(iter_symbols(db, tmp_path, stats=stats))
    assert [s["title"] for s in kept] == ["m.f"]
    assert stats["denied"] == 1


def test_the_summary_counts_denials_from_the_commit_walk(tmp_path, monkeypatch,
                                                         capsys):
    """The printed number is the whole point: a pass that withheld commits said
    "0 denied", which reads as "nothing was withheld"."""
    write(tmp_path / "notes/one.md", "---\ntitle: One\n---\n\nalpha.\n")
    monkeypatch.setattr(mi, "embed", fake_embed)
    monkeypatch.setattr(mi, "get_classification", lambda p: "corporate")
    monkeypatch.setattr(mi, "model_digest", lambda **k: None)

    def _iter(repo, *, repo_label, include_paths=True, deny_prefixes=(),
              deny_segments=(), stats=None):
        if stats is not None:
            stats["denied"] = 3
        return iter(())

    monkeypatch.setattr(mi, "iter_commits", _iter)
    build(tmp_path, host="http://a:1")

    assert "3 denied" in capsys.readouterr().out


def test_the_summary_counts_denials_from_the_symbol_walk(tmp_path, monkeypatch,
                                                         capsys):
    """The commit branch and the symbol branch are two copies of one shape, and
    a fix that lands in one of two copies is what this audit keeps finding."""
    write(tmp_path / "notes/one.md", "---\ntitle: One\n---\n\nalpha.\n")
    monkeypatch.setattr(mi, "embed", fake_embed)
    monkeypatch.setattr(mi, "get_classification", lambda p: "corporate")
    monkeypatch.setattr(mi, "model_digest", lambda **k: None)

    def _iter(graph, root, *, deny_prefixes=(), deny_segments=(), stats=None):
        if stats is not None:
            stats["denied"] = 7
        return iter(())

    monkeypatch.setattr(mi, "iter_symbols", _iter)
    cfg = cfg_with_commit_layer("bge-m3", "http://a:1")
    cfg["layers"] = [
        {"layer": "notes", "glob": "notes/*.md"},
        {"layer": "symbols", "source": "codegraph"},
    ]
    mi._build_store(cfg, tmp_path, STORE_REL, {"notes", "symbols"}, False)

    assert "7 denied" in capsys.readouterr().out


def test_the_summary_does_not_call_a_commit_id_a_file(tmp_path, monkeypatch,
                                                      capsys):
    write(tmp_path / "notes/one.md", "---\ntitle: One\n---\n\nalpha.\n")
    monkeypatch.setattr(mi, "embed", fake_embed)
    monkeypatch.setattr(mi, "get_classification", lambda p: "corporate")
    monkeypatch.setattr(mi, "model_digest", lambda **k: None)
    monkeypatch.setattr(mi, "iter_commits", fake_commits("aaa", "bbb"))
    build(tmp_path, host="http://a:1")

    out = capsys.readouterr().out
    assert "records in scope" in out
    assert "files in scope" not in out, (
        "two of the three things counted here are a commit id and a symbol "
        "range, and neither is a file")


# ============================================================
# 3. the pointer regex that ate the rest of its line
# ============================================================

def _flagged(tmp_path, line: str) -> list:
    write(tmp_path / "MEMORY.md", f"# Memory index\n\n{line}\n")
    return mh.scan_volatile_hooks(tmp_path)["flagged"]


def test_two_pointers_on_one_line_are_both_scanned(tmp_path):
    """Separated by a middle dot, which is what the index actually uses."""
    found = _flagged(
        tmp_path,
        "- Money: [mortgage EUR 412,000](a.md) · [rate EUR 90,000](b.md)")
    assert sorted(f["target"] for f in found) == ["a.md", "b.md"]


def test_a_second_pointer_with_no_separator_is_still_seen(tmp_path):
    """The finding. The greedy tail swallowed everything after the first
    pointer, so `finditer` never reached the second."""
    found = _flagged(
        tmp_path, "- Money: [a bank](a.md) and [mortgage EUR 412,000](b.md)")
    assert [f["target"] for f in found] == ["b.md"], (
        "the value sits in the second hook, so the second file is where the "
        "operator must be sent")


def test_an_unseparated_first_pointer_keeps_its_own_value(tmp_path):
    found = _flagged(
        tmp_path, "- Money: [mortgage EUR 412,000](a.md) and [a rate](b.md)")
    assert [f["target"] for f in found] == ["a.md"]


def test_a_value_after_the_last_pointer_belongs_to_it(tmp_path):
    found = _flagged(tmp_path, "- Money: [a bank](a.md) at EUR 412,000")
    assert [f["target"] for f in found] == ["a.md"]


def test_bracketed_text_that_is_not_a_link_does_not_split_a_hook(tmp_path):
    """`[not a link]` has no `(` after it, so the tail must swallow it and keep
    the value attached to the pointer it follows."""
    found = _flagged(
        tmp_path, "- Money: [a bank](a.md) [see also] costing EUR 412,000")
    assert [f["target"] for f in found] == ["a.md"]


def test_a_clean_line_flags_nothing(tmp_path):
    assert _flagged(tmp_path, "- Money: [a bank](a.md) · [a rate](b.md)") == []


def test_a_label_after_the_separator_belongs_to_the_hook_it_introduces(tmp_path):
    """The separator ends a hook, and the words after it are the NEXT hook's
    label. Attributing them to the pointer before would send the operator to
    the wrong file; dropping them would say nothing at all.

    The scan gave the leading label to the first pointer only, so on a grouped
    line every later hook lost the words between the separator and its own
    bracket.
    """
    found = _flagged(
        tmp_path,
        "- Money: [a bank](a.md) · costing EUR 412,000, see [a rate](b.md)")
    assert [f["target"] for f in found] == ["b.md"]


def test_the_separator_still_ends_the_hook_before_it(tmp_path):
    """The mirror of the test above: the value must not stick to `a.md`."""
    found = _flagged(
        tmp_path,
        "- Money: [a bank](a.md) · costing EUR 412,000, see [a rate](b.md)")
    assert "a.md" not in {f["target"] for f in found}


def test_a_middle_hook_on_a_three_pointer_line_keeps_its_own_label(tmp_path):
    found = _flagged(
        tmp_path,
        "- Money: [a bank](a.md) · a rate of EUR 90,000 [the rate](b.md) · "
        "[a note](c.md)")
    assert [f["target"] for f in found] == ["b.md"]


# ============================================================
# 4 and 5. two checks that could not fail
# ============================================================

def test_the_href_scan_sees_a_link_written_without_escapehtml(tmp_path):
    """The old regex required the literal `escapeHtml(` after `href="${`."""
    from tests.bridge import test_the_dashboard_script_says_what_it_does as dash

    found = dash._href_interpolations('x = `<a href="${it.url}">go</a>`;')
    assert found == ["it.url"]


def test_the_href_scan_sees_through_two_levels_of_nesting(tmp_path):
    from tests.bridge import test_the_dashboard_script_says_what_it_does as dash

    src = 'x = `<a href="${escapeHtml(pick(first(it), fallback(it)))}">go</a>`;'
    assert dash._href_interpolations(src) == [
        "escapeHtml(pick(first(it), fallback(it)))"]


def test_the_href_scan_stops_at_the_matching_brace(tmp_path):
    from tests.bridge import test_the_dashboard_script_says_what_it_does as dash

    src = 'x = `<a href="${a}">${b}</a>`; y = `<a href="${c}">z</a>`;'
    assert dash._href_interpolations(src) == ["a", "c"]


def test_an_unclassified_href_site_is_reported(tmp_path):
    """The registry only helps if an unknown site actually fails the check."""
    from tests.bridge import test_the_dashboard_script_says_what_it_does as dash

    src = 'x = `<a href="${it.url}">go</a>`;'
    assert dash._unclassified_hrefs(src) == ["it.url"]


def test_a_classified_href_site_is_not_reported(tmp_path):
    from tests.bridge import test_the_dashboard_script_says_what_it_does as dash

    src = 'x = `<a href="${escapeHtml(link)}">go</a>`;'
    assert dash._unclassified_hrefs(src) == []


def test_a_registry_key_the_file_no_longer_has_is_reported(tmp_path):
    """A registry that outlives the code it describes classifies nothing."""
    from tests.bridge import test_the_dashboard_script_says_what_it_does as dash

    stale = dash._stale_href_gates('x = `<a href="${escapeHtml(link)}">go</a>`;')
    assert "escapeHtml(loc)" in stale
    assert "escapeHtml(link)" not in stale


def test_the_paging_fake_records_a_page_for_every_call():
    """The paging assertion now reads `client.returned`. An empty list there
    would make its loop pass over nothing."""
    from tests import test_a_pid_file_emptied_before_the_lock as paging
    import asyncio

    client = paging._PagingClient(paging._Msg(i) for i in range(1, 26))
    asyncio.run(paging._source(client)._fetch_since(object(), 0, 10, "chat"))
    assert len(client.returned) == len(client.calls) >= 3
    assert all(client.returned[:-1]), "only the last page may come back short"
