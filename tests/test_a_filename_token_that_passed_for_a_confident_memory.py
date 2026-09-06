#!/usr/bin/env python3
"""One word shared with a FILENAME was reported as a confident memory hit.

MEASURED 2026-09-06 against the live corpus at the shipped threshold. The query
`who runs our beekeeping operation` -- nothing in the overlay is about
beekeeping -- returned:

    gap False   near_miss None   confident None
    0.3957  auto-memory/a-yards-agent-runs-as-a-background-session.md
    0.3963  auto-memory/a-systemd-timer-runs-a-narrower-suite-than-your-shell.md
    0.3775  auto-memory/a-help-probe-runs-a-script-that-has-no-help.md
    0.3769  auto-memory/claude-code-runs-matching-hooks-in-parallel.md
    0.3583  auto-memory/a-workstation-runs-a-different-program-than-ci.md

Every row shares exactly one token with the question: `runs`. Every cosine is
far below the 0.55 cut and below the near-miss floor. Across eight nonsense
controls, five came back this way, at every threshold in the calibration grid.

The mechanism: `_path_match_ids` is ungated by design, so a query naming a
folder or client finds the file even when its body is unrelated. But its output
went into `combined_sparse`, and a non-empty `combined_sparse` SUPPRESSED the
gap/near-miss branch. The payload therefore carried no `near_miss` key, and
`.claude/hooks/recall-inject.py` reads that key and nothing else, so the block
was headed `## Memory relevant to this message` rather than the warning. That
hook fired on 726 prompts in 30 days against 12 deliberate lookups, so this is
the injection path, not the query path.

Two narrower fixes were tried and rejected, recorded so they are not retried:

* Raise `PATH_TOKEN_DF_CAP`. `runs` is already under the cap of 25; the cap
  measures how many documents a token appears in, not whether it names anything.
* Filter with `content_denylist.is_ordinary_english`. MEASURED the same day: it
  returns True for `meridian`, `syria` and `patagonia`. It is a dictionary of
  the language, and it would delete the channel's own worked example.

So the boundary moved, not the candidate rule. `sparse_ids` still confers
confidence, because BM25 is floored at `threshold - SPARSE_COS_MARGIN` and is
real convergence. `path_ids` alone does not. The file still surfaces; it is
flagged.

Both directions, and the second is the one a later "simplification" would break:
a path-only match must NOT be confident, and it must STILL be returned.

Run: .venv/bin/python -m pytest \\
     tests/test_a_filename_token_that_passed_for_a_confident_memory.py -q
"""
from __future__ import annotations

import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest

WORKSPACE = Path(__file__).resolve().parent.parent
SCRIPT = WORKSPACE / "scripts" / "memory-index.py"

# Cosine is a pure function of shared vocabulary, so a document can be placed at
# a chosen distance from a query without touching ollama. Same device as
# tests/test_memory_index_near_miss.py.
# Every word any query below uses is IN the vocabulary, so an unrelated query
# yields a vector orthogonal to the documents rather than the all-1e-6
# fallback, which is parallel to everything and made a nonsense query score
# 0.67 against both fixtures on the first run of this file.
VOCAB = ["omega", "prohibited", "sailing", "regatta",
         "beekeeping", "helicopter", "maintenance", "schedule"]


def fake_embed(texts, *, model, host, batch=32, timeout=120):
    out = []
    for t in texts:
        low = t.lower()
        v = [float(low.count(w)) for w in VOCAB]
        out.append(v if any(v) else [1e-6] * len(VOCAB))
    return out


def _load():
    sys.path.insert(0, str(WORKSPACE))
    spec = importlib.util.spec_from_file_location("memory_index_path_conf", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _write(path: Path, body: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def _prepare(tmp_path, monkeypatch):
    _write(tmp_path / "config/memory-index.yaml",
           "model: bge-m3\n"
           "host: http://localhost:11434\n"
           "threshold: 0.55\n"
           "near_miss_margin: 0.12\n"
           "top_k: 8\n"
           "layers:\n"
           "  - {layer: odin, glob: 'knowledge/odin-brain/**/*.md'}\n"
           "deny_prefixes: ['_secure/']\n"
           "deny_segments: ['personal']\n")

    # The token `beekeeping` appears ONLY in this filename. Its body shares no
    # vocabulary with any query below, so its cosine is ~0: far under the cut and
    # under the near-miss floor. The only way it can be returned is the path
    # channel, which is exactly the case under test.
    _write(tmp_path / "knowledge/odin-brain/episodes/beekeeping-ledger.md",
           "---\ntitle: Ledger\n---\n\nsailing sailing regatta\n")
    # A document that genuinely answers a query about omega.
    _write(tmp_path / "knowledge/odin-brain/episodes/note-alpha.md",
           "---\ntitle: Left alone\n---\n\nomega omega prohibited prohibited\n")

    mod = _load()
    monkeypatch.setenv("HEADING_OS_DATA", str(tmp_path))
    monkeypatch.setattr(mod, "get_workspace_root", lambda: tmp_path)
    monkeypatch.setattr(mod, "embed", fake_embed)
    monkeypatch.setattr(mod, "get_classification", lambda p: "ceo-only")
    monkeypatch.setattr(mod, "model_digest", lambda **k: None)
    monkeypatch.setattr(mod, "_resolve_embed_host", lambda host=None, **k: host)
    mod.cmd_build(types.SimpleNamespace(force=True, collection="content",
                                        allow_host_fallback=False))
    return mod


def _query(mod, capsys, text):
    mod.cmd_query(types.SimpleNamespace(text=text, layer=None, collection="content",
                                        top_k=8, threshold=None, json=True))
    out = capsys.readouterr().out.strip().splitlines()
    return json.loads(out[-1])


# ============================================================
# The defect
# ============================================================

def test_a_filename_only_match_is_not_confident(tmp_path, monkeypatch, capsys):
    """The failing half. Before the fix this payload carried no `near_miss`."""
    mod = _prepare(tmp_path, monkeypatch)
    payload = _query(mod, capsys, "beekeeping")

    assert payload.get("near_miss") is True, (
        "a match on a FILENAME token alone was reported as confident memory; "
        "that is what put five of eight unanswerable questions into the "
        "injection block on 2026-09-06")
    assert payload.get("confident") is False


def test_a_filename_only_match_is_still_returned(tmp_path, monkeypatch, capsys):
    """The other half, and the one a later simplification would break.

    Deleting the path channel would also make the test above pass. The channel
    exists so a query naming a folder or a client finds the file even when the
    body is unrelated, and that must keep working.
    """
    mod = _prepare(tmp_path, monkeypatch)
    payload = _query(mod, capsys, "beekeeping")

    paths = [h["path"] for h in payload.get("hits", [])]
    assert any("beekeeping-ledger" in p for p in paths), (
        f"the path channel no longer surfaces a file named by the query at all; "
        f"got {paths}")
    assert payload.get("gap") is False
    flagged = [h for h in payload["hits"] if "beekeeping-ledger" in h["path"]]
    assert flagged[0].get("below_threshold") is True, (
        "the hit is returned unflagged, so a consumer reading the per-hit field "
        "instead of the payload key still treats it as confident")


# ============================================================
# What must not have changed
# ============================================================

def test_a_real_semantic_match_is_still_confident(tmp_path, monkeypatch, capsys):
    """A guard that flags everything is not a guard."""
    mod = _prepare(tmp_path, monkeypatch)
    payload = _query(mod, capsys, "omega prohibited")

    assert payload.get("gap") is False
    assert "near_miss" not in payload, (
        f"a genuine above-threshold match was downgraded to a lead: {payload}")
    assert "confident" not in payload
    assert any("note-alpha" in h["path"] for h in payload["hits"])


def test_a_query_matching_nothing_at_all_is_still_an_honest_gap(tmp_path,
                                                                monkeypatch,
                                                                capsys):
    """No dense, no sparse, no path, nothing near: the gap must still be said."""
    mod = _prepare(tmp_path, monkeypatch)
    payload = _query(mod, capsys, "helicopter maintenance schedule")

    assert payload.get("gap") is True, (
        f"a query with no match of any kind stopped reporting a gap: {payload}")
    assert payload.get("hits") == []


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-q"]))
