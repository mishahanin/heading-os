#!/usr/bin/env python3
"""Commit layers inside `memory-index.py build`: seam, prune and incrementality.

`iter_commits` is contracted in `tests/test_commit_source.py`. This file covers
what only appears once a commit layer is built through the real `_build_store`
loop alongside the glob layers.

The property that carries the risk is the PRUNE. `_build_store` deletes every
stored path not claimed in the pass. Commit rows are claimed by a branch that
does not walk the filesystem, so if that branch is ever skipped, the prune wipes
the whole commit corpus silently and the next query answers from nothing. The
build reports its counts, so a silent wipe would show as "0 notes" only to
someone reading closely.

Embedding is stubbed. These tests are about bookkeeping -- what is claimed,
pruned, skipped and routed. Running the real embedder would make them slow, need
Ollama up, and test nothing extra.
"""
import importlib.util
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_SRC = Path(__file__).resolve().parent.parent / "scripts" / "memory-index.py"
_spec = importlib.util.spec_from_file_location("memory_index", _SRC)
mi = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mi)


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=True
    ).stdout


def _repo(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "t@example.com")
    _git(root, "config", "user.name", "Test")
    return root


def _commit(repo: Path, rel: str, subject: str) -> str:
    f = repo / rel
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(subject + "\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", subject)
    return _git(repo, "rev-parse", "HEAD").strip()


def _cfg(**over):
    cfg = {
        "model": "stub", "host": "stub", "threshold": 0.55, "near_miss_margin": 0.12,
        "top_k": 8, "deny_prefixes": [], "deny_segments": [],
        "chunk": {"enabled_layers": [], "max_chars": 700, "overlap": 120, "max_chunks": 12},
        "layers": [
            {"layer": "notes", "glob": "docs/*.md"},
            {"layer": "commit-engine", "source": "git-log", "repo_label": "engine"},
        ],
        "collections": {"code": ["notes", "commit-engine"]},
    }
    cfg.update(over)
    return cfg


def _stub_embed(monkeypatch):
    """One deterministic vector per text. Dimension only has to be consistent."""
    monkeypatch.setattr(mi, "embed", lambda texts, model=None, host=None:
                        [[float(len(t) % 7) + 0.1] * 8 for t in texts])


def _rows(root: Path, store_rel: str):
    conn = mi.open_store(root, store_rel)
    out = conn.execute("SELECT id, path, layer, title FROM notes").fetchall()
    conn.close()
    return out


def test_commit_rows_land_in_the_store_beside_file_rows(tmp_path, monkeypatch):
    _stub_embed(monkeypatch)
    root = _repo(tmp_path / "eng")
    (root / "docs").mkdir()
    (root / "docs" / "a.md").write_text("# A\n\nbody\n", encoding="utf-8")
    _commit(root, "docs/a.md", "feat: the first change")

    mi._build_store(_cfg(), root, ".idx/index.db", {"notes", "commit-engine"}, False)
    layers = {r[2] for r in _rows(root, ".idx/index.db")}
    assert layers == {"notes", "commit-engine"}


def test_a_second_build_prunes_nothing_and_re_embeds_nothing(tmp_path, monkeypatch, capsys):
    """The prune wipes anything unclaimed. Commit rows must be claimed."""
    _stub_embed(monkeypatch)
    root = _repo(tmp_path / "eng")
    (root / "docs").mkdir()
    (root / "docs" / "a.md").write_text("# A\n\nbody\n", encoding="utf-8")
    _commit(root, "docs/a.md", "feat: the first change")

    mi._build_store(_cfg(), root, ".idx/index.db", {"notes", "commit-engine"}, False)
    first = _rows(root, ".idx/index.db")
    capsys.readouterr()

    mi._build_store(_cfg(), root, ".idx/index.db", {"notes", "commit-engine"}, False)
    second = _rows(root, ".idx/index.db")
    out = capsys.readouterr().out

    assert second == first, "a no-op rebuild changed the store"
    assert "0 files pruned" in out, out


def test_a_new_commit_is_added_without_re_embedding_the_old_ones(tmp_path, monkeypatch, capsys):
    _stub_embed(monkeypatch)
    root = _repo(tmp_path / "eng")
    (root / "docs").mkdir()
    (root / "docs" / "a.md").write_text("# A\n\nbody\n", encoding="utf-8")
    _commit(root, "docs/a.md", "feat: first")
    mi._build_store(_cfg(), root, ".idx/index.db", {"notes", "commit-engine"}, False)
    capsys.readouterr()

    _commit(root, "docs/b.md", "feat: second")
    mi._build_store(_cfg(), root, ".idx/index.db", {"notes", "commit-engine"}, False)
    out = capsys.readouterr().out

    titles = {r[3] for r in _rows(root, ".idx/index.db") if r[2] == "commit-engine"}
    assert titles == {"feat: first", "feat: second"}
    assert "0 files pruned" in out, out


def test_a_commit_layer_not_in_this_store_is_not_built(tmp_path, monkeypatch):
    """Store membership decides the seam: the data store must not hold engine commits."""
    _stub_embed(monkeypatch)
    root = _repo(tmp_path / "eng")
    (root / "docs").mkdir()
    (root / "docs" / "a.md").write_text("# A\n\nbody\n", encoding="utf-8")
    _commit(root, "docs/a.md", "feat: first")

    mi._build_store(_cfg(), root, ".idx/index.db", {"notes"}, False)
    assert {r[2] for r in _rows(root, ".idx/index.db")} == {"notes"}


def test_the_body_paths_switch_reaches_the_embedded_text(tmp_path, monkeypatch):
    """The spec calls for measuring both variants, so config must control it."""
    _stub_embed(monkeypatch)
    root = _repo(tmp_path / "eng")
    _commit(root, "scripts/action-queue.py", "feat: queue change")

    cfg_on = _cfg(layers=[{"layer": "commit-engine", "source": "git-log",
                           "repo_label": "engine", "body_paths": True}])
    mi._build_store(cfg_on, root, ".on/index.db", {"commit-engine"}, False)
    cfg_off = _cfg(layers=[{"layer": "commit-engine", "source": "git-log",
                            "repo_label": "engine", "body_paths": False}])
    mi._build_store(cfg_off, root, ".off/index.db", {"commit-engine"}, False)

    def body(store):
        conn = mi.open_store(root, store)
        b = conn.execute("SELECT body FROM notes").fetchone()[0]
        conn.close()
        return b

    assert "scripts/action-queue.py" in body(".on/index.db")
    assert "scripts/action-queue.py" not in body(".off/index.db")


def test_an_air_gapped_commit_never_reaches_the_store(tmp_path, monkeypatch):
    _stub_embed(monkeypatch)
    root = _repo(tmp_path / "dat")
    _commit(root, "docs/a.md", "public")
    _commit(root, "chronicle/personal/x.md", "private note")

    cfg = _cfg(layers=[{"layer": "commit-data", "source": "git-log", "repo_label": "data"}],
               collections={"history": ["commit-data"]})
    mi._build_store(cfg, root, ".idx/index.db", {"commit-data"}, False)
    titles = {r[3] for r in _rows(root, ".idx/index.db")}
    assert titles == {"public"}


def test_a_missing_git_repo_degrades_loudly_and_prunes_nothing(tmp_path, monkeypatch, capsys):
    """No git, no rows -- but the existing corpus must survive, not be wiped."""
    _stub_embed(monkeypatch)
    root = _repo(tmp_path / "eng")
    _commit(root, "docs/a.md", "feat: first")
    cfg = _cfg(layers=[{"layer": "commit-engine", "source": "git-log", "repo_label": "engine"}],
               collections={"code": ["commit-engine"]})
    mi._build_store(cfg, root, ".idx/index.db", {"commit-engine"}, False)
    before = _rows(root, ".idx/index.db")
    assert before
    capsys.readouterr()

    def boom(*a, **k):
        raise ValueError("not a git repository: stub")
    monkeypatch.setattr(mi, "iter_commits", boom)
    mi._build_store(cfg, root, ".idx/index.db", {"commit-engine"}, False)
    err = capsys.readouterr().err

    assert _rows(root, ".idx/index.db") == before, "a git failure wiped the corpus"
    assert "commit-engine" in err


def test_building_a_subset_of_layers_does_not_delete_the_others(tmp_path, monkeypatch):
    """The prune must stay inside the layers it walked.

    Found the hard way on 2026-08-21: measuring the two commit body variants
    meant rebuilding ONE layer, and the prune deleted 122 skill and rule rows
    that the pass never looked at. `_build_store` is private and `cmd_build`
    always passes the full set, so the CLI could not reach it -- but an A/B
    measurement is a normal thing to do, and a store that silently empties is
    the worst way to learn the call was wrong.
    """
    _stub_embed(monkeypatch)
    root = _repo(tmp_path / "eng")
    (root / "docs").mkdir()
    (root / "docs" / "a.md").write_text("# A\n\nbody\n", encoding="utf-8")
    _commit(root, "docs/a.md", "feat: first")

    mi._build_store(_cfg(), root, ".idx/index.db", {"notes", "commit-engine"}, False)
    assert {r[2] for r in _rows(root, ".idx/index.db")} == {"notes", "commit-engine"}

    # Rebuild ONLY the commit layer, as an A/B measurement would.
    mi._build_store(_cfg(), root, ".idx/index.db", {"commit-engine"}, True)
    layers = {r[2] for r in _rows(root, ".idx/index.db")}
    assert "notes" in layers, "a subset rebuild deleted a layer it never walked"


def test_a_layer_dropped_from_config_is_still_pruned(tmp_path, monkeypatch):
    """The guard above must not turn the store into a graveyard.

    Rows whose layer no longer exists anywhere in the config have no owner and
    no way back; they must still go, or removing a layer would leave its content
    answering queries forever.
    """
    _stub_embed(monkeypatch)
    root = _repo(tmp_path / "eng")
    (root / "docs").mkdir()
    (root / "docs" / "a.md").write_text("# A\n\nbody\n", encoding="utf-8")
    _commit(root, "docs/a.md", "feat: first")
    mi._build_store(_cfg(), root, ".idx/index.db", {"notes", "commit-engine"}, False)

    dropped = _cfg(layers=[{"layer": "commit-engine", "source": "git-log",
                            "repo_label": "engine"}],
                   collections={"code": ["commit-engine"]})
    mi._build_store(dropped, root, ".idx/index.db", {"commit-engine"}, False)
    assert {r[2] for r in _rows(root, ".idx/index.db")} == {"commit-engine"}
